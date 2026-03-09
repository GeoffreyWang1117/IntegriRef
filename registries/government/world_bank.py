"""World Bank API — global development data and indicators.

API: https://api.worldbank.org/v2
Auth: None
Rate: No documented limit, use 1 req/s
Coverage: 16,000+ indicators, 200+ countries, 60+ years of data
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class WorldBankRegistry(RegistryAdapter):

    BASE = "https://api.worldbank.org/v2"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="world_bank",
            domain="government",
            base_url=self.BASE,
            auth_type="none",
            rate_limit=1.0,
            coverage="16,000+ global development indicators, 200+ countries",
            entity_types=["dataset", "report"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "indicator":
            return None
        resp = self._get(f"{self.BASE}/indicator/{id_value}",
                         params={"format": "json"})
        if not resp:
            return None
        try:
            data = resp.json()
            if len(data) > 1 and data[1]:
                ind = data[1][0]
                entity = ICEntity(
                    entity_type=EntityType.DATASET,
                    title=ind.get("name", ""),
                    venue="World Bank",
                    metadata={
                        "source": ind.get("source", {}).get("value", ""),
                        "topic": [t.get("value", "") for t in ind.get("topics", [])],
                    },
                    source_registries=["world_bank"],
                )
                entity.add_external_id("world_bank", "indicator", id_value)
                entity.normalize()
                return entity
        except (ValueError, KeyError, IndexError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # Search indicators by keyword
        resp = self._get(f"{self.BASE}/indicator",
                         params={"format": "json", "per_page": 100})
        if not resp:
            return []
        try:
            data = resp.json()
            if len(data) < 2:
                return []
            keywords = set(title.lower().split())
            matches = []
            for ind in data[1] or []:
                name = ind.get("name", "").lower()
                if any(k in name for k in keywords):
                    entity = ICEntity(
                        entity_type=EntityType.DATASET,
                        title=ind.get("name", ""),
                        venue="World Bank",
                        source_registries=["world_bank"],
                    )
                    entity.add_external_id("world_bank", "indicator", ind.get("id", ""))
                    entity.normalize()
                    matches.append(entity)
            return matches[:5]
        except (ValueError, KeyError):
            return []
