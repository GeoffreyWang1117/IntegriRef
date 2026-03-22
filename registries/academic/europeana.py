"""Europeana registry — European cultural heritage.

API: https://api.europeana.eu/record/v2/search.json
Auth: API key (EUROPEANA_API_KEY env var), parameter wskey
Rate: 1 req/s
Coverage: European cultural heritage collections (museums, libraries, archives)
"""

from __future__ import annotations

import os
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class EuropeanaRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="europeana",
            domain="academic",
            base_url="https://api.europeana.eu/record/v2",
            auth_type="api_key_free",
            rate_limit=1.0,
            coverage="European cultural heritage collections (museums, libraries, archives)",
            entity_types=["other"],
        )

    def _api_key(self) -> Optional[str]:
        return os.environ.get("EUROPEANA_API_KEY")

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "europeana_id":
            return None
        api_key = self._api_key()
        if not api_key:
            return None
        resp = self._get(
            "https://api.europeana.eu/record/v2/search.json",
            params={
                "wskey": api_key,
                "query": f'europeana_id:"{id_value}"',
            },
        )
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
        api_key = self._api_key()
        if not api_key:
            return []
        query = title
        if author:
            query = f"{query} {author}"
        if year:
            query = f"{query} {year}"
        resp = self._get(
            "https://api.europeana.eu/record/v2/search.json",
            params={
                "wskey": api_key,
                "query": query,
            },
        )
        if not resp:
            return []
        try:
            items = resp.json().get("items", [])
        except (ValueError, AttributeError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        # Title can be a list or a dict of language codes
        title_raw = data.get("title", [])
        if isinstance(title_raw, list):
            title = title_raw[0] if title_raw else ""
        elif isinstance(title_raw, dict):
            title = next(iter(title_raw.values()), [""])[0] if title_raw else ""
        else:
            title = str(title_raw)
        if not title:
            return None

        # Creator can be list or dict
        creator_raw = data.get("dcCreator", [])
        if isinstance(creator_raw, list):
            authors = [c.strip() for c in creator_raw if isinstance(c, str) and c.strip()]
        elif isinstance(creator_raw, dict):
            authors = []
            for vals in creator_raw.values():
                if isinstance(vals, list):
                    authors.extend(v.strip() for v in vals if isinstance(v, str) and v.strip())
        else:
            authors = []

        year = str(data.get("year", [""])[0]) if data.get("year") else ""
        data_provider = data.get("dataProvider", [""])[0] if data.get("dataProvider") else ""
        europeana_id = data.get("id", "")
        shown_at = data.get("edmIsShownAt", [""])[0] if data.get("edmIsShownAt") else ""

        entity = ICEntity(
            entity_type=EntityType.OTHER,
            title=title,
            authors=authors,
            year=year,
            venue=data_provider,
            metadata={
                "data_provider": data_provider,
                "edm_is_shown_at": shown_at,
                "europeana_id": europeana_id,
            },
            source_registries=["europeana"],
        )

        if europeana_id:
            entity.add_external_id("europeana", "europeana_id", europeana_id)

        entity.normalize()
        return entity
