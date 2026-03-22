"""CiNii Research registry — Japanese scholarly database.

API: https://cir.nii.ac.jp/opensearch/articles (OpenSearch)
Auth: None
Rate: 1 req/s
Coverage: Japanese academic papers and dissertations
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class CiNiiRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="cinii",
            domain="academic",
            base_url="https://cir.nii.ac.jp",
            auth_type="none",
            rate_limit=1.0,
            coverage="Japanese scholarly articles and dissertations",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            params = {"title": "", "doi": id_value, "format": "json", "count": 1}
        elif id_type == "naid":
            params = {"title": "", "naid": id_value, "format": "json", "count": 1}
        else:
            return None

        resp = self._get(
            "https://cir.nii.ac.jp/opensearch/articles",
            params=params,
        )
        if not resp:
            return None
        try:
            data = resp.json()
            items = data.get("items", [])
            if not items:
                return None
        except (KeyError, ValueError):
            return None
        return self._parse(items[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"title": title, "format": "json", "count": 5}
        if author:
            params["creator"] = author
        if year:
            params["year_from"] = year
            params["year_to"] = year

        resp = self._get(
            "https://cir.nii.ac.jp/opensearch/articles",
            params=params,
        )
        if not resp:
            return []
        try:
            data = resp.json()
            items = data.get("items", [])
        except (KeyError, ValueError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("dc:title", "")
        if isinstance(title, list):
            title = title[0] if title else ""
        if not title:
            return None

        creators = data.get("dc:creator", [])
        if isinstance(creators, str):
            creators = [creators]
        authors = [c for c in creators if c]

        pub_date = data.get("prism:publicationDate", "")
        year = pub_date[:4] if pub_date else ""

        venue = data.get("prism:publicationName", "")

        # Extract identifiers
        doi = data.get("prism:doi", "") or data.get("doi", "")
        naid = data.get("dc:identifier", "")

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={},
            source_registries=["cinii"],
        )
        if doi:
            entity.add_external_id("cinii", "doi", doi)
        if naid:
            entity.add_external_id("cinii", "naid", naid)

        entity.normalize()
        return entity
