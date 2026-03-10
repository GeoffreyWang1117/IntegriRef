"""Zenodo registry — open-access research data repository.

API: https://zenodo.org/api/records
Auth: None (OAuth optional for higher rate)
Rate: 2.0s (30/min)
Coverage: 3M+ research outputs (datasets, software, papers)
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class ZenodoRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="zenodo",
            domain="academic",
            base_url="https://zenodo.org/api",
            auth_type="none",
            rate_limit=2.0,
            coverage="3M+ research outputs (datasets, software, papers)",
            entity_types=["paper", "dataset", "other"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            resp = self._get(
                "https://zenodo.org/api/records",
                params={"q": f'doi:"{id_value}"', "size": 1},
            )
        elif id_type == "zenodo_id":
            resp = self._get(f"https://zenodo.org/api/records/{id_value}")
            if resp:
                try:
                    return self._parse(resp.json())
                except (ValueError, KeyError):
                    return None
            return None
        else:
            return None

        if not resp:
            return None
        try:
            hits = resp.json().get("hits", {}).get("hits", [])
            if hits:
                return self._parse(hits[0])
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = title
        if author:
            query += f" {author}"
        params = {"q": query, "size": 5, "sort": "bestmatch"}
        if year:
            params["q"] += f" publication_date:[{year}-01-01 TO {year}-12-31]"

        resp = self._get("https://zenodo.org/api/records", params=params)
        if not resp:
            return []
        try:
            hits = resp.json().get("hits", {}).get("hits", [])
        except (ValueError, AttributeError):
            return []
        return [e for h in hits if (e := self._parse(h)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        metadata = data.get("metadata", data)
        title = metadata.get("title", "").strip()
        if not title:
            return None

        authors = []
        for creator in metadata.get("creators", []):
            name = creator.get("name", "")
            if name:
                authors.append(name)

        year = ""
        pub_date = metadata.get("publication_date", "")
        if pub_date:
            m = re.match(r"(\d{4})", pub_date)
            if m:
                year = m.group(1)

        # Map resource type to entity type
        resource_type = metadata.get("resource_type", {})
        rtype = resource_type.get("type", "") if isinstance(resource_type, dict) else ""
        if rtype == "dataset":
            entity_type = EntityType.DATASET
        elif rtype in ("software", "image", "video", "lesson"):
            entity_type = EntityType.OTHER
        elif rtype == "publication":
            entity_type = EntityType.PAPER
        else:
            entity_type = EntityType.OTHER

        doi = metadata.get("doi", "") or data.get("doi", "")

        entity = ICEntity(
            entity_type=entity_type,
            title=title,
            authors=authors,
            year=year,
            venue="Zenodo",
            metadata={
                "description": (metadata.get("description", "") or "")[:2000],
                "resource_type": rtype,
                "license": metadata.get("license", {}).get("id", "") if isinstance(metadata.get("license"), dict) else "",
                "access_right": metadata.get("access_right", ""),
                "keywords": metadata.get("keywords", []),
            },
            source_registries=["zenodo"],
        )

        if doi:
            entity.add_external_id("zenodo", "doi", doi)

        record_id = data.get("id") or data.get("record_id")
        if record_id:
            entity.add_external_id("zenodo", "zenodo_id", str(record_id))

        entity.normalize()
        return entity
