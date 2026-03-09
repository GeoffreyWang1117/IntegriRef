"""IMF Data API — International Monetary Fund.

API: https://www.imf.org/external/datamapper/api/v1
     https://dataservices.imf.org/REST/SDMX_JSON.svc
Auth: None
Rate: No documented limit, use 1 req/s
Coverage: Global macroeconomic data, financial indicators, country reports
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class IMFRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="imf",
            domain="government",
            base_url="https://www.imf.org/external/datamapper/api/v1",
            auth_type="none",
            rate_limit=1.0,
            coverage="Global macroeconomic data and financial indicators",
            entity_types=["dataset", "report"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "imf_indicator":
            return None
        resp = self._get(
            f"https://www.imf.org/external/datamapper/api/v1/indicators/{id_value}",
        )
        if not resp:
            return None
        try:
            data = resp.json()
            label = data.get("indicators", {}).get(id_value, {}).get("label", "")
            if label:
                entity = ICEntity(
                    entity_type=EntityType.DATASET,
                    title=label,
                    venue="IMF",
                    source_registries=["imf"],
                )
                entity.add_external_id("imf", "imf_indicator", id_value)
                entity.normalize()
                return entity
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # Get all indicators and filter
        resp = self._get("https://www.imf.org/external/datamapper/api/v1/indicators")
        if not resp:
            return []
        try:
            indicators = resp.json().get("indicators", {})
            keywords = set(title.lower().split())
            matches = []
            for code, info in indicators.items():
                label = info.get("label", "").lower()
                if any(k in label for k in keywords):
                    entity = ICEntity(
                        entity_type=EntityType.DATASET,
                        title=info.get("label", ""),
                        venue="IMF",
                        source_registries=["imf"],
                    )
                    entity.add_external_id("imf", "imf_indicator", code)
                    entity.normalize()
                    matches.append(entity)
            return matches[:5]
        except (ValueError, AttributeError):
            return []
