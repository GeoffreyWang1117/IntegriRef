"""Shared PMC Open Access full-text fetcher.

Fetches JATS XML from Europe PMC for articles containing statistical content.
Caches extracted text locally. Used by GRIM and StatCheck crawlers.

Usage:
    python -m benchmarks.crawlers pmc --count 5000
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .base import BaseCrawler

logger = logging.getLogger(__name__)

# Queries targeting papers with statistical content
_STAT_QUERIES = [
    '(OPEN_ACCESS:y) AND ("mean" OR "standard deviation") AND ("sample size" OR "participants")',
    '(OPEN_ACCESS:y) AND ("ANOVA" OR "t-test" OR "chi-square") AND ("p <" OR "p =")',
    '(OPEN_ACCESS:y) AND ("F(" OR "t(" OR "χ²(") AND ("p =" OR "p <")',
    '(OPEN_ACCESS:y) AND ("Likert" OR "scale") AND ("M =" OR "mean =")',
    '(OPEN_ACCESS:y) AND ("regression" OR "correlation") AND ("p value" OR "significance")',
]


class PMCFullTextCrawler(BaseCrawler):
    """Fetch and cache full-text articles from PubMed Central (Europe PMC)."""

    name = "pmc_fulltext"

    def __init__(self, output_dir: Path, checkpoint: bool = True):
        super().__init__(output_dir, checkpoint)
        self.cache_dir = output_dir / "pmc_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def crawl(self, count: int = 5000, **kwargs) -> int:
        """Fetch articles with statistical content from Europe PMC.

        Returns number of articles successfully cached.
        """
        pmcids = self._search_europe_pmc(count)
        logger.info("Found %d PMCIDs to fetch", len(pmcids))

        fetched = 0
        for i, pmcid in enumerate(pmcids):
            if self._is_cached(pmcid):
                fetched += 1
                continue

            text = self._fetch_fulltext(pmcid)
            if text:
                self._cache_text(pmcid, text)
                fetched += 1

            if (i + 1) % 100 == 0:
                logger.info("  PMC: fetched %d/%d articles...", fetched, len(pmcids))
                self._save_checkpoint(i + 1, fetched)

        self._clear_checkpoint()
        logger.info("PMCFullTextCrawler: %d articles cached", fetched)
        return fetched

    def _search_europe_pmc(self, count: int) -> list[str]:
        """Search Europe PMC for articles with statistical content."""
        pmcids: list[str] = []
        seen = set()
        per_query = count // len(_STAT_QUERIES) + 1
        page_size = 100

        for query in _STAT_QUERIES:
            if len(pmcids) >= count:
                break

            cursor_mark = "*"
            fetched_for_query = 0

            while fetched_for_query < per_query and len(pmcids) < count:
                params = {
                    "query": query,
                    "format": "json",
                    "pageSize": page_size,
                    "cursorMark": cursor_mark,
                    "resultType": "idlist",
                    "sort": "CITED desc",
                }

                resp = self._get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                  params=params)
                if resp is None:
                    break

                data = resp.json()
                results = data.get("resultList", {}).get("result", [])
                next_cursor = data.get("nextCursorMark")

                if not results:
                    break

                for r in results:
                    pmcid = r.get("pmcid", "")
                    if pmcid and pmcid not in seen:
                        seen.add(pmcid)
                        pmcids.append(pmcid)
                        fetched_for_query += 1
                        if len(pmcids) >= count:
                            break

                if not next_cursor or next_cursor == cursor_mark:
                    break
                cursor_mark = next_cursor

        return pmcids[:count]

    def _fetch_fulltext(self, pmcid: str) -> Optional[str]:
        """Fetch full-text XML from Europe PMC and extract body text."""
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
        resp = self._get(url, timeout=30)
        if resp is None:
            return None

        try:
            return self._extract_body_text(resp.text)
        except Exception as e:
            logger.debug("Failed to parse XML for %s: %s", pmcid, e)
            return None

    @staticmethod
    def _extract_body_text(xml_text: str) -> Optional[str]:
        """Extract body text paragraphs from JATS XML."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        paragraphs = []
        for p in root.iter("p"):
            text = ET.tostring(p, encoding="unicode", method="text")
            text = text.strip()
            if text:
                paragraphs.append(text)

        if not paragraphs:
            return None

        return "\n\n".join(paragraphs)

    def _is_cached(self, pmcid: str) -> bool:
        return (self.cache_dir / f"{pmcid}.txt").exists()

    def _cache_text(self, pmcid: str, text: str) -> None:
        path = self.cache_dir / f"{pmcid}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def get_cached_text(self, pmcid: str) -> Optional[str]:
        """Read cached text for a PMCID."""
        path = self.cache_dir / f"{pmcid}.txt"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def list_cached(self) -> list[str]:
        """List all cached PMCIDs."""
        return [p.stem for p in self.cache_dir.glob("*.txt")]
