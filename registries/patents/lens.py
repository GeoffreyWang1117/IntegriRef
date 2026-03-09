"""Lens.org registry — unified scholarly + patent search.

API: https://api.lens.org
Auth: Free API key (registration required, generous quota)
Rate: 10 req/min (free), 30 req/min (institutional)
Coverage: 250M+ scholarly works + 160M+ patent records
Key feature: bridges academic and patent worlds in one API
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class LensRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="lens",
            domain="patents",
            base_url="https://api.lens.org",
            auth_type="api_key_free",
            rate_limit=6.0,
            coverage="250M+ scholarly + 160M+ patents, unified search",
            entity_types=["paper", "patent"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            return self._search_scholarly({"query": {"match": {"doi": id_value}}, "size": 1})
        if id_type == "patent_number":
            return self._search_patent(id_value)
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        domain = kwargs.get("domain", "scholarly")
        if domain == "patent":
            return self._search_patents_by_title(title)

        query = {
            "query": {"match": {"title": title}},
            "size": 5,
        }
        result = self._search_scholarly(query, multi=True)
        return result if isinstance(result, list) else ([result] if result else [])

    def _search_scholarly(self, body: dict, multi: bool = False):
        resp = self._post(
            "https://api.lens.org/scholarly/search",
            json=body,
            headers={"Accept": "application/json"},
        )
        if not resp:
            return [] if multi else None
        try:
            data = resp.json().get("data", [])
        except (ValueError, AttributeError):
            return [] if multi else None
        entities = [e for d in data if (e := self._parse_scholarly(d)) is not None]
        if multi:
            return entities
        return entities[0] if entities else None

    def _search_patent(self, patent_number: str) -> Optional[ICEntity]:
        resp = self._post(
            "https://api.lens.org/patent/search",
            json={
                "query": {"match": {"doc_number": patent_number}},
                "size": 1,
            },
            headers={"Accept": "application/json"},
        )
        if not resp:
            return None
        try:
            data = resp.json().get("data", [])
            if data:
                return self._parse_patent(data[0])
        except (ValueError, KeyError):
            pass
        return None

    def _search_patents_by_title(self, title: str) -> list[ICEntity]:
        resp = self._post(
            "https://api.lens.org/patent/search",
            json={"query": {"match": {"title": title}}, "size": 5},
            headers={"Accept": "application/json"},
        )
        if not resp:
            return []
        try:
            data = resp.json().get("data", [])
        except (ValueError, AttributeError):
            return []
        return [e for d in data if (e := self._parse_patent(d)) is not None]

    def _parse_scholarly(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None
        authors = [a.get("display_name", "") for a in data.get("authors", []) if a.get("display_name")]
        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=str(data.get("year_published", "")),
            venue=data.get("source", {}).get("title", ""),
            metadata={
                "citing_patents_count": data.get("citing_patent_count", 0),
                "references_count": data.get("references_count", 0),
            },
            source_registries=["lens"],
        )
        for eid in data.get("external_ids", []):
            if eid.get("type") == "doi":
                entity.add_external_id("lens", "doi", eid["value"])
            elif eid.get("type") == "pmid":
                entity.add_external_id("lens", "pmid", eid["value"])
        entity.normalize()
        return entity

    def _parse_patent(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None
        applicants = [a.get("extracted_name", {}).get("value", "")
                      for a in data.get("applicants", [])
                      if a.get("extracted_name", {}).get("value")]
        entity = ICEntity(
            entity_type=EntityType.PATENT,
            title=title,
            authors=applicants,
            year=str(data.get("date_published", ""))[:4],
            venue=data.get("jurisdiction", ""),
            metadata={
                "doc_number": data.get("doc_number", ""),
                "kind": data.get("kind", ""),
                "legal_status": data.get("legal_status", {}),
                "npl_citation_count": data.get("npl_citation_count", 0),
            },
            source_registries=["lens"],
        )
        if data.get("doc_number"):
            entity.add_external_id("lens", "patent_number", data["doc_number"])
        entity.normalize()
        return entity
