"""Fusion ablation: Naive Bayes vs Grouped Bayesian vs Dempster-Shafer.

Runs all three L4 scoring methods on the golden test set and compares:
  - Risk tier accuracy
  - Signal correlation handling
  - Calibration quality

Uses pre-computed L0 results from the comparison benchmark to avoid
re-running the expensive registry queries.

Usage:
    python -m benchmarks.bench_fusion

Output: benchmarks/results/fusion_{timestamp}.json
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scoring.bayesian import BayesianRiskScorer, SignalObservation, RiskTier
from scoring.dempster_shafer import DempsterShaferScorer, GroupedBayesianScorer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bench_fusion")

RESULTS_DIR = Path(__file__).parent / "results"


def load_comparison_data() -> dict:
    """Load the most recent comparison results (has L0 signal data)."""
    results_dir = Path(__file__).parent / "results"
    comparison_files = sorted(results_dir.glob("comparison_*.json"), reverse=True)
    if not comparison_files:
        raise FileNotFoundError("No comparison results found. Run bench_comparison first.")
    path = comparison_files[0]
    logger.info("Loading comparison data from %s", path.name)
    with open(path) as f:
        return json.load(f)


def load_benchmark_data() -> dict:
    """Load the most recent integrity benchmark results."""
    results_dir = Path(__file__).parent / "results"
    bench_files = sorted(results_dir.glob("integrity_benchmark_*.json"), reverse=True)
    if not bench_files:
        raise FileNotFoundError("No integrity benchmark results found.")
    path = bench_files[0]
    logger.info("Loading benchmark data from %s", path.name)
    with open(path) as f:
        return json.load(f)


# Map signal names that might be in benchmark results
KNOWN_L0_SIGNALS = {
    "reference_not_found", "phantom_doi", "metadata_mismatch",
    "no_id_match", "retracted_citation",
}


def run_fusion_comparison():
    """Run all three scorers on the same signal observations."""
    # Try comparison data first (has more detail), fall back to benchmark
    try:
        data = load_comparison_data()
        per_case = data["per_case"]
    except FileNotFoundError:
        data = load_benchmark_data()
        per_case = data["per_case"]

    results = []
    risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3}

    for case in per_case:
        case_id = case["id"]
        category = case["category"]
        signals_fired = set(case.get("integriref", case).get("signals_fired", []))

        # Reconstruct signal observations
        observations = []
        for sig_name in KNOWN_L0_SIGNALS:
            fired = sig_name in signals_fired
            if sig_name == "reference_not_found":
                conf = 0.9 if fired else 1.0
            elif sig_name == "phantom_doi":
                conf = 1.0
            elif sig_name == "metadata_mismatch":
                conf = 0.8 if fired else 0.9
            elif sig_name == "no_id_match":
                conf = 0.85 if fired else 0.9
            elif sig_name == "retracted_citation":
                conf = 1.0
            else:
                conf = 0.9
            observations.append(SignalObservation(
                signal_name=sig_name, fired=fired,
                confidence=conf, details="",
            ))

        # Run all three scorers
        bayes = BayesianRiskScorer()
        grouped = GroupedBayesianScorer()
        ds = DempsterShaferScorer(prior_strength=0.8)

        for obs in observations:
            bayes.observe(obs.signal_name, obs.fired, obs.confidence)
            grouped.observe(obs.signal_name, obs.fired, obs.confidence)
            ds.observe(obs.signal_name, obs.fired, obs.confidence)

        b_report = bayes.compute()
        g_report = grouped.compute()
        d_report = ds.compute()

        # Determine expected tier
        if category in ("hallucinated", "chimera"):
            expected_min = "HIGH"  # Should be at least HIGH
        elif category == "retracted":
            expected_min = "ELEVATED"  # Should be at least ELEVATED
        else:
            expected_min = "LOW"  # Should be LOW

        def _correct(tier_str, expected_min, category):
            level = risk_order.get(tier_str.upper(), -1)
            exp_level = risk_order.get(expected_min, -1)
            if category in ("retracted", "hallucinated", "chimera"):
                return level >= exp_level
            else:
                return level <= 0  # Must be LOW

        results.append({
            "id": case_id,
            "category": category,
            "signals_fired": sorted(signals_fired),
            "bayes": {
                "posterior": b_report.posterior_probability,
                "tier": b_report.risk_tier.value,
                "correct": _correct(b_report.risk_tier.value, expected_min, category),
            },
            "grouped": {
                "posterior": g_report.posterior_probability,
                "tier": g_report.risk_tier.value,
                "correct": _correct(g_report.risk_tier.value, expected_min, category),
                "conflict": g_report.conflict_level,
            },
            "ds": {
                "posterior": d_report.posterior_probability,
                "tier": d_report.risk_tier.value,
                "correct": _correct(d_report.risk_tier.value, expected_min, category),
                "uncertainty": d_report.uncertainty,
                "conflict": d_report.conflict_level,
            },
        })

    # Compute aggregate metrics
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    print("=" * 90)
    print("FUSION ABLATION: Naive Bayes vs Grouped Bayesian vs Dempster-Shafer")
    print("=" * 90)

    # Per-method accuracy
    for method in ["bayes", "grouped", "ds"]:
        total_correct = sum(1 for r in results if r[method]["correct"])
        print(f"\n{method.upper():>10s}: {total_correct}/{len(results)} correct ({total_correct/len(results):.1%})")
        for cat, cat_results in sorted(by_cat.items()):
            correct = sum(1 for r in cat_results if r[method]["correct"])
            n = len(cat_results)
            avg_post = sum(r[method]["posterior"] for r in cat_results) / n
            print(f"  {cat:15s}: {correct}/{n} ({correct/n:.1%})  avg_posterior={avg_post:.4f}")

    # Hallucination recall at different thresholds
    print("\n── Hallucination Recall by Threshold ──")
    halluc = by_cat.get("hallucinated", [])
    if halluc:
        for method in ["bayes", "grouped", "ds"]:
            for threshold in ["ELEVATED", "HIGH", "CRITICAL"]:
                level = risk_order[threshold]
                count = sum(1 for r in halluc
                            if risk_order.get(r[method]["tier"].upper(), -1) >= level)
                print(f"  {method:>10s} ≥{threshold:8s}: {count}/{len(halluc)} ({count/len(halluc):.1%})")
            print()

    # FPR
    print("── False Positive Rate ──")
    real = by_cat.get("real", [])
    if real:
        for method in ["bayes", "grouped", "ds"]:
            fp = sum(1 for r in real
                     if risk_order.get(r[method]["tier"].upper(), -1) >= 1)
            print(f"  {method:>10s}: {fp}/{len(real)} ({fp/len(real):.1%})")

    # Cases where methods disagree
    print("\n── Disagreement Cases ──")
    for r in results:
        tiers = {
            "B": r["bayes"]["tier"],
            "G": r["grouped"]["tier"],
            "DS": r["ds"]["tier"],
        }
        if len(set(tiers.values())) > 1:
            print(f"  {r['id']:20s} {r['category']:12s} "
                  f"B={tiers['B']:8s} G={tiers['G']:8s} DS={tiers['DS']:8s} "
                  f"sig={r['signals_fired']}")

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"fusion_{timestamp}.json"

    # Compute summary metrics
    summary = {}
    for method in ["bayes", "grouped", "ds"]:
        method_metrics = {
            "total_correct": sum(1 for r in results if r[method]["correct"]),
            "total_cases": len(results),
        }
        for cat in sorted(by_cat.keys()):
            cat_results = by_cat[cat]
            method_metrics[f"{cat}_correct"] = sum(
                1 for r in cat_results if r[method]["correct"])
            method_metrics[f"{cat}_count"] = len(cat_results)
            method_metrics[f"{cat}_avg_posterior"] = round(
                sum(r[method]["posterior"] for r in cat_results) / len(cat_results), 4)

        # FPR
        if real:
            method_metrics["fpr"] = round(
                sum(1 for r in real
                    if risk_order.get(r[method]["tier"].upper(), -1) >= 1)
                / len(real), 4)

        # Hallucination recall at HIGH
        if halluc:
            method_metrics["halluc_recall_high"] = round(
                sum(1 for r in halluc
                    if risk_order.get(r[method]["tier"].upper(), -1) >= 2)
                / len(halluc), 4)
            method_metrics["halluc_recall_elevated"] = round(
                sum(1 for r in halluc
                    if risk_order.get(r[method]["tier"].upper(), -1) >= 1)
                / len(halluc), 4)

        summary[method] = method_metrics

    output = {
        "metadata": {"timestamp": timestamp, "total_cases": len(results)},
        "summary": summary,
        "per_case": results,
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info("Results saved to %s", out_path)

    return output


if __name__ == "__main__":
    run_fusion_comparison()
