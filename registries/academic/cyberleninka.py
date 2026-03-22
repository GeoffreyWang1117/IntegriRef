"""CyberLeninka — Russian open-access academic library.

Largest open-access repository for Russian-language academic publications.
Covers 2,600+ journals across all disciplines.
"""

from __future__ import annotations

from core.entity import EntityType
from core.oai_pmh import OaiPmhRegistry
from core.registry import RegistryInfo


class CyberLeninkaRegistry(OaiPmhRegistry):
    """CyberLeninka OAI-PMH adapter."""

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="cyberleninka",
            domain="academic",
            base_url="https://cyberleninka.ru/oai",
            auth_type="none",
            rate_limit=2.0,
            coverage="Russian academic journals (2600+ journals, all disciplines)",
            entity_types=["paper"],
        )

    def _oai_base_url(self) -> str:
        return "https://cyberleninka.ru/oai"

    def _entity_type(self) -> EntityType:
        return EntityType.PAPER

    def query_by_id(self, id_type: str, id_value: str):
        if id_type == "oai_id":
            return super().query_by_id(id_type, id_value)
        return None
