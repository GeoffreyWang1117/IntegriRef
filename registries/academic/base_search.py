"""BASE (Bielefeld Academic Search Engine) — aggregated academic search.

BASE indexes 300+ million documents from 10,000+ content providers.
Provides OAI-PMH interface for metadata harvesting and a search API.
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.oai_pmh import OaiPmhRegistry
from core.registry import RegistryInfo


class BASERegistry(OaiPmhRegistry):
    """BASE OAI-PMH adapter with search API support."""

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="base",
            domain="academic",
            base_url="https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
            auth_type="none",
            rate_limit=1.0,
            coverage="300M+ academic documents from 10,000+ providers worldwide",
            entity_types=["paper", "thesis", "dataset"],
        )

    def _oai_base_url(self) -> str:
        return "https://oai.base-search.net/oai"

    def _entity_type(self) -> EntityType:
        return EntityType.PAPER

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "doi":
            return self._search_by_doi(id_value)
        if id_type == "oai_id":
            return super().query_by_id(id_type, id_value)
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        """Search BASE via its HTTP search API."""
        if not title:
            return []

        query_parts = [f'dctitle:"{title}"']
        if author:
            query_parts.append(f"dccreator:{author}")
        if year:
            query_parts.append(f"dcyear:{year}")

        params = {
            "func": "PerformSearch",
            "query": " AND ".join(query_parts),
            "format": "json",
            "hits": 5,
        }

        resp = self._get(self.info().base_url, params=params, timeout=15)
        if not resp:
            return []

        try:
            data = resp.json()
        except (ValueError, AttributeError):
            return []

        results = []
        response = data.get("response", {})
        docs = response.get("docs", [])

        for doc in docs[:5]:
            entity = self._parse_base_doc(doc)
            if entity:
                results.append(entity)

        return results

    def _search_by_doi(self, doi: str) -> Optional[ICEntity]:
        """Search BASE by DOI."""
        params = {
            "func": "PerformSearch",
            "query": f"dcdoi:{doi}",
            "format": "json",
            "hits": 1,
        }

        resp = self._get(self.info().base_url, params=params, timeout=15)
        if not resp:
            return None

        try:
            data = resp.json()
        except (ValueError, AttributeError):
            return None

        docs = data.get("response", {}).get("docs", [])
        if not docs:
            return None

        return self._parse_base_doc(docs[0])

    def _parse_base_doc(self, doc: dict) -> Optional[ICEntity]:
        """Parse a BASE search result document."""
        title = doc.get("dctitle", "")
        if isinstance(title, list):
            title = title[0] if title else ""
        if not title:
            return None

        authors = doc.get("dccreator", [])
        if isinstance(authors, str):
            authors = [authors]

        year = ""
        dcyear = doc.get("dcyear", "")
        if dcyear:
            if isinstance(dcyear, list):
                dcyear = dcyear[0]
            m = re.match(r"(\d{4})", str(dcyear))
            if m:
                year = m.group(1)

        doi = doc.get("dcdoi", "") or ""
        if isinstance(doi, list):
            doi = doi[0] if doi else ""

        venue = doc.get("dcpublisher", "") or ""
        if isinstance(venue, list):
            venue = venue[0] if venue else ""

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            source_registries=["base"],
        )

        if doi:
            entity.add_external_id("base", "doi", doi)

        entity.normalize()
        return entity
