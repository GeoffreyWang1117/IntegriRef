"""SEC EDGAR registry — US Securities and Exchange Commission filings.

API: https://efts.sec.gov/LATEST/search-index
     https://data.sec.gov
Auth: None (User-Agent with contact required)
Rate: 10 req/s
Coverage: All SEC filings (10-K, 10-Q, 8-K, S-1, proxy, etc.) since 1993
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class EDGARRegistry(RegistryAdapter):

    def __init__(self):
        super().__init__()
        self._session.headers.update({
            "User-Agent": "IntegriRef/1.0 integriref@example.com",
        })

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="edgar",
            domain="financial",
            base_url="https://efts.sec.gov/LATEST",
            auth_type="none",
            rate_limit=0.2,
            coverage="All SEC filings since 1993 (10-K, 10-Q, 8-K, S-1, etc.)",
            entity_types=["filing"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "accession_number":
            # SEC expects accession number format: 0000000000-00-000000
            an = id_value.replace("-", "")
            cik = an[:10]
            resp = self._get(
                f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json",
            )
            if not resp:
                return None
            try:
                data = resp.json()
                return self._parse_company(data, id_value)
            except (ValueError, KeyError):
                return None
        if id_type == "cik":
            resp = self._get(
                f"https://data.sec.gov/submissions/CIK{id_value.zfill(10)}.json",
            )
            if resp:
                try:
                    return self._parse_company(resp.json())
                except (ValueError, KeyError):
                    pass
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "q": title,
            "dateRange": "custom",
            "startdt": f"{year}-01-01" if year else "1993-01-01",
            "enddt": f"{year}-12-31" if year else "2030-12-31",
        }
        resp = self._get("https://efts.sec.gov/LATEST/search-index", params=params)
        if not resp:
            return []
        try:
            hits = resp.json().get("hits", {}).get("hits", [])
        except (ValueError, AttributeError):
            return []
        return [e for h in hits[:5] if (e := self._parse_hit(h)) is not None]

    def _parse_company(self, data: dict, accession: str = "") -> Optional[ICEntity]:
        name = data.get("name", "")
        cik = str(data.get("cik", ""))
        entity = ICEntity(
            entity_type=EntityType.FILING,
            title=f"{name} SEC Filing" + (f" ({accession})" if accession else ""),
            authors=[name],
            venue="SEC EDGAR",
            metadata={
                "company": name,
                "sic": data.get("sic", ""),
                "sic_description": data.get("sicDescription", ""),
                "tickers": data.get("tickers", []),
                "exchanges": data.get("exchanges", []),
            },
            source_registries=["edgar"],
        )
        entity.add_external_id("edgar", "cik", cik)
        if accession:
            entity.add_external_id("edgar", "accession_number", accession)
        entity.normalize()
        return entity

    def _parse_hit(self, hit: dict) -> Optional[ICEntity]:
        src = hit.get("_source", {})
        title = src.get("display_names", [""])[0] if src.get("display_names") else ""
        if not title:
            title = src.get("file_description", "")
        if not title:
            return None
        entity = ICEntity(
            entity_type=EntityType.FILING,
            title=title,
            year=str(src.get("file_date", ""))[:4],
            venue="SEC EDGAR",
            metadata={
                "form_type": src.get("form_type", ""),
                "file_date": src.get("file_date", ""),
            },
            source_registries=["edgar"],
        )
        if src.get("file_num"):
            entity.add_external_id("edgar", "file_number", src["file_num"])
        entity.normalize()
        return entity
