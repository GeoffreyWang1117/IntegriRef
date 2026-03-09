"""Federal Register registry — daily journal of the US Government.

API: https://www.federalregister.gov/api/v1
Auth: None
Rate: No documented limit, use 1 req/s
Coverage: All Federal Register documents since 1994 (rules, proposed rules, notices, presidential documents)
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class FederalRegisterRegistry(RegistryAdapter):

    BASE = "https://www.federalregister.gov/api/v1"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="federal_register",
            domain="legal",
            base_url=self.BASE,
            auth_type="none",
            rate_limit=1.0,
            coverage="All US Federal Register documents since 1994",
            entity_types=["statute", "report"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "fr_document_number":
            resp = self._get(f"{self.BASE}/documents/{id_value}.json")
            if resp:
                try:
                    return self._parse(resp.json())
                except (ValueError, KeyError):
                    pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "conditions[term]": title,
            "per_page": 5,
            "order": "relevance",
        }
        if year:
            params["conditions[publication_date][year]"] = year
        resp = self._get(f"{self.BASE}/documents.json", params=params)
        if not resp:
            return []
        try:
            results = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None

        doc_type = data.get("type", "").lower()
        entity_type = EntityType.STATUTE if "rule" in doc_type else EntityType.REPORT

        agencies = [a.get("name", "") for a in data.get("agencies", []) if a.get("name")]

        entity = ICEntity(
            entity_type=entity_type,
            title=title,
            authors=agencies,
            year=str(data.get("publication_date", ""))[:4],
            venue="Federal Register",
            metadata={
                "document_number": data.get("document_number", ""),
                "type": data.get("type", ""),
                "abstract": data.get("abstract", ""),
                "action": data.get("action", ""),
                "cfr_references": data.get("cfr_references", []),
                "page_range": f"{data.get('start_page', '')}-{data.get('end_page', '')}",
            },
            source_registries=["federal_register"],
        )
        if data.get("document_number"):
            entity.add_external_id("federal_register", "fr_document_number",
                                   data["document_number"])
        if data.get("citation"):
            entity.add_external_id("federal_register", "fr_citation", data["citation"])
        entity.normalize()
        return entity
