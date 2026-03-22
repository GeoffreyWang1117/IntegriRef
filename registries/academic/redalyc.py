"""Redalyc — Latin American open-access journal repository.

Red de Revistas Científicas de América Latina y el Caribe, España y Portugal.
Covers 1,300+ journals across social sciences, humanities, and natural sciences.
"""

from __future__ import annotations

from core.entity import EntityType
from core.oai_pmh import OaiPmhRegistry
from core.registry import RegistryInfo


class RedalycRegistry(OaiPmhRegistry):
    """Redalyc OAI-PMH adapter."""

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="redalyc",
            domain="academic",
            base_url="https://www.redalyc.org/oai/oai.jsp",
            auth_type="none",
            rate_limit=2.0,
            coverage="Latin American and Caribbean academic journals (1300+ journals)",
            entity_types=["paper"],
        )

    def _oai_base_url(self) -> str:
        return "https://www.redalyc.org/oai/oai.jsp"

    def _entity_type(self) -> EntityType:
        return EntityType.PAPER

    def query_by_id(self, id_type: str, id_value: str):
        if id_type == "oai_id":
            return super().query_by_id(id_type, id_value)
        return None
