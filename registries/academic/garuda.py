"""GARUDA — Indonesian academic repository.

Garba Rujukan Digital (GARUDA) is Indonesia's national referencing portal
managed by the Ministry of Education, covering Indonesian academic journals.
"""

from __future__ import annotations

from core.entity import EntityType
from core.oai_pmh import OaiPmhRegistry
from core.registry import RegistryInfo


class GARUDARegistry(OaiPmhRegistry):
    """GARUDA OAI-PMH adapter."""

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="garuda",
            domain="academic",
            base_url="https://garuda.kemdikbud.go.id/oai",
            auth_type="none",
            rate_limit=2.0,
            coverage="Indonesian academic journals and publications",
            entity_types=["paper"],
        )

    def _oai_base_url(self) -> str:
        return "https://garuda.kemdikbud.go.id/oai"

    def _entity_type(self) -> EntityType:
        return EntityType.PAPER

    def query_by_id(self, id_type: str, id_value: str):
        if id_type == "oai_id":
            return super().query_by_id(id_type, id_value)
        return None
