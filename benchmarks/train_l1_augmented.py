"""Enhanced L1 intent classifier training with data augmentation.

Strategies:
1. Class-balanced oversampling with text augmentation
2. ACL-ARC joint training (adds contrasting examples)
3. Label smoothing for better calibration

Target: 89%+ accuracy on SciCite test (up from 87.0% baseline).

Usage:
    python -m benchmarks.train_l1_augmented [--epochs 5] [--batch-size 16]
    python -m benchmarks.train_l1_augmented --dry-run  # data stats only
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from collections import Counter
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

from benchmarks.datasets import DATA_DIR

# ── Label mapping ──────────────────────────────────────────────────────────
# SciCite: background=0, method=1, result=2
# Our mapping at inference: 0→mentioning, 1→supporting, 2→supporting

SCICITE_LABELS = {"background": 0, "method": 1, "result": 2}
LABEL_NAMES = ["background", "method", "result"]
OUR_LABEL_MAP = {0: "mentioning", 1: "supporting", 2: "supporting"}

# ACL-ARC → SciCite label mapping
ACL_ARC_LABEL_MAP = {
    "Background": 0,           # → background (mentioning)
    "CompareOrContrast": 1,    # → method (supporting, closest match)
    "Uses": 1,                 # → method (supporting)
    "Extends": 1,              # → method (supporting)
    "Motivation": 2,           # → result (supporting)
    "Future": 0,               # → background (mentioning)
}


# ── Text augmentation ─────────────────────────────────────────────────────

# Simple synonym map for scientific text (no external model needed)
_SYNONYM_MAP = {
    "show": "demonstrate",
    "shows": "demonstrates",
    "showed": "demonstrated",
    "shown": "demonstrated",
    "demonstrate": "show",
    "demonstrates": "shows",
    "demonstrated": "showed",
    "use": "employ",
    "uses": "employs",
    "used": "employed",
    "using": "employing",
    "employ": "use",
    "employs": "uses",
    "employed": "used",
    "employing": "using",
    "propose": "introduce",
    "proposes": "introduces",
    "proposed": "introduced",
    "introduce": "propose",
    "introduces": "proposes",
    "introduced": "proposed",
    "achieve": "obtain",
    "achieves": "obtains",
    "achieved": "obtained",
    "obtain": "achieve",
    "obtains": "achieves",
    "obtained": "achieved",
    "improve": "enhance",
    "improves": "enhances",
    "improved": "enhanced",
    "enhance": "improve",
    "enhances": "improves",
    "enhanced": "improved",
    "significant": "substantial",
    "substantial": "significant",
    "method": "approach",
    "approach": "method",
    "model": "framework",
    "framework": "model",
    "results": "findings",
    "findings": "results",
    "performance": "accuracy",
    "accuracy": "performance",
    "similar": "comparable",
    "comparable": "similar",
    "study": "work",
    "work": "study",
    "previous": "prior",
    "prior": "previous",
    "suggest": "indicate",
    "suggests": "indicates",
    "suggested": "indicated",
    "indicate": "suggest",
    "indicates": "suggests",
    "indicated": "suggested",
    "evaluate": "assess",
    "evaluates": "assesses",
    "evaluated": "assessed",
    "analysis": "examination",
    "examination": "analysis",
    "effective": "successful",
    "successful": "effective",
    "outperform": "surpass",
    "outperforms": "surpasses",
    "outperformed": "surpassed",
}


class TextAugmenter:
    """Simple text augmentation without external models.

    Implements three lightweight augmentation strategies suitable for
    citation intent classification text:
    - Random word deletion (10% probability per word)
    - Random adjacent word swap (15% probability per position)
    - Random word insertion (duplicate a random word at a random position)
    - Synonym replacement using a curated scientific synonym map
    """

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def random_deletion(self, text: str, p: float = 0.1) -> str:
        """Randomly delete words with probability p.

        Always keeps at least one word. Preserves citation markers like
        [CIT], [1], (Author, Year).
        """
        words = text.split()
        if len(words) <= 1:
            return text

        # Protect citation markers
        kept = []
        for w in words:
            is_citation = (
                re.match(r"^\[.*\]$", w)
                or re.match(r"^\(.*\)$", w)
                or w in ("[CIT]",)
            )
            if is_citation or self.rng.random() >= p:
                kept.append(w)

        # Ensure at least one word remains
        if not kept:
            kept = [self.rng.choice(words)]

        return " ".join(kept)

    def random_swap(self, text: str, n: int = 2) -> str:
        """Randomly swap n pairs of adjacent words.

        For each position, swap with 15% probability, up to n total swaps.
        """
        words = text.split()
        if len(words) < 2:
            return text

        words = list(words)
        swaps_done = 0
        for i in range(len(words) - 1):
            if swaps_done >= n:
                break
            if self.rng.random() < 0.15:
                words[i], words[i + 1] = words[i + 1], words[i]
                swaps_done += 1

        return " ".join(words)

    def random_insertion(self, text: str, n: int = 1) -> str:
        """Insert a random word from the sentence at a random position."""
        words = text.split()
        if len(words) < 2:
            return text

        words = list(words)
        for _ in range(n):
            word_to_insert = self.rng.choice(words)
            pos = self.rng.randint(0, len(words))
            words.insert(pos, word_to_insert)

        return " ".join(words)

    def synonym_replacement(self, text: str, p: float = 0.15) -> str:
        """Replace words with synonyms from the curated map.

        Args:
            text: Input text.
            p: Probability of replacing each eligible word.
        """
        words = text.split()
        result = []
        for w in words:
            w_lower = w.lower()
            if w_lower in _SYNONYM_MAP and self.rng.random() < p:
                replacement = _SYNONYM_MAP[w_lower]
                # Preserve capitalization of first letter
                if w[0].isupper():
                    replacement = replacement[0].upper() + replacement[1:]
                result.append(replacement)
            else:
                result.append(w)
        return " ".join(result)

    def augment(self, text: str) -> str:
        """Apply a random combination of augmentation strategies.

        Picks 1-2 strategies randomly and applies them sequentially.
        """
        strategies = [
            lambda t: self.random_deletion(t, p=0.1),
            lambda t: self.random_swap(t, n=2),
            lambda t: self.random_insertion(t, n=1),
            lambda t: self.synonym_replacement(t, p=0.15),
        ]

        # Apply 1-2 randomly chosen strategies
        n_strategies = self.rng.randint(1, 2)
        chosen = self.rng.sample(strategies, n_strategies)
        result = text
        for fn in chosen:
            result = fn(result)
        return result


# ── Dataset ────────────────────────────────────────────────────────────────

class AugmentedSciCiteDataset(Dataset):
    """SciCite dataset with class-balanced oversampling and augmentation.

    When augment=True and oversample=True:
    1. Counts samples per class
    2. Oversamples minority classes to match the majority class count
    3. Creates augmented copies of oversampled instances
    """

    def __init__(
        self,
        samples: list[dict],
        tokenizer,
        max_length: int = 256,
        augment: bool = True,
        oversample: bool = True,
        augment_factor: int = 2,
        seed: int = 42,
    ):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.augmenter = TextAugmenter(seed=seed) if augment else None

        # Group by label
        by_label: dict[int, list[dict]] = {}
        for s in samples:
            lbl = s["label"]
            by_label.setdefault(lbl, []).append(s)

        self.samples = list(samples)

        if oversample and len(by_label) > 1:
            max_count = max(len(v) for v in by_label.values())

            for lbl, items in by_label.items():
                deficit = max_count - len(items)
                if deficit <= 0:
                    continue

                rng = random.Random(seed + lbl)
                # Oversample with augmentation
                for i in range(deficit):
                    original = rng.choice(items)
                    new_sample = dict(original)
                    if augment and self.augmenter:
                        new_sample["text"] = self.augmenter.augment(
                            original["text"])
                        new_sample["_augmented"] = True
                    self.samples.append(new_sample)

        # Create additional augmented copies of minority samples
        if augment and self.augmenter and augment_factor > 1:
            augmented_extras = []
            for lbl, items in by_label.items():
                # Only augment non-majority classes
                if len(items) == max(len(v) for v in by_label.values()):
                    continue
                for _ in range(augment_factor - 1):
                    for item in items:
                        new_sample = dict(item)
                        new_sample["text"] = self.augmenter.augment(
                            item["text"])
                        new_sample["_augmented"] = True
                        augmented_extras.append(new_sample)
            self.samples.extend(augmented_extras)

        # Shuffle
        rng = random.Random(seed)
        rng.shuffle(self.samples)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        encoding = self.tokenizer(
            sample["text"],
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "labels": torch.tensor(sample["label"], dtype=torch.long),
        }

    def label_distribution(self) -> dict[str, int]:
        """Return label counts for this dataset."""
        counts = Counter(s["label"] for s in self.samples)
        return {LABEL_NAMES[k]: v for k, v in sorted(counts.items())}


# ── Data loading ───────────────────────────────────────────────────────────

def load_scicite_samples(filepath: str) -> list[dict]:
    """Load SciCite JSONL file into list of {text, label} dicts."""
    samples = []
    with open(filepath) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            label_str = rec.get("label", "").lower().strip()
            if label_str not in SCICITE_LABELS:
                continue
            samples.append({
                "text": rec["string"],
                "label": SCICITE_LABELS[label_str],
            })
    return samples


def load_acl_arc() -> list[dict]:
    """Load ACL-ARC dataset from HuggingFace and map to SciCite labels.

    Uses the ``citation_intent_classification`` dataset from the
    HuggingFace datasets hub.

    Returns list of {"text": ..., "label": 0/1/2} dicts.
    Falls back gracefully if dataset can't be loaded.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        print("  [warn] HuggingFace datasets library not installed, "
              "skipping ACL-ARC")
        return []

    try:
        ds = load_dataset("citation_intent_classification")
    except Exception as e:
        print(f"  [warn] Failed to load ACL-ARC dataset: {e}")
        return []

    # ACL-ARC label names (from the dataset features)
    # The dataset uses integer labels; get the string names from features
    try:
        label_names = ds["train"].features["label"].names
    except (KeyError, AttributeError):
        label_names = [
            "Background", "CompareOrContrast", "Extends",
            "Future", "Motivation", "Uses",
        ]

    samples = []
    for split in ["train", "validation", "test"]:
        if split not in ds:
            continue
        for rec in ds[split]:
            text = rec.get("text", rec.get("string", ""))
            label_idx = rec.get("label", -1)

            if isinstance(label_idx, int) and 0 <= label_idx < len(label_names):
                label_name = label_names[label_idx]
            elif isinstance(label_idx, str):
                label_name = label_idx
            else:
                continue

            mapped_label = ACL_ARC_LABEL_MAP.get(label_name)
            if mapped_label is None:
                continue

            samples.append({
                "text": text,
                "label": mapped_label,
                "source": "acl_arc",
            })

    return samples


