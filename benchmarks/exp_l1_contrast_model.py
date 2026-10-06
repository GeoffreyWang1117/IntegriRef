"""Rebuttal Exp 2 (Track B) — trained contrast detector replacing the heuristic.

The production SciCite 3-class SciBERT cannot output CONTRASTING (the
"3-class bug"); contrast detection fell to a lexical heuristic whose
threshold was patched on the evaluation set. This script trains a binary
CompareOrContrast detector on ACL-ARC train (independent of Borderline-L1),
selects the decision threshold on ACL-ARC validation, and evaluates ONCE on
ACL-ARC test and Borderline-L1.

Usage:
    python -m benchmarks.exp_l1_contrast_model [--epochs 4] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
CONTRAST_CLASS = 2
MODEL_NAME = "allenai/scibert_scivocab_uncased"
MODEL_OUT = REPO / "models" / "contrast_detector"


class SentDataset(Dataset):
    def __init__(self, rows, tokenizer, max_len=256):
        self.rows = rows
        self.tok = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        enc = self.tok(r["sentence"], truncation=True, max_length=self.max_len,
                       padding="max_length", return_tensors="pt")
        return {"input_ids": enc["input_ids"][0],
                "attention_mask": enc["attention_mask"][0],
                "label": torch.tensor(float(r["gold"]))}


def load_rows():
    from datasets import load_dataset
    d = load_dataset("hrithikpiyush/acl-arc")
    out = {}
    for split in ("train", "validation", "test"):
        rows = []
        for r in d[split]:
            sent = re.sub(r"@@CITATION[a-z]?", "[1]", r["cleaned_cite_text"] or r["text"])
            rows.append({"sentence": sent, "gold": int(r["intent"]) == CONTRAST_CLASS})
        out[split] = rows
    return out


def borderline_rows():
    d = json.loads((REPO / "benchmarks/data/borderline_l1.json").read_text())
    rows = []
    for sub in ("supporting_misrepresents", "contrasting_misrepresents", "citation_drift"):
        for c in d[sub]:
            rows.append({"sentence": c["citing_sentence"],
                         "gold": c.get("expected_intent") == "contrasting",
                         "sub_pattern": sub})
    return rows


def prf(preds, golds):
    tp = sum(p and g for p, g in zip(preds, golds))
    fp = sum(p and not g for p, g in zip(preds, golds))
    fn = sum((not p) and g for p, g in zip(preds, golds))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    fpr = fp / max(sum(not g for g in golds), 1)
    return {"precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(f1, 3), "fpr": round(fpr, 4),
            "tp": tp, "fp": fp, "fn": fn}


@torch.no_grad()
def predict_probs(model, tokenizer, rows, device, batch_size=64):
    model.eval()
    probs = []
    for i in range(0, len(rows), batch_size):
        batch = [r["sentence"] for r in rows[i:i + batch_size]]
        enc = tokenizer(batch, truncation=True, max_length=256,
                        padding=True, return_tensors="pt").to(device)
        logits = model(**enc).logits.squeeze(-1)
        probs.extend(torch.sigmoid(logits).cpu().tolist())
    return probs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    data = load_rows()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME, num_labels=1).to(device)

    n_pos = sum(r["gold"] for r in data["train"])
    pos_weight = torch.tensor((len(data["train"]) - n_pos) / n_pos).to(device)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    loader = DataLoader(SentDataset(data["train"], tokenizer),
                        batch_size=args.batch_size, shuffle=True)

    best_val_f1, best_state, best_thr = -1.0, None, 0.5
    for epoch in range(args.epochs):
        model.train()
        total = 0.0
        for batch in loader:
            opt.zero_grad()
            logits = model(input_ids=batch["input_ids"].to(device),
                           attention_mask=batch["attention_mask"].to(device)
                           ).logits.squeeze(-1)
            loss = loss_fn(logits, batch["label"].to(device))
            loss.backward()
            opt.step()
            total += loss.item()

        val_probs = predict_probs(model, tokenizer, data["validation"], device)
        val_golds = [r["gold"] for r in data["validation"]]
        thr_f1 = []
        for t in np.arange(0.1, 0.95, 0.05):
            m = prf([p > t for p in val_probs], val_golds)
            thr_f1.append((m["f1"], round(float(t), 2)))
        f1, thr = max(thr_f1)
        print(f"epoch {epoch+1}: train_loss={total/len(loader):.4f} "
              f"val_f1={f1:.3f} @thr={thr}")
        if f1 > best_val_f1:
            best_val_f1, best_thr = f1, thr
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    model.to(device)

    # Final evaluation — once, at the validation-chosen threshold
    test_probs = predict_probs(model, tokenizer, data["test"], device)
    test_m = prf([p > best_thr for p in test_probs],
                 [r["gold"] for r in data["test"]])

    bl = borderline_rows()
    bl_probs = predict_probs(model, tokenizer, bl, device)
    bl_m = prf([p > best_thr for p in bl_probs], [r["gold"] for r in bl])
    contrast_fired = sum(1 for p, r in zip(bl_probs, bl)
                         if r["sub_pattern"] == "contrasting_misrepresents"
                         and p > best_thr)
    n_contrast = sum(1 for r in bl if r["sub_pattern"] == "contrasting_misrepresents")

    MODEL_OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(MODEL_OUT)
    tokenizer.save_pretrained(MODEL_OUT)

    report = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "protocol": {
            "train": "ACL-ARC train (binary CompareOrContrast, pos_weight balanced)",
            "threshold_selection": "ACL-ARC validation (max F1)",
            "final_eval": "ACL-ARC test + Borderline-L1, evaluated once",
        },
        "val_best_f1": round(best_val_f1, 3),
        "val_chosen_threshold": best_thr,
        "acl_arc_test": test_m,
        "borderline_l1": bl_m,
        "borderline_contrast_subpattern_fired": f"{contrast_fired}/{n_contrast}",
        "model_saved_to": str(MODEL_OUT),
        "args": vars(args),
    }
    print(json.dumps(report, indent=1))
    out = args.out or (REPO / "benchmarks/results" /
                       f"l1_contrast_model_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps(report, indent=2))
    print("saved ->", out)


if __name__ == "__main__":
    main()
