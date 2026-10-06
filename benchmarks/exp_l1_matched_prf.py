"""Rebuttal Exp 1 (L1 detector, matched) — precision/recall/F1 of the trained
contrast detector on a matched pool of correct vs. misrepresenting citations.

Positives  = Borderline-L1 `contrasting_misrepresents` (n=10), where the
             citing sentence contrasts with / misrepresents the cited source.
Negatives  = matched normal-citation controls (correct SUPPORT citations).

Uses the ACL-ARC-trained contrast_detector at its validation-selected
threshold (0.7). Everything here is out-of-domain for the detector: neither
the borderline nor the control sentences were seen in training.

Usage:
    python -m benchmarks.exp_l1_matched_prf [--threshold 0.7] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO / "models" / "contrast_detector"


def prf(preds, golds):
    tp = sum(p and g for p, g in zip(preds, golds))
    fp = sum(p and not g for p, g in zip(preds, golds))
    fn = sum((not p) and g for p, g in zip(preds, golds))
    tn = sum((not p) and not g for p, g in zip(preds, golds))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(f1, 3), "fpr": round(fpr, 4),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


@torch.no_grad()
def score(model, tok, sents, device):
    out = []
    for i in range(0, len(sents), 64):
        enc = tok(sents[i:i + 64], truncation=True, max_length=256,
                  padding=True, return_tensors="pt").to(device)
        out.extend(torch.sigmoid(model(**enc).logits.squeeze(-1)).cpu().tolist())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR).to(device).eval()

    bl = json.loads((REPO / "benchmarks/data/borderline_l1.json").read_text())
    pos = [c["citing_sentence"] for c in bl["contrasting_misrepresents"]]
    controls = json.loads((REPO / "benchmarks/data/matched_controls_l1l2.json").read_text())
    neg = [c["citing_sentence"] for c in controls["controls"]]

    sents = pos + neg
    golds = [True] * len(pos) + [False] * len(neg)
    probs = score(model, tok, sents, device)
    preds = [p > args.threshold for p in probs]
    m = prf(preds, golds)

    report = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "detector": "ACL-ARC-trained SciBERT contrast detector",
        "threshold": args.threshold,
        "pool": {"positives(border_contrast)": len(pos),
                 "negatives(matched_controls)": len(neg)},
        "matched_prf": m,
    }
    print(json.dumps(report, indent=1))
    out = args.out or (REPO / "benchmarks/results" /
                       f"l1_matched_prf_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps(report, indent=2))
    print("saved ->", out)


if __name__ == "__main__":
    main()
