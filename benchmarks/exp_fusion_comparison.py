"""Rebuttal Exp 3 — decision-level comparison of fusion strategies.

Compares, on the pooled large external splits (rv-1926 + ret-6391):

  A. Hand-set naive-Bayes LRs (the paper's L4)          — cached posterior
  B. Empirical naive-Bayes LRs (Laplace, fold-fitted)   — same NB machinery
  C. L1-regularized logistic regression (fold-fitted)   — accounts for
     inter-signal correlation; exp(beta_i) is the conditional odds multiplier

All learned methods use 5-fold stratified CV; every reported prediction is
out-of-fold. Comparison metrics: ROC-AUC, recall at matched FPR levels
(interpolated from the ROC), and the paper's operating point (posterior
>= 0.05, ELEVATED+) for the hand-set system.

Also reports the pairwise phi-coefficient matrix and conditional co-fire
rates for all signals firing >= 10 times, addressing the LR double-counting
concern.

Usage:
    python -m benchmarks.exp_fusion_comparison [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scoring.bayesian import SIGNAL_DEFINITIONS, DOMAIN_PRIORS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SPLIT_FILES = {
    "rv-1926": REPO / "paper/gem2026/bench_results/bench_reference_verification_20260524_215031.json",
    "ret-6391": REPO / "paper/gem2026/bench_results/bench_retracted_papers_20260524_202842.json",
}
POS = {"hallucinated", "chimera", "retracted"}
NEG = {"real", "real_control"}
L0_SIGNALS = ["reference_not_found", "phantom_doi", "metadata_mismatch",
              "no_id_match", "chimera_detected", "retracted_citation"]
PRIOR = DOMAIN_PRIORS["default"]
ELEVATED = 0.05


def load_pool():
    rows, labels, splits = [], [], []
    for split, path in SPLIT_FILES.items():
        d = json.loads(path.read_text())
        for r in d["results"]:
            cat = r.get("category", "")
            if cat in POS:
                y = 1
            elif cat in NEG:
                y = 0
            else:
                continue
            rows.append({
                "signals": [s for s in r.get("signals_fired", [])],
                "posterior": r.get("posterior"),
                "split": split,
            })
            labels.append(y)
            splits.append(split)
    return rows, np.array(labels), np.array(splits)


def signal_matrix(rows, signal_names):
    X = np.zeros((len(rows), len(signal_names)), dtype=np.int8)
    idx = {s: j for j, s in enumerate(signal_names)}
    for i, r in enumerate(rows):
        for s in r["signals"]:
            if s in idx:
                X[i, idx[s]] = 1
    return X


def nb_posterior(x_row, lr_plus, lr_minus, prior=PRIOR):
    log_odds = math.log(prior / (1 - prior))
    for j, fired in enumerate(x_row):
        log_odds += math.log(lr_plus[j] if fired else lr_minus[j])
    if log_odds > 700:
        return 1.0
    odds = math.exp(log_odds)
    return odds / (1 + odds)


def fit_empirical_lrs(X, y, laplace=0.5):
    n_pos = y.sum()
    n_neg = len(y) - n_pos
    lr_plus, lr_minus = [], []
    for j in range(X.shape[1]):
        fp = X[y == 1, j].sum()
        fn = X[y == 0, j].sum()
        p1 = (fp + laplace) / (n_pos + 2 * laplace)
        p0 = (fn + laplace) / (n_neg + 2 * laplace)
        lr_plus.append(p1 / p0)
        lr_minus.append((1 - p1) / (1 - p0))
    return np.array(lr_plus), np.array(lr_minus)


def recall_at_fpr(y, scores, target_fpr):
    """Interpolated recall at a given FPR from the empirical ROC."""
    from sklearn.metrics import roc_curve
    fpr, tpr, _ = roc_curve(y, scores)
    return float(np.interp(target_fpr, fpr, tpr))


def evaluate(y, scores, splits, label):
    from sklearn.metrics import roc_auc_score
    out = {"method": label, "auc": round(float(roc_auc_score(y, scores)), 4)}
    for f in (0.0, 0.01, 0.05):
        out[f"recall@fpr={f:.0%}"] = round(recall_at_fpr(y, scores, f), 4)
    for split in np.unique(splits):
        m = splits == split
        if len(np.unique(y[m])) == 2:
            out[f"auc[{split}]"] = round(float(roc_auc_score(y[m], np.asarray(scores)[m])), 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows, y, splits = load_pool()
    # Signals observed anywhere in the pool (the six L0 signals in practice)
    seen = sorted({s for r in rows for s in r["signals"]})
    signal_names = sorted(set(L0_SIGNALS) | set(seen))
    X = signal_matrix(rows, signal_names)
    print(f"Pool: {len(y)} cases ({y.sum()} pos / {len(y)-y.sum()} neg); "
          f"signals observed: {seen}")

    hand_plus = np.array([SIGNAL_DEFINITIONS[s].lr_positive for s in signal_names])
    hand_minus = np.array([SIGNAL_DEFINITIONS[s].lr_negative for s in signal_names])

    # A. hand-set NB — recomputed analytically (conf=1); also sanity-check
    #    against the cached pipeline posterior
    hand_scores = np.array([nb_posterior(X[i], hand_plus, hand_minus)
                            for i in range(len(y))])
    cached = np.array([r["posterior"] if r["posterior"] is not None else np.nan
                       for r in rows])
    ok = ~np.isnan(cached)
    sanity = float(np.mean(np.abs(hand_scores[ok] - cached[ok])))
    print(f"Sanity: mean |analytic - cached posterior| = {sanity:.4f} "
          f"(confidence interpolation in the live run explains small gaps)")

    # B/C. fold-fitted methods — out-of-fold predictions
    from sklearn.model_selection import StratifiedKFold
    from sklearn.linear_model import LogisticRegression
    emp_scores = np.zeros(len(y))
    lr_scores = np.zeros(len(y))
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    for tr, te in skf.split(X, y):
        lp, lm = fit_empirical_lrs(X[tr], y[tr])
        emp_scores[te] = [nb_posterior(X[i], lp, lm) for i in te]
        clf = LogisticRegression(penalty="l1", solver="liblinear", C=1.0)
        clf.fit(X[tr], y[tr])
        lr_scores[te] = clf.predict_proba(X[te])[:, 1]

    results = [
        evaluate(y, hand_scores, splits, "hand-set NB (paper L4)"),
        evaluate(y, emp_scores, splits, "empirical-LR NB (5-fold CV)"),
        evaluate(y, lr_scores, splits, "L1-logistic regression (5-fold CV)"),
    ]

    # Paper operating point for hand-set NB
    dec = hand_scores >= ELEVATED
    op = {
        "threshold": ELEVATED,
        "recall": round(float(dec[y == 1].mean()), 4),
        "fpr": round(float(dec[y == 0].mean()), 4),
    }

    # Correlation analysis among firing signals
    fire_counts = X.sum(axis=0)
    active = [j for j in range(len(signal_names)) if fire_counts[j] >= 10]
    phi = {}
    cofire = {}
    for a_i, j in enumerate(active):
        for k in active[a_i + 1:]:
            xa, xb = X[:, j].astype(float), X[:, k].astype(float)
            denom = np.std(xa) * np.std(xb)
            phi_v = float(np.mean((xa - xa.mean()) * (xb - xb.mean())) / denom) if denom else 0.0
            pair = f"{signal_names[j]} ~ {signal_names[k]}"
            phi[pair] = round(phi_v, 3)
            both = float((X[:, j] & X[:, k]).sum())
            cofire[pair] = {
                "P(b|a)": round(both / max(fire_counts[j], 1), 3),
                "P(a|b)": round(both / max(fire_counts[k], 1), 3),
            }

    report = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "pool": {"n": int(len(y)), "n_pos": int(y.sum()),
                 "n_neg": int(len(y) - y.sum()),
                 "splits": {s: int((splits == s).sum()) for s in np.unique(splits)}},
        "sanity_mean_abs_diff_vs_cached": round(sanity, 4),
        "methods": results,
        "hand_set_operating_point": op,
        "signal_fire_counts": {signal_names[j]: int(fire_counts[j])
                               for j in range(len(signal_names)) if fire_counts[j] > 0},
        "phi_correlation": phi,
        "conditional_cofire": cofire,
    }

    print(json.dumps(report["methods"], indent=1))
    print("Operating point (hand-set, ELEVATED>=0.05):", op)
    print("phi:", json.dumps(phi, indent=1))

    out = args.out or (REPO / "benchmarks/results" /
                       f"fusion_comparison_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps(report, indent=2))
    print("saved ->", out)


if __name__ == "__main__":
    main()
