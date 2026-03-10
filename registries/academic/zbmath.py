"""zbMATH Open registry — mathematical publications.

API: https://api.zbmath.org/v1
Auth: None
Rate: 1.0s
Coverage: 4.9M mathematical publications (1755 to present)
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class ZbMATHRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="zbmath",
            domain="academic",
            base_url="https://api.zbmath.org/v1",
            auth_type="none",
            rate_limit=1.0,
            coverage="4.9M mathematical publications (1755–present)",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            search_string = f"doi:{id_value}"
        elif id_type == "zbmath_id":
            search_string = f"an:{id_value}"
        else:
            return None

        resp = self._get(
            "https://api.zbmath.org/v1/document",
            params={"search_string": search_string, "results_per_page": 1},
        )
        if not resp:
            return None
        try:
            results = resp.json().get("result", [])
            if results:
                return self._parse(results[0])
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        parts = [f"ti:{title}"]
        if author:
            parts.append(f"au:{author}")
        if year:
            parts.append(f"py:{year}")
        search_string = " & ".join(parts)

        resp = self._get(
            "https://api.zbmath.org/v1/document",
            params={"search_string": search_string, "results_per_page": 5},
        )
        if not resp:
            return []
        try:
            results = resp.json().get("result", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "").strip()
        if not title:
            return None

        authors = []
        for a in data.get("authors", []):
            name = a.get("name", "") if isinstance(a, dict) else str(a)
            if name:
                authors.append(name)

        year = str(data.get("year", ""))
        doi = data.get("doi", "")

        source = data.get("source", {})
        venue = source.get("series", "") if isinstance(source, dict) else ""

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={
                "msc_codes": data.get("msc_codes", []) or data.get("classification", []),
                "keywords": data.get("keywords", []),
                "zbmath_id": data.get("id", ""),
                "review_text": (data.get("review", {}) or {}).get("text", ""),
                "document_type": data.get("document_type", ""),
            },
            source_registries=["zbmath"],
        )

        if doi:
            entity.add_external_id("zbmath", "doi", doi)
        zbmath_id = data.get("id")
        if zbmath_id:
            entity.add_external_id("zbmath", "zbmath_id", str(zbmath_id))

        entity.normalize()
        return entity
