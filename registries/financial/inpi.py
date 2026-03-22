"""INPI registry — French patent/trademark office.

API: https://data.inpi.fr/api/
Auth: None
Rate: 1 req/s
Coverage: French patents and trademarks (Institut National de la Propriété Industrielle)
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class INPIRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="inpi",
            domain="financial",
            base_url="https://data.inpi.fr/api",
            auth_type="none",
            rate_limit=1.0,
            coverage="French patents and trademarks (Institut National de la Propriété Industrielle)",
            entity_types=["patent"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "patent_number":
            return None
        resp = self._get(
            "https://data.inpi.fr/api/search",
            params={"q": id_value},
        )
        if not resp:
            return None
        try:
            items = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return None
        if not items:
            return None
        return self._parse(items[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        query = title
        if author:
            query = f"{query} {author}"
        if year:
            query = f"{query} {year}"
        resp = self._get(
            "https://data.inpi.fr/api/search",
            params={"q": query},
        )
        if not resp:
            return []
        try:
            items = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        if not title:
            return None

        applicant = data.get("applicant", "")
        authors = [applicant.strip()] if isinstance(applicant, str) and applicant.strip() else []

        pub_date = data.get("publicationDate", "")
        year = str(pub_date)[:4] if pub_date and len(str(pub_date)) >= 4 else str(pub_date)

        pub_number = data.get("publicationNumber", "")

        entity = ICEntity(
            entity_type=EntityType.PATENT,
            title=title,
            authors=authors,
            year=year,
            venue="INPI (France)",
            metadata={
                "publication_number": pub_number,
                "publication_date": pub_date,
                "applicant": applicant,
            },
            source_registries=["inpi"],
        )

        if pub_number:
            entity.add_external_id("inpi", "patent_number", pub_number)

        entity.normalize()
        return entity
