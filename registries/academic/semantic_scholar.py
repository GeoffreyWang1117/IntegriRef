"""Semantic Scholar registry — AI-powered academic search.

API: https://api.semanticscholar.org/graph/v1
Auth: None (or free API key for higher rate)
Rate: 1 req/s without key, 10 req/s with key
Coverage: 200M+ papers across all disciplines
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class SemanticScholarRegistry(RegistryAdapter):

    FIELDS = "title,authors,year,venue,citationCount,isOpenAccess,externalIds,abstract,referenceCount,fieldsOfStudy"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="semantic_scholar",
            domain="academic",
            base_url="https://api.semanticscholar.org/graph/v1",
            auth_type="none",
            rate_limit=1.0,
            coverage="200M+ papers, all disciplines",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        # S2 accepts DOI, arXiv, PMID, CorpusID, etc.
        prefix_map = {
            "doi": "DOI:",
            "arxiv": "ArXiv:",
            "pmid": "PMID:",
            "s2_id": "",
        }
        prefix = prefix_map.get(id_type)
        if prefix is None:
            return None
        paper_id = f"{prefix}{id_value}"
        resp = self._get(
            f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}",
            params={"fields": self.FIELDS},
        )
        if not resp:
            return None
        try:
            return self._parse(resp.json())
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "query": title,
            "limit": 10,
            "fields": self.FIELDS,
        }
        if year:
            params["year"] = year
        resp = self._get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params=params,
        )
        if not resp:
            return []
        try:
            papers = resp.json().get("data", [])
        except (ValueError, AttributeError):
            return []
        return [e for p in papers if (e := self._parse(p)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None

        authors = [a.get("name", "") for a in data.get("authors", [])]
        ext_ids = data.get("externalIds", {}) or {}

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=str(data["year"]) if data.get("year") else "",
            venue=data.get("venue", ""),
            metadata={
                "citation_count": data.get("citationCount"),
                "reference_count": data.get("referenceCount"),
                "is_open_access": data.get("isOpenAccess"),
                "fields_of_study": data.get("fieldsOfStudy", []),
                "abstract": data.get("abstract", ""),
            },
            source_registries=["semantic_scholar"],
        )

        if ext_ids.get("DOI"):
            entity.add_external_id("semantic_scholar", "doi", ext_ids["DOI"])
        if ext_ids.get("ArXiv"):
            entity.add_external_id("semantic_scholar", "arxiv", ext_ids["ArXiv"])
        if ext_ids.get("PubMed"):
            entity.add_external_id("semantic_scholar", "pmid", ext_ids["PubMed"])
        if ext_ids.get("CorpusId"):
            entity.add_external_id("semantic_scholar", "s2_id", str(ext_ids["CorpusId"]))

        entity.normalize()
        return entity
