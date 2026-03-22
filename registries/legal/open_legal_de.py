"""Open Legal Data registry — German court decisions.

API: https://de.openlegaldata.io/api/v1/
Auth: None
Rate: 1 req/s
Coverage: German court decisions and case law
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class OpenLegalDeRegistry(RegistryAdapter):

    BASE_URL = "https://de.openlegaldata.io/api/v1"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="open_legal_de",
            domain="legal",
            base_url=self.BASE_URL,
            auth_type="none",
            rate_limit=1.0,
            coverage="German court decisions and case law",
            entity_types=["case"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "case_cite":
            return None
        # Search by Aktenzeichen (file number)
        resp = self._get(
            f"{self.BASE_URL}/cases/",
            params={"file_number": id_value},
            headers={"Accept": "application/json"},
        )
        if not resp:
            return None
        try:
            data = resp.json()
            results = data.get("results", [])
        except (ValueError, AttributeError):
            return None
        if not results:
            return None
        return self._parse(results[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {"search": title, "limit": 5}
        resp = self._get(
            f"{self.BASE_URL}/cases/",
            params=params,
            headers={"Accept": "application/json"},
        )
        if not resp:
            return []
        try:
            data = resp.json()
            results = data.get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for item in results if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        name = data.get("name", "")
        if not name:
            return None

        date = data.get("date", "")
        year = date[:4] if len(date) >= 4 else ""

        court_data = data.get("court", {})
        court_name = ""
        if isinstance(court_data, dict):
            court_name = court_data.get("name", "")
        elif isinstance(court_data, str):
            court_name = court_data

        file_number = data.get("file_number", "")

        entity = ICEntity(
            entity_type=EntityType.CASE,
            title=name,
            authors=[],
            year=year,
            venue=court_name,
            metadata={
                "court": court_name,
                "file_number": file_number,
                "date": date,
                "jurisdiction": "Germany",
            },
            source_registries=["open_legal_de"],
        )

        case_id = data.get("id", "")
        if case_id:
            entity.add_external_id("open_legal_de", "old_id", str(case_id))

        if file_number:
            entity.add_external_id("open_legal_de", "case_cite", file_number)

        entity.normalize()
        return entity
