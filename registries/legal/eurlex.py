"""EUR-Lex registry — European Union law.

API: https://eur-lex.europa.eu/eurlex-ws (SOAP) + SPARQL endpoint
We use the SPARQL endpoint for structured queries and REST search for text.
Auth: None
Rate: Best-effort, use 2s between requests
Coverage: 2M+ EU legal documents (legislation, case law, treaties)
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class EURLexRegistry(RegistryAdapter):

    SPARQL_URL = "https://publications.europa.eu/webapi/rdf/sparql"
    SEARCH_URL = "https://eur-lex.europa.eu/search.html"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="eurlex",
            domain="legal",
            base_url=self.SPARQL_URL,
            auth_type="none",
            rate_limit=2.0,
            coverage="2M+ EU legal documents (legislation, case law, treaties)",
            entity_types=["statute", "case"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "celex":
            return None
        # SPARQL query by CELEX number
        query = f"""
        SELECT ?title ?date WHERE {{
          <http://publications.europa.eu/resource/celex/{id_value}> cdm:resource_legal_title ?title .
          OPTIONAL {{ <http://publications.europa.eu/resource/celex/{id_value}> cdm:work_date_document ?date }}
          FILTER(lang(?title) = 'en')
        }} LIMIT 1
        """
        resp = self._get(
            self.SPARQL_URL,
            params={"query": query, "format": "application/json"},
        )
        if not resp:
            return None
        try:
            results = resp.json().get("results", {}).get("bindings", [])
            if not results:
                return None
            r = results[0]
            entity = ICEntity(
                entity_type=EntityType.STATUTE,
                title=r.get("title", {}).get("value", ""),
                year=r.get("date", {}).get("value", "")[:4],
                venue="European Union",
                source_registries=["eurlex"],
            )
            entity.add_external_id("eurlex", "celex", id_value)
            entity.normalize()
            return entity
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # SPARQL full-text search
        query = f"""
        SELECT ?celex ?title ?date WHERE {{
          ?doc cdm:resource_legal_title ?title .
          ?doc cdm:resource_legal_id_celex ?celex .
          OPTIONAL {{ ?doc cdm:work_date_document ?date }}
          FILTER(lang(?title) = 'en')
          FILTER(contains(lcase(?title), lcase("{title[:80]}")))
        }} LIMIT 5
        """
        resp = self._get(
            self.SPARQL_URL,
            params={"query": query, "format": "application/json"},
        )
        if not resp:
            return []
        try:
            bindings = resp.json().get("results", {}).get("bindings", [])
        except (ValueError, AttributeError):
            return []
        entities = []
        for r in bindings:
            entity = ICEntity(
                entity_type=EntityType.STATUTE,
                title=r.get("title", {}).get("value", ""),
                year=r.get("date", {}).get("value", "")[:4],
                venue="European Union",
                source_registries=["eurlex"],
            )
            celex = r.get("celex", {}).get("value", "")
            if celex:
                entity.add_external_id("eurlex", "celex", celex)
            entity.normalize()
            entities.append(entity)
        return entities
