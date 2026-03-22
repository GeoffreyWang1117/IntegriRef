"""KCI (Korea Citation Index) registry — Korean scholarly database.

API: https://open.kci.go.kr/po/openapi/openApiSearch.kci
Auth: API key (env KCI_API_KEY)
Rate: 3.0 req/s (government API)
Coverage: Korean academic journal articles indexed by NRF
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class KCIRegistry(RegistryAdapter):

    _BASE_URL = "https://open.kci.go.kr/po/openapi/openApiSearch.kci"

    def __init__(self):
        super().__init__()
        self._api_key = os.environ.get("KCI_API_KEY", "")

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="kci",
            domain="academic",
            base_url="https://open.kci.go.kr",
            auth_type="api_key",
            rate_limit=3.0,
            coverage="Korean academic journal articles indexed by NRF",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        params = {
            "key": self._api_key,
            "doi": id_value,
        }
        resp = self._get(self._BASE_URL, params=params)
        if not resp:
            return None
        entries = self._parse_xml(resp.text)
        return entries[0] if entries else None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "key": self._api_key,
            "title": title,
        }
        if author:
            params["author"] = author
        if year:
            params["pubYear"] = year

        resp = self._get(self._BASE_URL, params=params)
        if not resp:
            return []
        return self._parse_xml(resp.text)

    def _parse_xml(self, xml_text: str) -> list[ICEntity]:
        """Parse KCI XML response into a list of ICEntity objects."""
        results: list[ICEntity] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        for record in root.iter("record"):
            entity = self._parse_record(record)
            if entity:
                results.append(entity)
        return results

    def _parse_record(self, record: ET.Element) -> Optional[ICEntity]:
        title_el = record.find("articleTitle")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            return None

        authors = []
        for author_el in record.iter("authorName"):
            if author_el.text and author_el.text.strip():
                authors.append(author_el.text.strip())

        year_el = record.find("pubYear")
        year = year_el.text.strip() if year_el is not None and year_el.text else ""

        journal_el = record.find("journalTitle")
        venue = journal_el.text.strip() if journal_el is not None and journal_el.text else ""

        doi_el = record.find("doi")
        doi = doi_el.text.strip() if doi_el is not None and doi_el.text else ""

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={},
            source_registries=["kci"],
        )
        if doi:
            entity.add_external_id("kci", "doi", doi)

        entity.normalize()
        return entity
