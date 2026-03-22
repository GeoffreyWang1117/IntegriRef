"""J-STAGE registry — Japan Science and Technology Agency.

API: https://api.jstage.jst.go.jp/searchapi/do
Auth: None
Rate: 1 req/s
Coverage: Japanese scientific and technical journals
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class JStageRegistry(RegistryAdapter):

    # XML namespace used in J-STAGE API responses
    _NS = {
        "atom": "http://www.w3.org/2005/Atom",
        "prism": "http://prismstandard.org/namespaces/basic/2.0/",
    }

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="jstage",
            domain="academic",
            base_url="https://api.jstage.jst.go.jp",
            auth_type="none",
            rate_limit=1.0,
            coverage="Japanese scientific and technical journal articles",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        resp = self._get(
            "https://api.jstage.jst.go.jp/searchapi/do",
            params={"doi": id_value, "count": 1},
        )
        if not resp:
            return None
        entries = self._parse_xml(resp.text)
        return entries[0] if entries else None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"article": title, "count": 5}
        if author:
            params["creator"] = author
        if year:
            params["pubyearfrom"] = year
            params["pubyearto"] = year
        if kwargs.get("journal"):
            params["pubtitle"] = kwargs["journal"]
        if kwargs.get("cdjournal"):
            params["cdjournal"] = kwargs["cdjournal"]

        resp = self._get(
            "https://api.jstage.jst.go.jp/searchapi/do",
            params=params,
        )
        if not resp:
            return []
        return self._parse_xml(resp.text)

    def _parse_xml(self, xml_text: str) -> list[ICEntity]:
        """Parse J-STAGE XML response into a list of ICEntity objects."""
        results: list[ICEntity] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        for entry in root.findall("atom:entry", self._NS):
            entity = self._parse_entry(entry)
            if entity:
                results.append(entity)
        return results

    def _parse_entry(self, entry: ET.Element) -> Optional[ICEntity]:
        title_el = entry.find("atom:title", self._NS)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            # Fallback: look for article_title element without namespace
            title_el = entry.find("article_title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            return None

        authors = []
        for author_el in entry.findall("atom:author", self._NS):
            name_el = author_el.find("atom:name", self._NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())
        # Fallback: creator elements
        if not authors:
            for creator_el in entry.findall("creator"):
                if creator_el.text:
                    authors.append(creator_el.text.strip())

        year_el = entry.find("pubyear")
        year = year_el.text.strip() if year_el is not None and year_el.text else ""

        cdjournal_el = entry.find("cdjournal")
        venue = cdjournal_el.text.strip() if cdjournal_el is not None and cdjournal_el.text else ""

        # Try prism:publicationName as alternative venue
        if not venue:
            pub_el = entry.find("prism:publicationName", self._NS)
            venue = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

        doi_el = entry.find("doi")
        doi = doi_el.text.strip() if doi_el is not None and doi_el.text else ""

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={},
            source_registries=["jstage"],
        )
        if doi:
            entity.add_external_id("jstage", "doi", doi)

        entity.normalize()
        return entity
