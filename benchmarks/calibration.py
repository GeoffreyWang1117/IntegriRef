"""Calibration evaluation for IntegriRef L4 posterior probabilities.

Computes Expected Calibration Error (ECE) and a reliability diagram from
existing benchmark JSON results — no new pipeline runs needed.

Inputs:
  Any of the benchmark JSONs that contain per-case `posterior` (float in
  [0,1]) and `category` (one of real, retracted, hallucinated, chimera).

Ground-truth binarisation:
  positive (integrity issue) := category in {retracted, hallucinated, chimera}
  negative                   := category == real

ECE is computed with `n_bins` equal-width bins on the posterior space.
Optionally applies Platt scaling (logistic recalibration on a held-out fold)
to report ECE-before / ECE-after.

Usage:
    python -m benchmarks.calibration RESULT_JSON [RESULT_JSON ...]
        [--bins 10] [--platt] [--plot OUT.png]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np


# ─── Loading and binarisation ─────────────────────────────────────────────

POS_CATEGORIES = {"retracted", "hallucinated", "chimera"}
NEG_CATEGORIES = {"real", "real_control"}


def load_pairs(paths: Iterable[Path]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return (posteriors, binary_labels, sources) extracted from JSON files.

    Skips cases whose category cannot be mapped to a binary label.
    """
    probs, labels, srcs = [], [], []
    for p in paths:
        with open(p) as f:
            d = json.load(f)
        cases = d.get("results") or d.get("per_case") or d.get("cases") or []
        for c in cases:
            post = c.get("posterior")
            if post is None or isinstance(post, str):
                continue
            cat = c.get("category", "")
            if cat in POS_CATEGORIES:
                y = 1
            elif cat in NEG_CATEGORIES:
                y = 0
            else:
                continue
            probs.append(float(post))
            labels.append(y)
            srcs.append(p.name)
    return np.array(probs), np.array(labels), srcs


# ─── Expected Calibration Error ───────────────────────────────────────────

def compute_ece(probs: np.ndarray, labels: np.ndarray,
                n_bins: int = 10) -> dict:
    """Equal-width-bin ECE with per-bin diagnostics."""
    assert probs.shape == labels.shape
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    edges[-1] = 1.0 + 1e-9  # ensure 1.0 lands in the last bin
    bin_idx = np.digitize(probs, edges) - 1

    rows = []
    total = len(probs)
    ece = 0.0
    mce = 0.0  # maximum calibration error
    for b in range(n_bins):
        mask = bin_idx == b
        n = int(mask.sum())
        if n == 0:
            rows.append({"bin": b, "lo": float(edges[b]), "hi": float(edges[b+1]),
                         "n": 0, "mean_prob": None, "freq_pos": None, "gap": None})
            continue
        mean_prob = float(probs[mask].mean())
        freq_pos = float(labels[mask].mean())
        gap = abs(freq_pos - mean_prob)
        ece += (n / total) * gap
        mce = max(mce, gap)
        rows.append({"bin": b, "lo": float(edges[b]), "hi": float(edges[b+1]),
                     "n": n, "mean_prob": round(mean_prob, 4),
                     "freq_pos": round(freq_pos, 4),
                     "gap": round(gap, 4)})
    return {"ece": round(ece, 4), "mce": round(mce, 4),
            "total": total, "bins": rows}


def compute_brier(probs: np.ndarray, labels: np.ndarray) -> float:
    return float(np.mean((probs - labels) ** 2))


# ─── Platt scaling (logistic recalibration) ───────────────────────────────

