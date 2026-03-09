"""Europe PMC registry — European biomedical literature.

API: https://www.ebi.ac.uk/europepmc/webservices/rest
Auth: None
Rate: ~10 req/s
Coverage: 43M+ abstracts, 9M+ full-text OA articles
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class EuropePMCRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="europe_pmc",
            domain="academic",
            base_url="https://www.ebi.ac.uk/europepmc/webservices/rest",
            auth_type="none",
            rate_limit=0.5,
            coverage="43M+ abstracts, 9M+ full-text OA",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "pmid":
            query = f"EXT_ID:{id_value} AND SRC:MED"
        elif id_type == "doi":
            query = f'DOI:"{id_value}"'
        elif id_type == "pmc":
            query = f"PMCID:{id_value}"
        else:
            return None
        results = self._do_search(query, page_size=1)
        return results[0] if results else None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = f'TITLE:"{title}"'
        if author:
            query += f' AND AUTH:"{author.split()[-1]}"'
        return self._do_search(query, page_size=5)

    def _do_search(self, query: str, page_size: int = 5) -> list[ICEntity]:
        resp = self._get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "pageSize": page_size,
                    "resultType": "core", "format": "json"},
        )
        if not resp:
            return []
        try:
            results = resp.json().get("resultList", {}).get("result", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None
        authors = []
        for a in data.get("authorList", {}).get("author", []):
            name = a.get("fullName", "")
            if name:
                authors.append(name)

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=str(data.get("pubYear", "")),
            venue=data.get("journalTitle", ""),
            metadata={
                "abstract": data.get("abstractText", ""),
                "cited_by_count": data.get("citedByCount"),
                "is_open_access": data.get("isOpenAccess") == "Y",
            },
            source_registries=["europe_pmc"],
        )
        if data.get("doi"):
            entity.add_external_id("europe_pmc", "doi", data["doi"])
        if data.get("pmid"):
            entity.add_external_id("europe_pmc", "pmid", data["pmid"])
        if data.get("pmcid"):
            entity.add_external_id("europe_pmc", "pmc", data["pmcid"])
        entity.normalize()
        return entity
