"""Rebuttal Exp 1 — run the pipeline on matched normal-citation controls.

Runs L0+L4, L0+L1+L4, and L0+L1+L2+L4 on the correct-citation controls
built by build_matched_controls.py and reports, per config:

  - tier FPR (fraction of controls at ELEVATED or above)
  - L1 contrast-fire FPR (citation_misrepresents_source on correct citations)
  - L2 NLI label distribution (claim_contradicted / claim_unsupported FPR)

Combined with the Borderline-L1/L2 positives, this yields
precision / recall / F1 for L1- and L2-targeted detection with matched
controls (printed as a combined summary if the borderline result files
are available).

Usage:
    python -m benchmarks.bench_matched_controls [--workers 4] [--output PATH]
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
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.discovery import RegistryDiscovery
from core.pipeline import IntegriRefPipeline

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("matched_controls")
logger.setLevel(logging.INFO)

REPO = Path(__file__).resolve().parent.parent
DATA_PATH = REPO / "benchmarks/data/matched_controls_l1l2.json"
RESULTS_DIR = REPO / "benchmarks/results"
TIER_ORDER = {"low": 0, "elevated": 1, "high": 2, "critical": 3}
CONFIGS = {
    "L0+L4": ["L0", "L4"],
    "L0+L1+L4": ["L0", "L1", "L4"],
    "L0+L1+L2+L4": ["L0", "L1", "L2", "L4"],
}


def init_pipeline(layers: list[str]) -> IntegriRefPipeline:
    from registries.academic.crossref import CrossRefRegistry
    from registries.academic.openalex import OpenAlexRegistry
    from registries.academic.arxiv import ArXivRegistry

    discovery = RegistryDiscovery()
    discovery.register_all([CrossRefRegistry, OpenAlexRegistry, ArXivRegistry])
    return IntegriRefPipeline(
        discovery=discovery,
        layers=layers,
        domain="default",
        enable_l2_only_on_escalation=False,
    )


@dataclass
class Outcome:
    case_id: str
    actual_tier: dict = field(default_factory=dict)
    posterior: dict = field(default_factory=dict)
    intent_label: str = ""
    intent_fired: bool = False
    nli_label: str = ""
    latency_ms: dict = field(default_factory=dict)
    error: Optional[str] = None


def evaluate_case(case: dict, pipelines: dict) -> Outcome:
    o = Outcome(case_id=case["id"])
    for cfg, pipe in pipelines.items():
        t0 = time.monotonic()
        try:
            report = pipe.verify(
                ref=case["cited_paper"],
                citing_sentence=case["citing_sentence"],
                abstract=case.get("cited_abstract", ""),
            )
            o.actual_tier[cfg] = report.risk_tier.lower()
            o.posterior[cfg] = round(
                getattr(report, "risk_probability", 0.0) or 0.0, 4)
            if cfg == "L0+L1+L2+L4":
                if getattr(report, "l1", None) is not None:
                    o.intent_label = report.l1.intent.value
                    o.intent_fired = o.intent_label == "contrasting"
                if getattr(report, "l2", None) is not None:
                    lab = report.l2.label
                    o.nli_label = lab.value if hasattr(lab, "value") else str(lab)
        except Exception as e:
            o.error = f"{cfg}: {type(e).__name__}: {e}"
        o.latency_ms[cfg] = round((time.monotonic() - t0) * 1000, 1)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    cases = json.loads(DATA_PATH.read_text())["controls"]
    if args.limit:
        cases = cases[:args.limit]
    logger.info("%d control cases", len(cases))

    pipelines = {name: init_pipeline(layers) for name, layers in CONFIGS.items()}

    outcomes: list[Outcome] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(evaluate_case, c, pipelines): c["id"] for c in cases}
        for i, fut in enumerate(as_completed(futs), 1):
            outcomes.append(fut.result())
            if i % 10 == 0:
                logger.info("  %d/%d done", i, len(cases))

    n = len(outcomes)
    metrics = {"total_controls": n,
               "errors": sum(1 for o in outcomes if o.error),
               "by_config": {}}
    for cfg in CONFIGS:
        esc = sum(1 for o in outcomes
                  if TIER_ORDER.get(o.actual_tier.get(cfg, "low"), 0) >= 1)
        metrics["by_config"][cfg] = {
            "tier_distribution": dict(Counter(
                o.actual_tier.get(cfg, "?") for o in outcomes)),
            "tier_fpr_elevated_plus": round(esc / n, 4),
            "escalated": f"{esc}/{n}",
        }
    metrics["l1_intent_distribution"] = dict(Counter(
        o.intent_label for o in outcomes if o.intent_label))
    l1_fp = sum(1 for o in outcomes if o.intent_fired)
    metrics["l1_contrast_fire_fpr"] = {"fired": l1_fp, "n": n,
                                       "fpr": round(l1_fp / n, 4)}
    metrics["l2_nli_label_distribution"] = dict(Counter(
        o.nli_label for o in outcomes if o.nli_label))
    l2_labels = [o.nli_label for o in outcomes if o.nli_label]
    l2_fp = sum(1 for lab in l2_labels if lab in ("contradicted", "unsupported"))
    metrics["l2_signal_fire_fpr"] = {
        "fired": l2_fp, "n": len(l2_labels),
        "fpr": round(l2_fp / len(l2_labels), 4) if l2_labels else None}

    print(json.dumps(metrics, indent=1))

    out = args.output or (RESULTS_DIR /
                          f"matched_controls_{datetime.now():%Y%m%d_%H%M%S}.json")
    Path(out).write_text(json.dumps(
        {"metrics": metrics,
         "cases": [vars(o) for o in outcomes]}, indent=1))
    print("saved ->", out)


if __name__ == "__main__":
    main()
