"""OAI-PMH base registry adapter — shared harvesting logic for OAI-PMH sources.

OAI-PMH protocol verbs:
  - Identify: describe the repository
  - ListRecords: harvest records with optional date range + metadata prefix
  - GetRecord: single record by identifier
  - ListSets: available collections/sets

Subclasses only need to implement:
  - info() → RegistryInfo
  - _oai_base_url() → str
  - Optionally override _metadata_prefix(), _entity_type(), _parse_oai_record()
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class OaiPmhRegistry(RegistryAdapter):
    """Base class for OAI-PMH repositories.

    Default parser handles Dublin Core (oai_dc).
    Override _parse_oai_record() for custom metadata formats.
    """

    OAI_DC_NS = "http://purl.org/dc/elements/1.1/"
    OAI_NS = "http://www.openarchives.org/OAI/2.0/"

    def _oai_base_url(self) -> str:
        raise NotImplementedError

    def _metadata_prefix(self) -> str:
        return "oai_dc"

    def _entity_type(self) -> EntityType:
        return EntityType.PAPER

    def _parse_oai_record(self, metadata: ET.Element,
                          header: ET.Element) -> Optional[ICEntity]:
        """Default Dublin Core parser."""
        ns = {"dc": self.OAI_DC_NS}

        title_el = metadata.find(".//dc:title", ns)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            return None

        authors = []
        for creator in metadata.findall(".//dc:creator", ns):
            if creator.text and creator.text.strip():
                authors.append(creator.text.strip())

        year = ""
        date_el = metadata.find(".//dc:date", ns)
        if date_el is not None and date_el.text:
            m = re.match(r"(\d{4})", date_el.text.strip())
            if m:
                year = m.group(1)

        doi = ""
        urls = []
        for ident in metadata.findall(".//dc:identifier", ns):
            if ident.text:
                val = ident.text.strip()
                if val.startswith("10.") or "doi.org/" in val:
                    doi = re.sub(r"^https?://doi\.org/", "", val)
                elif val.startswith("http"):
                    urls.append(val)

        description = ""
        desc_el = metadata.find(".//dc:description", ns)
        if desc_el is not None and desc_el.text:
            description = desc_el.text.strip()[:2000]

        publisher = ""
        pub_el = metadata.find(".//dc:publisher", ns)
        if pub_el is not None and pub_el.text:
            publisher = pub_el.text.strip()

        subjects = []
        for subj in metadata.findall(".//dc:subject", ns):
            if subj.text and subj.text.strip():
                subjects.append(subj.text.strip())

        language = ""
        lang_el = metadata.find(".//dc:language", ns)
        if lang_el is not None and lang_el.text:
            language = lang_el.text.strip()

        oai_id = ""
        oai_id_el = header.find(f"{{{self.OAI_NS}}}identifier")
        if oai_id_el is not None and oai_id_el.text:
            oai_id = oai_id_el.text.strip()

        entity = ICEntity(
            entity_type=self._entity_type(),
            title=title,
            authors=authors,
            year=year,
            venue=publisher,
            metadata={
                "abstract": description,
                "subjects": subjects,
                "language": language,
                "urls": urls,
                "oai_identifier": oai_id,
            },
            source_registries=[self.info().name],
        )

        if doi:
            entity.add_external_id(self.info().name, "doi", doi)
        if oai_id:
            entity.add_external_id(self.info().name, "oai_id", oai_id)

        entity.normalize()
        return entity

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "oai_id":
            return None

        params = {
            "verb": "GetRecord",
            "identifier": id_value,
            "metadataPrefix": self._metadata_prefix(),
        }
        resp = self._get(self._oai_base_url(), params=params, timeout=30)
        if not resp:
            return None

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return None

        record = root.find(f".//{{{self.OAI_NS}}}record")
        if record is None:
            return None

        header = record.find(f"{{{self.OAI_NS}}}header")
        metadata = record.find(f"{{{self.OAI_NS}}}metadata")
        if metadata is None or header is None:
            return None

        return self._parse_oai_record(metadata, header)

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # OAI-PMH doesn't support search — only harvesting
        return []

    def harvest(self, from_date: str = "", until_date: str = "",
                set_spec: str = "", max_records: int = 100) -> list[ICEntity]:
        """Harvest records from the OAI-PMH repository.

        Args:
            from_date: ISO date (YYYY-MM-DD) start
            until_date: ISO date end
            set_spec: OAI-PMH set specification
            max_records: Maximum records to harvest
        """
        params = {
            "verb": "ListRecords",
            "metadataPrefix": self._metadata_prefix(),
        }
        if from_date:
            params["from"] = from_date
        if until_date:
            params["until"] = until_date
        if set_spec:
            params["set"] = set_spec

        results = []
        while len(results) < max_records:
            resp = self._get(self._oai_base_url(), params=params, timeout=60)
            if not resp:
                break

            try:
                root = ET.fromstring(resp.text)
            except ET.ParseError:
                break

            for record in root.findall(f".//{{{self.OAI_NS}}}record"):
                if len(results) >= max_records:
                    break
                header = record.find(f"{{{self.OAI_NS}}}header")
                if header is not None and header.get("status") == "deleted":
                    continue
                metadata = record.find(f"{{{self.OAI_NS}}}metadata")
                if metadata is None or header is None:
                    continue
                entity = self._parse_oai_record(metadata, header)
                if entity:
                    results.append(entity)

            # Resumption token for pagination
            token_el = root.find(f".//{{{self.OAI_NS}}}resumptionToken")
            if token_el is not None and token_el.text and token_el.text.strip():
                params = {
                    "verb": "ListRecords",
                    "resumptionToken": token_el.text.strip(),
                }
            else:
                break

        return results
