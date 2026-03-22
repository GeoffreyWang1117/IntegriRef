"""DergiPark registry — Turkish academic journal platform.

API: https://dergipark.org.tr/api/public/
Auth: None
Rate: 1 req/s
Coverage: Turkish academic journal articles
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class DergiParkRegistry(RegistryAdapter):

    _BASE_URL = "https://dergipark.org.tr/api/public"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="dergipark",
            domain="academic",
            base_url="https://dergipark.org.tr",
            auth_type="none",
            rate_limit=1.0,
            coverage="Turkish academic journal articles",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        resp = self._get(
            f"{self._BASE_URL}/search",
            params={"query": id_value, "count": 1},
            headers={"Accept": "application/json"},
        )
        if not resp:
            return None
        try:
            data = resp.json()
            items = data.get("items", data.get("results", data.get("data", [])))
            if not items:
                return None
        except (KeyError, ValueError):
            return None
        # Find the item whose DOI matches
        for item in items:
            entity = self._parse(item)
            if entity and entity.get_id("doi") == id_value:
                return entity
        # Fallback: return first result
        return self._parse(items[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = title
        if author:
            query = f"{query} {author}"

        resp = self._get(
            f"{self._BASE_URL}/search",
            params={"query": query, "count": 5},
            headers={"Accept": "application/json"},
        )
        if not resp:
            return []
        try:
            data = resp.json()
            items = data.get("items", data.get("results", data.get("data", [])))
        except (KeyError, ValueError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if isinstance(title, list):
            title = title[0] if title else ""
        if isinstance(title, dict):
            title = title.get("text", title.get("en", title.get("tr", "")))
        if not title:
            return None

        authors_raw = data.get("authors", [])
        if isinstance(authors_raw, str):
            authors = [authors_raw]
        elif isinstance(authors_raw, list):
            authors = []
            for a in authors_raw:
                if isinstance(a, dict):
                    name = a.get("name", a.get("fullName", ""))
                    if name:
                        authors.append(name)
                elif isinstance(a, str) and a:
                    authors.append(a)
        else:
            authors = []

        year = str(data.get("year", data.get("publicationYear", "")))
        venue = data.get("journal", data.get("journalTitle", ""))
        if isinstance(venue, dict):
            venue = venue.get("name", venue.get("title", ""))

        doi = data.get("doi", "")

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={},
            source_registries=["dergipark"],
        )
        if doi:
            entity.add_external_id("dergipark", "doi", doi)

        entity.normalize()
        return entity
