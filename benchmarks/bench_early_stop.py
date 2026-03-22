"""Early stopping simulation — measure L1-L3 skip rates and latency savings.

Uses pre-computed L0 signal data from the comparison benchmark to simulate
early stopping at various thresholds. Reports:
  - Skip rate (% of cases where L1-L3 are skipped)
  - Estimated latency savings
  - Risk tier changes (any regressions from early stopping?)

Usage:
    python -m benchmarks.bench_early_stop

Output: benchmarks/results/early_stop_{timestamp}.json
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scoring.bayesian import BayesianRiskScorer, SignalObservation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bench_early_stop")

RESULTS_DIR = Path(__file__).parent / "results"

KNOWN_L0_SIGNALS = {
    "reference_not_found", "phantom_doi", "metadata_mismatch",
    "no_id_match", "retracted_citation",
}

# Estimated L1-L3 costs (ms) per reference
L1_COST_MS = 50    # Intent classification (local model, fast)
L2_COST_MS = 200   # NLI verification (local model)
L3_COST_MS = 2000  # Graph building (OpenAlex API calls)


def simulate_early_stopping(
    threshold_high: float = 0.80,
    threshold_low: float = 0.01,
) -> dict:
    """Simulate early stopping on pre-computed L0 signals."""
    # Load comparison data
    results_dir = Path(__file__).parent / "results"
    files = sorted(results_dir.glob("comparison_*.json"), reverse=True)
    if not files:
        raise FileNotFoundError("No comparison results. Run bench_comparison first.")

    with open(files[0]) as f:
        data = json.load(f)

    per_case = data["per_case"]
    results = []
    risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3}

    for case in per_case:
        signals_fired = set(case.get("integriref", case).get("signals_fired", []))
        original_tier = case.get("integriref", case).get("risk_tier", "UNKNOWN")

        # Reconstruct L0 signals
        observations = []
        for sig_name in KNOWN_L0_SIGNALS:
            fired = sig_name in signals_fired
            conf = {
                "reference_not_found": 0.9 if fired else 1.0,
                "phantom_doi": 1.0,
                "metadata_mismatch": 0.8 if fired else 0.9,
                "no_id_match": 0.85 if fired else 0.9,
                "retracted_citation": 1.0,
            }.get(sig_name, 0.9)
            observations.append(SignalObservation(
                signal_name=sig_name, fired=fired, confidence=conf))

        # Run L4 on L0 signals only
        scorer = BayesianRiskScorer()
        for obs in observations:
            scorer.observe(obs.signal_name, obs.fired, obs.confidence)
        report = scorer.compute()

        l0_posterior = report.posterior_probability
        l0_tier = report.risk_tier.value

        # Would early stop?
        would_skip = (l0_posterior > threshold_high or
                      l0_posterior < threshold_low)
        skip_reason = ""
        if l0_posterior > threshold_high:
            skip_reason = "HIGH_CONFIDENCE_BAD"
        elif l0_posterior < threshold_low:
            skip_reason = "HIGH_CONFIDENCE_GOOD"

        # Check if tier changes
        tier_change = l0_tier != original_tier.lower()

        results.append({
            "id": case["id"],
            "category": case["category"],
            "signals_fired": sorted(signals_fired),
            "l0_posterior": round(l0_posterior, 4),
            "l0_tier": l0_tier,
            "original_tier": original_tier,
            "would_skip": would_skip,
            "skip_reason": skip_reason,
            "tier_change": tier_change,
        })

    return results


def run_early_stop_analysis():
    """Run early stopping analysis at multiple thresholds."""
    thresholds = [
        (0.80, 0.01, "Conservative"),
        (0.70, 0.02, "Moderate"),
        (0.50, 0.05, "Aggressive"),
    ]

    print("=" * 80)
    print("EARLY STOPPING ANALYSIS: L0→L4 Cascade Optimization")
    print("=" * 80)

    all_results = {}

    for t_high, t_low, name in thresholds:
        results = simulate_early_stopping(t_high, t_low)
        n = len(results)

        skip_count = sum(1 for r in results if r["would_skip"])
        skip_high = sum(1 for r in results
                        if r["skip_reason"] == "HIGH_CONFIDENCE_BAD")
        skip_low = sum(1 for r in results
                       if r["skip_reason"] == "HIGH_CONFIDENCE_GOOD")

        # Tier regressions (early stop changes tier vs full pipeline)
        regressions = [r for r in results
                       if r["would_skip"] and r["tier_change"]]

        # Latency savings
        saved_per_ref_ms = L1_COST_MS + L2_COST_MS + L3_COST_MS
        total_saved_ms = skip_count * saved_per_ref_ms

        # Per-category breakdown
        by_cat = defaultdict(list)
        for r in results:
            by_cat[r["category"]].append(r)

        print(f"\n{'─' * 80}")
        print(f"Threshold: {name} (skip if posterior > {t_high} or < {t_low})")
        print(f"{'─' * 80}")
        print(f"  Skip rate: {skip_count}/{n} ({skip_count/n:.1%})")
        print(f"    Skipped (HIGH confidence bad): {skip_high}")
        print(f"    Skipped (HIGH confidence good): {skip_low}")
        print(f"  Estimated savings: {total_saved_ms/1000:.1f}s "
              f"({saved_per_ref_ms}ms × {skip_count} refs)")
        print(f"  Tier regressions: {len(regressions)}")

        if regressions:
            print("  Regressions:")
            for r in regressions:
                print(f"    {r['id']:20s} {r['category']:12s} "
                      f"L0={r['l0_tier']:8s} full={r['original_tier']:8s}")

        print(f"\n  Per-category skip rates:")
        for cat, cat_results in sorted(by_cat.items()):
            skipped = sum(1 for r in cat_results if r["would_skip"])
            cat_n = len(cat_results)
            print(f"    {cat:15s}: {skipped}/{cat_n} ({skipped/cat_n:.1%})")

        all_results[name] = {
            "threshold_high": t_high,
            "threshold_low": t_low,
            "total_cases": n,
            "skip_count": skip_count,
            "skip_rate": round(skip_count / n, 4),
            "skip_high_bad": skip_high,
            "skip_low_good": skip_low,
            "regressions": len(regressions),
            "estimated_savings_ms": total_saved_ms,
            "per_category": {
                cat: {
                    "count": len(cr),
                    "skipped": sum(1 for r in cr if r["would_skip"]),
                    "skip_rate": round(
                        sum(1 for r in cr if r["would_skip"]) / len(cr), 4),
                }
                for cat, cr in sorted(by_cat.items())
            },
        }

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"early_stop_{timestamp}.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    logger.info("Results saved to %s", out_path)

    print(f"\n{'=' * 80}")
    return all_results


if __name__ == "__main__":
    run_early_stop_analysis()
