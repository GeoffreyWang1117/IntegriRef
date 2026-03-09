"""CourtListener registry — US federal and state case law.

API: https://www.courtlistener.com/api/rest/v4
Auth: Free API key (registration required)
Rate: 5,000 req/day (free tier)
Coverage: 10M+ court opinions, all federal courts + many state courts
Key: cited_by and citing relationships for citation graph
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class CourtListenerRegistry(RegistryAdapter):

    BASE = "https://www.courtlistener.com/api/rest/v4"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="courtlistener",
            domain="legal",
            base_url=self.BASE,
            auth_type="api_key_free",
            rate_limit=1.0,
            coverage="10M+ US court opinions with citation graph",
            entity_types=["case"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "courtlistener_id":
            resp = self._get(f"{self.BASE}/opinions/{id_value}/")
            if resp:
                try:
                    return self._parse_opinion(resp.json())
                except (ValueError, KeyError):
                    pass
        elif id_type == "case_cite":
            return self._search_citation(id_value)
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"q": title, "type": "o"}  # opinions
        if year:
            params["filed_after"] = f"{year}-01-01"
            params["filed_before"] = f"{year}-12-31"
        resp = self._get(f"{self.BASE}/search/", params=params)
        if not resp:
            return []
        try:
            results = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse_search(r)) is not None]

    def get_cited_opinions(self, opinion_id: str) -> list[str]:
        """Get opinions cited BY this opinion — forward citation graph."""
        resp = self._get(f"{self.BASE}/opinions/{opinion_id}/cited/")
        if not resp:
            return []
        try:
            return [str(r.get("id", "")) for r in resp.json().get("results", [])]
        except (ValueError, AttributeError):
            return []

    def _search_citation(self, cite: str) -> Optional[ICEntity]:
        """Search by standard legal citation (e.g. '410 U.S. 113')."""
        resp = self._get(f"{self.BASE}/search/", params={"q": f'citation:"{cite}"', "type": "o"})
        if not resp:
            return None
        try:
            results = resp.json().get("results", [])
            if results:
                return self._parse_search(results[0])
        except (ValueError, KeyError):
            pass
        return None

    def _parse_opinion(self, data: dict) -> Optional[ICEntity]:
        cluster = data.get("cluster", "")
        case_name = data.get("case_name", "") or data.get("caseName", "")
        entity = ICEntity(
            entity_type=EntityType.CASE,
            title=case_name,
            year=str(data.get("date_filed", ""))[:4],
            venue=data.get("court", ""),
            metadata={
                "date_filed": data.get("date_filed", ""),
                "judges": data.get("judges", ""),
                "type": data.get("type", ""),
            },
            source_registries=["courtlistener"],
        )
        entity.add_external_id("courtlistener", "courtlistener_id", str(data.get("id", "")))
        entity.normalize()
        return entity

    def _parse_search(self, data: dict) -> Optional[ICEntity]:
        case_name = data.get("caseName", "") or data.get("case_name", "")
        if not case_name:
            return None
        entity = ICEntity(
            entity_type=EntityType.CASE,
            title=case_name,
            year=str(data.get("dateFiled", ""))[:4],
            venue=data.get("court", ""),
            metadata={
                "date_filed": data.get("dateFiled", ""),
                "citation_count": data.get("citeCount", 0),
                "court_citation": data.get("citation", []),
            },
            source_registries=["courtlistener"],
        )
        if data.get("id"):
            entity.add_external_id("courtlistener", "courtlistener_id", str(data["id"]))
        for cite in data.get("citation", []):
            if cite:
                entity.add_external_id("courtlistener", "case_cite", cite)
        entity.normalize()
        return entity
