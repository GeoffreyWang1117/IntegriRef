"""theses.fr registry — French doctoral theses.

API: https://theses.fr/api/v1/theses
Auth: None
Rate: 1 req/s
Coverage: French doctoral theses (all disciplines)
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class ThesesFrRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="theses_fr",
            domain="academic",
            base_url="https://theses.fr/api/v1/theses",
            auth_type="none",
            rate_limit=1.0,
            coverage="French doctoral theses (all disciplines)",
            entity_types=["thesis"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "nnt":
            return None
        resp = self._get(
            "https://theses.fr/api/v1/theses",
            params={"q": id_value},
        )
        if not resp:
            return None
        try:
            items = resp.json().get("theses", [])
        except (ValueError, AttributeError):
            return None
        if not items:
            return None
        return self._parse(items[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = title
        if author:
            query = f"{query} {author}"
        if year:
            query = f"{query} {year}"
        resp = self._get(
            "https://theses.fr/api/v1/theses",
            params={"q": query},
        )
        if not resp:
            return []
        try:
            items = resp.json().get("theses", [])
        except (ValueError, AttributeError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None

        author_raw = data.get("author", "")
        authors = [author_raw.strip()] if isinstance(author_raw, str) and author_raw.strip() else []

        year = str(data.get("year", ""))
        discipline = data.get("discipline", "")
        nnt = data.get("nnt", "")

        entity = ICEntity(
            entity_type=EntityType.THESIS,
            title=title,
            authors=authors,
            year=year,
            venue=discipline,
            metadata={
                "nnt": nnt,
                "discipline": discipline,
            },
            source_registries=["theses_fr"],
        )

        if nnt:
            entity.add_external_id("theses_fr", "nnt", nnt)

        entity.normalize()
        return entity
