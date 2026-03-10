"""NASA ADS registry — astrophysics and physics literature.

API: https://api.adsabs.harvard.edu/v1
Auth: Bearer token required (free registration)
Rate: 1.0s
Coverage: 16M+ astrophysics/physics records
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo

try:
    from config import NASA_ADS_TOKEN
except ImportError:
    NASA_ADS_TOKEN = ""


class NASAADSRegistry(RegistryAdapter):

    FIELDS = "title,author,year,bibcode,doi,citation_count,abstract,pub,arxiv_class,doctype,identifier"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="nasa_ads",
            domain="academic",
            base_url="https://api.adsabs.harvard.edu/v1",
            auth_type="api_key_free",
            rate_limit=1.0,
            coverage="16M+ astrophysics/physics records",
            entity_types=["paper"],
        )

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if NASA_ADS_TOKEN:
            headers["Authorization"] = f"Bearer {NASA_ADS_TOKEN}"
        return headers

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if not NASA_ADS_TOKEN:
            return None

        if id_type == "doi":
            query = f'doi:"{id_value}"'
        elif id_type == "arxiv":
            query = f'arxiv:"{id_value}"'
        elif id_type == "bibcode":
            query = f'bibcode:"{id_value}"'
        else:
            return None

        resp = self._get(
            "https://api.adsabs.harvard.edu/v1/search/query",
            params={"q": query, "fl": self.FIELDS, "rows": 1},
            headers=self._headers(),
        )
        if not resp:
            return None
        try:
            docs = resp.json().get("response", {}).get("docs", [])
            if docs:
                return self._parse(docs[0])
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        if not NASA_ADS_TOKEN:
            return []

        query = f'title:"{title}"'
        if author:
            query += f' author:"{author}"'
        if year:
            query += f" year:{year}"

        resp = self._get(
            "https://api.adsabs.harvard.edu/v1/search/query",
            params={"q": query, "fl": self.FIELDS, "rows": 5, "sort": "score desc"},
            headers=self._headers(),
        )
        if not resp:
            return []
        try:
            docs = resp.json().get("response", {}).get("docs", [])
        except (ValueError, AttributeError):
            return []
        return [e for d in docs if (e := self._parse(d)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        titles = data.get("title", [])
        title = titles[0] if titles else ""
        if not title:
            return None

        authors = data.get("author", [])
        year = str(data.get("year", ""))
        bibcode = data.get("bibcode", "")

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=data.get("pub", ""),
            metadata={
                "abstract": data.get("abstract", ""),
                "citation_count": data.get("citation_count", 0),
                "bibcode": bibcode,
                "arxiv_class": data.get("arxiv_class", []),
                "doctype": data.get("doctype", ""),
            },
            source_registries=["nasa_ads"],
        )

        dois = data.get("doi", [])
        if dois:
            entity.add_external_id("nasa_ads", "doi", dois[0])

        if bibcode:
            entity.add_external_id("nasa_ads", "bibcode", bibcode)

        # Extract arxiv from identifiers
        for ident in data.get("identifier", []):
            if ident and ("arxiv" in ident.lower() or "/" in ident):
                arxiv_id = ident.replace("arXiv:", "").strip()
                entity.add_external_id("nasa_ads", "arxiv", arxiv_id)
                break

        entity.normalize()
        return entity
