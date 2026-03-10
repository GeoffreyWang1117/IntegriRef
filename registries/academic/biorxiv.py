"""bioRxiv/medRxiv registry — preprint servers for biology and medicine.

API: https://api.biorxiv.org
Auth: None
Rate: 1.0s
Coverage: 250K+ bio/med preprints
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class BioRxivRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="biorxiv",
            domain="academic",
            base_url="https://api.biorxiv.org",
            auth_type="none",
            rate_limit=1.0,
            coverage="250K+ bio/med preprints",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "doi":
            return None
        # bioRxiv DOIs start with 10.1101/
        doi = id_value.strip()
        # Try bioRxiv details endpoint
        resp = self._get(
            f"https://api.biorxiv.org/details/biorxiv/{doi}",
        )
        if resp:
            try:
                collection = resp.json().get("collection", [])
                if collection:
                    return self._parse(collection[0])
            except (ValueError, KeyError, IndexError):
                pass
        # Try medRxiv
        resp = self._get(
            f"https://api.biorxiv.org/details/medrxiv/{doi}",
        )
        if resp:
            try:
                collection = resp.json().get("collection", [])
                if collection:
                    return self._parse(collection[0])
            except (ValueError, KeyError, IndexError):
                pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # bioRxiv API doesn't support title search directly
        # Return empty — discovery will use other registries for search
        return []

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "").strip()
        if not title:
            return None

        # Authors come as "Smith, J.; Jones, A. B."
        authors = []
        author_str = data.get("authors", "")
        if author_str:
            for a in author_str.split(";"):
                a = a.strip()
                if a:
                    authors.append(a)

        year = ""
        date = data.get("date", "")
        if date:
            m = re.match(r"(\d{4})", date)
            if m:
                year = m.group(1)

        biorxiv_doi = data.get("biorxiv_doi", "") or data.get("medrxiv_doi", "")
        published_doi = data.get("published", "") or ""

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue="bioRxiv" if data.get("biorxiv_doi") else "medRxiv",
            metadata={
                "category": data.get("category", ""),
                "preprint_date": date,
                "published_doi": published_doi if published_doi != "NA" else "",
                "abstract": data.get("abstract", ""),
                "server": data.get("server", "biorxiv"),
                "version": data.get("version", ""),
            },
            source_registries=["biorxiv"],
        )

        if biorxiv_doi:
            entity.add_external_id("biorxiv", "doi", biorxiv_doi)
        if published_doi and published_doi != "NA":
            entity.add_external_id("biorxiv", "published_doi", published_doi)

        entity.normalize()
        return entity
