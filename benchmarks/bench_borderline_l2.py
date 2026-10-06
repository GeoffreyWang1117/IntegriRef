"""Borderline-L2 benchmark runner.

Evaluates IntegriRef on the borderline_l2.json dataset across L0+L4,
L0+L1+L4, and L0+L1+L2+L4 to measure L2's incremental contribution.

Unlike borderline-L1, each case carries a `cited_abstract` so L2 NLI
can be evaluated without network calls for abstract fetching.

The pipeline's default `enable_l2_only_on_escalation=True` would block L2
on cases where L0 passes; we disable that gating so L2 runs on every case
(matching the borderline-L2 design where L0 is by construction silent).

Reports:
  - Per-config risk tier distribution
  - Tier-flip rate when L2 is added
  - L2 NLI label distribution vs expected
  - Per-sub-pattern breakdown

Usage:
    python -m benchmarks.bench_borderline_l2 [--output PATH] [--workers 4]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.discovery import RegistryDiscovery
from core.pipeline import IntegriRefPipeline

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("border_l2")
logger.setLevel(logging.INFO)


DATA_PATH = Path(__file__).parent / "data" / "borderline_l2.json"
TIER_ORDER = {"low": 0, "elevated": 1, "high": 2, "critical": 3}

# Map between SciFact-style NLI labels (in the dataset) and the L2
# AlignmentLabel vocabulary (in the pipeline output). The "neutral" case
# accepts either unsupported or partially_supported as correct.
NLI_LABEL_EQUIVALENCE = {
    "entailment":    {"supported"},
    "neutral":       {"unsupported", "partially_supported"},
    "contradiction": {"contradicted"},
}


def nli_matches_expected(expected: str, actual: str) -> bool:
    actual_norm = (actual or "").lower()
    expected_norm = (expected or "").lower()
    accepted = NLI_LABEL_EQUIVALENCE.get(expected_norm, set())
    return actual_norm in accepted


def load_borderline_l2(path: Path = DATA_PATH) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    cases = []
    for sub in ("supporting_overstatement",
                "domain_mismatch",
                "direct_contradiction"):
        cases.extend(data[sub])
    return cases


def init_pipeline(layers: list[str]) -> IntegriRefPipeline:
    """Build a pipeline that always runs L2 when present (escalation
    gating disabled), with no Semantic Scholar to avoid rate-limit stalls.
    """
    from registries.academic.crossref import CrossRefRegistry
    from registries.academic.openalex import OpenAlexRegistry
    from registries.academic.arxiv import ArXivRegistry

    discovery = RegistryDiscovery()
    discovery.register_all([
        CrossRefRegistry,
        OpenAlexRegistry,
        ArXivRegistry,
    ])
    return IntegriRefPipeline(
        discovery=discovery,
        layers=layers,
        domain="default",
        enable_l2_only_on_escalation=False,
    )


@dataclass
class CaseOutcome:
    case_id: str
    sub_pattern: str
    expected_nli: str
    ground_truth_risk: str
    actual_tier: dict = field(default_factory=dict)
    actual_nli: str = ""
    nli_correct: bool = False
    posterior: dict = field(default_factory=dict)
    latency_ms: dict = field(default_factory=dict)
    error: Optional[str] = None


def evaluate_case(case: dict, pipelines: dict[str, IntegriRefPipeline]) -> CaseOutcome:
    outcome = CaseOutcome(
        case_id=case["id"],
        sub_pattern=case["sub_pattern"],
        expected_nli=case.get("expected_nli_label", ""),
        ground_truth_risk=case["ground_truth_risk"],
    )
    ref = case["cited_paper"]
    sentence = case["citing_sentence"]
    abstract = case.get("cited_abstract", "")

    for cfg_name, pipe in pipelines.items():
        t0 = time.monotonic()
        try:
            report = pipe.verify(
                ref=ref,
                citing_sentence=sentence,
                abstract=abstract,
            )
            outcome.actual_tier[cfg_name] = report.risk_tier.lower()
            outcome.posterior[cfg_name] = round(
                getattr(report, "risk_probability", 0.0) or 0.0, 4)
            # Record L2 NLI label on the full config only
            if cfg_name == "L0+L1+L2+L4" and getattr(report, "l2", None) is not None:
                label = getattr(report.l2, "label", None)
                if label is not None:
                    label_str = label.value if hasattr(label, "value") else str(label)
                    outcome.actual_nli = label_str
                    outcome.nli_correct = nli_matches_expected(
                        outcome.expected_nli, label_str)
        except Exception as e:
            outcome.error = f"{cfg_name}: {type(e).__name__}: {e}"
            logger.warning("Case %s config %s failed: %s",
                           case["id"], cfg_name, e)
        outcome.latency_ms[cfg_name] = round(
            (time.monotonic() - t0) * 1000, 1)
    return outcome


def compute_metrics(outcomes: list[CaseOutcome]) -> dict:
    configs = list(outcomes[0].actual_tier.keys()) if outcomes else []
    metrics = {
        "total_cases": len(outcomes),
        "errors": sum(1 for o in outcomes if o.error),
        "by_config": {},
        "tier_flips": {},
        "by_sub_pattern": {},
        "nli_correct": 0,
        "nli_label_distribution": {},
        "nli_confusion": {},
    }

    for cfg in configs:
        dist = Counter(o.actual_tier.get(cfg, "?") for o in outcomes)
        correct = sum(1 for o in outcomes
                      if o.actual_tier.get(cfg) == o.ground_truth_risk)
        escalated = sum(1 for o in outcomes
                        if TIER_ORDER.get(o.actual_tier.get(cfg, "low"), 0)
                        >= TIER_ORDER["elevated"])
        metrics["by_config"][cfg] = {
            "tier_distribution": dict(dist),
            "exact_tier_match": f"{correct}/{len(outcomes)}",
            "escalated_at_all": f"{escalated}/{len(outcomes)}",
        }

    # Tier flips: which pairs of consecutive configs to compare
    pairs = [("L0+L4", "L0+L1+L4"), ("L0+L1+L4", "L0+L1+L2+L4")]
    flips = {}
    for base, plus in pairs:
        if base not in configs or plus not in configs:
            continue
        up = down = correct_up = wrong_up = same = 0
        for o in outcomes:
            b = TIER_ORDER.get(o.actual_tier.get(base, "low"), 0)
            a = TIER_ORDER.get(o.actual_tier.get(plus, "low"), 0)
            gt = TIER_ORDER.get(o.ground_truth_risk, 0)
            if a > b:
                up += 1
                if a <= gt:
                    correct_up += 1
                else:
                    wrong_up += 1
            elif a < b:
                down += 1
            else:
                same += 1
        flips[f"{base} -> {plus}"] = {
            "same": same,
            "escalated": up,
            "  toward_gt": correct_up,
            "  past_gt": wrong_up,
            "de-escalated": down,
        }
    metrics["tier_flips"] = flips

    # Per-sub-pattern
    for sub in ("supporting_overstatement",
                "domain_mismatch",
                "direct_contradiction"):
        sub_cases = [o for o in outcomes if o.sub_pattern == sub]
        if not sub_cases:
            continue
        sub_metric = {}
        for cfg in configs:
            esc = sum(1 for o in sub_cases
                      if TIER_ORDER.get(o.actual_tier.get(cfg, "low"), 0)
                      >= TIER_ORDER["elevated"])
            sub_metric[cfg] = f"{esc}/{len(sub_cases)} escalated"
        nli_match = sum(1 for o in sub_cases if o.nli_correct)
        sub_metric["nli_correct"] = f"{nli_match}/{len(sub_cases)}"
        metrics["by_sub_pattern"][sub] = sub_metric

    # NLI confusion
    nli_correct = sum(1 for o in outcomes if o.nli_correct)
    metrics["nli_correct"] = f"{nli_correct}/{len(outcomes)}"
    metrics["nli_label_distribution"] = dict(
        Counter(o.actual_nli for o in outcomes if o.actual_nli))
    confusion = Counter((o.expected_nli, o.actual_nli) for o in outcomes
                        if o.actual_nli)
    metrics["nli_confusion"] = {f"{e}->{a}": n for (e, a), n in confusion.items()}

    return metrics


def format_report(metrics: dict) -> str:
    lines = [
        "=" * 70,
        "Borderline-L2 Benchmark Report",
        "=" * 70,
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
    lines.append("── Tier flips ──")
    for transition, info in metrics["tier_flips"].items():
        lines.append(f"  {transition}:")
        for k, v in info.items():
            lines.append(f"    {k}: {v}")

    lines.append("")
    lines.append("── By sub-pattern (≥ELEVATED rate, NLI correctness) ──")
    for sub, info in metrics["by_sub_pattern"].items():
        lines.append(f"  {sub}:")
        for k, v in info.items():
            lines.append(f"    {k}: {v}")

    lines.append("")
    lines.append("── NLI overall ──")
    lines.append(f"  correct: {metrics['nli_correct']}")
    lines.append(f"  label distribution: {metrics['nli_label_distribution']}")
    lines.append(f"  confusion (expected->actual): {metrics['nli_confusion']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", type=str, default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    cases = load_borderline_l2()
    if args.max_cases:
        cases = cases[: args.max_cases]
    logger.info("Loaded %d borderline-L2 cases", len(cases))

    logger.info("Initialising pipelines (L0+L4, L0+L1+L4, L0+L1+L2+L4) ...")
    pipelines = {
        "L0+L4":          init_pipeline(["L0", "L4"]),
        "L0+L1+L4":       init_pipeline(["L0", "L1", "L4"]),
        "L0+L1+L2+L4":    init_pipeline(["L0", "L1", "L2", "L4"]),
    }

    logger.info("Running %d cases with %d workers ...",
                len(cases), args.workers)
    outcomes = []
    start = time.monotonic()
    # Lower concurrency to avoid hammering registries and rate-limiting L2
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(evaluate_case, c, pipelines): c for c in cases}
        for i, fut in enumerate(as_completed(futures), 1):
            outcomes.append(fut.result())
            if i % 5 == 0 or i == len(cases):
                logger.info("  [%d/%d] done (%.1fs elapsed)",
                            i, len(cases), time.monotonic() - start)

    metrics = compute_metrics(outcomes)
    print()
    print(format_report(metrics))

    if args.output:
        out = {
            "metrics": metrics,
            "cases": [
                {
                    "case_id": o.case_id,
                    "sub_pattern": o.sub_pattern,
                    "expected_nli": o.expected_nli,
                    "actual_nli": o.actual_nli,
                    "nli_correct": o.nli_correct,
                    "ground_truth_risk": o.ground_truth_risk,
                    "actual_tier": o.actual_tier,
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
