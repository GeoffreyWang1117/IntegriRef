"""A-tier crawler: StatCheck cases from PMC full-text articles.

Extracts APA-formatted statistics (F, t, chi-square) from real papers,
recomputes p-values to find inconsistencies.

Usage:
    python -m benchmarks.crawlers statcheck --count 2000
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .base import BaseCrawler

logger = logging.getLogger(__name__)

# APA-formatted test statistic patterns
_STAT_PATTERNS = {
    "F": re.compile(
        r'F\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*=\s*([\d.]+)\s*,\s*p\s*([=<>])\s*\.?(\d+\.?\d*)',
    ),
    "t": re.compile(
        r't\s*\(\s*(\d+)\s*\)\s*=\s*([-\d.]+)\s*,\s*p\s*([=<>])\s*\.?(\d+\.?\d*)',
    ),
    "chi2": re.compile(
        r'[χXx]²?\s*\(\s*(\d+)\s*\)\s*=\s*([\d.]+)\s*,\s*p\s*([=<>])\s*\.?(\d+\.?\d*)',
    ),
}


def _parse_p_value(p_str: str) -> float:
    """Parse p-value string that may lack leading zero/dot."""
    if "." in p_str:
        return float(p_str)
    return float(f"0.{p_str}")


def _p_consistent(computed: float, reported: float) -> bool:
    """Check if computed and reported p-values are consistent."""
    if computed is None or reported is None:
        return True
    same_significance = (computed < 0.05) == (reported < 0.05)
    abs_diff = abs(computed - reported)
    if reported < 0.001:
        tolerance = 0.0005
    elif reported < 0.01:
        tolerance = 0.002
    else:
        tolerance = 0.01
    return same_significance and abs_diff < tolerance


class StatCheckCrawler(BaseCrawler):
    """Extract and verify APA-formatted statistics from PMC articles."""

    name = "statcheck_cases"

    def crawl(self, count: int = 2000, pmc_dir: str = "", **kwargs) -> int:
        """Extract StatCheck test cases from cached PMC articles."""
        try:
            from scipy import stats as scipy_stats
        except ImportError:
            logger.error("scipy is required for StatCheck. Install with: pip install scipy")
            return 0

        if pmc_dir:
            pmc_cache_dir = Path(pmc_dir)
        else:
            pmc_cache_dir = self.output_dir / "pmc_cache"

        if not pmc_cache_dir.exists():
            logger.error("PMC cache not found at %s. Run 'pmc' crawler first.", pmc_cache_dir)
            return 0

        cached_files = list(pmc_cache_dir.glob("*.txt"))
        logger.info("Scanning %d PMC articles for APA statistics...", len(cached_files))

        results = []
        for i, txt_path in enumerate(cached_files):
            if len(results) >= count:
                break

            pmcid = txt_path.stem
            with open(txt_path, encoding="utf-8") as f:
                text = f.read()

            cases = self._extract_statcheck_cases(text, pmcid, scipy_stats)
            results.extend(cases)

            if (i + 1) % 500 == 0:
                logger.info("  Scanned %d/%d articles, found %d statcheck cases...",
                             i + 1, len(cached_files), len(results))

        results = results[:count]

        for idx, case in enumerate(results):
            case["id"] = f"statcheck_pmc_{idx:04d}"

        out_path = self.output_dir / "statcheck_cases.jsonl"
        self._write_jsonl(results, out_path)

        n_inconsistent = sum(1 for r in results if r["expected_fired"])
        logger.info("StatCheckCrawler: %d cases (%d inconsistent, %d consistent)",
                     len(results), n_inconsistent, len(results) - n_inconsistent)
        return len(results)

    def _extract_statcheck_cases(self, text: str, pmcid: str,
                                  scipy_stats) -> list[dict]:
        """Extract and verify APA statistics from text."""
        cases = []
        seen: set[str] = set()

        for stat_type, pattern in _STAT_PATTERNS.items():
            for match in pattern.finditer(text):
                match_text = match.group(0)
                if match_text in seen:
                    continue
                seen.add(match_text)

                try:
                    case = self._verify_stat(match, stat_type, pmcid, scipy_stats)
                    if case:
                        cases.append(case)
                except Exception as e:
                    logger.debug("Failed to verify stat in %s: %s", pmcid, e)

        return cases

    def _verify_stat(self, match, stat_type: str, pmcid: str,
                     scipy_stats) -> dict | None:
        """Compute p-value and compare with reported value."""
        if stat_type == "F":
            df1 = int(match.group(1))
            df2 = int(match.group(2))
            stat_val = float(match.group(3))
            p_operator = match.group(4)
            p_str = match.group(5)

            if df1 < 1 or df2 < 1 or stat_val < 0:
                return None

            computed_p = 1 - scipy_stats.f.cdf(stat_val, df1, df2)
            reported_p = _parse_p_value(p_str)

            return self._make_case(
                stat_type, match.group(0), computed_p, reported_p,
                pmcid, f"F({df1},{df2})={stat_val}",
                df1=df1, df2=df2, test_statistic=stat_val,
            )

        elif stat_type == "t":
            df = int(match.group(1))
            t_val = float(match.group(2))
            p_operator = match.group(3)
            p_str = match.group(4)

            if df < 1:
                return None

            computed_p = 2 * (1 - scipy_stats.t.cdf(abs(t_val), df))
            reported_p = _parse_p_value(p_str)

            return self._make_case(
                stat_type, match.group(0), computed_p, reported_p,
                pmcid, f"t({df})={t_val}",
                df1=df, test_statistic=t_val,
            )

        elif stat_type == "chi2":
            df = int(match.group(1))
            chi_val = float(match.group(2))
            p_operator = match.group(3)
            p_str = match.group(4)

            if df < 1 or chi_val < 0:
                return None

            computed_p = 1 - scipy_stats.chi2.cdf(chi_val, df)
            reported_p = _parse_p_value(p_str)

            return self._make_case(
                stat_type, match.group(0), computed_p, reported_p,
                pmcid, f"chi2({df})={chi_val}",
                df1=df, test_statistic=chi_val,
            )

        return None

    @staticmethod
    def _make_case(stat_type: str, stat_text: str, computed_p: float,
                   reported_p: float, pmcid: str, detail: str,
                   **extra) -> dict:
        """Create a statcheck case record."""
        consistent = _p_consistent(computed_p, reported_p)
        return {
            "id": "",  # assigned later
            "stat_text": stat_text[:200],
            "test_type": stat_type,
            "test_statistic": float(extra.get("test_statistic", 0)),
            "df1": int(extra.get("df1", 0)),
            "df2": int(extra.get("df2") or 0),
            "reported_p": float(round(reported_p, 6)),
            "computed_p": float(round(computed_p, 6)),
            "consistent": bool(consistent),
            "expected_fired": bool(not consistent),
            "source_pmcid": pmcid,
            "note": f"{detail}, computed p={computed_p:.6f}, reported p={reported_p:.6f}, "
                    f"{'consistent' if consistent else 'INCONSISTENT'}",
        }
