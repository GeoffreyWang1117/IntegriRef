"""Dialnet — Spanish/Portuguese academic article repository.

One of the largest portals for Spanish-language academic content,
covering journals, dissertations, and conference proceedings from
Spain and Latin America.
"""

from __future__ import annotations

from core.entity import EntityType
from core.oai_pmh import OaiPmhRegistry
from core.registry import RegistryInfo


class DialnetRegistry(OaiPmhRegistry):
    """Dialnet OAI-PMH adapter."""

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="dialnet",
            domain="academic",
            base_url="https://dialnet.unirioja.es/oai/OAIHandler",
            auth_type="none",
            rate_limit=2.0,
            coverage="Spanish/Portuguese academic journals, theses, conference proceedings",
            entity_types=["paper", "thesis"],
        )

    def _oai_base_url(self) -> str:
        return "https://dialnet.unirioja.es/oai/OAIHandler"

    def _entity_type(self) -> EntityType:
        return EntityType.PAPER

    def query_by_id(self, id_type: str, id_value: str):
        if id_type == "oai_id":
            return super().query_by_id(id_type, id_value)
        return None
