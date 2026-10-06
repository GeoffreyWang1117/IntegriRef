"""Rebuttal Exp 4 — does L4 fusion change decisions relative to L0 alone?

Reviewer HE9x asked: "Can you show L4 producing measurable tier-flips over
L0 alone at N>100?"

On the pooled large splits (rv-1926 + ret-6391) we compare, for every case
where at least one L0 signal fires:

  strongest-single-signal tier — posterior computed with ONLY the strongest
      fired signal (highest hand-set LR+) active and all other L0 signals
      multiplying their lr_negative (i.e., L0-without-fusion: each signal
      escalates on its own)
  fused tier — posterior with ALL fired signals active (the paper's L4)

Both use the identical hand-set LR table and prior, so any tier difference
is attributable purely to the fusion of multiple signals. We report the
multi-signal subset (>=2 signals fired) separately, since single-signal
cases are identical under both schemes by construction.

Usage:
    python -m benchmarks.exp_l4_tierflips [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

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
TIERS = [(0.50, "critical"), (0.20, "high"), (0.05, "elevated")]
TIER_ORDER = {"low": 0, "elevated": 1, "high": 2, "critical": 3}


def tier(p: float) -> str:
    for thr, name in TIERS:
        if p >= thr:
            return name
    return "low"


def posterior(fired: set[str]) -> float:
    log_odds = math.log(PRIOR / (1 - PRIOR))
    for s in L0_SIGNALS:
        d = SIGNAL_DEFINITIONS[s]
        log_odds += math.log(d.lr_positive if s in fired else d.lr_negative)
    odds = math.exp(min(log_odds, 700))
    return odds / (1 + odds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cases = []
    for split, path in SPLIT_FILES.items():
        d = json.loads(path.read_text())
        for r in d["results"]:
            cat = r.get("category", "")
            if cat not in POS | NEG:
                continue
            fired = {s for s in r.get("signals_fired", []) if s in L0_SIGNALS}
            if not fired:
                continue
            cases.append({
                "split": split,
                "label": 1 if cat in POS else 0,
                "category": cat,
                "fired": fired,
            })

    multi = [c for c in cases if len(c["fired"]) >= 2]
    print(f"{len(cases)} cases with >=1 L0 signal; "
          f"{len(multi)} with >=2 signals (multi-signal regime)")

    flips = Counter()
    flip_matrix = Counter()
    per_combo = Counter()
    detail = []
    for c in multi:
        strongest = max(c["fired"],
                        key=lambda s: SIGNAL_DEFINITIONS[s].lr_positive)
        t_single = tier(posterior({strongest}))
        t_fused = tier(posterior(c["fired"]))
        per_combo["+".join(sorted(c["fired"]))] += 1
        if t_single != t_fused:
            flips["flipped"] += 1
            up = TIER_ORDER[t_fused] > TIER_ORDER[t_single]
            correct = (up and c["label"] == 1) or (not up and c["label"] == 0)
            flips["toward_truth" if correct else "away_from_truth"] += 1
            flip_matrix[f"{t_single} -> {t_fused} ({'pos' if c['label'] else 'neg'})"] += 1
        else:
            flips["no_change"] += 1
        detail.append({"split": c["split"], "category": c["category"],
                       "fired": sorted(c["fired"]),
                       "single_tier": t_single, "fused_tier": t_fused})

    # Decision-level (ELEVATED >= 0.05) flips
    dec_flips = sum(1 for c in multi
                    if (posterior({max(c['fired'], key=lambda s: SIGNAL_DEFINITIONS[s].lr_positive)}) >= 0.05)
                    != (posterior(c["fired"]) >= 0.05))

    report = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "n_signal_cases": len(cases),
        "n_multi_signal": len(multi),
        "multi_signal_by_label": dict(Counter(
            "pos" if c["label"] else "neg" for c in multi)),
        "tier_flips": dict(flips),
        "flip_matrix": dict(flip_matrix),
        "decision_flips_at_elevated": dec_flips,
        "top_signal_combos": dict(per_combo.most_common(10)),
    }
    print(json.dumps(report, indent=1))

    out = args.out or (REPO / "benchmarks/results" /
                       f"l4_tierflips_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps({**report, "cases": detail}, indent=1))
    print("saved ->", out)


if __name__ == "__main__":
    main()
