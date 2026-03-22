"""e-Gov Japan registry — Japanese law database (e-Gov法令検索API).

API: https://elaws.e-gov.go.jp/api/1/
Auth: None
Rate: 2 req/s (conservative)
Coverage: Japanese laws, cabinet orders, ministerial ordinances
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class EGovJapanRegistry(RegistryAdapter):

    BASE_URL = "https://elaws.e-gov.go.jp/api/1"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="egov_japan",
            domain="government",
            base_url=self.BASE_URL,
            auth_type="none",
            rate_limit=2.0,
            coverage="Japanese laws, cabinet orders, ministerial ordinances",
            entity_types=["statute"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "law_number":
            return None
        resp = self._get(
            f"{self.BASE_URL}/lawdata/{id_value}",
        )
        if not resp:
            return None
        return self._parse_lawdata(resp.text, id_value=id_value)

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        resp = self._get(
            f"{self.BASE_URL}/lawlists/1",
        )
        if not resp:
            return []
        return self._parse_lawlists(resp.text, query=title)

    def _parse_lawdata(self, xml_text: str,
                       id_value: str = "") -> Optional[ICEntity]:
        """Parse a single law document from the lawdata endpoint."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        result_code = self._text(root, ".//Code")
        if result_code and result_code != "0":
            return None

        law_name = (
            self._text(root, ".//LawName")
            or self._text(root, ".//LawBody/LawTitle")
        )
        if not law_name:
            return None

        law_no = self._text(root, ".//LawNo") or id_value
        promulgate_date = self._text(root, ".//PromulgateDate")

        year = ""
        if promulgate_date and len(promulgate_date) >= 4:
            year = promulgate_date[:4]

        entity = ICEntity(
            entity_type=EntityType.STATUTE,
            title=law_name,
            authors=[],
            year=year,
            venue="e-Gov Japan",
            metadata={
                "law_number": law_no,
                "promulgate_date": promulgate_date,
                "jurisdiction": "Japan",
            },
            source_registries=["egov_japan"],
        )

        if law_no:
            entity.add_external_id("egov_japan", "law_number", law_no)

        entity.normalize()
        return entity

    def _parse_lawlists(self, xml_text: str,
                        query: str = "") -> list[ICEntity]:
        """Parse the lawlists endpoint and filter by query string."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        query_lower = query.lower()
        results = []

        for law_info in root.iter("LawNameListInfo"):
            law_name = self._text(law_info, "LawName")
            if not law_name:
                continue

            # Filter: only include laws whose name contains the query
            if query_lower and query_lower not in law_name.lower():
                continue

            law_no = self._text(law_info, "LawNo")
            promulgate_date = self._text(law_info, "PromulgateDate")

            year = ""
            if promulgate_date and len(promulgate_date) >= 4:
                year = promulgate_date[:4]

            entity = ICEntity(
                entity_type=EntityType.STATUTE,
                title=law_name,
                authors=[],
                year=year,
                venue="e-Gov Japan",
                metadata={
                    "law_number": law_no,
                    "promulgate_date": promulgate_date,
                    "jurisdiction": "Japan",
                },
                source_registries=["egov_japan"],
            )

            if law_no:
                entity.add_external_id("egov_japan", "law_number", law_no)

            entity.normalize()
            results.append(entity)

            if len(results) >= 5:
                break

        return results

    @staticmethod
    def _text(element: ET.Element, path: str) -> str:
        """Safely extract text from an XML element by path."""
        el = element.find(path)
        if el is not None and el.text:
            return el.text.strip()
        return ""
