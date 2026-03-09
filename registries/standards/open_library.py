"""Open Library registry — Internet Archive's book database.

API: https://openlibrary.org/api
Auth: None
Rate: No strict limit, use 1 req/s
Coverage: 30M+ book records, 20M+ editions
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class OpenLibraryRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="open_library",
            domain="standards",
            base_url="https://openlibrary.org",
            auth_type="none",
            rate_limit=1.0,
            coverage="30M+ book records",
            entity_types=["book"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "isbn":
            resp = self._get(f"https://openlibrary.org/isbn/{id_value}.json")
        elif id_type == "open_library":
            resp = self._get(f"https://openlibrary.org/works/{id_value}.json")
        else:
            return None
        if not resp:
            return None
        try:
            return self._parse(resp.json(), id_type, id_value)
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"title": title, "limit": 5}
        if author:
            params["author"] = author
        resp = self._get("https://openlibrary.org/search.json", params=params)
        if not resp:
            return []
        try:
            docs = resp.json().get("docs", [])
        except (ValueError, AttributeError):
            return []
        entities = []
        for doc in docs[:5]:
            entity = ICEntity(
                entity_type=EntityType.BOOK,
                title=doc.get("title", ""),
                authors=[a for a in doc.get("author_name", [])],
                year=str(doc.get("first_publish_year", "")),
                venue=doc.get("publisher", [""])[0] if doc.get("publisher") else "",
                metadata={
                    "isbn": doc.get("isbn", [])[:3],
                    "subjects": doc.get("subject", [])[:5],
                    "edition_count": doc.get("edition_count", 0),
                },
                source_registries=["open_library"],
            )
            if doc.get("key"):
                entity.add_external_id("open_library", "open_library",
                                       doc["key"].replace("/works/", ""))
            for isbn in doc.get("isbn", [])[:1]:
                entity.add_external_id("open_library", "isbn", isbn)
            entity.normalize()
            entities.append(entity)
        return entities

    def _parse(self, data: dict, id_type: str, id_value: str) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None
        entity = ICEntity(
            entity_type=EntityType.BOOK,
            title=title,
            year=str(data.get("publish_date", ""))[:4],
            metadata={"subjects": [s.get("name", "") if isinstance(s, dict) else s
                                   for s in data.get("subjects", [])[:5]]},
            source_registries=["open_library"],
        )
        entity.add_external_id("open_library", id_type, id_value)
        entity.normalize()
        return entity
