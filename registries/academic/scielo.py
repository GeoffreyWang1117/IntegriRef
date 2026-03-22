"""SciELO registry — Scientific Electronic Library Online.

API: https://search.scielo.org/api/v1/
Auth: None
Rate: 1 req/s
Coverage: Latin American, Caribbean, Iberian, and South African scholarly journals
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class SciELORegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="scielo",
            domain="academic",
            base_url="https://search.scielo.org",
            auth_type="none",
            rate_limit=1.0,
            coverage="Latin American, Caribbean, Iberian, and South African scholarly journals",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            q = f'doi:"{id_value}"'
        elif id_type == "pid":
            q = f'pid:"{id_value}"'
        else:
            return None

        resp = self._get(
            "https://search.scielo.org/api/v1/",
            params={"q": q, "format": "json", "count": 1},
        )
        if not resp:
            return None
        try:
            data = resp.json()
            items = data.get("items", data.get("results", []))
            if not items:
                return None
        except (KeyError, ValueError):
            return None
        return self._parse(items[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        parts = [title]
        if author:
            parts.append(f'au:"{author}"')
        if year:
            parts.append(f"year:{year}")
        q = " AND ".join(parts)

        resp = self._get(
            "https://search.scielo.org/api/v1/",
            params={"q": q, "format": "json", "count": 5},
        )
        if not resp:
            return []
        try:
            data = resp.json()
            items = data.get("items", data.get("results", []))
        except (KeyError, ValueError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if isinstance(title, list):
            title = title[0] if title else ""
        if not title:
            return None

        authors_raw = data.get("authors", [])
        if isinstance(authors_raw, str):
            authors = [authors_raw]
        elif isinstance(authors_raw, list):
            authors = []
            for a in authors_raw:
                if isinstance(a, dict):
                    name = a.get("name", "")
                    if name:
                        authors.append(name)
                elif isinstance(a, str) and a:
                    authors.append(a)
        else:
            authors = []

        year = str(data.get("year", data.get("publication_year", "")))
        venue = data.get("journal", data.get("journal_title", ""))
        if isinstance(venue, dict):
            venue = venue.get("title", "")

        doi = data.get("doi", "")
        pid = data.get("pid", data.get("scielo_pid", ""))

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={},
            source_registries=["scielo"],
        )
        if doi:
            entity.add_external_id("scielo", "doi", doi)
        if pid:
            entity.add_external_id("scielo", "pid", pid)

        entity.normalize()
        return entity
