"""Fine-tune DeBERTa-v3 on SciFact for L2 Claim Verification.

Creates NLI training pairs from SciFact gold annotations:
  - Claim + gold evidence sentences → ENTAILMENT or CONTRADICTION
  - Claim + random non-evidence sentences → NEUTRAL

Usage:
    python -m benchmarks.train_l2 [--epochs 10] [--batch-size 16] [--lr 1e-5]
                                   [--output models/scifact_nli]
"""

from __future__ import annotations

import argparse
import json
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


# NLI labels: 0=contradiction, 1=entailment, 2=neutral
# (matches cross-encoder/nli-deberta label order)
LABEL_MAP = {"SUPPORT": 1, "CONTRADICT": 0}
LABEL_NAMES = ["contradiction", "entailment", "neutral"]


def build_nli_pairs(data_dir: Path, split: str = "train") -> list[dict]:
    """Build NLI training pairs from SciFact annotations.

    For each claim with evidence:
      - Gold evidence sentences with SUPPORT label → entailment (1)
      - Gold evidence sentences with CONTRADICT label → contradiction (0)
      - Random non-evidence sentences from same abstract → neutral (2)
      - Random sentences from other abstracts → neutral (2)
    """
    corpus_path = data_dir / "corpus.jsonl"
    claims_path = data_dir / f"claims_{split}.jsonl"

    if not corpus_path.exists() or not claims_path.exists():
        print(f"Missing files: {corpus_path} or {claims_path}")
        return []

    # Load corpus
    corpus = {}
    with open(corpus_path) as f:
        for line in f:
            doc = json.loads(line)
            doc_id = doc.get("doc_id", doc.get("id"))
            corpus[doc_id] = doc.get("abstract", [])

    # All corpus sentences for negative sampling
    all_sentences = []
    for doc_id, abstract in corpus.items():
        for sent in abstract:
            if len(sent) > 20:
                all_sentences.append(sent)

    # Build pairs
    pairs = []
    with open(claims_path) as f:
        for line in f:
            claim_data = json.loads(line)
            claim_text = claim_data.get("claim", "")
            evidence = claim_data.get("evidence", {})

            if not evidence:
                continue

            for doc_id_str, rationales in evidence.items():
                doc_id = int(doc_id_str)
                abstract = corpus.get(doc_id, [])

                for rat in rationales:
                    label_str = rat.get("label", "")
                    sent_ids = rat.get("sentences", [])
                    nli_label = LABEL_MAP.get(label_str)

                    if nli_label is None:
                        continue

                    # Gold evidence sentences
                    evidence_sents = []
                    for sid in sent_ids:
                        if sid < len(abstract):
                            evidence_sents.append(abstract[sid])

                    if evidence_sents:
                        # Positive pair: claim + gold evidence
                        pairs.append({
                            "premise": " ".join(evidence_sents),
                            "hypothesis": claim_text,
                            "label": nli_label,
                        })

                    # Negative pairs: non-evidence sentences from same abstract
                    non_evidence = [abstract[i] for i in range(len(abstract))
                                    if i not in sent_ids and len(abstract[i]) > 20]
                    if non_evidence:
                        # Sample up to 2 negative pairs per positive
                        for neg_sent in random.sample(non_evidence,
                                                       min(2, len(non_evidence))):
                            pairs.append({
                                "premise": neg_sent,
                                "hypothesis": claim_text,
                                "label": 2,  # neutral
                            })

                    # Hard negative: random sentence from other abstract
                    if all_sentences:
                        rand_sent = random.choice(all_sentences)
                        pairs.append({
                            "premise": rand_sent,
                            "hypothesis": claim_text,
                            "label": 2,  # neutral
                        })

    return pairs


class NLIPairDataset(Dataset):
    """PyTorch dataset for NLI pairs."""

    def __init__(self, pairs: list[dict], tokenizer, max_length: int = 512):
        self.pairs = pairs
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, idx):
        pair = self.pairs[idx]
        encoding = self.tokenizer(
            pair["premise"],
            pair["hypothesis"],
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "labels": torch.tensor(pair["label"], dtype=torch.long),
        }


def evaluate(model, dataloader, device):
    """Evaluate NLI model. Returns (accuracy, per_class_metrics, loss)."""
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

            outputs = model(input_ids=input_ids, attention_mask=attention_mask,
                            labels=labels)
            total_loss += outputs.loss.item() * labels.size(0)

            preds = outputs.logits.argmax(dim=-1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)
            all_preds.extend(preds.cpu().tolist())
            all_labels.extend(labels.cpu().tolist())

    acc = correct / total if total > 0 else 0
    avg_loss = total_loss / total if total > 0 else 0

    per_class = {}
    for cls_idx, cls_name in enumerate(LABEL_NAMES):
        tp = sum(1 for p, l in zip(all_preds, all_labels) if p == cls_idx and l == cls_idx)
        fp = sum(1 for p, l in zip(all_preds, all_labels) if p == cls_idx and l != cls_idx)
        fn = sum(1 for p, l in zip(all_preds, all_labels) if p != cls_idx and l == cls_idx)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        per_class[cls_name] = {"P": round(prec, 3), "R": round(rec, 3),
                                "F1": round(f1, 3), "TP": tp, "FP": fp, "FN": fn}

    return acc, per_class, avg_loss


