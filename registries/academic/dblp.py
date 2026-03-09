"""DBLP registry — computer science bibliography.

API: https://dblp.org/search/publ/api
Auth: None
Rate: 1s between requests
Coverage: 7M+ CS publications
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class DBLPRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="dblp",
            domain="academic",
            base_url="https://dblp.org/search/publ/api",
            auth_type="none",
            rate_limit=1.0,
            coverage="7M+ computer science publications",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "dblp_key":
            return None
        resp = self._get(f"https://dblp.org/rec/{id_value}.json")
        if not resp:
            return None
        try:
            data = resp.json().get("result", {}).get("hits", {}).get("hit", [{}])[0]
            return self._parse_hit(data)
        except (KeyError, IndexError, ValueError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"q": title, "format": "json", "h": 10}
        resp = self._get("https://dblp.org/search/publ/api", params=params)
        if not resp:
            return []
        try:
            hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
        except (ValueError, AttributeError):
            return []
        return [e for hit in hits if (e := self._parse_hit(hit)) is not None]

    def _parse_hit(self, hit: dict) -> Optional[ICEntity]:
        info = hit.get("info", {})
        title = info.get("title", "").rstrip(".")
        if not title:
            return None

        authors_data = info.get("authors", {}).get("author", [])
        if isinstance(authors_data, dict):
            authors_data = [authors_data]
        authors = [
            re.sub(r"\s+\d{4}$", "", a.get("text", a) if isinstance(a, dict) else str(a)).strip()
            for a in authors_data
        ]

        venue = info.get("venue", "")
        if isinstance(venue, list):
            venue = venue[0] if venue else ""

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=info.get("year", ""),
            venue=venue,
            metadata={
                "pages": info.get("pages", ""),
                "type": info.get("type", ""),
                "access": info.get("access", ""),
            },
            source_registries=["dblp"],
        )
        if info.get("doi"):
            entity.add_external_id("dblp", "doi", info["doi"])
        if info.get("url"):
            entity.add_external_id("dblp", "dblp_url", info["url"])
        if info.get("key"):
            entity.add_external_id("dblp", "dblp_key", info["key"])

        entity.normalize()
        return entity
