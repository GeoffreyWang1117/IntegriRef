"""Shodhganga — Indian theses and dissertations repository.

Shodhganga (शोधगंगा) is the reservoir of Indian theses maintained by
INFLIBNET Centre, University Grants Commission. Contains 500,000+ theses.
"""

from __future__ import annotations

from core.entity import EntityType
from core.oai_pmh import OaiPmhRegistry
from core.registry import RegistryInfo


class ShodhgangaRegistry(OaiPmhRegistry):
    """Shodhganga OAI-PMH adapter."""

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="shodhganga",
            domain="academic",
            base_url="https://shodhganga.inflibnet.ac.in/oai/request",
            auth_type="none",
            rate_limit=2.0,
            coverage="Indian doctoral theses and dissertations (500,000+ records)",
            entity_types=["thesis"],
        )

    def _oai_base_url(self) -> str:
        return "https://shodhganga.inflibnet.ac.in/oai/request"

    def _entity_type(self) -> EntityType:
        return EntityType.THESIS

    def query_by_id(self, id_type: str, id_value: str):
        if id_type == "oai_id":
            return super().query_by_id(id_type, id_value)
        return None
