"""OpenCitations registry — open citation data.

API: https://opencitations.net/index/coci/api/v1
Auth: None
Rate: No strict limit, use 1 req/s
Coverage: 2B+ citation links (COCI, POCI, DOCI indices)
Key feature: citation link data (who cites whom) — crucial for graph building
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class OpenCitationsRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="opencitations",
            domain="academic",
            base_url="https://opencitations.net/index/coci/api/v1",
            auth_type="none",
            rate_limit=1.0,
            coverage="2B+ citation links (open citation data)",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        # OpenCitations is citation-link focused, not metadata
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        return []

    def get_citations(self, doi: str) -> list[dict]:
        """Get all papers that cite the given DOI.

        Returns list of {"citing_doi": ..., "cited_doi": ..., "creation_date": ...}
        """
        resp = self._get(
            f"https://opencitations.net/index/coci/api/v1/citations/{doi}",
        )
        if not resp:
            return []
        try:
            return resp.json()
        except (ValueError, AttributeError):
            return []

    def get_references(self, doi: str) -> list[dict]:
        """Get all papers referenced by the given DOI.

        Returns list of {"citing_doi": ..., "cited_doi": ..., "creation_date": ...}
        """
        resp = self._get(
            f"https://opencitations.net/index/coci/api/v1/references/{doi}",
        )
        if not resp:
            return []
        try:
            return resp.json()
        except (ValueError, AttributeError):
            return []

    def get_citation_count(self, doi: str) -> int:
        """Get the number of citations for a DOI."""
        resp = self._get(
            f"https://opencitations.net/index/coci/api/v1/citation-count/{doi}",
        )
        if not resp:
            return 0
        try:
            data = resp.json()
            if data:
                return int(data[0].get("count", 0))
        except (ValueError, IndexError, KeyError):
            pass
        return 0
