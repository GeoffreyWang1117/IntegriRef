"""CORE registry — aggregator of open access research.

API: https://api.core.ac.uk/v3
Auth: Free API key required
Rate: 10 req/s
Coverage: 300M+ metadata records, 40M+ full-text OA papers
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class CORERegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="core",
            domain="academic",
            base_url="https://api.core.ac.uk/v3",
            auth_type="api_key_free",
            rate_limit=0.5,
            coverage="300M+ metadata records, 40M+ full-text OA",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            return self._search_one(f'doi:"{id_value}"')
        if id_type == "core":
            resp = self._get(f"https://api.core.ac.uk/v3/works/{id_value}")
            if resp:
                try:
                    return self._parse(resp.json())
                except (ValueError, KeyError):
                    pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = title
        if author:
            query += f" {author}"
        resp = self._get(
            "https://api.core.ac.uk/v3/search/works",
            params={"q": query, "limit": 5},
        )
        if not resp:
            return []
        try:
            results = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def _search_one(self, query: str) -> Optional[ICEntity]:
        resp = self._get(
            "https://api.core.ac.uk/v3/search/works",
            params={"q": query, "limit": 1},
        )
        if not resp:
            return None
        try:
            results = resp.json().get("results", [])
            if results:
                return self._parse(results[0])
        except (ValueError, KeyError):
            pass
        return None

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None
        authors = data.get("authors", [])
        if isinstance(authors, list) and authors and isinstance(authors[0], dict):
            authors = [a.get("name", "") for a in authors]

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors if isinstance(authors, list) else [],
            year=str(data.get("yearPublished", "")),
            venue=data.get("publisher", ""),
            metadata={
                "full_text_available": bool(data.get("fullText")),
                "language": data.get("language", {}).get("code", ""),
            },
            source_registries=["core"],
        )
        for doi in (data.get("doi") or "").split():
            if doi:
                entity.add_external_id("core", "doi", doi)
        entity.normalize()
        return entity
