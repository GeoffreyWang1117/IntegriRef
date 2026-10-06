"""Borderline-L3 benchmark runner.

Evaluates IntegriRef on the 22 hand-curated bibliography-level graph-anomaly
cases (citation rings, self-citation, orphan clusters) from the public
graph_anomaly split, plus matched real-bibliography controls.

The graph_anomaly cases are real documented incidents (e.g., the
Brazilian Clinics/USP citation cartel, the IOP paper-mill orphan cluster).
Each case carries a list of DOIs that form a bibliography; L3 builds the
two-hop citation subgraph from OpenAlex and runs the seven anomaly
detectors. We compare configurations with and without L3 to measure
its incremental contribution.

Usage:
    python -m benchmarks.bench_borderline_l3 [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
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
logger = logging.getLogger("border_l3")
logger.setLevel(logging.INFO)


GRAPH_FILE = Path(__file__).parent / "data" / "hf_export" / "graph_anomaly.jsonl"
TIER_ORDER = {"low": 0, "elevated": 1, "high": 2, "critical": 3}


def load_borderline_l3() -> list[dict]:
    """Load the 22 hand-curated bibliography cases (skip the 3008 single-paper
    temporal-anomaly cases which test L3 per-reference, not per-bibliography).
    """
    cases = []
    with open(GRAPH_FILE) as f:
        for line in f:
            c = json.loads(line)
            if c.get("category") in ("citation_ring", "self_citation", "orphan_cluster"):
                cases.append(c)
    return cases


def init_pipeline(layers: list[str]) -> IntegriRefPipeline:
    from registries.academic.crossref import CrossRefRegistry
    from registries.academic.openalex import OpenAlexRegistry
    discovery = RegistryDiscovery()
    discovery.register_all([CrossRefRegistry, OpenAlexRegistry])
    return IntegriRefPipeline(
        discovery=discovery,
        layers=layers,
        domain="default",
        enable_l2_only_on_escalation=False,
    )


@dataclass
class BibOutcome:
    case_id: str
    category: str
    description: str
    dois: list
    expected_anomaly: bool
    anomalies_detected: dict = field(default_factory=dict)   # config -> list of types
    max_tier: dict = field(default_factory=dict)             # config -> highest tier across refs
    n_refs_processed: int = 0
    error: Optional[str] = None


def evaluate_bibliography(case: dict,
                          pipelines: dict[str, IntegriRefPipeline]) -> BibOutcome:
    """For each DOI in the bibliography, run the pipeline and record the
    L3 anomalies detected and the highest risk tier observed.
    """
    dois_raw = case.get("dois", "[]")
    if isinstance(dois_raw, str):
        try:
            dois = json.loads(dois_raw)
        except Exception:
            dois = []
    else:
        dois = dois_raw

    outcome = BibOutcome(
        case_id=case["id"],
        category=case["category"],
        description=case.get("description", "")[:100],
        dois=dois,
        expected_anomaly=bool(case.get("expected_anomaly", True)),
    )

    for cfg_name, pipe in pipelines.items():
        tiers, anomaly_types = [], []
        for doi in dois[:6]:   # cap at first 6 refs per bib to keep runtime sane
            try:
                ref = {"doi": doi, "title": "", "authors": [], "year": ""}
                report = pipe.verify(ref=ref)
                tiers.append(report.risk_tier.lower())
                if cfg_name.endswith("+L3") and getattr(report, "l3_anomalies", None):
                    for a in report.l3_anomalies:
                        anomaly_types.append(getattr(a, "anomaly_type", str(a)))
            except Exception as e:
                outcome.error = f"{cfg_name} on {doi}: {type(e).__name__}: {e}"
                logger.warning("%s", outcome.error)
        outcome.n_refs_processed = max(outcome.n_refs_processed, len(tiers))
        # Highest tier across the bibliography
        if tiers:
            outcome.max_tier[cfg_name] = max(tiers, key=lambda t: TIER_ORDER.get(t, 0))
        else:
            outcome.max_tier[cfg_name] = "low"
        if cfg_name.endswith("+L3"):
            outcome.anomalies_detected[cfg_name] = anomaly_types

    return outcome


def compute_metrics(outcomes: list[BibOutcome]) -> dict:
    configs = list(outcomes[0].max_tier.keys()) if outcomes else []
    metrics = {
        "total_bibs": len(outcomes),
        "errors": sum(1 for o in outcomes if o.error),
        "by_config": {},
        "by_category": {},
        "l3_anomaly_fire_rate": {},
    }
    for cfg in configs:
        flagged = sum(1 for o in outcomes
                      if TIER_ORDER.get(o.max_tier.get(cfg, "low"), 0)
                      >= TIER_ORDER["elevated"])
        metrics["by_config"][cfg] = f"{flagged}/{len(outcomes)} escalated"

    for cat in ("citation_ring", "self_citation", "orphan_cluster"):
        cat_outs = [o for o in outcomes if o.category == cat]
        if not cat_outs:
            continue
        per_cfg = {}
        for cfg in configs:
            flagged = sum(1 for o in cat_outs
                          if TIER_ORDER.get(o.max_tier.get(cfg, "low"), 0)
                          >= TIER_ORDER["elevated"])
            per_cfg[cfg] = f"{flagged}/{len(cat_outs)}"
        metrics["by_category"][cat] = per_cfg

    # L3 anomaly fire rate per category (using the L3-enabled config)
    l3_cfg = next((c for c in configs if c.endswith("+L3")), None)
    if l3_cfg:
        for cat in ("citation_ring", "self_citation", "orphan_cluster"):
            cat_outs = [o for o in outcomes if o.category == cat]
            fired = sum(1 for o in cat_outs
                        if o.anomalies_detected.get(l3_cfg))
            metrics["l3_anomaly_fire_rate"][cat] = (
                f"{fired}/{len(cat_outs)} bibs with any L3 anomaly detected")

    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", type=str, default=None)
    parser.add_argument("--max-cases", type=int, default=0)
    args = parser.parse_args()

    cases = load_borderline_l3()
    if args.max_cases:
        cases = cases[: args.max_cases]
    logger.info("Loaded %d borderline-L3 bibliographies: %s",
                len(cases),
                dict(Counter(c["category"] for c in cases)))

    logger.info("Initialising pipelines ...")
    pipelines = {
        "L0+L4":       init_pipeline(["L0", "L4"]),
        "L0+L4+L3":    init_pipeline(["L0", "L4", "L3"]),
    }

    logger.info("Running %d bibliographies ...", len(cases))
    outcomes = []
    start = time.monotonic()
    for i, c in enumerate(cases, 1):
        outcomes.append(evaluate_bibliography(c, pipelines))
        if i % 3 == 0 or i == len(cases):
            logger.info("  [%d/%d] done (%.1fs elapsed)",
                        i, len(cases), time.monotonic() - start)

    metrics = compute_metrics(outcomes)
    print()
    print("=" * 60)
    print("Borderline-L3 Bench Report")
    print("=" * 60)
    print(f"Total bibliographies: {metrics['total_bibs']}, errors: {metrics['errors']}")
    print(f"\nBy config (any ref in bib at >=ELEV):")
    for cfg, v in metrics["by_config"].items():
        print(f"  {cfg}: {v}")
    print(f"\nBy category:")
    for cat, sub in metrics["by_category"].items():
        print(f"  {cat}:")
        for cfg, v in sub.items():
            print(f"    {cfg}: {v}")
    print(f"\nL3 anomaly fire rate:")
    for cat, v in metrics["l3_anomaly_fire_rate"].items():
        print(f"  {cat}: {v}")

    if args.output:
        out = {
            "metrics": metrics,
            "cases": [
                {
                    "case_id": o.case_id,
                    "category": o.category,
                    "description": o.description,
                    "n_refs_processed": o.n_refs_processed,
                    "max_tier": o.max_tier,
                    "anomalies_detected": o.anomalies_detected,
                    "error": o.error,
                }
                for o in outcomes
            ],
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2))
        logger.info("Wrote results to %s", args.output)


if __name__ == "__main__":
    main()
