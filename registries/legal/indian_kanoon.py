"""Indian Kanoon registry — Indian case law database.

API: https://api.indiankanoon.org/search/
Auth: API key (INDIAN_KANOON_TOKEN env var)
Rate: 1 req/s
Coverage: Indian court decisions and judgments
"""

from __future__ import annotations

import os
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class IndianKanoonRegistry(RegistryAdapter):

    BASE_URL = "https://api.indiankanoon.org"

    def __init__(self):
        super().__init__()
        self._token = os.environ.get("INDIAN_KANOON_TOKEN", "")
        if self._token:
            self._session.headers.update(
                {"Authorization": f"Token {self._token}"}
            )

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="indian_kanoon",
            domain="legal",
            base_url=self.BASE_URL,
            auth_type="api_key",
            rate_limit=1.0,
            coverage="Indian court decisions and judgments",
            entity_types=["case"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "case_cite":
            return None
        # Search by citation to locate the document
        results = self.search(title=id_value)
        return results[0] if results else None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"q": title, "pagenum": 0}
        resp = self._get(
            f"{self.BASE_URL}/search/",
            params=params,
            headers={"Accept": "application/json"},
        )
        if not resp:
            return []
        try:
            data = resp.json()
            docs = data.get("docs", [])
        except (ValueError, AttributeError):
            return []
        results = []
        for doc in docs[:5]:
            entity = self._parse(doc)
            if entity is not None:
                results.append(entity)
        return results

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None

        publish_date = data.get("publishdate", "")
        year = ""
        if publish_date:
            # publishdate may be in various formats; extract the year
            parts = publish_date.strip().split("-")
            for part in parts:
                part = part.strip()
                if len(part) == 4 and part.isdigit():
                    year = part
                    break

        docsource = data.get("docsource", "")

        entity = ICEntity(
            entity_type=EntityType.CASE,
            title=title,
            authors=[],
            year=year,
            venue=docsource,
            metadata={
                "docsource": docsource,
                "publish_date": publish_date,
                "jurisdiction": "India",
            },
            source_registries=["indian_kanoon"],
        )

        tid = data.get("tid", "")
        if tid:
            entity.add_external_id("indian_kanoon", "tid", str(tid))

        # Use the title as a case citation identifier
        entity.add_external_id("indian_kanoon", "case_cite", title)

        entity.normalize()
        return entity
