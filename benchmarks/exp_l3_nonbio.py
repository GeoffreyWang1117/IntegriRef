"""L3 evaluation on three non-biomedical domains (3tFe-S3).

The Borderline-L3 clean split showed that top-cited topic-coherent
bibliographies trip L3's structural detectors, so L3 cannot separate "topic
coherence" from "cartel". That split was dominated by biomedical topics. This
script asks whether the confound is biomedical-specific by building several
clean topic-coherent bibliographies in each of three clearly non-biomedical
domains and measuring L3's false-positive rate per domain.

Each bibliography is a disjoint block of top-cited real Crossref papers from
one domain, so every reference is a true negative: any escalation is a false
positive.
"""
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.discovery import RegistryDiscovery  # noqa: E402
from core.pipeline import IntegriRefPipeline  # noqa: E402
from registries.academic.crossref import CrossRefRegistry  # noqa: E402
from registries.academic.openalex import OpenAlexRegistry  # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("l3_nonbio")
logger.setLevel(logging.INFO)

DATA = ROOT / "benchmarks" / "data"
CRAWLED = DATA / "crawled"
RESULTS = ROOT / "benchmarks" / "results"
TIER_ORDER = {"low": 0, "elevated": 1, "high": 2, "critical": 3, "unknown": -1}

DOMAINS = ["quantum_computing", "climate_change", "materials_science"]
BIBS_PER_DOMAIN = 6
REFS_PER_BIB = 7


def build_split():
    """Partition each domain's top-cited papers into disjoint bibliographies."""
    bibs = []
    for domain in DOMAINS:
        path = CRAWLED / f"crossref_real_{domain}.jsonl"
        papers = [json.loads(line) for line in open(path)]
        papers = [p for p in papers if p.get("doi")]
        papers.sort(key=lambda p: int(p.get("citation_count", 0) or 0),
                    reverse=True)
        need = BIBS_PER_DOMAIN * REFS_PER_BIB
        if len(papers) < need:
            logger.warning("%s: only %d papers, need %d — truncating",
                           domain, len(papers), need)
        for b in range(BIBS_PER_DOMAIN):
            block = papers[b * REFS_PER_BIB:(b + 1) * REFS_PER_BIB]
            if len(block) < REFS_PER_BIB:
                break
            bibs.append({
                "id": f"nonbio_{domain}_{b:02d}",
                "domain": domain,
                "category": "clean_bibliography",
                "papers": [{"doi": p["doi"], "title": p.get("title", ""),
                            "authors": p.get("authors", []),
                            "year": str(p.get("year", "")),
                            "venue": p.get("venue", "")} for p in block],
            })
    return bibs


def init_pipeline(layers):
    discovery = RegistryDiscovery()
    discovery.register_all([CrossRefRegistry, OpenAlexRegistry])
    return IntegriRefPipeline(
        discovery=discovery,
        layers=layers,
        domain="default",
        enable_l2_only_on_escalation=False,
    )


def run_bib(case, pipelines):
    out = {"id": case["id"], "domain": case["domain"],
           "ref_tiers": {}, "ref_signals": {}, "anomalies": {}, "errors": []}
    for cfg, pipe in pipelines.items():
        tiers, signals, anomalies = [], [], []
        for p in case["papers"]:
            try:
                report = pipe.verify(ref={
                    "doi": p["doi"], "title": p.get("title", ""),
                    "authors": p.get("authors", []), "year": p.get("year", ""),
                    "venue": p.get("venue", ""),
                })
                tiers.append(report.risk_tier.lower())
                signals.append([s.signal_name for s in report.signals if s.fired])
                if "L3" in cfg and getattr(report, "l3_anomalies", None):
                    anomalies.extend(getattr(a, "anomaly_type", str(a))
                                     for a in report.l3_anomalies)
            except Exception as exc:  # noqa: BLE001 — record and keep sweeping
                out["errors"].append(f"{cfg}/{p['doi']}: "
                                     f"{type(exc).__name__}: {exc}")
        out["ref_tiers"][cfg] = tiers
        out["ref_signals"][cfg] = signals
        if "L3" in cfg:
            out["anomalies"][cfg] = anomalies
    return out


def domain_metrics(outcomes, cfgs):
    per_domain = {}
    for domain in DOMAINS + ["ALL"]:
        rows = [o for o in outcomes
                if domain == "ALL" or o["domain"] == domain]
        if not rows:
            continue
        block = {"n_bibs": len(rows)}
        for cfg in cfgs:
            tiers = [t for o in rows for t in o["ref_tiers"].get(cfg, [])]
            if not tiers:
                continue
            fp = sum(1 for t in tiers if TIER_ORDER.get(t, 0) >= 1)
            crit_bibs = sum(
                1 for o in rows
                if o["ref_tiers"].get(cfg)
                and max(TIER_ORDER.get(t, 0) for t in o["ref_tiers"][cfg]) >= 3)
            block[cfg] = {
                "n_refs": len(tiers),
                "ref_fp_elevated": fp,
                "ref_fpr_elevated": fp / len(tiers),
                "bibs_at_critical": crit_bibs,
                "bib_crit_rate": crit_bibs / len(rows),
            }
        anomalies = Counter(a for o in rows
                            for a in o["anomalies"].get("L0+L4+L3", []))
        block["l3_anomaly_counts"] = dict(anomalies)
        per_domain[domain] = block
    return per_domain


def main():
    bibs = build_split()
    split_path = DATA / "borderline_l3_nonbio.jsonl"
    with open(split_path, "w") as fh:
        for b in bibs:
            fh.write(json.dumps(b) + "\n")
    logger.info("Built %d bibliographies across %d domains -> %s",
                len(bibs), len(DOMAINS), split_path)

    pipelines = {"L0+L4": init_pipeline(["L0", "L4"]),
                 "L0+L4+L3": init_pipeline(["L0", "L4", "L3"])}

    outcomes = []
    t0 = time.monotonic()
    for i, case in enumerate(bibs, 1):
        outcomes.append(run_bib(case, pipelines))
        logger.info("[%d/%d] %s (%.0fs)", i, len(bibs), case["id"],
                    time.monotonic() - t0)

    metrics = domain_metrics(outcomes, list(pipelines))
    ts = time.strftime("%Y%m%d_%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"l3_nonbio_{ts}.json"
    out_path.write_text(json.dumps(
        {"domains": DOMAINS, "metrics": metrics, "outcomes": outcomes},
        indent=2, default=str))

    print("\n=== L3 on non-biomedical domains ===")
    for domain, block in metrics.items():
        print(f"\n{domain} ({block['n_bibs']} bibs)")
        for cfg in ("L0+L4", "L0+L4+L3"):
            if cfg in block:
                c = block[cfg]
                print(f"  {cfg:10s} ref-FPR {c['ref_fp_elevated']}/"
                      f"{c['n_refs']} = {100*c['ref_fpr_elevated']:.1f}%   "
                      f"bibs@CRITICAL {c['bibs_at_critical']}/{block['n_bibs']}")
        if block.get("l3_anomaly_counts"):
            print(f"  anomalies {block['l3_anomaly_counts']}")
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
