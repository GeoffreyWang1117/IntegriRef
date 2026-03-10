"""INSPIRE-HEP registry — high-energy physics literature.

API: https://inspirehep.net/api
Auth: None
Rate: 3.0s (15 req/5s)
Coverage: 1.7M high-energy physics papers
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class INSPIRERegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="inspire_hep",
            domain="academic",
            base_url="https://inspirehep.net/api",
            auth_type="none",
            rate_limit=3.0,
            coverage="1.7M high-energy physics papers",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            resp = self._get(f"https://inspirehep.net/api/doi/{id_value}")
        elif id_type == "arxiv":
            resp = self._get(f"https://inspirehep.net/api/arxiv/{id_value}")
        else:
            return None

        if not resp:
            return None
        try:
            data = resp.json()
            metadata = data.get("metadata", data)
            return self._parse(metadata)
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = f't "{title}"'
        if author:
            query += f' and a "{author}"'
        if year:
            query += f" and de {year}"
        resp = self._get(
            "https://inspirehep.net/api/literature",
            params={"q": query, "size": 5, "sort": "mostrecent"},
        )
        if not resp:
            return []
        try:
            hits = resp.json().get("hits", {}).get("hits", [])
        except (ValueError, AttributeError):
            return []
        results = []
        for hit in hits:
            metadata = hit.get("metadata", {})
            entity = self._parse(metadata)
            if entity:
                results.append(entity)
        return results

    def _parse(self, data: dict) -> Optional[ICEntity]:
        titles = data.get("titles", [])
        title = titles[0].get("title", "") if titles else ""
        if not title:
            return None

        authors = []
        for a in data.get("authors", []):
            name = a.get("full_name", "")
            if name:
                authors.append(name)

        year = ""
        earliest = data.get("earliest_date", "")
        if earliest:
            year = earliest[:4]

        venue = ""
        pub_info = data.get("publication_info", [])
        if pub_info:
            venue = pub_info[0].get("journal_title", "")

        abstract = ""
        abstracts = data.get("abstracts", [])
        if abstracts:
            abstract = abstracts[0].get("value", "")

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors[:50],  # Cap authors for HEP papers with 1000+ authors
            year=year,
            venue=venue,
            metadata={
                "abstract": abstract,
                "citation_count": data.get("citation_count", 0),
                "texkeys": data.get("texkeys", []),
                "inspire_categories": [
                    c.get("term", "") for c in data.get("inspire_categories", [])
                ],
                "document_type": data.get("document_type", []),
            },
            source_registries=["inspire_hep"],
        )

        # External IDs
        for doi_entry in data.get("dois", []):
            doi = doi_entry.get("value", "")
            if doi:
                entity.add_external_id("inspire_hep", "doi", doi)

        arxiv_eprints = data.get("arxiv_eprints", [])
        if arxiv_eprints:
            entity.add_external_id("inspire_hep", "arxiv", arxiv_eprints[0].get("value", ""))

        control_number = data.get("control_number")
        if control_number:
            entity.add_external_id("inspire_hep", "inspire_id", str(control_number))

        entity.normalize()
        return entity
