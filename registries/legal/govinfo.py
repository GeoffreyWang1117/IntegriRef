"""GovInfo registry — US Government Publishing Office.

API: https://api.govinfo.gov
Auth: Free API key (register at api.data.gov)
Rate: 1,000 req/hour
Coverage: US Congress bills, public laws, Federal Register, CFR, congressional reports
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class GovInfoRegistry(RegistryAdapter):

    BASE = "https://api.govinfo.gov"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="govinfo",
            domain="legal",
            base_url=self.BASE,
            auth_type="api_key_free",
            rate_limit=3.6,
            coverage="US public laws, bills, CFR, Federal Register",
            entity_types=["statute", "report"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "govinfo_package":
            resp = self._get(f"{self.BASE}/packages/{id_value}/summary",
                             params={"api_key": "DEMO_KEY"})
        elif id_type == "public_law":
            return self._search_one(f'"{id_value}"')
        else:
            return None

        if not resp:
            return None
        try:
            return self._parse(resp.json())
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "query": title,
            "pageSize": 5,
            "api_key": "DEMO_KEY",
        }
        if year:
            params["publishDateStartDate"] = f"{year}-01-01"
            params["publishDateEndDate"] = f"{year}-12-31"
        resp = self._get(f"{self.BASE}/search", params=params)
        if not resp:
            return []
        try:
            results = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def _search_one(self, query: str) -> Optional[ICEntity]:
        results = self.search(query)
        return results[0] if results else None

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None

        # Determine entity type
        doc_class = data.get("docClass", "").lower()
        if "law" in doc_class or "plaw" in data.get("packageId", "").lower():
            entity_type = EntityType.STATUTE
        else:
            entity_type = EntityType.REPORT

        entity = ICEntity(
            entity_type=entity_type,
            title=title,
            year=str(data.get("dateIssued", ""))[:4],
            venue="US Government",
            metadata={
                "collection": data.get("collectionName", ""),
                "doc_class": data.get("docClass", ""),
                "government_author": data.get("governmentAuthor1", ""),
                "su_doc_class": data.get("suDocClassNumber", ""),
            },
            source_registries=["govinfo"],
        )
        if data.get("packageId"):
            entity.add_external_id("govinfo", "govinfo_package", data["packageId"])
        entity.normalize()
        return entity
