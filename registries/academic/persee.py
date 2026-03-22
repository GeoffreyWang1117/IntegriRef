"""Persée registry — French academic journal archive.

API: https://www.persee.fr/api/
Auth: None
Rate: 1 req/s
Coverage: French academic journals (humanities, social sciences)
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class PerseeRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="persee",
            domain="academic",
            base_url="https://www.persee.fr/api",
            auth_type="none",
            rate_limit=1.0,
            coverage="French academic journals (humanities, social sciences)",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            resp = self._get(
                f"https://www.persee.fr/api/search",
                params={"q": id_value},
            )
        elif id_type == "persee_id":
            resp = self._get(
                f"https://www.persee.fr/api/search",
                params={"q": id_value},
            )
        else:
            return None
        if not resp:
            return None
        try:
            items = resp.json().get("items", [])
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
            "https://www.persee.fr/api/search",
            params={"q": query},
        )
        if not resp:
            return []
        try:
            items = resp.json().get("items", [])
        except (ValueError, AttributeError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None

        author_raw = data.get("author", "")
        authors = [a.strip() for a in author_raw.split(";") if a.strip()] \
            if isinstance(author_raw, str) and author_raw else []

        year = str(data.get("year", ""))
        journal = data.get("journal", "")
        doi = data.get("doi", "")
        persee_id = data.get("perseeId", "")

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=journal,
            metadata={
                "persee_id": persee_id,
            },
            source_registries=["persee"],
        )

        if doi:
            entity.add_external_id("persee", "doi", doi)
        if persee_id:
            entity.add_external_id("persee", "persee_id", persee_id)

        entity.normalize()
        return entity
