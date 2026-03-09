"""OpenAlex registry — fully open scientific knowledge graph.

API: https://api.openalex.org
Auth: None (mailto for polite pool)
Rate: 10 req/s (polite pool), 100K req/day
Coverage: 250M+ works, replaces Microsoft Academic Graph
Key feature: full citation graph data — essential for graph modeling
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class OpenAlexRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="openalex",
            domain="academic",
            base_url="https://api.openalex.org",
            auth_type="none",
            rate_limit=0.5,
            coverage="250M+ works, full citation graph, all disciplines",
            entity_types=["paper", "book", "dataset"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            url = f"https://api.openalex.org/works/doi:{id_value}"
        elif id_type == "openalex":
            url = f"https://api.openalex.org/works/{id_value}"
        elif id_type == "pmid":
            url = f"https://api.openalex.org/works/pmid:{id_value}"
        else:
            return None
        resp = self._get(url, params={"mailto": "integriref@example.com"})
        if not resp:
            return None
        try:
            return self._parse(resp.json())
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "search": title,
            "per_page": 5,
            "mailto": "integriref@example.com",
        }
        if year:
            params["filter"] = f"publication_year:{year}"
        resp = self._get("https://api.openalex.org/works", params=params)
        if not resp:
            return []
        try:
            results = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def get_references(self, openalex_id: str) -> list[str]:
        """Get referenced work IDs — essential for citation graph building."""
        resp = self._get(
            f"https://api.openalex.org/works/{openalex_id}",
            params={"select": "referenced_works", "mailto": "integriref@example.com"},
        )
        if not resp:
            return []
        try:
            return resp.json().get("referenced_works", [])
        except (ValueError, AttributeError):
            return []

    def get_citing_works(self, openalex_id: str, per_page: int = 50) -> list[str]:
        """Get works that cite this work — reverse citation graph."""
        resp = self._get(
            "https://api.openalex.org/works",
            params={
                "filter": f"cites:{openalex_id}",
                "per_page": per_page,
                "select": "id",
                "mailto": "integriref@example.com",
            },
        )
        if not resp:
            return []
        try:
            return [r["id"] for r in resp.json().get("results", [])]
        except (ValueError, KeyError):
            return []

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "") or ""
        if not title:
            return None

        authors = []
        for a in data.get("authorships", []):
            name = a.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)

        year = str(data["publication_year"]) if data.get("publication_year") else ""

        venue = ""
        primary_loc = data.get("primary_location", {}) or {}
        source = primary_loc.get("source", {}) or {}
        venue = source.get("display_name", "")

        doi = data.get("doi", "")
        if doi and doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={
                "cited_by_count": data.get("cited_by_count"),
                "is_retracted": data.get("is_retracted", False),
                "is_oa": data.get("open_access", {}).get("is_oa", False),
                "type": data.get("type", ""),
                "concepts": [c.get("display_name", "") for c in data.get("concepts", [])[:5]],
                "referenced_works_count": len(data.get("referenced_works", [])),
            },
            source_registries=["openalex"],
        )
        if doi:
            entity.add_external_id("openalex", "doi", doi)
        oa_id = data.get("id", "")
        if oa_id:
            entity.add_external_id("openalex", "openalex", oa_id)

        # Extract cross-registry IDs from OpenAlex
        ids = data.get("ids", {})
        if ids.get("pmid"):
            pmid = ids["pmid"].replace("https://pubmed.ncbi.nlm.nih.gov/", "")
            entity.add_external_id("openalex", "pmid", pmid)

        entity.normalize()
        return entity
