"""DiVA portal — Scandinavian academic repository (Sweden, Norway, Finland).

DiVA (Digitala Vetenskapliga Arkivet) provides OAI-PMH access to theses,
dissertations, and publications from 50+ Nordic universities.
"""

from __future__ import annotations

from core.entity import EntityType
from core.oai_pmh import OaiPmhRegistry
from core.registry import RegistryInfo


class DiVARegistry(OaiPmhRegistry):
    """DiVA portal OAI-PMH adapter."""

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="diva",
            domain="academic",
            base_url="https://www.diva-portal.org/smash/oai",
            auth_type="none",
            rate_limit=2.0,
            coverage="Scandinavian academic publications (50+ universities, theses, dissertations)",
            entity_types=["paper", "thesis"],
        )

    def _oai_base_url(self) -> str:
        return "https://www.diva-portal.org/smash/oai"

    def _entity_type(self) -> EntityType:
        return EntityType.PAPER

    def query_by_id(self, id_type: str, id_value: str):
        if id_type == "doi":
            # DiVA stores DOIs in metadata; try OAI identifier format
            oai_id = f"oai:DiVA.org:doi-{id_value.replace('/', '-')}"
            return super().query_by_id("oai_id", oai_id)
        if id_type == "oai_id":
            return super().query_by_id(id_type, id_value)
        return None
