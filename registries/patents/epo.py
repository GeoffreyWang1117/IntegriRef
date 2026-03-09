"""EPO Open Patent Services — European Patent Office.

API: https://ops.epo.org/3.2/rest-services
Auth: OAuth2 (free registration required at developers.epo.org)
Rate: 10 req/min (free tier), 30 req/min (registered)
Coverage: 130M+ patent documents worldwide
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class EPORegistry(RegistryAdapter):

    BASE = "https://ops.epo.org/3.2/rest-services"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="epo",
            domain="patents",
            base_url=self.BASE,
            auth_type="api_key_free",
            rate_limit=6.0,  # 10 req/min
            coverage="130M+ patent documents worldwide",
            entity_types=["patent"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "patent_number":
            return None
        # EPO expects format like EP1234567
        pn = re.sub(r"[,\s\-]", "", id_value)
        resp = self._get(
            f"{self.BASE}/published-data/publication/docdb/{pn}/biblio",
            headers={"Accept": "application/xml"},
        )
        if not resp:
            return None
        return self._parse_xml(resp.text, pn)

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # EPO OPS search via CQL
        query = f'ti="{title}"'
        if year:
            query += f" and pd={year}"
        resp = self._get(
            f"{self.BASE}/published-data/search",
            params={"q": query, "Range": "1-5"},
            headers={"Accept": "application/xml"},
        )
        if not resp:
            return []
        # Parse search results to extract publication numbers, then fetch each
        try:
            root = ET.fromstring(resp.text)
            ns = {"ops": "http://ops.epo.org", "exch": "http://www.epo.org/exchange"}
            docs = root.findall(".//exch:publication-reference/exch:document-id", ns)
            results = []
            for doc in docs[:5]:
                country = doc.findtext("exch:country", "", ns)
                doc_number = doc.findtext("exch:doc-number", "", ns)
                if country and doc_number:
                    entity = self.query_by_id("patent_number", f"{country}{doc_number}")
                    if entity:
                        results.append(entity)
            return results
        except ET.ParseError:
            return []

    def _parse_xml(self, xml_text: str, patent_id: str) -> Optional[ICEntity]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        ns = {"exch": "http://www.epo.org/exchange"}

        title = ""
        for title_el in root.findall(".//exch:invention-title", ns):
            if title_el.get("lang") == "en" or not title:
                title = (title_el.text or "").strip()

        applicants = []
        for app in root.findall(".//exch:applicant/exch:applicant-name/exch:name", ns):
            name = (app.text or "").strip()
            if name:
                applicants.append(name)

        date_el = root.find(".//exch:publication-date", ns)
        date = (date_el.text or "") if date_el is not None else ""
        year = date[:4] if len(date) >= 4 else ""

        entity = ICEntity(
            entity_type=EntityType.PATENT,
            title=title or f"Patent {patent_id}",
            authors=applicants,
            year=year,
            venue="EPO",
            metadata={
                "publication_date": date,
            },
            source_registries=["epo"],
        )
        entity.add_external_id("epo", "patent_number", patent_id)
        entity.normalize()
        return entity