# ── Evaluation ─────────────────────────────────────────────────────────────

def evaluate(model, tokenizer, test_data, device, max_length=256):
    """Evaluate model on test data. Returns (accuracy, per_class_f1, our_acc).

    Args:
        model: The trained model.
        tokenizer: Tokenizer for encoding inputs.
        test_data: List of {text, label} dicts.
        device: torch device.
        max_length: Maximum sequence length.

    Returns:
        Tuple of (3-class accuracy, per-class metrics dict, 2-class accuracy).
    """
    model.eval()
    all_preds = []
    all_labels = []

    # Process in batches
    batch_size = 32
    for i in range(0, len(test_data), batch_size):
        batch = test_data[i:i + batch_size]
        texts = [s["text"] for s in batch]
        labels = [s["label"] for s in batch]

        encoding = tokenizer(
            texts,
            truncation=True,
            max_length=max_length,
            padding="max_length",
            return_tensors="pt",
        )

        with torch.no_grad():
            outputs = model(
                input_ids=encoding["input_ids"].to(device),
                attention_mask=encoding["attention_mask"].to(device),
            )
            preds = outputs.logits.argmax(dim=-1).cpu().tolist()

        all_preds.extend(preds)
        all_labels.extend(labels)

    total = len(all_labels)
    correct = sum(1 for p, l in zip(all_preds, all_labels) if p == l)
    acc = correct / total if total > 0 else 0

    # Per-class F1
    per_class = {}
    for cls_idx, cls_name in enumerate(LABEL_NAMES):
        tp = sum(1 for p, l in zip(all_preds, all_labels)
                 if p == cls_idx and l == cls_idx)
        fp = sum(1 for p, l in zip(all_preds, all_labels)
                 if p == cls_idx and l != cls_idx)
        fn = sum(1 for p, l in zip(all_preds, all_labels)
                 if p != cls_idx and l == cls_idx)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        per_class[cls_name] = {"precision": prec, "recall": rec, "f1": f1}

    # 2-class accuracy (mentioning vs supporting)
    our_correct = sum(
        1 for p, l in zip(all_preds, all_labels)
        if OUR_LABEL_MAP[p] == OUR_LABEL_MAP[l]
    )
    our_acc = our_correct / total if total > 0 else 0

    macro_f1 = sum(m["f1"] for m in per_class.values()) / len(per_class)

    return acc, per_class, our_acc, macro_f1