def platt_recalibrate(probs: np.ndarray, labels: np.ndarray,
                      cv_folds: int = 5) -> np.ndarray:
    """Fit a logistic regression p_calib = sigmoid(a*logit(p) + b) with k-fold CV.

    Returns out-of-fold calibrated probabilities of the same length as `probs`.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import KFold

    eps = 1e-6
    logit = np.log(np.clip(probs, eps, 1 - eps) /
                   np.clip(1 - probs, eps, 1 - eps))
    out = np.zeros_like(probs)
    kf = KFold(n_splits=cv_folds, shuffle=True, random_state=0)
    for train_idx, test_idx in kf.split(logit):
        lr = LogisticRegression(C=1.0, solver="lbfgs")
        lr.fit(logit[train_idx].reshape(-1, 1), labels[train_idx])
        out[test_idx] = lr.predict_proba(logit[test_idx].reshape(-1, 1))[:, 1]
    return out


# ─── Reliability diagram (matplotlib) ─────────────────────────────────────

def plot_reliability(ece_result: dict, title: str = "",
                     out_path: Path | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    bins = [b for b in ece_result["bins"] if b["n"] > 0]
    if not bins:
        return
    xs = [b["mean_prob"] for b in bins]
    ys = [b["freq_pos"] for b in bins]
    ns = [b["n"] for b in bins]
    fig, ax = plt.subplots(figsize=(4.2, 4.0))
    # Perfect calibration line
    ax.plot([0, 1], [0, 1], "k--", linewidth=1, label="perfect")
    # Per-bin points sized by support
    ax.scatter(xs, ys, s=[max(20, n * 2) for n in ns],
               alpha=0.7, c="C0", edgecolors="k", linewidths=0.5,
               label="bins (size $\\propto$ n)")
    for x, y, n in zip(xs, ys, ns):
        ax.annotate(f"n={n}", (x, y), fontsize=7,
                    xytext=(4, 4), textcoords="offset points")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Empirical positive rate")
    ttl = title or "Reliability diagram"
    ax.set_title(f"{ttl}\nECE={ece_result['ece']:.3f}  MCE={ece_result['mce']:.3f}",
                 fontsize=10)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    if out_path:
        fig.savefig(out_path, dpi=150)
        print(f"  Wrote reliability plot to {out_path}")
    plt.close(fig)


# ─── Entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+", help="Benchmark result JSON files")
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--platt", action="store_true",
                        help="Also report ECE after Platt-scaling recalibration")
    parser.add_argument("--plot", type=str, default=None,
                        help="Write reliability diagram to this path "
                             "(or directory; one file per input result)")
    args = parser.parse_args()

    paths = [Path(p) for p in args.results]
    probs, labels, srcs = load_pairs(paths)
    print(f"Loaded {len(probs)} (posterior, label) pairs from "
          f"{len(set(srcs))} files")
    print(f"Class balance: positives={int(labels.sum())}, "
          f"negatives={int((1-labels).sum())}")
    print()

    # Headline metrics
    ece_raw = compute_ece(probs, labels, n_bins=args.bins)
    brier_raw = compute_brier(probs, labels)
    print(f"=== Raw L4 posteriors ===")
    print(f"  ECE = {ece_raw['ece']:.4f}  (max bin gap MCE = {ece_raw['mce']:.4f})")
    print(f"  Brier score = {brier_raw:.4f}")
    print(f"  Per-bin diagnostics:")
    print(f"    {'bin':>3} {'range':>14} {'n':>5} {'mean_p':>8} {'freq+':>8} {'gap':>7}")
    for b in ece_raw["bins"]:
        if b["n"] == 0:
            continue
        rng = f"[{b['lo']:.2f},{b['hi']:.2f})"
        print(f"    {b['bin']:>3} {rng:>14} {b['n']:>5} "
              f"{b['mean_prob']:>8.4f} {b['freq_pos']:>8.4f} {b['gap']:>7.4f}")

    if args.plot:
        out = Path(args.plot)
        out.parent.mkdir(parents=True, exist_ok=True)
        plot_reliability(ece_raw, title="IntegriRef L4 posterior", out_path=out)

    if args.platt:
        calibrated = platt_recalibrate(probs, labels)
        ece_cal = compute_ece(calibrated, labels, n_bins=args.bins)
        brier_cal = compute_brier(calibrated, labels)
        print()
        print(f"=== After Platt recalibration (5-fold CV) ===")
        print(f"  ECE = {ece_cal['ece']:.4f}  (was {ece_raw['ece']:.4f}, "
              f"delta = {ece_cal['ece']-ece_raw['ece']:+.4f})")
        print(f"  Brier = {brier_cal:.4f}  (was {brier_raw:.4f}, "
              f"delta = {brier_cal-brier_raw:+.4f})")
        if args.plot:
            calib_out = Path(args.plot).with_name(
                Path(args.plot).stem + "_platt" + Path(args.plot).suffix)
            plot_reliability(ece_cal, title="After Platt recalibration",
                             out_path=calib_out)


if __name__ == "__main__":
    main()
