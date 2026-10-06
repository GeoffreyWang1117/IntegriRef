"""Camera-ready re-validation of the year-only chimera fix (3tFe-S2).

Root cause: verification/engine.py downgraded a year FAIL to WARN whenever
title+author matched, including for DOI-resolved records, so the FAIL never
reached the chimera_detected signal. The downgrade is now restricted to
search-matched sources.

This script re-runs the affected evaluations:
  A. idchim_year_only  (60 chimera + 60 control, Crossref-only) — the fix target
  B. idchim_author_only (60 + 60, Crossref-only)                — regression
  C. rv-1926 real controls (349, Crossref+OpenAlex+arXiv)       — FPR regression
  D. ret-6391 real controls (903, 4 core registries)            — FPR regression
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
from registries.academic.arxiv import ArXivRegistry  # noqa: E402
from registries.academic.crossref import CrossRefRegistry  # noqa: E402
from registries.academic.openalex import OpenAlexRegistry  # noqa: E402
from registries.academic.semantic_scholar import SemanticScholarRegistry  # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("yearfix")
logger.setLevel(logging.INFO)

DATA = ROOT / "benchmarks" / "data"
RESULTS = ROOT / "benchmarks" / "results"
TIERS = {"low": 0, "elevated": 1, "high": 2, "critical": 3, "unknown": -1}


def make_pipeline(registries):
    discovery = RegistryDiscovery()
    discovery.register_all(registries)
    pipe = IntegriRefPipeline(
        discovery=discovery,
        layers=["L0", "L4"],
        domain="default",
        enable_l2_only_on_escalation=True,
        early_stop=True,
    )
    # Chimera and control share DOIs — caching would cross-contaminate them.
    pipe._engine._cache = None
    return pipe


def to_ref(case):
    ref = {
        "title": case.get("title", ""),
        "doi": case.get("doi", ""),
        "year": case.get("year", ""),
    }
    authors = case.get("authors", [])
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except json.JSONDecodeError:
            authors = [a.strip() for a in authors.split(";") if a.strip()]
    ref["authors"] = authors
    if case.get("venue"):
        ref["venue"] = case["venue"]
    return ref


def run(pipe, cases, label, sleep=0.8):
    out = []
    t_start = time.monotonic()
    for i, case in enumerate(cases):
        try:
            report = pipe.verify(ref=to_ref(case))
            out.append({
                "id": case.get("id"),
                "category": case.get("category", ""),
                "tier": report.risk_tier.lower(),
                "posterior": report.risk_probability,
                "fired": [s.signal_name for s in report.signals if s.fired],
                "error": "",
            })
        except Exception as exc:  # noqa: BLE001 — record and continue the sweep
            out.append({"id": case.get("id"),
                        "category": case.get("category", ""),
                        "tier": "unknown", "posterior": 0.0, "fired": [],
                        "error": str(exc)})
        if (i + 1) % 50 == 0:
            logger.info("%s %d/%d (%.0fs)", label, i + 1, len(cases),
                        time.monotonic() - t_start)
        time.sleep(sleep)
    return out


def summarise(results, positive_categories):
    pos = [r for r in results if r["category"] in positive_categories]
    neg = [r for r in results if r["category"] not in positive_categories]

    def rate(rows, tier):
        if not rows:
            return None
        hit = sum(1 for r in rows if TIERS[r["tier"]] >= TIERS[tier])
        return {"n": len(rows), "hit": hit, "rate": hit / len(rows)}

    signal_counts = Counter(s for r in results for s in r["fired"])
    return {
        "n": len(results),
        "recall_elevated": rate(pos, "elevated"),
        "recall_high": rate(pos, "high"),
        "recall_critical": rate(pos, "critical"),
        "fpr_elevated": rate(neg, "elevated"),
        "fpr_high": rate(neg, "high"),
        "signal_fire_counts": dict(signal_counts),
        "errors": sum(1 for r in results if r["error"]),
    }


def load_jsonl(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh]


def main():
    jobs = []

    # A/B: ID-resolving chimera stress sets, Crossref-only (paper protocol).
    for name, fname in [("A_year_only", "idchim_year_only.jsonl"),
                        ("B_author_only", "idchim_author_only.jsonl")]:
        jobs.append({
            "name": name,
            "cases": load_jsonl(DATA / fname),
            "registries": [CrossRefRegistry],
            "positive": {"chimera"},
        })

    # C: rv-1926 real controls, Crossref+OpenAlex+arXiv (no Semantic Scholar,
    # matching the registry-set disclosure in the paper).
    rv = load_jsonl(DATA / "hf_export" / "reference_verification.jsonl")
    jobs.append({
        "name": "C_rv1926_real_controls",
        "cases": [c for c in rv if c.get("category") == "real"],
        "registries": [CrossRefRegistry, OpenAlexRegistry, ArXivRegistry],
        "positive": set(),
    })

    # D: ret-6391 real controls, all four core registries.
    ret = load_jsonl(DATA / "hf_export" / "retracted_papers.jsonl")
    jobs.append({
        "name": "D_ret6391_real_controls",
        "cases": [c for c in ret if c.get("category") == "real_control"],
        "registries": [CrossRefRegistry, OpenAlexRegistry, ArXivRegistry,
                       SemanticScholarRegistry],
        "positive": set(),
    })

    summary = {}
    ts = time.strftime("%Y%m%d_%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"yearfix_revalidate_{ts}.json"

    for job in jobs:
        logger.info("=== %s: %d cases ===", job["name"], len(job["cases"]))
        pipe = make_pipeline(job["registries"])
        results = run(pipe, job["cases"], job["name"])
        summary[job["name"]] = {
            "registries": [r.__name__ for r in job["registries"]],
            "metrics": summarise(results, job["positive"]),
            "results": results,
        }
        out_path.write_text(json.dumps(summary, indent=2, default=str))
        logger.info("%s done -> %s", job["name"],
                    json.dumps(summary[job["name"]]["metrics"], default=str))

    print(f"\nWrote {out_path}")
    for name, block in summary.items():
        m = block["metrics"]
        print(f"\n=== {name} (n={m['n']}, errors={m['errors']}) ===")
        for key in ("recall_elevated", "recall_high", "recall_critical",
                    "fpr_elevated", "fpr_high"):
            if m[key]:
                print(f"  {key:18s} {m[key]['hit']}/{m[key]['n']} "
                      f"= {m[key]['rate']:.3f}")
        print(f"  signals {m['signal_fire_counts']}")


if __name__ == "__main__":
    main()
