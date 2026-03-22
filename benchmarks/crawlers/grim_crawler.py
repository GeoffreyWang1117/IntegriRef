"""A-tier crawler: GRIM test cases from PMC full-text articles.

Extracts reported means and sample sizes from real papers, applies GRIM test
to produce deterministic pass/fail labels.

Usage:
    python -m benchmarks.crawlers grim --count 1000
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .base import BaseCrawler
from .pmc_fulltext import PMCFullTextCrawler

logger = logging.getLogger(__name__)

# Patterns to extract mean + sample size from text
_MEAN_N_PATTERNS = [
    # M = 3.47, SD = 1.23, N = 25
    re.compile(
        r'M\s*=\s*(\d+\.\d{2,3})\s*[,;]\s*(?:SD\s*=\s*[\d.]+\s*[,;]\s*)?'
        r'[Nn]\s*=\s*(\d+)',
        re.IGNORECASE,
    ),
    # mean = 3.47 ... N = 25
    re.compile(
        r'mean\s*(?:=|was|of)\s*(\d+\.\d{2,3})\s*'
        r'.*?'
        r'(?:N|n|sample\s*size)\s*(?:=|was|of)\s*(\d+)',
        re.IGNORECASE | re.DOTALL,
    ),
    # M(SD) = 3.47(1.23), n = 25
    re.compile(
        r'M\s*\(?\s*SD\s*\)?\s*=\s*(\d+\.\d{2,3})\s*\(\s*[\d.]+\s*\)\s*'
        r'.*?'
        r'[Nn]\s*=\s*(\d+)',
        re.IGNORECASE | re.DOTALL,
    ),
]

# Patterns to detect scale type from context
_SCALE_PATTERNS = [
    (re.compile(r'[1-7]\s*-?\s*point\s+(?:Likert|scale)', re.IGNORECASE), 1),
    (re.compile(r'Likert\s+scale\s*\(?\s*1\s*[-–]\s*5', re.IGNORECASE), 1),
    (re.compile(r'Likert\s+scale\s*\(?\s*1\s*[-–]\s*7', re.IGNORECASE), 1),
    (re.compile(r'(?:rated|scored)\s+(?:on\s+)?(?:a\s+)?(?:1|0)\s*[-–]\s*(?:5|7|10)', re.IGNORECASE), 1),
]


def grim_test(mean: float, n: int, scale: int = 1, dp: int = 2) -> bool:
    """GRIM test: returns True if consistent, False if impossible."""
    total = mean * n
    granularity = scale
    remainder = total % granularity
    tolerance = 0.5 * 10 ** (-dp)
    return remainder < tolerance or (granularity - remainder) < tolerance


class GRIMCrawler(BaseCrawler):
    """Extract GRIM-testable cases from PMC full-text articles."""

    name = "grim_cases"

    def crawl(self, count: int = 1000, pmc_dir: str = "", **kwargs) -> int:
        """Extract GRIM test cases from cached PMC articles.

        Args:
            count: Target number of GRIM-testable cases.
            pmc_dir: Directory containing PMC cache. Defaults to output_dir parent.
        """
        # Locate PMC cache
        if pmc_dir:
            pmc_cache_dir = Path(pmc_dir)
        else:
            pmc_cache_dir = self.output_dir / "pmc_cache"

        if not pmc_cache_dir.exists():
            logger.error("PMC cache not found at %s. Run 'pmc' crawler first.", pmc_cache_dir)
            return 0

        cached_files = list(pmc_cache_dir.glob("*.txt"))
        logger.info("Scanning %d PMC articles for GRIM-testable statistics...",
                     len(cached_files))

        results = []
        for i, txt_path in enumerate(cached_files):
            if len(results) >= count:
                break

            pmcid = txt_path.stem
            with open(txt_path, encoding="utf-8") as f:
                text = f.read()

            cases = self._extract_grim_cases(text, pmcid)
            results.extend(cases)

            if (i + 1) % 500 == 0:
                logger.info("  Scanned %d/%d articles, found %d GRIM cases...",
                             i + 1, len(cached_files), len(results))

        # Trim to target count
        results = results[:count]

        # Assign IDs
        for idx, case in enumerate(results):
            case["id"] = f"grim_pmc_{idx:04d}"

        # Write output
        out_path = self.output_dir / "grim_cases.jsonl"
        self._write_jsonl(results, out_path)

        # Summary
        n_inconsistent = sum(1 for r in results if r["expected_fired"])
        n_consistent = len(results) - n_inconsistent
        logger.info("GRIMCrawler: %d cases (%d inconsistent, %d consistent)",
                     len(results), n_inconsistent, n_consistent)
        return len(results)

    def _extract_grim_cases(self, text: str, pmcid: str) -> list[dict]:
        """Extract (mean, N) pairs from text and apply GRIM test."""
        cases = []
        seen_in_article: set[tuple[float, int]] = set()

        for pattern in _MEAN_N_PATTERNS:
            for match in pattern.finditer(text):
                try:
                    mean = float(match.group(1))
                    n = int(match.group(2))
                except (ValueError, IndexError):
                    continue

                # Sanity filters
                if n < 5 or n > 10000:
                    continue
                if mean <= 0 or mean > 100:
                    continue

                key = (mean, n)
                if key in seen_in_article:
                    continue
                seen_in_article.add(key)

                # Detect scale from context
                context_start = max(0, match.start() - 200)
                context = text[context_start:match.end() + 100]
                scale = self._detect_scale(context)

                # Determine decimal places
                dp = len(match.group(1).split(".")[1]) if "." in match.group(1) else 2

                # Run GRIM test
                consistent = grim_test(mean, n, scale, dp)

                cases.append({
                    "id": "",  # assigned later
                    "reported_mean": mean,
                    "sample_size_n": n,
                    "scale": scale,
                    "decimal_places": dp,
                    "grim_consistent": consistent,
                    "expected_fired": not consistent,
                    "source_pmcid": pmcid,
                    "source_text": match.group(0)[:200],
                    "note": f"GRIM {'inconsistent' if not consistent else 'consistent'}: "
                            f"M={mean}, N={n}, total={mean * n:.4f}",
                })

        return cases

    @staticmethod
    def _detect_scale(context: str) -> int:
        """Detect measurement scale from surrounding context."""
        for pattern, scale in _SCALE_PATTERNS:
            if pattern.search(context):
                return scale
        return 1  # Default: integer scale
