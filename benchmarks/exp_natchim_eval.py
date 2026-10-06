"""Score IntegriRef on the mined naturally-occurring chimera split (3tFe-S2).

Positives are references whose printed metadata contradicts both Crossref and
OpenAlex; controls are references from the same bibliographies whose DOI, year
and first author all agree. Run with the year-only fix in place, so this is the
real-data counterpart of the synthetic stress set.
"""
import argparse
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
logger = logging.getLogger("natchim")
logger.setLevel(logging.INFO)

DATA = ROOT / "benchmarks" / "data"
RESULTS = ROOT / "benchmarks" / "results"
TIERS = {"low": 0, "elevated": 1, "high": 2, "critical": 3, "unknown": -1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=str(DATA / "natural_chimeras_v1.jsonl"))
    ap.add_argument("--multi-registry", action="store_true",
                    help="add OpenAlex alongside Crossref")
    args = ap.parse_args()

    cases = [json.loads(line) for line in open(args.split) if line.strip()]
    logger.info("loaded %d cases: %s", len(cases),
                dict(Counter(c["category"] for c in cases)))

    regs = [CrossRefRegistry] + ([OpenAlexRegistry] if args.multi_registry
                                 else [])
    discovery = RegistryDiscovery()
    discovery.register_all(regs)
    pipe = IntegriRefPipeline(discovery=discovery, layers=["L0", "L4"],
                              domain="default",
                              enable_l2_only_on_escalation=True,
                              early_stop=True)
    # Chimeras and controls can share DOIs across bibliographies.
    pipe._engine._cache = None

    results = []
    for i, case in enumerate(cases):
        authors = case.get("authors", [])
        if isinstance(authors, str):
            try:
                authors = json.loads(authors)
            except json.JSONDecodeError:
                authors = []
        ref = {"title": case.get("title", ""), "doi": case.get("doi", ""),
               "year": case.get("year", ""), "authors": authors,
               "venue": case.get("venue", "")}
        try:
            report = pipe.verify(ref=ref)
            results.append({
                "id": case["id"], "category": case["category"],
                "reasons": case.get("mismatch_reasons", []),
                "tier": report.risk_tier.lower(),
                "posterior": report.risk_probability,
                "fired": [s.signal_name for s in report.signals if s.fired],
            })
        except Exception as exc:  # noqa: BLE001 — record and continue
            results.append({"id": case["id"], "category": case["category"],
                            "reasons": case.get("mismatch_reasons", []),
                            "tier": "unknown", "posterior": 0.0, "fired": [],
                            "error": str(exc)})
        if (i + 1) % 25 == 0:
            logger.info("%d/%d", i + 1, len(cases))
        time.sleep(0.6)

    pos = [r for r in results if r["category"] == "chimera"]
    neg = [r for r in results if r["category"] != "chimera"]

    def rate(rows, tier):
        if not rows:
            return None
        hit = sum(1 for r in rows if TIERS[r["tier"]] >= TIERS[tier])
        return {"hit": hit, "n": len(rows), "rate": round(hit / len(rows), 4)}

    def subset(kind):
        rows = [r for r in pos
                if any(x.startswith(kind) for x in r["reasons"])]
        return rate(rows, "elevated")

    summary = {
        "split": Path(args.split).name,
        "registries": [r.__name__ for r in regs],
        "recall_elevated": rate(pos, "elevated"),
        "recall_high": rate(pos, "high"),
        "recall_critical": rate(pos, "critical"),
        "fpr_elevated": rate(neg, "elevated"),
        "fpr_high": rate(neg, "high"),
        "recall_by_reason": {"year": subset("year"), "author": subset("author")},
        "signal_fire_counts_positives": dict(
            Counter(s for r in pos for s in r["fired"])),
        "signal_fire_counts_controls": dict(
            Counter(s for r in neg for s in r["fired"])),
    }

    ts = time.strftime("%Y%m%d_%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"natchim_eval_{ts}.json"
    out.write_text(json.dumps({"summary": summary, "results": results},
                              indent=2, default=str))
    print(json.dumps(summary, indent=2))
    print("saved ->", out)


if __name__ == "__main__":
    main()
