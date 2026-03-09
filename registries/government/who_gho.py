"""WHO Global Health Observatory — health statistics.

API: https://ghoapi.azureedge.net/api
Auth: None
Rate: No documented limit, use 1 req/s
Coverage: 2,000+ health indicators, 194 WHO member states
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class WHOGHORegistry(RegistryAdapter):

    BASE = "https://ghoapi.azureedge.net/api"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="who_gho",
            domain="government",
            base_url=self.BASE,
            auth_type="none",
            rate_limit=1.0,
            coverage="2,000+ health indicators, 194 WHO member states",
            entity_types=["dataset"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "gho_indicator":
            return None
        resp = self._get(f"{self.BASE}/Indicator?$filter=IndicatorCode eq '{id_value}'")
        if not resp:
            return None
        try:
            values = resp.json().get("value", [])
            if values:
                ind = values[0]
                entity = ICEntity(
                    entity_type=EntityType.DATASET,
                    title=ind.get("IndicatorName", ""),
                    venue="WHO",
                    metadata={"language": ind.get("Language", "")},
                    source_registries=["who_gho"],
                )
                entity.add_external_id("who_gho", "gho_indicator", id_value)
                entity.normalize()
                return entity
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        resp = self._get(
            f"{self.BASE}/Indicator",
            params={"$filter": f"contains(IndicatorName,'{title[:50]}')"},
        )
        if not resp:
            return []
        try:
            values = resp.json().get("value", [])
        except (ValueError, AttributeError):
            return []
        entities = []
        for ind in values[:5]:
            entity = ICEntity(
                entity_type=EntityType.DATASET,
                title=ind.get("IndicatorName", ""),
                venue="WHO",
                source_registries=["who_gho"],
            )
            entity.add_external_id("who_gho", "gho_indicator", ind.get("IndicatorCode", ""))
            entity.normalize()
            entities.append(entity)
        return entities
