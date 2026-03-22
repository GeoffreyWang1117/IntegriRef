"""Fine-tune SciBERT on SciCite for L1 Citation Intent Classification.

Trains a 3-class classifier: background→mentioning, method→supporting, result→supporting.

Usage:
    python -m benchmarks.train_l1 [--epochs 5] [--batch-size 16] [--lr 2e-5]
                                   [--output models/intent_classifier]

SciBERT baseline on SciCite: ~84-86% accuracy (Cohan et al. 2019)
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import torch
from torch.utils.data import DataLoader, Dataset
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

from benchmarks.datasets import DATA_DIR

# ── Label mapping ──────────────────────────────────────────────────────────
# SciCite has: background, method, result (result comparison)
# Map to 3-class: 0=mentioning (background), 1=supporting (method+result)
# Note: We use 3 output classes to match SciCite exactly for training,
# then map at inference: 0=mentioning, 1=method(→supporting), 2=result(→supporting)

SCICITE_LABELS = {"background": 0, "method": 1, "result": 2}
LABEL_NAMES = ["background", "method", "result"]

# Our 3-class mapping for evaluation
OUR_LABEL_MAP = {0: "mentioning", 1: "supporting", 2: "supporting"}


class SciCiteDataset(Dataset):
    """PyTorch dataset for SciCite."""

    def __init__(self, filepath: str, tokenizer, max_length: int = 256):
        self.samples = []
        self.tokenizer = tokenizer
        self.max_length = max_length

        with open(filepath) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                label_str = rec.get("label", "").lower().strip()
                if label_str not in SCICITE_LABELS:
                    continue
                self.samples.append({
                    "text": rec["string"],
                    "label": SCICITE_LABELS[label_str],
                })

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


def evaluate(model, dataloader, device):
    """Evaluate model on a dataset. Returns (accuracy, per_class_f1, loss)."""
    model.eval()
    correct = 0
    total = 0
    total_loss = 0.0
    all_preds = []
    all_labels = []

    with torch.no_grad():
        for batch in dataloader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            total_loss += outputs.loss.item() * labels.size(0)

            preds = outputs.logits.argmax(dim=-1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)
            all_preds.extend(preds.cpu().tolist())
            all_labels.extend(labels.cpu().tolist())

    acc = correct / total if total > 0 else 0
    avg_loss = total_loss / total if total > 0 else 0

    # Per-class F1
    per_class = {}
    for cls_idx, cls_name in enumerate(LABEL_NAMES):
        tp = sum(1 for p, l in zip(all_preds, all_labels) if p == cls_idx and l == cls_idx)
        fp = sum(1 for p, l in zip(all_preds, all_labels) if p == cls_idx and l != cls_idx)
        fn = sum(1 for p, l in zip(all_preds, all_labels) if p != cls_idx and l == cls_idx)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        per_class[cls_name] = {"precision": prec, "recall": rec, "f1": f1}

    # Also compute our 2-class accuracy (mentioning vs supporting)
    our_correct = 0
    for p, l in zip(all_preds, all_labels):
        our_p = OUR_LABEL_MAP[p]
        our_l = OUR_LABEL_MAP[l]
        if our_p == our_l:
            our_correct += 1
    our_acc = our_correct / total if total > 0 else 0

    return acc, per_class, avg_loss, our_acc


def main():
    parser = argparse.ArgumentParser(description="Fine-tune SciBERT on SciCite")
    parser.add_argument("--model-name", type=str,
                        default="allenai/scibert_scivocab_uncased",
                        help="Base model to fine-tune")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=str, default="models/intent_classifier",
                        help="Output directory for the fine-tuned model")
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()

    # Seed
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # Load tokenizer & model
    print(f"Loading base model: {args.model_name}")
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=3,  # background, method, result
    ).to(device)

    # Load datasets
    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR / "scicite"
    print(f"Loading SciCite from {data_dir}")

    train_ds = SciCiteDataset(str(data_dir / "train.jsonl"), tokenizer, args.max_length)
    dev_ds = SciCiteDataset(str(data_dir / "dev.jsonl"), tokenizer, args.max_length)
    test_ds = SciCiteDataset(str(data_dir / "test.jsonl"), tokenizer, args.max_length)

    print(f"  Train: {len(train_ds)}, Dev: {len(dev_ds)}, Test: {len(test_ds)}")

    # Label distribution
    for name, ds in [("train", train_ds), ("dev", dev_ds), ("test", test_ds)]:
        counts = [0, 0, 0]
        for s in ds.samples:
            counts[s["label"]] += 1
        print(f"  {name}: bg={counts[0]} method={counts[1]} result={counts[2]}")

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              num_workers=2, pin_memory=True)
    dev_loader = DataLoader(dev_ds, batch_size=args.batch_size * 2,
                            num_workers=2, pin_memory=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size * 2,
                             num_workers=2, pin_memory=True)

    # Optimizer & scheduler
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=warmup_steps, num_training_steps=total_steps)

    # Training loop
    best_dev_acc = 0.0
    best_epoch = -1

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
                labels=labels,
            )
            loss = outputs.loss

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / n_batches

        # Evaluate on dev
        dev_acc, dev_per_class, dev_loss, dev_our_acc = evaluate(
            model, dev_loader, device)

        macro_f1 = sum(m["f1"] for m in dev_per_class.values()) / len(dev_per_class)

        print(f"Epoch {epoch+1}/{args.epochs}: "
              f"train_loss={avg_train_loss:.4f} dev_loss={dev_loss:.4f} "
              f"dev_acc={dev_acc:.4f} dev_macro_f1={macro_f1:.4f} "
              f"dev_our_acc={dev_our_acc:.4f}")
        for cls_name, m in dev_per_class.items():
            print(f"  {cls_name:12s}: P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f}")

        # Save best model
        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            best_epoch = epoch + 1
            out_path = Path(args.output)
            out_path.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(out_path)
            tokenizer.save_pretrained(out_path)
            print(f"  → Saved best model (dev_acc={dev_acc:.4f})")

    # Final evaluation on test set
    print(f"\n--- Best model from epoch {best_epoch} (dev_acc={best_dev_acc:.4f}) ---")
    # Reload best model
    best_model = AutoModelForSequenceClassification.from_pretrained(
        args.output).to(device)
    test_acc, test_per_class, test_loss, test_our_acc = evaluate(
        best_model, test_loader, device)

    macro_f1 = sum(m["f1"] for m in test_per_class.values()) / len(test_per_class)

    print(f"\nTest Results:")
    print(f"  SciCite 3-class accuracy: {test_acc:.4f}")
    print(f"  SciCite macro F1:         {macro_f1:.4f}")
    print(f"  Our 2-class accuracy:     {test_our_acc:.4f}")
    for cls_name, m in test_per_class.items():
        print(f"  {cls_name:12s}: P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f}")

    # Save results
    results = {
        "model": args.model_name,
        "best_epoch": best_epoch,
        "best_dev_acc": best_dev_acc,
        "test_acc": test_acc,
        "test_macro_f1": macro_f1,
        "test_our_acc": test_our_acc,
        "test_per_class": test_per_class,
        "args": vars(args),
    }
    results_path = Path(args.output) / "training_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
