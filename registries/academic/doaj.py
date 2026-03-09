"""DOAJ registry — Directory of Open Access Journals.

API: https://doaj.org/api
Auth: None (API key optional for higher rate)
Rate: 2 req/s
Coverage: 21,000+ OA journals, 10M+ articles
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class DOAJRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="doaj",
            domain="academic",
            base_url="https://doaj.org/api",
            auth_type="none",
            rate_limit=1.0,
            coverage="21K+ OA journals, 10M+ articles",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        resp = self._get(
            f"https://doaj.org/api/search/articles/doi:{id_value}",
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

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = f'title:"{title}"'
        resp = self._get(
            f"https://doaj.org/api/search/articles/{query}",
            params={"pageSize": 5},
        )
        if not resp:
            return []
        try:
            results = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        bibjson = data.get("bibjson", {})
        title = bibjson.get("title", "")
        if not title:
            return None

        authors = [a.get("name", "") for a in bibjson.get("author", []) if a.get("name")]
        year = bibjson.get("year", "")
        venue = bibjson.get("journal", {}).get("title", "")

        doi = ""
        for ident in bibjson.get("identifier", []):
            if ident.get("type") == "doi":
                doi = ident.get("id", "")

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={"is_open_access": True},
            source_registries=["doaj"],
        )
        if doi:
            entity.add_external_id("doaj", "doi", doi)
        entity.normalize()
        return entity
