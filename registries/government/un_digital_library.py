"""UN Digital Library — United Nations document repository.

API: OAI-PMH at https://digitallibrary.un.org/oai2d
     + SRU search at https://digitallibrary.un.org/api
Auth: None
Rate: Best-effort, use 2s between requests
Coverage: 1M+ UN documents (resolutions, reports, treaties)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class UNDigitalLibraryRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="un_digital_library",
            domain="government",
            base_url="https://digitallibrary.un.org",
            auth_type="none",
            rate_limit=2.0,
            coverage="1M+ UN documents (resolutions, reports, treaties)",
            entity_types=["report", "statute"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "un_doc_symbol":
            return self._search_one(id_value)
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # Use SRU search
        query = f'title="{title}"'
        resp = self._get(
            "https://digitallibrary.un.org/api/v1/search",
            params={"q": title, "ln": "en", "rg": 5, "of": "xm"},
        )
        if not resp:
            return []
        return self._parse_marcxml(resp.text)

    def _search_one(self, symbol: str) -> Optional[ICEntity]:
        results = self.search(symbol)
        return results[0] if results else None

    def _parse_marcxml(self, xml_text: str) -> list[ICEntity]:
        entities = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        ns = {"marc": "http://www.loc.gov/MARC21/slim"}
        for record in root.findall(".//marc:record", ns):
            title = ""
            year = ""
            for df in record.findall("marc:datafield", ns):
                tag = df.get("tag", "")
                if tag == "245":
                    for sf in df.findall("marc:subfield", ns):
                        if sf.get("code") == "a":
                            title = (sf.text or "").strip()
                elif tag == "260" or tag == "264":
                    for sf in df.findall("marc:subfield", ns):
                        if sf.get("code") == "c":
                            year = (sf.text or "").strip()[:4]

            if title:
                entity = ICEntity(
                    entity_type=EntityType.REPORT,
                    title=title,
                    year=year,
                    venue="United Nations",
                    source_registries=["un_digital_library"],
                )
                entity.normalize()
                entities.append(entity)
        return entities
