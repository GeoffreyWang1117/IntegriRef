"""Head-to-head comparison: IntegriRef (L0+L4) vs CheckIfExist baseline.

Runs both systems on the golden test set and compares:
  - Hallucination recall & precision
  - Retraction recall
  - Chimera recall
  - False positive rate
  - Latency

Usage:
    python -m benchmarks.bench_comparison
    python -m benchmarks.bench_comparison --max-cases 10   # quick test

Output: benchmarks/results/comparison_{timestamp}.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.bench_integrity import (
    IntegrityTestCase, IntegrityResult,
    load_golden_test_set, init_pipeline, run_test_case,
    compute_metrics, format_detailed_results,
)
from benchmarks.baselines.checkifexist import check_if_exist, CheckIfExistResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bench_comparison")

RESULTS_DIR = Path(__file__).parent / "results"


# ── CheckIfExist evaluation ──────────────────────────────────────────────

@dataclass
class BaselineResult:
    """Result of running one test case through CheckIfExist baseline."""
    test_case: IntegrityTestCase
    cie_result: CheckIfExistResult = field(default_factory=CheckIfExistResult)
    flagged: bool = False       # Did the baseline flag this as problematic?
    risk_level: str = "LOW"     # LOW / ELEVATED / HIGH mapped from CIE status
    correct: bool = False
    latency_ms: float = 0.0


def run_cie_case(case: IntegrityTestCase,
                 timeout: float = 10.0) -> BaselineResult:
    """Run a single test case through CheckIfExist."""
    result = BaselineResult(test_case=case)
    ref = case.to_ref_dict()

    cie = check_if_exist(ref, timeout=timeout)
    result.cie_result = cie
    result.latency_ms = cie.latency_ms

    # Map CheckIfExist status to risk levels:
    # NOT_FOUND → HIGH (flagged)
    # SUSPICIOUS → ELEVATED (flagged)
    # VERIFIED → LOW (not flagged)
    if cie.status == "NOT_FOUND":
        result.risk_level = "HIGH"
        result.flagged = True
    elif cie.status == "SUSPICIOUS":
        result.risk_level = "ELEVATED"
        result.flagged = True
    else:
        result.risk_level = "LOW"
        result.flagged = False

    # Evaluate correctness
    if case.category in ("retracted", "hallucinated", "chimera"):
        # Bad paper: should be flagged (ELEVATED or higher)
        result.correct = result.flagged
    else:
        # Good paper: should NOT be flagged
        result.correct = not result.flagged

    return result


def compute_cie_metrics(results: list[BaselineResult]) -> dict:
    """Compute metrics for CheckIfExist baseline."""
    by_cat: dict[str, list[BaselineResult]] = defaultdict(list)
    for r in results:
        by_cat[r.test_case.category].append(r)

    metrics = {
        "total_cases": len(results),
        "total_correct": sum(1 for r in results if r.correct),
    }

    # Hallucination recall (flagged ≥ HIGH)
    halluc = by_cat.get("hallucinated", [])
    if halluc:
        flagged_high = sum(1 for r in halluc if r.risk_level in ("HIGH", "CRITICAL"))
        metrics["hallucination_recall"] = round(flagged_high / len(halluc), 4)
        metrics["hallucination_count"] = len(halluc)
    else:
        metrics["hallucination_recall"] = 0.0

    # Hallucination precision (of all flagged ≥ HIGH, % actually bad)
    all_flagged_high = [r for r in results if r.risk_level in ("HIGH", "CRITICAL")]
    if all_flagged_high:
        tp = sum(1 for r in all_flagged_high
                 if r.test_case.category in ("retracted", "hallucinated", "chimera"))
        metrics["hallucination_precision"] = round(tp / len(all_flagged_high), 4)
    else:
        metrics["hallucination_precision"] = 0.0

    # Retraction recall (flagged ≥ ELEVATED)
    retracted = by_cat.get("retracted", [])
    if retracted:
        flagged = sum(1 for r in retracted if r.flagged)
        metrics["retraction_recall"] = round(flagged / len(retracted), 4)
        metrics["retraction_count"] = len(retracted)
    else:
        metrics["retraction_recall"] = 0.0

    # Chimera recall (flagged ≥ ELEVATED)
    chimera = by_cat.get("chimera", [])
    if chimera:
        flagged = sum(1 for r in chimera if r.flagged)
        metrics["chimera_recall"] = round(flagged / len(chimera), 4)
        metrics["chimera_count"] = len(chimera)
    else:
        metrics["chimera_recall"] = 0.0

    # False positive rate (real papers flagged ≥ ELEVATED)
    real = by_cat.get("real", [])
    if real:
        fp = sum(1 for r in real if r.flagged)
        metrics["false_positive_rate"] = round(fp / len(real), 4)
        metrics["real_count"] = len(real)
    else:
        metrics["false_positive_rate"] = 0.0

    # Latency
    latencies = [r.latency_ms for r in results]
    if latencies:
        metrics["avg_latency_ms"] = round(sum(latencies) / len(latencies), 1)
        latencies_sorted = sorted(latencies)
        p95_idx = int(len(latencies_sorted) * 0.95)
        metrics["p95_latency_ms"] = round(
            latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)], 1)

    # Per-category breakdown
    cat_breakdown = {}
    for cat, cat_results in by_cat.items():
        risk_dist = Counter(r.risk_level for r in cat_results)
        cat_breakdown[cat] = {
            "count": len(cat_results),
            "correct_rate": round(
                sum(1 for r in cat_results if r.correct) / len(cat_results), 4),
            "risk_distribution": dict(risk_dist),
            "avg_title_sim": round(
                sum(r.cie_result.best_title_sim for r in cat_results)
                / len(cat_results), 4),
        }
    metrics["category_breakdown"] = cat_breakdown

    return metrics


# ── Comparison output ────────────────────────────────────────────────────

def format_comparison(
    ir_metrics: dict,
    cie_metrics: dict,
) -> str:
    """Format a side-by-side comparison table."""
    lines = [
        "=" * 78,
        "HEAD-TO-HEAD COMPARISON: IntegriRef (L0+L4) vs CheckIfExist (baseline)",
        "=" * 78,
        "",
        f"{'Metric':<35s}  {'IntegriRef':>15s}  {'CheckIfExist':>15s}",
        "-" * 78,
    ]

    def _pct(v):
        if isinstance(v, (int, float)):
            return f"{v:.1%}"
        return str(v)

    rows = [
        ("Hallucination Recall (≥HIGH)",
         ir_metrics.get("hallucination_recall", 0),
         cie_metrics.get("hallucination_recall", 0)),
        ("Hallucination Precision",
         ir_metrics.get("hallucination_precision", 0),
         cie_metrics.get("hallucination_precision", 0)),
        ("Retraction Recall (≥ELEVATED)",
         ir_metrics.get("retraction_recall", 0),
         cie_metrics.get("retraction_recall", 0)),
        ("Chimera Recall (≥ELEVATED)",
         ir_metrics.get("chimera_recall", 0),
         cie_metrics.get("chimera_recall", 0)),
        ("False Positive Rate",
         ir_metrics.get("false_positive_rate", 0),
         cie_metrics.get("false_positive_rate", 0)),
    ]

    for label, ir_val, cie_val in rows:
        ir_str = _pct(ir_val)
        cie_str = _pct(cie_val)
        # Highlight winner
        if label == "False Positive Rate":
            better = "←" if ir_val <= cie_val else "→"
        else:
            better = "←" if ir_val >= cie_val else "→"
        lines.append(f"  {label:<33s}  {ir_str:>15s}  {cie_str:>15s}  {better}")

    lines.append("-" * 78)

    # Latency
    ir_lat = ir_metrics.get("avg_latency_ms", 0)
    cie_lat = cie_metrics.get("avg_latency_ms", 0)
    lines.append(
        f"  {'Avg Latency (ms)':<33s}  {ir_lat:>14.0f}ms  {cie_lat:>14.0f}ms")

    ir_p95 = ir_metrics.get("p95_latency_ms", 0)
    cie_p95 = cie_metrics.get("p95_latency_ms", 0)
    lines.append(
        f"  {'P95 Latency (ms)':<33s}  {ir_p95:>14.0f}ms  {cie_p95:>14.0f}ms")

    lines.append("")
    lines.append("← = IntegriRef wins   → = CheckIfExist wins")
    lines.append("=" * 78)

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────

def run_comparison(
    max_cases: int = 0,
    timeout: float = 15.0,
    save_results: bool = True,
) -> dict:
    """Run head-to-head comparison benchmark.

    Returns dict with both systems' metrics and comparison.
    """
    # Load test cases
    cases = load_golden_test_set()
    if max_cases > 0:
        cases = cases[:max_cases]

    logger.info("Running comparison benchmark: %d cases", len(cases))

    # ── Run IntegriRef (L0+L4) ──────────────────────────────────────
    logger.info("=== Phase 1: IntegriRef (L0+L4) ===")
    pipeline = init_pipeline(layers=["L0", "L4"])
    ir_results = []
    for i, case in enumerate(cases):
        logger.info("[IR %d/%d] %s (%s): %s",
                    i + 1, len(cases), case.id, case.category,
                    case.title[:50])
        ir_results.append(run_test_case(pipeline, case, timeout=timeout))

    ir_metrics_obj = compute_metrics(ir_results)
    ir_metrics = ir_metrics_obj.to_dict()

    # ── Run CheckIfExist baseline ───────────────────────────────────
    logger.info("=== Phase 2: CheckIfExist (baseline) ===")
    cie_results = []
    for i, case in enumerate(cases):
        logger.info("[CIE %d/%d] %s (%s): %s",
                    i + 1, len(cases), case.id, case.category,
                    case.title[:50])
        cie_results.append(run_cie_case(case, timeout=timeout))

    cie_metrics = compute_cie_metrics(cie_results)

    # ── Print comparison ────────────────────────────────────────────
    comparison_text = format_comparison(ir_metrics, cie_metrics)
    print("\n" + comparison_text)

    # Detailed IntegriRef results
    print("\n── IntegriRef Detailed Results ──")
    print(format_detailed_results(ir_results))

    # Detailed CheckIfExist results
    print("\n── CheckIfExist Detailed Results ──")
    for r in cie_results:
        c = r.test_case
        icon = "+" if r.correct else "X"
        print(
            f"[{icon}] {c.id:20s} | {c.category:12s} | "
            f"status={r.cie_result.status:12s} risk={r.risk_level:8s} | "
            f"title_sim={r.cie_result.best_title_sim:.2f} "
            f"author_ovl={r.cie_result.author_overlap:.2f} | "
            f"{r.latency_ms:.0f}ms"
        )

    # ── Save ────────────────────────────────────────────────────────
    output = {
        "metadata": {
            "timestamp": time.strftime("%Y%m%d_%H%M%S"),
            "total_cases": len(cases),
            "case_distribution": dict(Counter(c.category for c in cases)),
        },
        "integriref": {
            "method": "IntegriRef L0+L4 (Bayesian risk scoring)",
            "layers": ["L0", "L4"],
            "metrics": ir_metrics,
        },
        "checkifexist": {
            "method": "CheckIfExist baseline (CrossRef→S2→OpenAlex cascade)",
            "metrics": cie_metrics,
        },
        "comparison": {
            "hallucination_recall_delta": round(
                ir_metrics.get("hallucination_recall", 0)
                - cie_metrics.get("hallucination_recall", 0), 4),
            "retraction_recall_delta": round(
                ir_metrics.get("retraction_recall", 0)
                - cie_metrics.get("retraction_recall", 0), 4),
            "chimera_recall_delta": round(
                ir_metrics.get("chimera_recall", 0)
                - cie_metrics.get("chimera_recall", 0), 4),
            "fpr_delta": round(
                ir_metrics.get("false_positive_rate", 0)
                - cie_metrics.get("false_positive_rate", 0), 4),
        },
        "per_case": [
            {
                "id": cases[i].id,
                "category": cases[i].category,
                "title": cases[i].title[:80],
                "integriref": {
                    "risk_tier": ir_results[i].actual_risk,
                    "risk_prob": (round(ir_results[i].report.risk_probability, 4)
                                  if ir_results[i].report else None),
                    "signals_fired": ir_results[i].signals_fired,
                    "correct": ir_results[i].risk_correct,
                    "latency_ms": round(ir_results[i].latency_ms, 1),
                },
                "checkifexist": {
                    "status": cie_results[i].cie_result.status,
                    "risk_level": cie_results[i].risk_level,
                    "title_sim": round(cie_results[i].cie_result.best_title_sim, 4),
                    "author_overlap": round(cie_results[i].cie_result.author_overlap, 4),
                    "correct": cie_results[i].correct,
                    "latency_ms": round(cie_results[i].latency_ms, 1),
                },
            }
            for i in range(len(cases))
        ],
    }

    if save_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"comparison_{timestamp}.json"
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logger.info("Results saved to %s", out_path)

    return output


def main():
    parser = argparse.ArgumentParser(
        description="IntegriRef vs CheckIfExist comparison benchmark")
    parser.add_argument("--max-cases", type=int, default=0,
                        help="Limit total test cases (0=unlimited)")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="Per-reference timeout in seconds")
    parser.add_argument("--no-save", action="store_true",
                        help="Don't save results to file")
    args = parser.parse_args()

    run_comparison(
        max_cases=args.max_cases,
        timeout=args.timeout,
        save_results=not args.no_save,
    )


if __name__ == "__main__":
    main()
