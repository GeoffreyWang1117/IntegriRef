"""A-tier crawler: Temporal anomalies from OpenAlex citation graph.

Finds real cases where citing_year < cited_year (impossible citations)
by scanning the OpenAlex citation graph.

Usage:
    python -m benchmarks.crawlers temporal --count 2000
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .base import BaseCrawler, CONTACT_EMAIL

logger = logging.getLogger(__name__)


class TemporalAnomalyCrawler(BaseCrawler):
    """Find temporal anomalies in the OpenAlex citation graph."""

    name = "temporal_anomaly"

    def crawl(self, count: int = 2000, anomaly_ratio: float = 0.3,
              **kwargs) -> int:
        """Find temporal anomalies and normal controls from OpenAlex.

        Args:
            count: Total number of cases to produce.
            anomaly_ratio: Target ratio of anomalies vs total.
        """
        n_anomalies_target = int(count * anomaly_ratio)
        n_normal_target = count - n_anomalies_target

        anomalies = []
        normals = []
        papers_scanned = 0
        cursor = "*"
        batch_size = 200

        logger.info("Scanning OpenAlex for temporal anomalies (target: %d anomalies, %d normals)...",
                     n_anomalies_target, n_normal_target)

        while (len(anomalies) < n_anomalies_target or len(normals) < n_normal_target):
            # Sample papers from OpenAlex
            params = {
                "filter": "has_doi:true,publication_year:2010-2024,cited_by_count:>5",
                "per_page": batch_size,
                "cursor": cursor,
                "select": "id,title,publication_year,referenced_works,doi,authorships",
                "mailto": CONTACT_EMAIL,
            }

            resp = self._get("https://api.openalex.org/works", params=params)
            if resp is None:
                break

            data = resp.json()
            results = data.get("results", [])
            next_cursor = data.get("meta", {}).get("next_cursor")

            if not results:
                break

            # For each paper, check its references
            ref_ids = set()
            for paper in results:
                for ref_url in paper.get("referenced_works", [])[:30]:
                    ref_ids.add(ref_url)

            # Batch-fetch referenced works
            ref_map = self._batch_fetch_works(list(ref_ids))

            for paper in results:
                citing_year = paper.get("publication_year")
                citing_doi = (paper.get("doi") or "").replace("https://doi.org/", "")
                if not citing_year or not citing_doi:
                    continue

                for ref_url in paper.get("referenced_works", [])[:30]:
                    ref_info = ref_map.get(ref_url)
                    if not ref_info:
                        continue

                    cited_year = ref_info.get("publication_year")
                    cited_doi = (ref_info.get("doi") or "").replace("https://doi.org/", "")
                    if not cited_year or not cited_doi:
                        continue

                    # Check for temporal anomaly
                    is_anomaly = citing_year < cited_year

                    if is_anomaly and len(anomalies) < n_anomalies_target:
                        pair_key = f"{citing_doi}|{cited_doi}"
                        if not self._is_seen(pair_key):
                            self._mark_seen(pair_key)
                            anomalies.append(self._make_case(
                                paper, ref_info, citing_year, cited_year,
                                True, len(anomalies),
                            ))
                    elif not is_anomaly and len(normals) < n_normal_target:
                        pair_key = f"{citing_doi}|{cited_doi}"
                        if not self._is_seen(pair_key):
                            self._mark_seen(pair_key)
                            normals.append(self._make_case(
                                paper, ref_info, citing_year, cited_year,
                                False, len(normals),
                            ))

            papers_scanned += len(results)
            logger.info("  Scanned %d papers: %d anomalies, %d normals",
                         papers_scanned, len(anomalies), len(normals))

            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

            # Check if we have enough
            if (len(anomalies) >= n_anomalies_target and
                    len(normals) >= n_normal_target):
                break

        # Combine and write
        all_cases = anomalies + normals
        for idx, case in enumerate(all_cases):
            case["id"] = f"temporal_oa_{idx:04d}"

        out_path = self.output_dir / "temporal_anomaly_cases.jsonl"
        self._write_jsonl(all_cases, out_path)
        self._flush_seen_ids()

        logger.info("TemporalAnomalyCrawler: %d cases (%d anomalies, %d normals)",
                     len(all_cases), len(anomalies), len(normals))
        return len(all_cases)

    def _batch_fetch_works(self, work_urls: list[str]) -> dict[str, dict]:
        """Batch-fetch work metadata from OpenAlex using pipe-separated IDs."""
        result = {}
        if not work_urls:
            return result

        # OpenAlex supports up to 50 IDs per filter query
        batch_size = 50
        for i in range(0, len(work_urls), batch_size):
            batch = work_urls[i:i + batch_size]
            # Extract OpenAlex IDs from URLs
            ids = [url.split("/")[-1] for url in batch if "/" in url]
            if not ids:
                continue

            params = {
                "filter": f"openalex_id:{'|'.join(ids)}",
                "per_page": len(ids),
                "select": "id,title,publication_year,doi",
                "mailto": CONTACT_EMAIL,
            }

            resp = self._get("https://api.openalex.org/works", params=params)
            if resp is None:
                continue

            for work in resp.json().get("results", []):
                oa_id = work.get("id", "")
                result[oa_id] = work

        return result

    @staticmethod
    def _make_case(citing_paper: dict, cited_paper: dict,
                   citing_year: int, cited_year: int,
                   is_anomaly: bool, idx: int) -> dict:
        """Create a temporal anomaly case record."""
        citing_doi = (citing_paper.get("doi") or "").replace("https://doi.org/", "")
        cited_doi = (cited_paper.get("doi") or "").replace("https://doi.org/", "")

        citing_title = citing_paper.get("title", "") or ""
        cited_title = cited_paper.get("title", "") or ""

        gap_years = citing_year - cited_year

        return {
            "id": "",  # assigned later
            "citing_paper": {
                "doi": citing_doi,
                "title": citing_title[:200],
                "year": citing_year,
            },
            "cited_paper": {
                "doi": cited_doi,
                "title": cited_title[:200],
                "year": cited_year,
            },
            "citing_year": citing_year,
            "cited_year": cited_year,
            "time_gap_years": gap_years,
            "expected_anomaly": is_anomaly,
            "expected_fired": is_anomaly,
            "note": (
                f"Citing paper ({citing_year}) {'predates' if is_anomaly else 'postdates'} "
                f"cited paper ({cited_year}), gap={gap_years} years"
            ),
        }
