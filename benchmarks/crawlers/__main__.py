"""CLI entry point for IntegriRef dataset crawlers.

Usage:
    python -m benchmarks.crawlers --list
    python -m benchmarks.crawlers retracted --count 10000
    python -m benchmarks.crawlers all-s --count 5000
    python -m benchmarks.crawlers all --count 2000
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import CRAWLERS, S_TIER, A_TIER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crawlers")

DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "crawled"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="IntegriRef dataset crawlers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available crawlers:
  S-tier (ground truth):
    retracted     Retracted papers from Crossref + PubMed
    reference     Real / hallucinated / chimera references

  A-tier (deterministic signals):
    pmc           PMC full-text fetcher (prerequisite for grim/statcheck)
    grim          GRIM test cases from PMC articles
    statcheck     StatCheck cases from PMC articles
    temporal      Temporal anomalies from OpenAlex
    scifact       SciFact → L2 NLI converter

  Groups:
    all-s         Run all S-tier crawlers
    all           Run all S + A tier crawlers
""",
    )
    parser.add_argument(
        "crawler",
        choices=list(CRAWLERS.keys()) + ["all-s", "all", "list"],
        help="Crawler to run (or 'list' to show available)",
    )
    parser.add_argument("--count", type=int, default=1000,
                        help="Target number of records (default: 1000)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output directory (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--source", default="both",
                        help="Source filter for retracted crawler (crossref/pubmed/both)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    parser.add_argument("--no-checkpoint", action="store_true",
                        help="Disable checkpoint/resume")
    parser.add_argument("--pmc-dir", default="",
                        help="PMC cache directory (for grim/statcheck)")
    parser.add_argument("--scifact-dir", default="",
                        help="SciFact data directory")

    args = parser.parse_args(argv)

    if args.crawler == "list":
        print("Available crawlers:")
        for name in CRAWLERS:
            tier = "S" if name in S_TIER else "A"
            print(f"  [{tier}] {name:15s} {CRAWLERS[name].__doc__.strip().split(chr(10))[0]}")
        return

    # Determine which crawlers to run
    if args.crawler == "all-s":
        to_run = S_TIER
    elif args.crawler == "all":
        to_run = S_TIER + A_TIER
    else:
        to_run = [args.crawler]

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    total_records = 0
    start_time = time.time()

    for name in to_run:
        cls = CRAWLERS[name]
        crawler = cls(
            output_dir=output_dir,
            checkpoint=not args.no_checkpoint,
        )

        logger.info("=" * 60)
        logger.info("Running crawler: %s", name)
        logger.info("=" * 60)

        kwargs = {"count": args.count}
        if name == "retracted":
            kwargs["source"] = args.source
        if name == "reference":
            kwargs["seed"] = args.seed
        if name in ("grim", "statcheck"):
            kwargs["pmc_dir"] = args.pmc_dir
        if name == "scifact":
            kwargs["scifact_dir"] = args.scifact_dir

        try:
            n = crawler.crawl(**kwargs)
            total_records += n
            logger.info("Crawler %s: wrote %d records", name, n)
        except KeyboardInterrupt:
            logger.warning("Interrupted! Progress saved via checkpoint.")
            sys.exit(1)
        except Exception as e:
            logger.error("Crawler %s failed: %s", name, e, exc_info=True)

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("DONE: %d total records in %.1f seconds", total_records, elapsed)
    logger.info("Output: %s", output_dir)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
