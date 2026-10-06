"""Rebuttal Exp 1 (L2, matched) — L2 discriminative ability with matched controls.

Reviewers: Borderline-L2 has only misrepresentation positives, so L2's FPR /
precision on *correct* citations is unknown; L2 is suspected of being
over-aggressive on neutral/supported cases.

Clean matched evaluation on SciFact dev, using bare claims against their
cited abstracts (no citation-template wrapper, which we found corrupts NLI):

  Positives (misrepresentation)  = CONTRADICT claims  -> L2 SHOULD fire
                                    (claim_contradicted or claim_unsupported)
  Negatives (correct citation)   = SUPPORT claims      -> L2 should NOT fire

We report the L2 signal-firing decision (fires if label in
{contradicted, unsupported}) as recall / FPR / precision / F1, plus the
raw 3-way confusion matrix. This is exactly the matched control the
reviewers asked for.

Caveat (stated in output): the scifact_nli checkpoint used SciFact dev for
epoch selection, so these numbers are mildly optimistic; test labels are not
public. We therefore also report on the held-out SciFact *train* claims that
were not among the selection-sensitive examples is not attempted — instead we
flag the dev caveat explicitly.

Usage:
    python -m benchmarks.exp_l2_matched_nli [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from semantic.nli_verifier import NLIVerifier  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCIFACT = REPO / "benchmarks/data/scifact"
FIRE_LABELS = {"contradicted", "unsupported"}


def load_claims():
    corpus = {}
    for line in open(SCIFACT / "corpus.jsonl"):
        d = json.loads(line)
        corpus[d["doc_id"]] = " ".join(d["abstract"])
    pos, neg = [], []  # (claim, abstract, scifact_label)
    for line in open(SCIFACT / "claims_dev.jsonl"):
        d = json.loads(line)
        for doc_id, evs in (d.get("evidence") or {}).items():
            labels = {e["label"] for e in evs}
            abstract = corpus.get(int(doc_id))
            if not abstract:
                continue
            if "CONTRADICT" in labels:
                pos.append((d["claim"], abstract, "CONTRADICT"))
            elif "SUPPORT" in labels:
                neg.append((d["claim"], abstract, "SUPPORT"))
            break
    return pos, neg


def prf(fired_pos, n_pos, fired_neg, n_neg):
    tp, fn = fired_pos, n_pos - fired_pos
    fp, tn = fired_neg, n_neg - fired_neg
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(f1, 3), "fpr": round(fp / n_neg, 4),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    v = NLIVerifier()
    pos, neg = load_claims()
    print(f"positives (CONTRADICT): {len(pos)}, negatives (SUPPORT): {len(neg)}")

    def run(pairs):
        labels = []
        for claim, abstract, _ in pairs:
            r = v.verify(claim=claim, abstract=abstract)
            lab = r.label.value if hasattr(r.label, "value") else str(r.label)
            labels.append(lab)
        return labels

    pos_labels = run(pos)
    neg_labels = run(neg)
    fired_pos = sum(1 for l in pos_labels if l in FIRE_LABELS)
    fired_neg = sum(1 for l in neg_labels if l in FIRE_LABELS)

    report = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "eval_set": "SciFact dev (bare claim vs full cited abstract)",
        "caveat": "scifact_nli used SciFact dev for epoch selection; numbers "
                  "mildly optimistic. Public test labels unavailable.",
        "n_positive_contradict": len(pos),
        "n_negative_support": len(neg),
        "l2_signal_fires_if_label_in": sorted(FIRE_LABELS),
        "matched_prf": prf(fired_pos, len(pos), fired_neg, len(neg)),
        "positive_label_distribution": dict(Counter(pos_labels)),
        "negative_label_distribution": dict(Counter(neg_labels)),
    }
    print(json.dumps(report, indent=1))
    out = args.out or (REPO / "benchmarks/results" /
                       f"l2_matched_nli_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps(report, indent=2))
    print("saved ->", out)


if __name__ == "__main__":
    main()
