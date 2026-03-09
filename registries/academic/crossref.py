"""CrossRef registry — the canonical DOI resolver.

API: https://api.crossref.org
Auth: None (polite pool with mailto gets better rate)
Rate: ~50 req/s polite pool, we use 1/s to be safe
Coverage: 150M+ works with DOIs
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class CrossRefRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="crossref",
            domain="academic",
            base_url="https://api.crossref.org",
            auth_type="none",
            rate_limit=1.0,
            coverage="150M+ DOI-registered works",
            entity_types=["paper", "book", "dataset"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        resp = self._get(f"https://api.crossref.org/works/{id_value}",
                         headers={"Accept": "application/json"})
        if not resp:
            return None
        try:
            data = resp.json()["message"]
        except (KeyError, ValueError):
            return None
        return self._parse(data)

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"query.bibliographic": title, "rows": 5}
        if author:
            params["query.author"] = author
        resp = self._get("https://api.crossref.org/works", params=params,
                         headers={"Accept": "application/json"})
        if not resp:
            return []
        try:
            items = resp.json()["message"]["items"]
        except (KeyError, ValueError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = (data.get("title") or [""])[0]
        if not title:
            return None

        authors = []
        for a in data.get("author", []):
            name = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if name:
                authors.append(name)

        year = ""
        for df in ("published-print", "published-online", "created"):
            if df in data:
                parts = data[df].get("date-parts", [[]])[0]
                if parts:
                    year = str(parts[0])
                    break

        venue = ""
        for v in data.get("container-title", []):
            if v:
                venue = v
                break

        doi = data.get("DOI", "")
        is_retracted = "retracted-article" in [
            u.get("type", "") for u in data.get("update-to", [])
        ]

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={
                "is_retracted": is_retracted,
                "type": data.get("type", ""),
                "publisher": data.get("publisher", ""),
                "issn": data.get("ISSN", []),
                "reference_count": data.get("reference-count"),
                "is_referenced_by_count": data.get("is-referenced-by-count"),
            },
            source_registries=["crossref"],
        )
        entity.add_external_id("crossref", "doi", doi)

        # Extract ISSN-linked IDs
        for issn in data.get("ISSN", []):
            entity.add_external_id("crossref", "issn", issn)

        entity.normalize()
        return entity
