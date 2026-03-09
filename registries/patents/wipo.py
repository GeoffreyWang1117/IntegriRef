"""WIPO PatentScope — World Intellectual Property Organization.

API: https://patentscope.wipo.int/search/en/structuredSearch.jsf (web-based)
Note: WIPO does not provide a fully open REST API. We use their search
endpoint with structured queries. For production, consider their
WIPO CASE API for patent family data.

Auth: None for basic search
Rate: Best-effort, use 3s between requests
Coverage: 120M+ patent documents from 80+ patent offices
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class WIPORegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="wipo",
            domain="patents",
            base_url="https://patentscope.wipo.int/search/en/result.jsf",
            auth_type="none",
            rate_limit=3.0,
            coverage="120M+ patent docs from 80+ patent offices (limited API)",
            entity_types=["patent"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type not in ("patent_number", "pct_number"):
            return None
        # WIPO search by document number
        resp = self._get(
            "https://patentscope.wipo.int/search/en/result.jsf",
            params={"query": id_value, "office": "", "lang": "EN"},
        )
        if not resp:
            return None
        # WIPO returns HTML — minimal parsing for existence check
        if id_value in resp.text:
            entity = ICEntity(
                entity_type=EntityType.PATENT,
                title=f"Patent {id_value}",
                year="",
                venue="WIPO",
                source_registries=["wipo"],
            )
            entity.add_external_id("wipo", id_type, id_value)
            entity.normalize()
            return entity
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # WIPO does not have a structured JSON API for title search
        # In production, use WIPO's SOAP-based service or Lens.org as proxy
        return []
