"""Korean Law Information Center registry (법제처 국가법령정보센터).

API: https://www.law.go.kr/DRF/lawSearch.do
Auth: None
Rate: 1 req/s
Coverage: Korean laws, presidential decrees, and ministerial ordinances
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class KoreanLawRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="korean_law",
            domain="legal",
            base_url="https://www.law.go.kr/DRF/lawSearch.do",
            auth_type="none",
            rate_limit=1.0,
            coverage="Korean laws, presidential decrees, and ministerial ordinances",
            entity_types=["statute"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "law_id":
            return None
        resp = self._get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={
                "target": "law",
                "type": "JSON",
                "query": id_value,
            },
        )
        if not resp:
            return None
        try:
            data = resp.json()
            items = data.get("LawSearch", {}).get("law", [])
        except (ValueError, AttributeError):
            return None
        if not items:
            return None
        if isinstance(items, dict):
            items = [items]
        return self._parse(items[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = title
        if year:
            query = f"{query} {year}"
        resp = self._get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={
                "target": "law",
                "type": "JSON",
                "query": query,
            },
        )
        if not resp:
            return []
        try:
            data = resp.json()
            items = data.get("LawSearch", {}).get("law", [])
        except (ValueError, AttributeError):
            return []
        if isinstance(items, dict):
            items = [items]
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        law_name = data.get("lawNm", "") or data.get("법령명한글", "")
        if not law_name:
            return None

        proclamation_date = str(data.get("proclamationDate", ""))
        year = proclamation_date[:4] if len(proclamation_date) >= 4 else proclamation_date
        law_id = str(data.get("lawId", ""))

        entity = ICEntity(
            entity_type=EntityType.STATUTE,
            title=law_name,
            authors=[],
            year=year,
            venue="Korean Law Information Center",
            metadata={
                "law_id": law_id,
                "proclamation_date": proclamation_date,
            },
            source_registries=["korean_law"],
        )

        if law_id:
            entity.add_external_id("korean_law", "law_id", law_id)

        entity.normalize()
        return entity
