"""Borderline-L1 benchmark runner.

Evaluates IntegriRef on the borderline_l1.json dataset across multiple layer
configurations to measure each layer's contribution to tier decisions.

Reports:
  - Per-config risk tier distribution
  - Tier-flip rate when target layer is added (i.e. did L1 change the
    decision from the L0+L4 baseline, and in the correct direction?)
  - L1 intent firing rate per sub-pattern
  - Per-sub-pattern accuracy

Usage:
    python -m benchmarks.bench_borderline_l1 [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Make repo importable when run as a script from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.discovery import RegistryDiscovery
from core.pipeline import IntegriRefPipeline

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("border_l1")
logger.setLevel(logging.INFO)


DATA_PATH = Path(__file__).parent / "data" / "borderline_l1.json"
RESULTS_DIR = Path(__file__).parent / "results"

TIER_ORDER = {"low": 0, "elevated": 1, "high": 2, "critical": 3}


# ─── Dataset loading ───────────────────────────────────────────────────────

def load_borderline_l1(path: Path = DATA_PATH) -> list[dict]:
    """Flatten the three sub-pattern arrays into a single list of cases."""
    with open(path) as f:
        data = json.load(f)
    cases = []
    for sub in ("supporting_misrepresents",
                "contrasting_misrepresents",
                "citation_drift"):
        cases.extend(data[sub])
    return cases


# ─── Pipeline factory ─────────────────────────────────────────────────────

def init_pipeline(layers: list[str], force_l2: bool = True) -> IntegriRefPipeline:
    """Build a pipeline with the requested layers.

    `force_l2` disables the default 'only run L2 on L0 escalation' gating —
    required for borderline cases where L0 passes but we want to see if L2
    catches the misrepresentation.
    """
    from registries.academic.crossref import CrossRefRegistry
    from registries.academic.openalex import OpenAlexRegistry
    from registries.academic.arxiv import ArXivRegistry
    from registries.academic.semantic_scholar import SemanticScholarRegistry

    discovery = RegistryDiscovery()
    discovery.register_all([
        CrossRefRegistry,
        OpenAlexRegistry,
        ArXivRegistry,
        SemanticScholarRegistry,
    ])
    return IntegriRefPipeline(
        discovery=discovery,
        layers=layers,
        domain="default",
        enable_l2_only_on_escalation=not force_l2,
    )


# ─── Single-case evaluation ───────────────────────────────────────────────

@dataclass
class CaseOutcome:
    case_id: str
    sub_pattern: str
    target_layer: str
    ground_truth_risk: str
    actual_tier: dict = field(default_factory=dict)        # config → tier
    intent_label: str = ""                                  # L1 intent classification
    intent_fired: bool = False                              # citation_misrepresents_source fired
    posterior: dict = field(default_factory=dict)           # config → posterior
    latency_ms: dict = field(default_factory=dict)          # config → ms
    error: Optional[str] = None


def evaluate_case(case: dict, pipelines: dict[str, IntegriRefPipeline]) -> CaseOutcome:
    """Run one case through every pipeline configuration."""
    outcome = CaseOutcome(
        case_id=case["id"],
        sub_pattern=case["sub_pattern"],
        target_layer=case["target_layer"],
        ground_truth_risk=case["ground_truth_risk"],
    )
    ref = case["cited_paper"]
    sentence = case["citing_sentence"]

    for cfg_name, pipe in pipelines.items():
        t0 = time.monotonic()
        try:
            report = pipe.verify(ref=ref, citing_sentence=sentence)
            outcome.actual_tier[cfg_name] = report.risk_tier.lower()
            outcome.posterior[cfg_name] = round(
                getattr(report, "risk_probability", 0.0) or 0.0, 4)
            if cfg_name == "L0+L1+L4" and report.l1 is not None:
                outcome.intent_label = report.l1.intent.value
                outcome.intent_fired = (outcome.intent_label == "contrasting")
        except Exception as e:
            outcome.error = f"{cfg_name}: {type(e).__name__}: {e}"
            logger.warning("Case %s config %s failed: %s",
                           case["id"], cfg_name, e)
        outcome.latency_ms[cfg_name] = round(
            (time.monotonic() - t0) * 1000, 1)

    return outcome


# ─── Metrics aggregation ──────────────────────────────────────────────────

def compute_metrics(outcomes: list[CaseOutcome]) -> dict:
    """Compute tier-flip and accuracy metrics."""
    # Configs are derived from whichever keys appear in the first outcome
    # (preserves insertion order so the report reads L0 → L0+L4 → L0+L1+L4).
    configs = list(outcomes[0].actual_tier.keys()) if outcomes else []

    metrics = {
        "total_cases": len(outcomes),
        "errors": sum(1 for o in outcomes if o.error),
        "by_config": {},
        "tier_flips": {},
        "by_sub_pattern": {},
        "by_target_layer": {},
        "l1_intent_distribution": {},
    }

    # Per-config tier distribution + correctness
    for cfg in configs:
        dist = Counter(o.actual_tier.get(cfg, "?") for o in outcomes)
        # 'correct' = predicted tier matches ground_truth_risk (exact match)
        correct = sum(1 for o in outcomes
                      if o.actual_tier.get(cfg) == o.ground_truth_risk)
        # 'escalated' = predicted tier >= elevated (caught the issue at all)
        escalated = sum(1 for o in outcomes
                        if TIER_ORDER.get(o.actual_tier.get(cfg, "low"), 0)
                        >= TIER_ORDER["elevated"])
        metrics["by_config"][cfg] = {
            "tier_distribution": dict(dist),
            "exact_tier_match": f"{correct}/{len(outcomes)}",
            "escalated_at_all": f"{escalated}/{len(outcomes)}",
        }

    # Tier flips between L0+L4 and L0+L1+L4 (if both configs were run)
    base, with_l1 = "L0+L4", "L0+L1+L4"
    if base in configs and with_l1 in configs:
        flips_up = flips_down = flips_correct = flips_wrong = no_change = 0
        for o in outcomes:
            b = TIER_ORDER.get(o.actual_tier.get(base, "low"), 0)
            a = TIER_ORDER.get(o.actual_tier.get(with_l1, "low"), 0)
            gt = TIER_ORDER.get(o.ground_truth_risk, 0)
            if a > b:
                flips_up += 1
                if a <= gt:
                    flips_correct += 1
                else:
                    flips_wrong += 1
            elif a < b:
                flips_down += 1
            else:
                no_change += 1
        metrics["tier_flips"] = {
            "with_addition_of_L1": {
                "no_change": no_change,
                "escalated": flips_up,
                "  toward_ground_truth": flips_correct,
                "  past_ground_truth": flips_wrong,
                "de-escalated": flips_down,
            },
        }

    # Per-sub-pattern breakdown
    for sub in ("supporting_misrepresents",
                "contrasting_misrepresents",
                "citation_drift"):
        sub_cases = [o for o in outcomes if o.sub_pattern == sub]
        if not sub_cases:
            continue
        sub_metric = {}
        for cfg in configs:
            esc = sum(1 for o in sub_cases
                      if TIER_ORDER.get(o.actual_tier.get(cfg, "low"), 0)
                      >= TIER_ORDER["elevated"])
            sub_metric[cfg] = f"{esc}/{len(sub_cases)} escalated"

        # L1 intent fire rate
        fired = sum(1 for o in sub_cases if o.intent_fired)
        sub_metric["l1_intent_fired"] = f"{fired}/{len(sub_cases)}"
        metrics["by_sub_pattern"][sub] = sub_metric

    # Per-target-layer breakdown
    for tl in ("L1", "L2", "L1+L2"):
        tl_cases = [o for o in outcomes if o.target_layer == tl]
        if not tl_cases:
            continue
        cfg_to_caught = {}
        for cfg in configs:
            caught = sum(1 for o in tl_cases
                         if TIER_ORDER.get(o.actual_tier.get(cfg, "low"), 0)
                         >= TIER_ORDER["elevated"])
            cfg_to_caught[cfg] = f"{caught}/{len(tl_cases)}"
        metrics["by_target_layer"][tl] = cfg_to_caught

    metrics["l1_intent_distribution"] = dict(
        Counter(o.intent_label for o in outcomes if o.intent_label))

    return metrics


# ─── Reporting ────────────────────────────────────────────────────────────

def format_report(metrics: dict) -> str:
    lines = [
        "=" * 68,
        "Borderline-L1 Benchmark Report",
        "=" * 68,
        f"Total cases: {metrics['total_cases']}, errors: {metrics['errors']}",
        "",
        "── Tier distribution by config ──",
    ]
    for cfg, info in metrics["by_config"].items():
        lines.append(f"  {cfg}:")
        lines.append(f"    tiers:    {info['tier_distribution']}")
        lines.append(f"    exact:    {info['exact_tier_match']}")
        lines.append(f"    ≥elev:    {info['escalated_at_all']}")

    lines.append("")
    lines.append("── Tier flips L0+L4 → L0+L1+L4 ──")
    for k, v in metrics["tier_flips"]["with_addition_of_L1"].items():
        lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append("── By sub-pattern (≥ELEVATED rate) ──")
    for sub, info in metrics["by_sub_pattern"].items():
        lines.append(f"  {sub}:")
        for k, v in info.items():
            lines.append(f"    {k}: {v}")

    lines.append("")
    lines.append("── By target layer (≥ELEVATED rate) ──")
    for tl, info in metrics["by_target_layer"].items():
        lines.append(f"  target={tl}:")
        for cfg, v in info.items():
            lines.append(f"    {cfg}: {v}")

    lines.append("")
    lines.append("── L1 intent label distribution ──")
    for k, v in metrics["l1_intent_distribution"].items():
        lines.append(f"  {k}: {v}")

    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Write JSON results to this path")
    parser.add_argument("--max-cases", type=int, default=0,
                        help="Max cases (0=all)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Concurrent worker count")
    parser.add_argument("--no-l2", action="store_true",
                        help="Skip L2 config (default skips it anyway since "
                             "abstracts are not in the dataset)")
    args = parser.parse_args()

    cases = load_borderline_l1()
    if args.max_cases:
        cases = cases[: args.max_cases]
    logger.info("Loaded %d borderline-L1 cases", len(cases))

    # Build pipelines for the two configs we can run without abstracts.
    # L0+L4 baseline (no deep layers)
    # L0+L1+L4 (adds intent)
    logger.info("Initialising pipelines (L0, L0+L4, L0+L1+L4) ...")
    pipelines = {
        "L0":       init_pipeline(["L0"]),
        "L0+L4":    init_pipeline(["L0", "L4"]),
        "L0+L1+L4": init_pipeline(["L0", "L1", "L4"]),
    }

    # Run cases with thread pool (L0 is the bottleneck; it's network-bound)
    logger.info("Running %d cases with %d workers ...",
                len(cases), args.workers)
    outcomes: list[CaseOutcome] = []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(evaluate_case, c, pipelines): c for c in cases}
        for i, fut in enumerate(as_completed(futures), 1):
            outcome = fut.result()
            outcomes.append(outcome)
            if i % 5 == 0 or i == len(cases):
                logger.info("  [%d/%d] done (%.1fs elapsed)",
                            i, len(cases), time.monotonic() - start)

    metrics = compute_metrics(outcomes)
    report = format_report(metrics)
    print()
    print(report)

    if args.output:
        out = {
            "metrics": metrics,
            "cases": [
                {
                    "case_id": o.case_id,
                    "sub_pattern": o.sub_pattern,
                    "target_layer": o.target_layer,
                    "ground_truth_risk": o.ground_truth_risk,
                    "actual_tier": o.actual_tier,
                    "intent_label": o.intent_label,
                    "intent_fired": o.intent_fired,
                    "posterior": o.posterior,
                    "latency_ms": o.latency_ms,
                    "error": o.error,
                }
                for o in outcomes
            ],
        }
        outpath = Path(args.output)
        outpath.parent.mkdir(parents=True, exist_ok=True)
        outpath.write_text(json.dumps(out, indent=2))
        logger.info("Wrote results to %s", outpath)


if __name__ == "__main__":
    main()
