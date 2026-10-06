"""Expanded >=200-case borderline evaluation for L1 and L2 (3tFe-S1b).

The submitted paper evaluated L1 and L2 on two 40-case hand-built splits,
which reviewers judged too small to carry the layer-contribution claims. This
script rebuilds both evaluations at N>=200 from public labelled corpora, which
also removes the author-curation bias of the hand-built sets.

Borderline-L1-v2 (contrast misreading, N=289 by default)
    A held-out slice of ACL-ARC train is set aside before training, so the
    binary CompareOrContrast detector never sees it. The decision threshold is
    still selected on ACL-ARC validation, and the union of the held-out slice
    and ACL-ARC test is scored exactly once.

Borderline-L2-v2 (claim-evidence contradiction, N=209)
    Every (claim, cited abstract) pair in SciFact dev, rather than the first
    evidence document per claim as in the 40-case split. CONTRADICT pairs are
    misrepresentation positives; SUPPORT pairs are matched controls.

Usage:
    python benchmarks/exp_borderline_expanded.py --layer both
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "benchmarks" / "results"
SCIFACT = REPO / "benchmarks" / "data" / "scifact"
CONTRAST_CLASS = 2
MODEL_NAME = "allenai/scibert_scivocab_uncased"
MODEL_OUT = REPO / "models" / "contrast_detector_v2"


def prf(preds, golds):
    tp = sum(p and g for p, g in zip(preds, golds))
    fp = sum(p and not g for p, g in zip(preds, golds))
    fn = sum((not p) and g for p, g in zip(preds, golds))
    tn = sum((not p) and (not g) for p, g in zip(preds, golds))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    n_neg = sum(not g for g in golds)
    return {"n": len(golds), "n_pos": sum(golds), "n_neg": n_neg,
            "precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(f1, 3), "fpr": round(fp / n_neg, 4) if n_neg else None,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


# --------------------------------------------------------------------------
# Borderline-L1-v2
# --------------------------------------------------------------------------

def run_l1(args):
    import numpy as np
    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    from datasets import load_dataset
    ds = load_dataset("hrithikpiyush/acl-arc")

    def to_rows(split):
        rows = []
        for r in ds[split]:
            sent = re.sub(r"@@CITATION[a-z]?", "[1]",
                          r["cleaned_cite_text"] or r["text"])
            rows.append({"sentence": sent,
                         "gold": int(r["intent"]) == CONTRAST_CLASS})
        return rows

    train_all = to_rows("train")
    validation = to_rows("validation")
    test = to_rows("test")

    # Stratified held-out slice carved from train BEFORE any training, so the
    # expanded evaluation set stays genuinely unseen.
    rng = np.random.default_rng(args.seed)
    pos_idx = [i for i, r in enumerate(train_all) if r["gold"]]
    neg_idx = [i for i, r in enumerate(train_all) if not r["gold"]]
    rng.shuffle(pos_idx)
    rng.shuffle(neg_idx)
    pos_rate = len(pos_idx) / len(train_all)
    n_hold_pos = max(1, round(args.holdout * pos_rate))
    n_hold_neg = args.holdout - n_hold_pos
    hold_idx = set(pos_idx[:n_hold_pos]) | set(neg_idx[:n_hold_neg])
    heldout = [r for i, r in enumerate(train_all) if i in hold_idx]
    train = [r for i, r in enumerate(train_all) if i not in hold_idx]

    eval_rows = heldout + test
    print(f"L1: train={len(train)} val={len(validation)} "
          f"eval={len(eval_rows)} (heldout {len(heldout)} + test {len(test)}), "
          f"positives in eval={sum(r['gold'] for r in eval_rows)}")

    class SentDataset(Dataset):
        def __init__(self, rows, tok, max_len=256):
            self.rows, self.tok, self.max_len = rows, tok, max_len

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, i):
            r = self.rows[i]
            enc = self.tok(r["sentence"], truncation=True,
                           max_length=self.max_len, padding="max_length",
                           return_tensors="pt")
            return {"input_ids": enc["input_ids"][0],
                    "attention_mask": enc["attention_mask"][0],
                    "label": torch.tensor(float(r["gold"]))}

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME, num_labels=1).to(device)

    n_pos = sum(r["gold"] for r in train)
    pos_weight = torch.tensor((len(train) - n_pos) / n_pos).to(device)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    loader = DataLoader(SentDataset(train, tokenizer),
                        batch_size=args.batch_size, shuffle=True)

    @torch.no_grad()
    def probs_for(rows, batch_size=64):
        model.eval()
        out = []
        for i in range(0, len(rows), batch_size):
            batch = [r["sentence"] for r in rows[i:i + batch_size]]
            enc = tokenizer(batch, truncation=True, max_length=256,
                            padding=True, return_tensors="pt").to(device)
            logits = model(**enc).logits.squeeze(-1)
            out.extend(torch.sigmoid(logits).cpu().tolist())
        return out

    best_f1, best_thr, best_state = -1.0, 0.5, None
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

        val_probs = probs_for(validation)
        val_golds = [r["gold"] for r in validation]
        f1, thr = max((prf([p > t for p in val_probs], val_golds)["f1"],
                       round(float(t), 2))
                      for t in np.arange(0.1, 0.95, 0.05))
        print(f"  epoch {epoch+1}: loss={total/len(loader):.4f} "
              f"val_f1={f1:.3f} @thr={thr}")
        if f1 > best_f1:
            best_f1, best_thr = f1, thr
            best_state = {k: v.cpu().clone()
                          for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    model.to(device)

    eval_probs = probs_for(eval_rows)
    eval_golds = [r["gold"] for r in eval_rows]
    metrics = prf([p > best_thr for p in eval_probs], eval_golds)

    # Lexical-heuristic baseline on the same expanded set, at its own best
    # threshold — a deliberately generous comparison.
    from semantic.intent_classifier import IntentClassifier
    heuristic = IntentClassifier(use_model=False)
    heur_scores = []
    for r in eval_rows:
        res = heuristic.classify(r["sentence"])
        label = res.intent.value if hasattr(res.intent, "value") else str(res.intent)
        heur_scores.append(res.confidence if "contrast" in label.lower() else 0.0)
    heur_best = max(
        (prf([s > t for s in heur_scores], eval_golds)["f1"], round(float(t), 2))
        for t in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
    heur_metrics = prf([s > heur_best[1] for s in heur_scores], eval_golds)

    MODEL_OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(MODEL_OUT)
    tokenizer.save_pretrained(MODEL_OUT)

    return {
        "split": "Borderline-L1-v2 (ACL-ARC held-out train slice + test)",
        "protocol": {
            "train": f"ACL-ARC train minus {args.holdout} held-out "
                     f"({len(train)} rows)",
            "threshold_selection": "ACL-ARC validation (max F1), disjoint",
            "final_eval": "held-out slice + ACL-ARC test, scored once",
        },
        "n_eval": len(eval_rows),
        "val_best_f1": round(best_f1, 3),
        "chosen_threshold": best_thr,
        "detector": metrics,
        "lexical_heuristic_baseline": heur_metrics,
        "lexical_heuristic_best_threshold": heur_best[1],
        "model_saved_to": str(MODEL_OUT),
    }


# --------------------------------------------------------------------------
# Borderline-L2-v2
# --------------------------------------------------------------------------

def load_scifact_pairs():
    corpus = {}
    for line in open(SCIFACT / "corpus.jsonl"):
        d = json.loads(line)
        corpus[d["doc_id"]] = " ".join(d["abstract"])
    pos, neg = [], []
    for line in open(SCIFACT / "claims_dev.jsonl"):
        d = json.loads(line)
        # Every evidence document, not just the first — this is what lifts the
        # split past 200 cases.
        for doc_id, evs in (d.get("evidence") or {}).items():
            abstract = corpus.get(int(doc_id))
            if not abstract:
                continue
            labels = {e["label"] for e in evs}
            if "CONTRADICT" in labels:
                pos.append((d["claim"], abstract))
            elif "SUPPORT" in labels:
                neg.append((d["claim"], abstract))
    return pos, neg


def run_l2(_args):
    from semantic.nli_verifier import NLIVerifier

    verifier = NLIVerifier()
    pos, neg = load_scifact_pairs()
    print(f"L2: positives(CONTRADICT)={len(pos)} controls(SUPPORT)={len(neg)} "
          f"total={len(pos)+len(neg)}")

    def labels_for(pairs):
        out = []
        for claim, abstract in pairs:
            r = verifier.verify(claim=claim, abstract=abstract)
            out.append(r.label.value if hasattr(r.label, "value")
                       else str(r.label))
        return out

    pos_labels, neg_labels = labels_for(pos), labels_for(neg)
    golds = [True] * len(pos) + [False] * len(neg)
    all_labels = pos_labels + neg_labels

    variants = {}
    for name, fire in [("contradicted_or_unsupported",
                        {"contradicted", "unsupported"}),
                       ("contradicted_only", {"contradicted"})]:
        variants[name] = prf([lab in fire for lab in all_labels], golds)

    return {
        "split": "Borderline-L2-v2 (all SciFact dev claim-evidence pairs)",
        "n_eval": len(golds),
        "n_positive_contradict": len(pos),
        "n_control_support": len(neg),
        "caveat": "the scifact_nli checkpoint used SciFact dev for epoch "
                  "selection, so these numbers are mildly optimistic; public "
                  "test labels are unavailable",
        "firing_variants": variants,
        "positive_label_distribution": dict(Counter(pos_labels)),
        "control_label_distribution": dict(Counter(neg_labels)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=["l1", "l2", "both"], default="both")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--holdout", type=int, default=150,
                    help="rows carved out of ACL-ARC train before training")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    report = {"created": datetime.now().isoformat(timespec="seconds"),
              "commitment": "3tFe-S1b: expanded >=200-case borderline splits"}
    if args.layer in ("l1", "both"):
        report["borderline_l1_v2"] = run_l1(args)
    if args.layer in ("l2", "both"):
        report["borderline_l2_v2"] = run_l2(args)

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = args.out or (RESULTS /
                       f"borderline_expanded_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print("saved ->", out)


if __name__ == "__main__":
    main()