def evaluate_scifact_task(model, tokenizer, data_dir, device, split="dev"):
    """Evaluate on the actual SciFact task (claim-level, not pair-level)."""
    from benchmarks.datasets import load_scifact

    data = load_scifact(str(data_dir))
    all_samples = data.get("samples_l2", [])

    # Filter to the right split
    claims_path = data_dir / f"claims_{split}.jsonl"
    split_ids = set()
    if claims_path.exists():
        with open(claims_path) as f:
            for line in f:
                c = json.loads(line)
                split_ids.add(str(c.get("id", "")))

    samples = [s for s in all_samples if s.evidence and s.claim_id in split_ids]
    if not samples:
        samples = [s for s in all_samples if s.evidence]

    import re
    correct = 0
    total = 0

    _LABEL_MAP = {
        "SUPPORT": "SUPPORTS", "SUPPORTS": "SUPPORTS",
        "CONTRADICT": "REFUTES", "REFUTES": "REFUTES",
    }

    model.eval()
    for sample in samples:
        evidence_text = " ".join(sample.evidence)
        claim = sample.claim[:500]

        # Per-sentence scoring
        sentences = re.split(r'(?<=[.!?])\s+', evidence_text.strip())
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
        if not sentences:
            sentences = [evidence_text[:1500]]

        best_ent, best_con = 0.0, 0.0
        for sent in sentences[:10]:
            inputs = tokenizer(sent, claim, return_tensors="pt",
                               truncation=True, max_length=512)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            with torch.no_grad():
                logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)[0].cpu().tolist()
            con_s, ent_s, neu_s = probs[0], probs[1], probs[2]
            best_ent = max(best_ent, ent_s)
            best_con = max(best_con, con_s)

        # Classify
        if best_con > best_ent and best_con > 0.3:
            pred = "REFUTES"
        elif best_ent > 0.3:
            pred = "SUPPORTS"
        else:
            pred = "NOT_ENOUGH_INFO"

        expected = _LABEL_MAP.get(sample.expected_label, "NOT_ENOUGH_INFO")
        if pred == expected:
            correct += 1
        total += 1

    acc = correct / total if total > 0 else 0
    return acc, total


def main():
    parser = argparse.ArgumentParser(description="Fine-tune NLI on SciFact")
    parser.add_argument("--model-name", type=str,
                        default="cross-encoder/nli-deberta-v3-base",
                        help="Base NLI model to fine-tune")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=str, default="models/scifact_nli")
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()

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
        args.model_name, num_labels=3).to(device)

    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR / "scifact"

    # Build training pairs
    print("Building NLI training pairs from SciFact train split...")
    train_pairs = build_nli_pairs(data_dir, "train")
    dev_pairs = build_nli_pairs(data_dir, "dev")

    # Label distribution
    for name, pairs in [("train", train_pairs), ("dev", dev_pairs)]:
        counts = [0, 0, 0]
        for p in pairs:
            counts[p["label"]] += 1
        print(f"  {name}: contradiction={counts[0]} entailment={counts[1]} neutral={counts[2]} total={len(pairs)}")

    train_ds = NLIPairDataset(train_pairs, tokenizer, args.max_length)
    dev_ds = NLIPairDataset(dev_pairs, tokenizer, args.max_length)

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              num_workers=2, pin_memory=True)
    dev_loader = DataLoader(dev_ds, batch_size=args.batch_size * 2,
                            num_workers=2, pin_memory=True)

    # Optimizer & scheduler
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=warmup_steps, num_training_steps=total_steps)

    # Pre-training evaluation
    print("\nPre-training SciFact task accuracy:")
    pre_acc, n = evaluate_scifact_task(model, tokenizer, data_dir, device, "dev")
    print(f"  Dev task accuracy: {pre_acc:.4f} ({n} samples)")

    # Training loop
    best_dev_acc = 0.0
    best_task_acc = 0.0
    best_epoch = -1

    for epoch in range(args.epochs):
        model.train()
        epoch_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask,
                            labels=labels)
            loss = outputs.loss

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            epoch_loss += loss.item()
            n_batches += 1

        avg_loss = epoch_loss / n_batches

        # Pair-level evaluation
        dev_acc, dev_per_class, dev_loss = evaluate(model, dev_loader, device)
        macro_f1 = sum(m["F1"] for m in dev_per_class.values()) / len(dev_per_class)

        # Task-level evaluation (actual SciFact classification)
        task_acc, task_n = evaluate_scifact_task(
            model, tokenizer, data_dir, device, "dev")

        print(f"Epoch {epoch+1}/{args.epochs}: "
              f"train_loss={avg_loss:.4f} dev_loss={dev_loss:.4f} "
              f"pair_acc={dev_acc:.4f} macro_f1={macro_f1:.4f} "
              f"task_acc={task_acc:.4f}")
        for cls_name, m in dev_per_class.items():
            print(f"  {cls_name:14s}: P={m['P']:.3f} R={m['R']:.3f} F1={m['F1']:.3f}")

        # Save best based on task accuracy
        if task_acc > best_task_acc:
            best_task_acc = task_acc
            best_dev_acc = dev_acc
            best_epoch = epoch + 1
            out_path = Path(args.output)
            out_path.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(out_path)
            tokenizer.save_pretrained(out_path)
            print(f"  → Saved best model (task_acc={task_acc:.4f})")

    # Final evaluation
    print(f"\n{'='*60}")
    print(f"Best model from epoch {best_epoch}")
    print(f"  Dev pair accuracy: {best_dev_acc:.4f}")
    print(f"  Dev task accuracy: {best_task_acc:.4f}")

    # Save results
    results = {
        "model": args.model_name,
        "best_epoch": best_epoch,
        "best_dev_pair_acc": best_dev_acc,
        "best_dev_task_acc": best_task_acc,
        "args": vars(args),
    }
    results_path = Path(args.output) / "training_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
