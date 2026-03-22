"""IntegriRef Integrity Benchmark — evaluate detection of retracted & hallucinated papers.

Tests the full L0-L4 pipeline against:
  1. Golden test set: curated retracted papers, real papers, LLM hallucinations, chimeras
  2. Crossref retracted papers (fetched via fetch_retracted.py)
  3. PubMed retracted papers

Metrics computed:
  - Retraction detection: recall (% of retracted flagged ≥ ELEVATED)
  - Hallucination detection: precision + recall (phantom DOI / not_found)
  - False positive rate: % of real papers incorrectly flagged
  - Risk tier accuracy: correct risk tier assignment
  - Per-signal analysis: which signals fire for which categories

Usage:
    python -m benchmarks.bench_integrity                      # Golden set only (fast)
    python -m benchmarks.bench_integrity --include-fetched    # + Crossref/PubMed data
    python -m benchmarks.bench_integrity --layers L0 L4       # Specific layers
    python -m benchmarks.bench_integrity --timeout 10         # Per-ref timeout (sec)

Output: benchmarks/results/integrity_benchmark_{timestamp}.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.metrics import ClassificationMetrics, BenchmarkResult
from core.discovery import RegistryDiscovery
from core.pipeline import IntegriRefPipeline, PipelineReport

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bench_integrity")

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"


# ── Test case data structures ────────────────────────────────────────────

@dataclass
class IntegrityTestCase:
    """A single test case for the integrity benchmark."""
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    doi: str = ""
    venue: str = ""
    category: str = ""          # retracted / real / hallucinated / chimera
    expected_risk: str = "LOW"  # LOW / ELEVATED / HIGH / CRITICAL
    expected_found: Optional[bool] = None
    expected_retracted: Optional[bool] = None
    note: str = ""
    source: str = ""            # golden / crossref / pubmed

    def to_ref_dict(self) -> dict:
        d = {}
        if self.title:
            d["title"] = self.title
        if self.authors:
            d["authors"] = self.authors
        if self.year:
            d["year"] = self.year
        if self.doi:
            d["doi"] = self.doi
        if self.venue:
            d["venue"] = self.venue
        return d


@dataclass
class IntegrityResult:
    """Result of running one test case through the pipeline."""
    test_case: IntegrityTestCase
    report: Optional[PipelineReport] = None
    actual_risk: str = "UNKNOWN"
    actual_found: bool = False
    actual_retracted: bool = False
    signals_fired: list[str] = field(default_factory=list)
    risk_correct: bool = False
    detection_correct: bool = False
    latency_ms: float = 0.0
    error: str = ""


# ── Load test cases ──────────────────────────────────────────────────────

def load_golden_test_set() -> list[IntegrityTestCase]:
    """Load the curated golden test set."""
    path = DATA_DIR / "golden_test_set.json"
    if not path.exists():
        logger.error("Golden test set not found: %s", path)
        return []

    with open(path) as f:
        data = json.load(f)

    cases = []

    # Retracted papers
    for item in data.get("retracted_papers", []):
        cases.append(IntegrityTestCase(
            id=item["id"],
            title=item["title"],
            authors=item.get("authors", []),
            year=item.get("year", ""),
            doi=item.get("doi", ""),
            venue=item.get("venue", ""),
            category="retracted" if item.get("expected_retracted") else "real",
            expected_risk=item.get("expected_risk", "HIGH"),
            expected_found=True,  # Retracted papers ARE findable
            expected_retracted=item.get("expected_retracted", True),
            note=item.get("note", ""),
            source="golden",
        ))

    # Real papers
    for item in data.get("real_papers", []):
        cases.append(IntegrityTestCase(
            id=item["id"],
            title=item["title"],
            authors=item.get("authors", []),
            year=item.get("year", ""),
            doi=item.get("doi", ""),
            venue=item.get("venue", ""),
            category="real",
            expected_risk="LOW",
            expected_found=True,
            expected_retracted=False,
            note=item.get("note", ""),
            source="golden",
        ))

    # LLM hallucinated
    for item in data.get("llm_hallucinated", []):
        expected_found = item.get("expected_found", False)
        cases.append(IntegrityTestCase(
            id=item["id"],
            title=item["title"],
            authors=item.get("authors", []),
            year=item.get("year", ""),
            doi=item.get("doi", ""),
            venue=item.get("venue", ""),
            category="hallucinated" if not expected_found else "real",
            expected_risk=item.get("expected_risk", "HIGH"),
            expected_found=expected_found,
            expected_retracted=False,
            note=item.get("note", ""),
            source="golden",
        ))

    # Metadata chimeras
    for item in data.get("metadata_chimera", []):
        expected_found = item.get("expected_found", False)
        cases.append(IntegrityTestCase(
            id=item["id"],
            title=item["title"],
            authors=item.get("authors", []),
            year=item.get("year", ""),
            doi=item.get("doi", ""),
            venue=item.get("venue", ""),
            category="chimera" if not expected_found else "real",
            expected_risk=item.get("expected_risk", "HIGH"),
            expected_found=expected_found,
            expected_retracted=False,
            note=item.get("note", ""),
            source="golden",
        ))

    logger.info("Loaded %d golden test cases (%s)",
                len(cases), Counter(c.category for c in cases))
    return cases


def load_fetched_data() -> list[IntegrityTestCase]:
    """Load test cases from previously fetched Crossref/PubMed data."""
    cases = []
    rw_dir = DATA_DIR / "retraction_watch"

    if not rw_dir.exists():
        logger.warning("No fetched data found at %s — run fetch_retracted.py first",
                       rw_dir)
        return []

    for jsonl_path in sorted(rw_dir.glob("*.jsonl")):
        count = 0
        with open(jsonl_path) as f:
            for line in f:
                try:
                    item = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                is_retracted = item.get("expected_retracted", False)
                is_real = item.get("expected_found", False) and not is_retracted

                if is_retracted:
                    category = "retracted"
                    expected_risk = "HIGH"
                elif is_real:
                    category = "real"
                    expected_risk = "LOW"
                else:
                    continue

                cases.append(IntegrityTestCase(
                    id=f"{jsonl_path.stem}_{count}",
                    title=item.get("title", ""),
                    authors=item.get("authors", []),
                    year=item.get("year", ""),
                    doi=item.get("doi", ""),
                    venue=item.get("venue", ""),
                    category=category,
                    expected_risk=expected_risk,
                    expected_found=True,
                    expected_retracted=is_retracted,
                    source=jsonl_path.stem,
                ))
                count += 1

        logger.info("Loaded %d cases from %s", count, jsonl_path.name)

    logger.info("Total fetched data: %d cases (%s)",
                len(cases), Counter(c.category for c in cases))
    return cases


# ── Pipeline runner ──────────────────────────────────────────────────────

def init_pipeline(
    layers: list[str],
    domain: str = "default",
) -> IntegriRefPipeline:
    """Initialize the IntegriRef pipeline with all registry adapters."""
    from registries.academic import ALL_ACADEMIC
    from registries.patents import ALL_PATENTS
    from registries.legal import ALL_LEGAL
    from registries.government import ALL_GOVERNMENT
    from registries.financial import ALL_FINANCIAL
    from registries.standards import ALL_STANDARDS

    discovery = RegistryDiscovery()
    all_registries = (
        ALL_ACADEMIC + ALL_PATENTS + ALL_LEGAL
        + ALL_GOVERNMENT + ALL_FINANCIAL + ALL_STANDARDS
    )
    discovery.register_all(all_registries)

    pipeline = IntegriRefPipeline(
        discovery=discovery,
        layers=layers,
        domain=domain,
        enable_l2_only_on_escalation=True,
    )

    logger.info("Pipeline initialized: %d registries, layers=%s",
                len(all_registries), layers)
    return pipeline


def run_test_case(
    pipeline: IntegriRefPipeline,
    case: IntegrityTestCase,
    timeout: float = 15.0,
) -> IntegrityResult:
    """Run a single test case through the pipeline."""
    result = IntegrityResult(test_case=case)
    start = time.monotonic()

    try:
        report = pipeline.verify(
            ref=case.to_ref_dict(),
            citing_sentence="",
            full_text="",
            abstract="",
        )
        result.report = report
        result.actual_risk = report.risk_tier.upper()
        result.actual_found = bool(report.l0 and report.l0.sources_hit)
        result.actual_retracted = bool(report.l0 and report.l0.is_retracted)
        result.signals_fired = [
            s.signal_name for s in report.signals if s.fired
        ]
    except Exception as e:
        result.error = str(e)
        logger.error("[%s] Error: %s", case.id, e)

    result.latency_ms = (time.monotonic() - start) * 1000

    # Evaluate correctness
    risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": -1}
    actual_level = risk_order.get(result.actual_risk, -1)
    expected_level = risk_order.get(case.expected_risk.upper(), -1)

    # Risk is "correct" if actual >= expected for bad papers,
    # or actual <= expected for good papers
    if case.category in ("retracted", "hallucinated", "chimera"):
        # Bad paper: should be flagged at or above expected level
        result.risk_correct = actual_level >= expected_level
    else:
        # Good paper: should NOT be overflagged
        result.risk_correct = actual_level <= expected_level

    # Detection correctness
    if case.expected_retracted:
        result.detection_correct = result.actual_retracted
    elif case.expected_found is not None:
        if case.expected_found:
            result.detection_correct = result.actual_found
        else:
            result.detection_correct = not result.actual_found

    return result


# ── Metrics computation ──────────────────────────────────────────────────

@dataclass
class IntegrityMetrics:
    """Comprehensive integrity benchmark metrics."""
    total_cases: int = 0
    total_errors: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0

    # Per-category metrics
    retraction_recall: float = 0.0  # % retracted papers flagged ≥ELEVATED
    retraction_retracted_flag_rate: float = 0.0  # % where is_retracted=True
    hallucination_recall: float = 0.0  # % hallucinated papers flagged ≥HIGH
    hallucination_precision: float = 0.0  # of flagged ≥HIGH, % actually bad
    chimera_recall: float = 0.0  # % chimeras flagged ≥ELEVATED
    false_positive_rate: float = 0.0  # % real papers flagged ≥ELEVATED

    # Risk tier confusion matrix
    risk_confusion: dict = field(default_factory=dict)

    # Signal analysis
    signal_fire_rates: dict = field(default_factory=dict)

    # Per-category breakdowns
    category_metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            "=" * 70,
            "INTEGRIREF INTEGRITY BENCHMARK RESULTS",
            "=" * 70,
            f"Total cases: {self.total_cases}  |  Errors: {self.total_errors}",
            f"Latency: avg={self.avg_latency_ms:.0f}ms  p95={self.p95_latency_ms:.0f}ms",
            "",
            "── Detection Performance ──────────────────────────────",
            f"  Retraction recall:        {self.retraction_recall:6.1%}  "
            f"(retracted papers flagged ≥ ELEVATED)",
            f"  Retraction flag rate:     {self.retraction_retracted_flag_rate:6.1%}  "
            f"(is_retracted=True detected)",
            f"  Hallucination recall:     {self.hallucination_recall:6.1%}  "
            f"(hallucinated refs flagged ≥ HIGH)",
            f"  Hallucination precision:  {self.hallucination_precision:6.1%}  "
            f"(of flagged ≥ HIGH, % actually bad)",
            f"  Chimera recall:           {self.chimera_recall:6.1%}  "
            f"(chimera refs flagged ≥ ELEVATED)",
            f"  FALSE POSITIVE RATE:      {self.false_positive_rate:6.1%}  "
            f"(real papers incorrectly flagged ≥ ELEVATED)",
            "",
        ]

        if self.category_metrics:
            lines.append("── Per-Category Breakdown ─────────────────────────────")
            for cat, m in sorted(self.category_metrics.items()):
                lines.append(
                    f"  {cat:15s}:  n={m['count']:3d}  "
                    f"risk_correct={m['risk_correct_rate']:.1%}  "
                    f"found_rate={m['found_rate']:.1%}  "
                    f"avg_risk_prob={m['avg_risk_prob']:.3f}"
                )
            lines.append("")

        if self.signal_fire_rates:
            lines.append("── Signal Fire Rates by Category ─────────────────────")
            # Header
            categories = sorted(set(
                cat for sig_cats in self.signal_fire_rates.values()
                for cat in sig_cats.keys()
            ))
            header = f"  {'Signal':30s}"
            for cat in categories:
                header += f"  {cat:>12s}"
            lines.append(header)
            lines.append("  " + "-" * (30 + 14 * len(categories)))

            for signal, cats in sorted(self.signal_fire_rates.items()):
                row = f"  {signal:30s}"
                for cat in categories:
                    rate = cats.get(cat, 0)
                    row += f"  {rate:11.1%} "
                lines.append(row)
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "total_cases": self.total_cases,
            "total_errors": self.total_errors,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "retraction_recall": round(self.retraction_recall, 4),
            "retraction_retracted_flag_rate": round(self.retraction_retracted_flag_rate, 4),
            "hallucination_recall": round(self.hallucination_recall, 4),
            "hallucination_precision": round(self.hallucination_precision, 4),
            "chimera_recall": round(self.chimera_recall, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "risk_confusion": self.risk_confusion,
            "signal_fire_rates": {
                sig: {cat: round(r, 4) for cat, r in cats.items()}
                for sig, cats in self.signal_fire_rates.items()
            },
            "category_metrics": self.category_metrics,
        }


def compute_metrics(results: list[IntegrityResult]) -> IntegrityMetrics:
    """Compute comprehensive benchmark metrics from results."""
    m = IntegrityMetrics()
    m.total_cases = len(results)
    m.total_errors = sum(1 for r in results if r.error)

    # Latency
    latencies = [r.latency_ms for r in results if not r.error]
    if latencies:
        m.avg_latency_ms = sum(latencies) / len(latencies)
        latencies_sorted = sorted(latencies)
        p95_idx = int(len(latencies_sorted) * 0.95)
        m.p95_latency_ms = latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)]

    # Group by category
    by_category: dict[str, list[IntegrityResult]] = defaultdict(list)
    for r in results:
        by_category[r.test_case.category].append(r)

    risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": -1}

    # Retraction recall
    retracted = by_category.get("retracted", [])
    if retracted:
        flagged = sum(1 for r in retracted
                      if risk_order.get(r.actual_risk, -1) >= 1)  # ≥ ELEVATED
        m.retraction_recall = flagged / len(retracted)

        retracted_detected = sum(1 for r in retracted if r.actual_retracted)
        m.retraction_retracted_flag_rate = retracted_detected / len(retracted)

    # Hallucination recall
    hallucinated = by_category.get("hallucinated", [])
    if hallucinated:
        flagged = sum(1 for r in hallucinated
                      if risk_order.get(r.actual_risk, -1) >= 2)  # ≥ HIGH
        m.hallucination_recall = flagged / len(hallucinated)

    # Hallucination precision (of all papers flagged ≥ HIGH, % actually bad)
    all_flagged_high = [r for r in results
                        if risk_order.get(r.actual_risk, -1) >= 2]
    if all_flagged_high:
        true_positives = sum(1 for r in all_flagged_high
                             if r.test_case.category in ("retracted", "hallucinated", "chimera"))
        m.hallucination_precision = true_positives / len(all_flagged_high)

    # Chimera recall
    chimeras = by_category.get("chimera", [])
    if chimeras:
        flagged = sum(1 for r in chimeras
                      if risk_order.get(r.actual_risk, -1) >= 1)  # ≥ ELEVATED
        m.chimera_recall = flagged / len(chimeras)

    # False positive rate (real papers flagged ≥ ELEVATED)
    real = by_category.get("real", [])
    if real:
        fp = sum(1 for r in real
                 if risk_order.get(r.actual_risk, -1) >= 1)  # ≥ ELEVATED
        m.false_positive_rate = fp / len(real)

    # Risk tier confusion matrix
    risk_tiers = ["LOW", "ELEVATED", "HIGH", "CRITICAL"]
    confusion = {exp: {act: 0 for act in risk_tiers + ["UNKNOWN"]}
                 for exp in risk_tiers}
    for r in results:
        exp = r.test_case.expected_risk.upper()
        act = r.actual_risk
        if exp in confusion and act in confusion[exp]:
            confusion[exp][act] += 1
    m.risk_confusion = confusion

    # Signal fire rates by category
    signal_counts: dict[str, dict[str, tuple[int, int]]] = defaultdict(
        lambda: defaultdict(lambda: (0, 0)))
    for r in results:
        cat = r.test_case.category
        fired_set = set(r.signals_fired)
        if r.report:
            for s in r.report.signals:
                key = s.signal_name
                prev_fired, prev_total = signal_counts[key][cat]
                signal_counts[key][cat] = (
                    prev_fired + (1 if s.signal_name in fired_set else 0),
                    prev_total + 1,
                )

    m.signal_fire_rates = {}
    for signal, cats in signal_counts.items():
        m.signal_fire_rates[signal] = {
            cat: fired / total if total > 0 else 0
            for cat, (fired, total) in cats.items()
        }

    # Per-category breakdowns
    for cat, cat_results in by_category.items():
        valid = [r for r in cat_results if not r.error]
        if not valid:
            continue
        m.category_metrics[cat] = {
            "count": len(cat_results),
            "errors": sum(1 for r in cat_results if r.error),
            "risk_correct_rate": sum(1 for r in valid if r.risk_correct) / len(valid),
            "detection_correct_rate": sum(1 for r in valid if r.detection_correct) / len(valid),
            "found_rate": sum(1 for r in valid if r.actual_found) / len(valid),
            "retracted_rate": sum(1 for r in valid if r.actual_retracted) / len(valid),
            "avg_risk_prob": (
                sum(r.report.risk_probability for r in valid if r.report)
                / len(valid)),
            "risk_distribution": dict(Counter(r.actual_risk for r in valid)),
            "avg_latency_ms": round(
                sum(r.latency_ms for r in valid) / len(valid), 1),
        }

    return m


# ── Detailed per-case report ─────────────────────────────────────────────

def format_detailed_results(results: list[IntegrityResult]) -> str:
    """Format per-case results for detailed analysis."""
    lines = []
    for r in results:
        c = r.test_case
        status = "PASS" if r.risk_correct else "FAIL"
        icon = "+" if r.risk_correct else "X"

        lines.append(
            f"[{icon}] {c.id:20s} | {c.category:12s} | "
            f"expected={c.expected_risk:8s} actual={r.actual_risk:8s} | "
            f"found={r.actual_found!s:5s} retracted={r.actual_retracted!s:5s} | "
            f"{r.latency_ms:.0f}ms"
        )
        if r.signals_fired:
            lines.append(f"     signals: {', '.join(r.signals_fired)}")
        if r.error:
            lines.append(f"     ERROR: {r.error}")
        if not r.risk_correct:
            lines.append(f"     note: {c.note}")
    return "\n".join(lines)


# ── Main benchmark runner ────────────────────────────────────────────────

def run_benchmark(
    layers: list[str] = None,
    domain: str = "default",
    include_fetched: bool = False,
    max_cases: int = 0,
    timeout: float = 15.0,
    save_results: bool = True,
) -> tuple[IntegrityMetrics, list[IntegrityResult]]:
    """Run the full integrity benchmark.

    Args:
        layers: Pipeline layers to run (default ["L0", "L4"]).
        domain: Bayesian prior domain.
        include_fetched: Include Crossref/PubMed fetched data.
        max_cases: Limit total cases (0=unlimited).
        timeout: Per-reference timeout in seconds.
        save_results: Save results to JSON file.

    Returns:
        (metrics, results) tuple.
    """
    if layers is None:
        layers = ["L0", "L4"]

    # Load test cases
    cases = load_golden_test_set()
    if include_fetched:
        cases.extend(load_fetched_data())

    if max_cases > 0:
        cases = cases[:max_cases]

    logger.info("Running integrity benchmark: %d cases, layers=%s",
                len(cases), layers)

    # Initialize pipeline
    pipeline = init_pipeline(layers=layers, domain=domain)

    # Run all test cases
    results = []
    for i, case in enumerate(cases):
        logger.info("[%d/%d] %s (%s): %s",
                    i + 1, len(cases), case.id, case.category,
                    case.title[:50])
        result = run_test_case(pipeline, case, timeout=timeout)
        results.append(result)

        # Progress summary every 10 cases
        if (i + 1) % 10 == 0:
            correct = sum(1 for r in results if r.risk_correct)
            logger.info("  Progress: %d/%d processed, %d/%d correct (%.1f%%)",
                        i + 1, len(cases), correct, len(results),
                        100 * correct / len(results))

    # Compute metrics
    metrics = compute_metrics(results)

    # Print results
    print("\n" + metrics.summary())
    print("\n── Detailed Results ──────────────────────────────────────")
    print(format_detailed_results(results))

    # Save
    if save_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"integrity_benchmark_{timestamp}.json"

        output = {
            "metadata": {
                "timestamp": timestamp,
                "layers": layers,
                "domain": domain,
                "total_cases": len(cases),
                "include_fetched": include_fetched,
            },
            "metrics": metrics.to_dict(),
            "per_case": [
                {
                    "id": r.test_case.id,
                    "category": r.test_case.category,
                    "title": r.test_case.title[:80],
                    "expected_risk": r.test_case.expected_risk,
                    "actual_risk": r.actual_risk,
                    "risk_correct": r.risk_correct,
                    "found": r.actual_found,
                    "retracted": r.actual_retracted,
                    "signals_fired": r.signals_fired,
                    "risk_probability": (
                        round(r.report.risk_probability, 4)
                        if r.report else None),
                    "latency_ms": round(r.latency_ms, 1),
                    "error": r.error or None,
                }
                for r in results
            ],
        }

        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logger.info("Results saved to %s", out_path)

    return metrics, results


def main():
    parser = argparse.ArgumentParser(
        description="IntegriRef Integrity Benchmark")
    parser.add_argument("--layers", nargs="+", default=["L0", "L4"],
                        help="Pipeline layers to run")
    parser.add_argument("--domain", default="default",
                        help="Bayesian prior domain")
    parser.add_argument("--include-fetched", action="store_true",
                        help="Include Crossref/PubMed fetched data")
    parser.add_argument("--max-cases", type=int, default=0,
                        help="Limit total test cases (0=unlimited)")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="Per-reference timeout in seconds")
    parser.add_argument("--no-save", action="store_true",
                        help="Don't save results to file")
    args = parser.parse_args()

    metrics, results = run_benchmark(
        layers=args.layers,
        domain=args.domain,
        include_fetched=args.include_fetched,
        max_cases=args.max_cases,
        timeout=args.timeout,
        save_results=not args.no_save,
    )

    # Exit code: 0 if false_positive_rate < 10% and hallucination_recall > 50%
    if metrics.false_positive_rate > 0.10:
        logger.warning("FAIL: False positive rate %.1f%% exceeds 10%% threshold",
                       metrics.false_positive_rate * 100)
        sys.exit(1)
    if metrics.hallucination_recall < 0.50:
        logger.warning("FAIL: Hallucination recall %.1f%% below 50%% threshold",
                       metrics.hallucination_recall * 100)
        sys.exit(1)

    logger.info("PASS: All quality thresholds met")


if __name__ == "__main__":
    main()
