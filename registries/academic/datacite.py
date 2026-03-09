"""DataCite registry — research data and dataset DOIs.

API: https://api.datacite.org
Auth: None
Rate: No hard limit documented, use 1 req/s
Coverage: 50M+ DOIs for datasets, software, preprints, etc.
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class DataCiteRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="datacite",
            domain="academic",
            base_url="https://api.datacite.org",
            auth_type="none",
            rate_limit=1.0,
            coverage="50M+ DOIs for datasets, software, other research outputs",
            entity_types=["dataset", "paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        resp = self._get(f"https://api.datacite.org/dois/{id_value}")
        if not resp:
            return None
        try:
            return self._parse(resp.json().get("data", {}).get("attributes", {}))
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"query": title, "page[size]": 5}
        resp = self._get("https://api.datacite.org/dois", params=params)
        if not resp:
            return []
        try:
            items = resp.json().get("data", [])
        except (ValueError, AttributeError):
            return []
        return [e for item in items
                if (e := self._parse(item.get("attributes", {}))) is not None]

    def _parse(self, attrs: dict) -> Optional[ICEntity]:
        titles = attrs.get("titles", [])
        title = titles[0].get("title", "") if titles else ""
        if not title:
            return None

        authors = []
        for c in attrs.get("creators", []):
            name = c.get("name", "")
            if not name and c.get("familyName"):
                name = f"{c.get('givenName', '')} {c['familyName']}".strip()
            if name:
                authors.append(name)

        year = str(attrs.get("publicationYear", ""))
        doi = attrs.get("doi", "")

        # Map DataCite resource types to our EntityType
        resource_type = (attrs.get("types", {}).get("resourceTypeGeneral", "")).lower()
        type_map = {
            "dataset": EntityType.DATASET,
            "text": EntityType.PAPER,
            "software": EntityType.OTHER,
        }
        entity_type = type_map.get(resource_type, EntityType.OTHER)

        entity = ICEntity(
            entity_type=entity_type,
            title=title,
            authors=authors,
            year=year,
            metadata={
                "resource_type": resource_type,
                "publisher": attrs.get("publisher", ""),
            },
            source_registries=["datacite"],
        )
        if doi:
            entity.add_external_id("datacite", "doi", doi)
        entity.normalize()
        return entity
