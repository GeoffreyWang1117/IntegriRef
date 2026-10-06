"""Learn per-signal likelihood ratios empirically from benchmark data.

Two estimators are reported:
  - Direct empirical LR  (P(signal=1|y=1) / P(signal=1|y=0), no model)
  - Logistic-regression LR  (exp(beta_i), accounts for correlations)

Both are compared against the hand-set LR table in scoring/bayesian.py.

Inputs:
  One or more benchmark JSONs with per-case `signals_fired` (list) and
  `category` (real / retracted / hallucinated / chimera).

Positive label := category in {retracted, hallucinated, chimera}.

Usage:
    python -m benchmarks.learn_lrs RESULT_JSON [RESULT_JSON ...] [--out OUT.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scoring.bayesian import SIGNAL_DEFINITIONS  # noqa: E402

POS_CATEGORIES = {"retracted", "hallucinated", "chimera"}
NEG_CATEGORIES = {"real", "real_control"}


def load_signal_matrix(paths) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return (X, y, signal_names).

    X: shape (n_cases, n_signals), binary 0/1
    y: shape (n_cases,), binary 0/1
    """
    rows = []
    labels = []
    seen_signals = set()
    raw_cases = []
    for p in paths:
        with open(p) as f:
            d = json.load(f)
        cases = d.get("results") or d.get("per_case") or d.get("cases") or []
        for c in cases:
            cat = c.get("category", "")
            if cat in POS_CATEGORIES:
                y = 1
            elif cat in NEG_CATEGORIES:
                y = 0
            else:
                continue
            sig = c.get("signals_fired", [])
            if not isinstance(sig, list):
                continue
            raw_cases.append((sig, y))
            for s in sig:
                seen_signals.add(s)
            labels.append(y)

    signal_names = sorted(seen_signals)
    n = len(raw_cases)
    X = np.zeros((n, len(signal_names)), dtype=np.int8)
    for i, (sig, _) in enumerate(raw_cases):
        for s in sig:
            j = signal_names.index(s)
            X[i, j] = 1
    y = np.array(labels)
    return X, y, signal_names


def direct_empirical_lrs(X: np.ndarray, y: np.ndarray,
                         signal_names: list[str],
                         laplace: float = 0.5) -> dict:
    """Compute LR+ and LR- per signal as direct conditional ratios.

    LR+_i = P(s_i=1 | y=1) / P(s_i=1 | y=0)
    LR-_i = P(s_i=0 | y=1) / P(s_i=0 | y=0)
    """
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    out = {}
    for j, name in enumerate(signal_names):
        fired_pos = int(X[y == 1, j].sum())
        fired_neg = int(X[y == 0, j].sum())
        p_fire_pos = (fired_pos + laplace) / (n_pos + 2 * laplace)
        p_fire_neg = (fired_neg + laplace) / (n_neg + 2 * laplace)
        lr_pos = p_fire_pos / p_fire_neg
        lr_neg = (1 - p_fire_pos) / (1 - p_fire_neg)
        out[name] = {
            "fired_pos": fired_pos,
            "n_pos": n_pos,
            "fired_neg": fired_neg,
            "n_neg": n_neg,
            "lr_plus": round(lr_pos, 3),
            "lr_minus": round(lr_neg, 3),
        }
    return out


def logreg_lrs(X: np.ndarray, y: np.ndarray,
               signal_names: list[str], C: float = 1.0) -> dict:
    """Logistic regression with L2; report exp(coef_i) as an LR-style score.

    Note: under independence, log(LR+_i / LR-_i) = coef_i. exp(coef_i)
    is the odds multiplier when signal i fires (holding others fixed).
    """
    from sklearn.linear_model import LogisticRegression
    if y.sum() == 0 or y.sum() == len(y):
        return {n: None for n in signal_names}
    lr = LogisticRegression(C=C, solver="lbfgs", max_iter=1000)
    lr.fit(X, y)
    coefs = lr.coef_.ravel()
    out = {}
    for name, beta in zip(signal_names, coefs):
        out[name] = {
            "beta": round(float(beta), 3),
            "odds_mult_when_fires": round(math.exp(float(beta)), 3),
        }
    return out


def format_comparison(direct: dict, logreg: dict,
                      signal_names: list[str]) -> str:
    lines = [
        f"{'signal':35s} {'fire+':>6} {'fire-':>6} "
        f"{'hand LR+':>9} {'emp LR+':>9} {'emp LR-':>9} "
        f"{'logreg β':>9} {'exp(β)':>8}"
    ]
    lines.append("-" * 112)
    for name in signal_names:
        d = direct[name]
        l = logreg[name]
        hand = SIGNAL_DEFINITIONS.get(name)
        hand_lr = f"{hand.lr_positive:.2f}" if hand else "—"
        beta = f"{l['beta']:.2f}" if l else "—"
        exp_b = f"{l['odds_mult_when_fires']:.2f}" if l else "—"
        lines.append(
            f"{name:35s} {d['fired_pos']:>6} {d['fired_neg']:>6} "
            f"{hand_lr:>9} {d['lr_plus']:>9.2f} {d['lr_minus']:>9.3f} "
            f"{beta:>9} {exp_b:>8}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+", help="Benchmark result JSON files")
    parser.add_argument("--out", type=str, default=None,
                        help="Write learned LRs to this JSON file")
    args = parser.parse_args()

    X, y, names = load_signal_matrix([Path(p) for p in args.results])
    print(f"Loaded {len(y)} cases, "
          f"{int(y.sum())} positive, {int(len(y)-y.sum())} negative")
    print(f"Distinct signals observed: {len(names)}")
    if not names:
        sys.exit("No signals observed in input data — nothing to learn.")
    print()

    direct = direct_empirical_lrs(X, y, names)
    logreg = logreg_lrs(X, y, names)
    print(format_comparison(direct, logreg, names))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump({
                "n_cases": int(len(y)),
                "n_positive": int(y.sum()),
                "n_negative": int(len(y) - y.sum()),
                "direct_empirical": direct,
                "logistic_regression": logreg,
                "hand_set": {n: {"lr_plus": SIGNAL_DEFINITIONS[n].lr_positive,
                                 "lr_minus": SIGNAL_DEFINITIONS[n].lr_negative}
                             for n in names if n in SIGNAL_DEFINITIONS},
            }, f, indent=2)
        print(f"\nWrote learned LRs to {args.out}")


if __name__ == "__main__":
    main()
