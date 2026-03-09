"""Wikidata registry — universal knowledge base with structured data.

API: https://www.wikidata.org/w/api.php (MediaWiki API)
     + SPARQL: https://query.wikidata.org/sparql
Auth: None
Rate: ~5 req/s recommended
Coverage: 110M+ items — papers, patents, laws, standards, people, organizations
Key: Universal bridge — Wikidata stores DOIs, patent numbers, case citations,
     ISO numbers, etc. as structured properties. Ideal for cross-domain entity linking.
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class WikidataRegistry(RegistryAdapter):

    SPARQL_URL = "https://query.wikidata.org/sparql"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="wikidata",
            domain="standards",
            base_url="https://www.wikidata.org/w/api.php",
            auth_type="none",
            rate_limit=1.0,
            coverage="110M+ items — universal cross-domain bridge",
            entity_types=["paper", "patent", "case", "statute", "standard", "book"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        # Map our ID types to Wikidata properties
        prop_map = {
            "doi": "P356",
            "pmid": "P698",
            "arxiv": "P818",
            "patent_number": "P1246",
            "iso_number": "P503",
            "rfc_number": "P892",
            "wikidata": None,
        }
        if id_type not in prop_map:
            return None

        if id_type == "wikidata":
            return self._fetch_entity(id_value)

        prop = prop_map[id_type]
        query = f"""
        SELECT ?item ?itemLabel WHERE {{
          ?item wdt:{prop} "{id_value}" .
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
        }} LIMIT 1
        """
        resp = self._get(self.SPARQL_URL,
                         params={"query": query, "format": "json"})
        if not resp:
            return None
        try:
            bindings = resp.json().get("results", {}).get("bindings", [])
            if bindings:
                qid = bindings[0]["item"]["value"].split("/")[-1]
                return self._fetch_entity(qid)
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        resp = self._get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities",
                "search": title,
                "language": "en",
                "limit": 5,
                "format": "json",
            },
        )
        if not resp:
            return []
        try:
            results = resp.json().get("search", [])
        except (ValueError, AttributeError):
            return []
        entities = []
        for r in results:
            entity = ICEntity(
                entity_type=EntityType.OTHER,
                title=r.get("label", ""),
                metadata={"description": r.get("description", "")},
                source_registries=["wikidata"],
            )
            entity.add_external_id("wikidata", "wikidata", r.get("id", ""))
            entity.normalize()
            entities.append(entity)
        return entities

    def _fetch_entity(self, qid: str) -> Optional[ICEntity]:
        resp = self._get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": qid,
                "format": "json",
                "languages": "en",
                "props": "labels|descriptions|claims",
            },
        )
        if not resp:
            return None
        try:
            data = resp.json().get("entities", {}).get(qid, {})
        except (ValueError, KeyError):
            return None

        title = data.get("labels", {}).get("en", {}).get("value", "")
        desc = data.get("descriptions", {}).get("en", {}).get("value", "")

        entity = ICEntity(
            entity_type=EntityType.OTHER,
            title=title,
            metadata={"description": desc},
            source_registries=["wikidata"],
        )
        entity.add_external_id("wikidata", "wikidata", qid)

        # Extract known external IDs from claims
        claims = data.get("claims", {})
        claim_map = {
            "P356": "doi", "P698": "pmid", "P818": "arxiv",
            "P1246": "patent_number", "P503": "iso_number",
        }
        for prop, id_type in claim_map.items():
            if prop in claims:
                for claim in claims[prop]:
                    val = claim.get("mainsnak", {}).get("datavalue", {}).get("value", "")
                    if val:
                        entity.add_external_id("wikidata", id_type, str(val))

        entity.normalize()
        return entity
