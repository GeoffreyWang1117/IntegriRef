"""FRED registry — Federal Reserve Economic Data.

API: https://api.stlouisfed.org/fred
Auth: API key required (free registration)
Rate: 0.5s (120/min)
Coverage: 840K+ economic time series
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo

try:
    from config import FRED_API_KEY
except ImportError:
    FRED_API_KEY = ""


class FREDRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="fred",
            domain="financial",
            base_url="https://api.stlouisfed.org/fred",
            auth_type="api_key_free",
            rate_limit=0.5,
            coverage="840K+ economic time series",
            entity_types=["dataset"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if not FRED_API_KEY:
            return None
        if id_type != "fred_series":
            return None

        resp = self._get(
            "https://api.stlouisfed.org/fred/series",
            params={
                "series_id": id_value.upper(),
                "api_key": FRED_API_KEY,
                "file_type": "json",
            },
        )
        if not resp:
            return None
        try:
            seriess = resp.json().get("seriess", [])
            if seriess:
                return self._parse(seriess[0])
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        if not FRED_API_KEY:
            return []

        resp = self._get(
            "https://api.stlouisfed.org/fred/series/search",
            params={
                "search_text": title,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "limit": 5,
            },
        )
        if not resp:
            return []
        try:
            seriess = resp.json().get("seriess", [])
        except (ValueError, AttributeError):
            return []
        return [e for s in seriess if (e := self._parse(s)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "").strip()
        if not title:
            return None

        series_id = data.get("id", "")

        entity = ICEntity(
            entity_type=EntityType.DATASET,
            title=title,
            authors=["Federal Reserve Bank of St. Louis"],
            year="",
            venue="FRED",
            metadata={
                "series_id": series_id,
                "frequency": data.get("frequency", ""),
                "units": data.get("units", ""),
                "seasonal_adjustment": data.get("seasonal_adjustment", ""),
                "last_updated": data.get("last_updated", ""),
                "observation_start": data.get("observation_start", ""),
                "observation_end": data.get("observation_end", ""),
                "notes": (data.get("notes", "") or "")[:500],
            },
            source_registries=["fred"],
        )

        if series_id:
            entity.add_external_id("fred", "fred_series", series_id)

        entity.normalize()
        return entity
