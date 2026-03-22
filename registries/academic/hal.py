"""HAL (Hyper Articles en Ligne) registry — French open-access archive.

API: https://api.archives-ouvertes.fr/search/
Auth: None
Rate: 1 req/s
Coverage: French and international open-access research papers
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class HALRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="hal",
            domain="academic",
            base_url="https://api.archives-ouvertes.fr",
            auth_type="none",
            rate_limit=1.0,
            coverage="French and international open-access research deposits",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            q = f'doiId_s:"{id_value}"'
        elif id_type == "hal_id":
            q = f'halId_s:"{id_value}"'
        else:
            return None

        resp = self._get(
            "https://api.archives-ouvertes.fr/search/",
            params={
                "q": q,
                "fl": "title_s,authFullName_s,producedDateY_i,journalTitle_s,doiId_s,halId_s,abstract_s",
                "rows": 1,
                "wt": "json",
            },
        )
        if not resp:
            return None
        try:
            docs = resp.json()["response"]["docs"]
            if not docs:
                return None
        except (KeyError, ValueError):
            return None
        return self._parse(docs[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        parts = [f'title_s:"{title}"']
        if author:
            parts.append(f'authFullName_s:"{author}"')
        if year:
            parts.append(f"producedDateY_i:{year}")
        q = " AND ".join(parts)

        resp = self._get(
            "https://api.archives-ouvertes.fr/search/",
            params={
                "q": q,
                "fl": "title_s,authFullName_s,producedDateY_i,journalTitle_s,doiId_s,halId_s,abstract_s",
                "rows": 5,
                "wt": "json",
            },
        )
        if not resp:
            return []
        try:
            docs = resp.json()["response"]["docs"]
        except (KeyError, ValueError):
            return []
        return [e for doc in docs if (e := self._parse(doc)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        # title_s can be a list or a string
        title_raw = data.get("title_s", "")
        if isinstance(title_raw, list):
            title = title_raw[0] if title_raw else ""
        else:
            title = title_raw
        if not title:
            return None

        authors = data.get("authFullName_s", [])
        if isinstance(authors, str):
            authors = [authors]

        year = str(data.get("producedDateY_i", ""))
        venue = data.get("journalTitle_s", "")
        doi = data.get("doiId_s", "")
        hal_id = data.get("halId_s", "")

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={
                "abstract": data.get("abstract_s", ""),
            },
            source_registries=["hal"],
        )
        if doi:
            entity.add_external_id("hal", "doi", doi)
        if hal_id:
            entity.add_external_id("hal", "hal_id", hal_id)

        entity.normalize()
        return entity
