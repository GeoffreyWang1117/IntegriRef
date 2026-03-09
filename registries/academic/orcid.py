"""ORCID registry — author identity disambiguation.

API: https://pub.orcid.org/v3.0
Auth: None (public API)
Rate: 24 req/s (public), 40 req/s (member)
Coverage: 19M+ researcher profiles with linked works
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class ORCIDRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="orcid",
            domain="academic",
            base_url="https://pub.orcid.org/v3.0",
            auth_type="none",
            rate_limit=0.5,
            coverage="19M+ researcher profiles",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "orcid":
            return None
        resp = self._get(
            f"https://pub.orcid.org/v3.0/{id_value}/record",
            headers={"Accept": "application/json"},
        )
        if not resp:
            return None
        # ORCID returns author profile, not a paper entity
        # Used for author disambiguation, not direct paper lookup
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # ORCID search is author-centric; useful for disambiguation
        if not author:
            return []
        resp = self._get(
            "https://pub.orcid.org/v3.0/search/",
            params={"q": f"family-name:{author.split()[-1]}"},
            headers={"Accept": "application/json"},
        )
        if not resp:
            return []
        # Returns ORCID profiles, not papers
        return []

    def resolve_author_orcid(self, name: str) -> Optional[str]:
        """Find the ORCID iD for an author name."""
        surname = name.split()[-1] if name.split() else name
        given = name.split()[0] if len(name.split()) > 1 else ""
        q = f"family-name:{surname}"
        if given:
            q += f" AND given-names:{given}"
        resp = self._get(
            "https://pub.orcid.org/v3.0/search/",
            params={"q": q, "rows": 3},
            headers={"Accept": "application/json"},
        )
        if not resp:
            return None
        try:
            results = resp.json().get("result", [])
            if results:
                return results[0].get("orcid-identifier", {}).get("path")
        except (ValueError, KeyError, IndexError):
            pass
        return None
