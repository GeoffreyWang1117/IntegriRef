"""Rebuttal Exp 2 (Track A) — held-out tuning of the L1 contrast threshold.

Addresses the test-set-adaptation concern (the 0.7 -> 0.45 threshold was
adjusted after observing under-firing on Borderline-L1 itself).

Protocol:
  dev  = ACL-ARC train + validation (Jurgens et al. 2018; positive class =
         CompareOrContrast) — fully independent of Borderline-L1
  test = ACL-ARC test, plus Borderline-L1 (evaluated ONCE at the dev-chosen
         threshold)

The heuristic contrast detector fires when the rule-based classifier
predicts CONTRASTING with confidence > t. We sweep t in [0.30, 0.80] on dev,
pick argmax-F1, and report a full sensitivity curve.

Usage:
    python -m benchmarks.exp_l1_threshold [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from semantic.intent_classifier import IntentClassifier, CitationIntent  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONTRAST_CLASS = 2  # ACL-ARC: 0=Background 1=Uses 2=CompareOrContrast 3=Extends 4=Motivation 5=Future
THRESHOLDS = [round(0.30 + 0.05 * i, 2) for i in range(11)]  # 0.30..0.80


def acl_arc_sentences():
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


def heuristic_scores(clf, rows):
    """Return (is_contrast_pred, confidence) per row — threshold applied later."""
    out = []
    for r in rows:
        res = clf._classify_heuristic(r["sentence"], "1")
        out.append((res.intent == CitationIntent.CONTRASTING, res.confidence))
    return out


def borderline_l1_rows():
    d = json.loads((REPO / "benchmarks/data/borderline_l1.json").read_text())
    rows = []
    for sub in ("supporting_misrepresents", "contrasting_misrepresents", "citation_drift"):
        for c in d[sub]:
            rows.append({
                "sentence": c["citing_sentence"],
                "gold": c.get("expected_intent") == "contrasting",
                "sub_pattern": sub,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    clf = IntentClassifier(use_model=False)
    data = acl_arc_sentences()
    dev_rows = data["train"] + data["validation"]
    test_rows = data["test"]

    dev_scores = heuristic_scores(clf, dev_rows)
    test_scores = heuristic_scores(clf, test_rows)
    dev_golds = [r["gold"] for r in dev_rows]
    test_golds = [r["gold"] for r in test_rows]

    curve = []
    for t in THRESHOLDS:
        preds = [c and conf > t for c, conf in dev_scores]
        m = prf(preds, dev_golds)
        curve.append({"threshold": t, **m})
    best = max(curve, key=lambda r: r["f1"])
    t_star = best["threshold"]

    test_at_star = prf([c and conf > t_star for c, conf in test_scores], test_golds)

    # Borderline-L1: evaluated once at the dev-chosen threshold
    bl_rows = borderline_l1_rows()
    bl_scores = heuristic_scores(clf, bl_rows)
    bl_golds = [r["gold"] for r in bl_rows]
    bl_at_star = prf([c and conf > t_star for c, conf in bl_scores], bl_golds)
    # firing on the contrast sub-pattern specifically
    contrast_fired = sum(
        1 for (c, conf), r in zip(bl_scores, bl_rows)
        if r["sub_pattern"] == "contrasting_misrepresents" and c and conf > t_star)
    n_contrast = sum(1 for r in bl_rows if r["sub_pattern"] == "contrasting_misrepresents")

    report = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "protocol": {
            "dev": "ACL-ARC train+validation (n=%d, %d contrast)" % (
                len(dev_rows), sum(dev_golds)),
            "test": "ACL-ARC test (n=%d, %d contrast)" % (
                len(test_rows), sum(test_golds)),
            "final_eval": "Borderline-L1 (n=%d) evaluated once at dev-chosen threshold" % len(bl_rows),
        },
        "dev_sensitivity_curve": curve,
        "dev_best_threshold": t_star,
        "acl_arc_test_at_best": test_at_star,
        "borderline_l1_at_best": bl_at_star,
        "borderline_contrast_subpattern_fired": f"{contrast_fired}/{n_contrast}",
    }
    print(json.dumps(report, indent=1))

    out = args.out or (REPO / "benchmarks/results" /
                       f"l1_threshold_heldout_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps(report, indent=2))
    print("saved ->", out)


if __name__ == "__main__":
    main()
