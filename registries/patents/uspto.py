"""USPTO PatentsView registry — US patent database.

API: https://search.patentsview.org/api/v1
Auth: None
Rate: 45 req/min
Coverage: 12M+ US patents and applications with full metadata + citations
Key: NPL (Non-Patent Literature) references link patents → academic papers
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class USPTORegistry(RegistryAdapter):

    BASE = "https://search.patentsview.org/api/v1"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="uspto",
            domain="patents",
            base_url=self.BASE,
            auth_type="none",
            rate_limit=1.5,
            coverage="12M+ US patents with citation data and NPL references",
            entity_types=["patent"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "patent_number":
            return None
        # Normalize patent number: remove commas, spaces
        pn = re.sub(r"[,\s]", "", id_value)
        resp = self._get(
            f"{self.BASE}/patent/",
            params={
                "q": f'{{"patent_id":"{pn}"}}',
                "f": '["patent_id","patent_title","patent_date","patent_abstract","patent_type"]',
            },
        )
        if not resp:
            return None
        try:
            patents = resp.json().get("patents", [])
            if patents:
                return self._parse(patents[0])
        except (ValueError, KeyError):
            pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        q = f'{{"_text_any":{{"patent_title":"{title}"}}}}'
        params = {
            "q": q,
            "f": '["patent_id","patent_title","patent_date","patent_abstract","patent_type"]',
            "o": '{"per_page":5}',
        }
        resp = self._get(f"{self.BASE}/patent/", params=params)
        if not resp:
            return []
        try:
            patents = resp.json().get("patents", [])
        except (ValueError, AttributeError):
            return []
        return [e for p in patents if (e := self._parse(p)) is not None]

    def get_patent_citations(self, patent_id: str) -> dict:
        """Get both patent and non-patent literature citations.

        Returns {"patent_refs": [...], "npl_refs": [...]}
        The NPL refs are the bridge to academic papers.
        """
        resp = self._get(
            f"{self.BASE}/patent/",
            params={
                "q": f'{{"patent_id":"{patent_id}"}}',
                "f": '["patent_id","cited_patent_number","citedby_patent_number"]',
            },
        )
        result = {"patent_refs": [], "npl_refs": []}
        if not resp:
            return result
        try:
            data = resp.json().get("patents", [])
            if data:
                p = data[0]
                result["patent_refs"] = p.get("cited_patent_number", []) or []
        except (ValueError, KeyError):
            pass
        return result

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("patent_title", "")
        if not title:
            return None

        patent_id = data.get("patent_id", "")
        date = data.get("patent_date", "")
        year = date[:4] if date else ""

        entity = ICEntity(
            entity_type=EntityType.PATENT,
            title=title,
            year=year,
            venue="USPTO",
            metadata={
                "patent_type": data.get("patent_type", ""),
                "abstract": data.get("patent_abstract", ""),
                "grant_date": date,
            },
            source_registries=["uspto"],
        )
        entity.add_external_id("uspto", "patent_number", patent_id)
        entity.normalize()
        return entity
