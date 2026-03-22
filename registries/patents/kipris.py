"""KIPRIS registry — Korean Intellectual Property Rights Information Service.

API: https://plus.kipris.or.kr/openapi/rest/
Auth: API key (KIPRIS_API_KEY env var)
Rate: 1 req/s
Coverage: Korean patents, utility models, designs, and trademarks
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class KiprisRegistry(RegistryAdapter):

    BASE_URL = "https://plus.kipris.or.kr/openapi/rest"

    def __init__(self):
        super().__init__()
        self._api_key = os.environ.get("KIPRIS_API_KEY", "")

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="kipris",
            domain="patents",
            base_url=self.BASE_URL,
            auth_type="api_key",
            rate_limit=1.0,
            coverage="Korean patents, utility models, designs, and trademarks",
            entity_types=["patent"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "patent_number":
            return None
        if not self._api_key:
            return None
        resp = self._get(
            f"{self.BASE_URL}/patUtiModInfoSearchSevice/applicationNumberSearchInfo",
            params={
                "applicationNumber": id_value,
                "accessKey": self._api_key,
            },
        )
        if not resp:
            return None
        items = self._parse_xml(resp.text)
        return items[0] if items else None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        if not self._api_key:
            return []
        resp = self._get(
            f"{self.BASE_URL}/patUtiModInfoSearchSevice/freeSearchInfo",
            params={
                "word": title,
                "patent": "true",
                "numOfRows": 5,
                "accessKey": self._api_key,
            },
        )
        if not resp:
            return []
        return self._parse_xml(resp.text)

    def _parse_xml(self, xml_text: str) -> list[ICEntity]:
        """Parse KIPRIS XML response into a list of ICEntity objects."""
        results = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        for item in root.iter("item"):
            entity = self._parse_item(item)
            if entity is not None:
                results.append(entity)
        return results

    def _parse_item(self, item: ET.Element) -> Optional[ICEntity]:
        title = self._text(item, "inventionTitle")
        if not title:
            return None

        applicant = self._text(item, "applicantName")
        app_date = self._text(item, "applicationDate")
        app_number = self._text(item, "applicationNumber")

        year = ""
        if app_date and len(app_date) >= 4:
            year = app_date[:4]

        authors = [applicant] if applicant else []

        entity = ICEntity(
            entity_type=EntityType.PATENT,
            title=title,
            authors=authors,
            year=year,
            venue="KIPRIS",
            metadata={
                "applicant": applicant,
                "application_date": app_date,
                "application_number": app_number,
                "jurisdiction": "South Korea",
            },
            source_registries=["kipris"],
        )

        if app_number:
            entity.add_external_id("kipris", "patent_number", app_number)

        registration_number = self._text(item, "registrationNumber")
        if registration_number:
            entity.add_external_id("kipris", "registration_number",
                                   registration_number)

        entity.normalize()
        return entity

    @staticmethod
    def _text(element: ET.Element, tag: str) -> str:
        """Safely extract text from an XML sub-element."""
        el = element.find(tag)
        if el is not None and el.text:
            return el.text.strip()
        return ""
