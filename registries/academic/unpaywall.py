"""Unpaywall registry — open access availability checker.

API: https://api.unpaywall.org/v2
Auth: Email required as parameter (free)
Rate: 100K req/day
Coverage: 50M+ DOI-based works with OA status
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class UnpaywallRegistry(RegistryAdapter):

    EMAIL = "integriref@example.com"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="unpaywall",
            domain="academic",
            base_url="https://api.unpaywall.org/v2",
            auth_type="none",
            rate_limit=0.5,
            coverage="50M+ works, OA availability checking",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        resp = self._get(
            f"https://api.unpaywall.org/v2/{id_value}",
            params={"email": self.EMAIL},
        )
        if not resp:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return self._parse(data)

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # Unpaywall only supports DOI lookup, no title search
        return []

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None
        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in data.get("z_authors", []) or []
            if a.get("family")
        ]
        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=str(data["year"]) if data.get("year") else "",
            venue=data.get("journal_name", ""),
            metadata={
                "is_oa": data.get("is_oa", False),
                "oa_status": data.get("oa_status", ""),
                "best_oa_url": (data.get("best_oa_location") or {}).get("url", ""),
            },
            source_registries=["unpaywall"],
        )
        entity.add_external_id("unpaywall", "doi", data.get("doi", ""))
        entity.normalize()
        return entity