# ── Training ───────────────────────────────────────────────────────────────

def train(args):
    """Main training loop with augmentation, oversampling, and label smoothing.

    Pipeline:
    1. Load SciCite train/dev/test
    2. Optionally load ACL-ARC and merge into training data
    3. Apply class-balanced oversampling with text augmentation
    4. Train SciBERT with CrossEntropyLoss(label_smoothing)
    5. Evaluate on SciCite dev each epoch, save best checkpoint
    6. Final evaluation on SciCite test
    7. Save training metrics to JSON
    """
    # Seed everything
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # ── 1. Load SciCite ────────────────────────────────────────────────
    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR / "scicite"
    print(f"\nLoading SciCite from {data_dir}")

    train_path = data_dir / "train.jsonl"
    dev_path = data_dir / "dev.jsonl"
    test_path = data_dir / "test.jsonl"

    if not train_path.exists():
        print(f"ERROR: {train_path} not found. Download SciCite first:")
        print("  python -m benchmarks.download scicite")
        return

    train_samples = load_scicite_samples(str(train_path))
    dev_samples = load_scicite_samples(str(dev_path))
    test_samples = load_scicite_samples(str(test_path))

    print(f"  SciCite train: {len(train_samples)}")
    print(f"  SciCite dev:   {len(dev_samples)}")
    print(f"  SciCite test:  {len(test_samples)}")

    # Label distribution (original)
    train_counts = Counter(s["label"] for s in train_samples)
    for lbl_idx, name in enumerate(LABEL_NAMES):
        print(f"    {name}: {train_counts.get(lbl_idx, 0)}")

    # ── 2. Load ACL-ARC (optional) ─────────────────────────────────────
    acl_arc_samples = []
    if args.acl_arc:
        print("\nLoading ACL-ARC dataset...")
        acl_arc_samples = load_acl_arc()
        if acl_arc_samples:
            print(f"  ACL-ARC samples loaded: {len(acl_arc_samples)}")
            arc_counts = Counter(s["label"] for s in acl_arc_samples)
            for lbl_idx, name in enumerate(LABEL_NAMES):
                print(f"    {name}: {arc_counts.get(lbl_idx, 0)}")
        else:
            print("  ACL-ARC not available, proceeding with SciCite only")

    # Merge ACL-ARC into training data
    combined_train = list(train_samples) + acl_arc_samples
    print(f"\nCombined training samples: {len(combined_train)}")
    combined_counts = Counter(s["label"] for s in combined_train)
    for lbl_idx, name in enumerate(LABEL_NAMES):
        print(f"  {name}: {combined_counts.get(lbl_idx, 0)}")

    # ── Dry run: just print stats and exit ─────────────────────────────
    if args.dry_run:
        print("\n--- Dry run: simulating augmented dataset ---")
        # Simulate the augmented dataset to show final sizes
        augmenter = TextAugmenter(seed=args.seed)

        # Show augmentation examples
        print("\nAugmentation examples:")
        for i, sample in enumerate(train_samples[:3]):
            print(f"\n  Original:  {sample['text'][:100]}...")
            print(f"  Deletion:  {augmenter.random_deletion(sample['text'], 0.1)[:100]}...")
            print(f"  Swap:      {augmenter.random_swap(sample['text'], 2)[:100]}...")
            print(f"  Synonym:   {augmenter.synonym_replacement(sample['text'], 0.15)[:100]}...")
            print(f"  Combined:  {augmenter.augment(sample['text'])[:100]}...")

        # Compute expected sizes after oversampling + augmentation
        max_count = max(combined_counts.values())
        total_after_oversample = max_count * len(combined_counts)
        minority_count = sum(
            v for v in combined_counts.values()
            if v < max_count
        )
        augmented_extras = minority_count * (args.augment_factor - 1)
        total_final = total_after_oversample + augmented_extras

        print(f"\nExpected dataset sizes:")
        print(f"  Original training:     {len(combined_train)}")
        print(f"  After oversampling:    ~{total_after_oversample}")
        print(f"  After augmentation:    ~{total_final}")
        print(f"  Augment factor:        {args.augment_factor}")
        print(f"  Label smoothing:       {args.label_smoothing}")
        print(f"\nTraining config:")
        print(f"  Model:     {args.model_name}")
        print(f"  Epochs:    {args.epochs}")
        print(f"  Batch:     {args.batch_size}")
        print(f"  LR:        {args.lr}")
        print(f"  Max len:   {args.max_length}")
        print(f"  Output:    {args.output}")
        print("\n--- Dry run complete. Remove --dry-run to train. ---")
        return

    # ── 3. Build datasets ──────────────────────────────────────────────
    print(f"\nLoading tokenizer: {args.model_name}")
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)

    train_ds = AugmentedSciCiteDataset(
        samples=combined_train,
        tokenizer=tokenizer,
        max_length=args.max_length,
        augment=True,
        oversample=True,
        augment_factor=args.augment_factor,
        seed=args.seed,
    )

    print(f"\nAugmented training set: {len(train_ds)} samples")
    dist = train_ds.label_distribution()
    for name, count in dist.items():
        print(f"  {name}: {count}")

    # Dev and test: no augmentation or oversampling
    dev_ds = AugmentedSciCiteDataset(
        samples=dev_samples,
        tokenizer=tokenizer,
        max_length=args.max_length,
        augment=False,
        oversample=False,
    )
    test_ds = AugmentedSciCiteDataset(
        samples=test_samples,
        tokenizer=tokenizer,
        max_length=args.max_length,
        augment=False,
        oversample=False,
    )

    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        num_workers=2, pin_memory=True,
    )
    dev_loader = DataLoader(
        dev_ds, batch_size=args.batch_size * 2,
        num_workers=2, pin_memory=True,
    )

    # ── 4. Model ───────────────────────────────────────────────────────
    print(f"\nLoading model: {args.model_name}")
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=3,
    ).to(device)

    # ── 5. Loss with label smoothing ───────────────────────────────────
    criterion = nn.CrossEntropyLoss(label_smoothing=args.label_smoothing)
    print(f"Label smoothing: {args.label_smoothing}")

    # ── 6. Optimizer & scheduler ───────────────────────────────────────
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=0.01)
    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_steps,
    )
    print(f"Total steps: {total_steps}, warmup: {warmup_steps}")

    # ── 7. Training loop ──────────────────────────────────────────────
    best_dev_acc = 0.0
    best_epoch = -1
    training_history = []

    out_path = Path(args.output)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Starting training for {args.epochs} epochs")
    print(f"{'='*60}\n")

    for epoch in range(args.epochs):
        model.train()
        epoch_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
            )
            # Use label-smoothed loss instead of model's built-in loss
            loss = criterion(outputs.logits, labels)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / n_batches

        # Evaluate on dev set
        dev_acc, dev_per_class, dev_our_acc, dev_macro_f1 = evaluate(
            model, tokenizer, dev_samples, device, args.max_length)

        epoch_record = {
            "epoch": epoch + 1,
            "train_loss": round(avg_train_loss, 4),
            "dev_acc": round(dev_acc, 4),
            "dev_macro_f1": round(dev_macro_f1, 4),
            "dev_our_acc": round(dev_our_acc, 4),
            "dev_per_class": {
                k: {mk: round(mv, 4) for mk, mv in v.items()}
                for k, v in dev_per_class.items()
            },
        }
        training_history.append(epoch_record)

        print(
            f"Epoch {epoch+1}/{args.epochs}: "
            f"train_loss={avg_train_loss:.4f} "
            f"dev_acc={dev_acc:.4f} "
            f"dev_macro_f1={dev_macro_f1:.4f} "
            f"dev_our_acc={dev_our_acc:.4f}"
        )
        for cls_name, m in dev_per_class.items():
            print(
                f"  {cls_name:12s}: "
                f"P={m['precision']:.3f} "
                f"R={m['recall']:.3f} "
                f"F1={m['f1']:.3f}"
            )

        # Save best model (by dev accuracy)
        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            best_epoch = epoch + 1
            model.save_pretrained(out_path)
            tokenizer.save_pretrained(out_path)
            print(f"  -> Saved best model (dev_acc={dev_acc:.4f})")

    # ── 8. Final evaluation on test set ────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Best model from epoch {best_epoch} (dev_acc={best_dev_acc:.4f})")
    print(f"{'='*60}\n")

    # Reload best checkpoint
    best_model = AutoModelForSequenceClassification.from_pretrained(
        str(out_path)).to(device)

    test_acc, test_per_class, test_our_acc, test_macro_f1 = evaluate(
        best_model, tokenizer, test_samples, device, args.max_length)

    print("Test Results:")
    print(f"  SciCite 3-class accuracy: {test_acc:.4f}")
    print(f"  SciCite macro F1:         {test_macro_f1:.4f}")
    print(f"  Our 2-class accuracy:     {test_our_acc:.4f}")
    for cls_name, m in test_per_class.items():
        print(
            f"  {cls_name:12s}: "
            f"P={m['precision']:.3f} "
            f"R={m['recall']:.3f} "
            f"F1={m['f1']:.3f}"
        )

    # ── 9. Save results ───────────────────────────────────────────────
    results = {
        "model": args.model_name,
        "strategies": {
            "oversampling": True,
            "augmentation": True,
            "augment_factor": args.augment_factor,
            "acl_arc": args.acl_arc and len(acl_arc_samples) > 0,
            "acl_arc_samples": len(acl_arc_samples),
            "label_smoothing": args.label_smoothing,
        },
        "best_epoch": best_epoch,
        "best_dev_acc": round(best_dev_acc, 4),
        "test_acc": round(test_acc, 4),
        "test_macro_f1": round(test_macro_f1, 4),
        "test_our_acc": round(test_our_acc, 4),
        "test_per_class": {
            k: {mk: round(mv, 4) for mk, mv in v.items()}
            for k, v in test_per_class.items()
        },
        "training_history": training_history,
        "train_size_original": len(combined_train),
        "train_size_augmented": len(train_ds),
        "args": vars(args),
    }

    results_path = out_path / "training_results_v2.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {results_path}")

    return results


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enhanced L1 training with augmentation + ACL-ARC")
    parser.add_argument("--model-name", type=str,
                        default="allenai/scibert_scivocab_uncased",
                        help="Base model to fine-tune")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--label-smoothing", type=float, default=0.1,
                        help="Label smoothing factor (0=hard labels)")
    parser.add_argument("--augment-factor", type=int, default=2,
                        help="Number of augmented copies per minority sample")
    parser.add_argument("--acl-arc", action="store_true", default=True,
                        help="Include ACL-ARC dataset")
    parser.add_argument("--no-acl-arc", action="store_false", dest="acl_arc",
                        help="Exclude ACL-ARC dataset")
    parser.add_argument("--output", type=str,
                        default="models/intent_classifier_v2",
                        help="Output directory for the fine-tuned model")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Path to SciCite data directory")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true",
                        help="Load data and print statistics without training")
    args = parser.parse_args()
    train(args)
