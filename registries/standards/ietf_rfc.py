"""IETF Datatracker — Request for Comments (RFC) documents.

API: https://datatracker.ietf.org/api/v1
Auth: None
Rate: No documented limit, use 1 req/s
Coverage: 9,000+ RFCs + 10,000+ Internet-Drafts
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class IETFRFCRegistry(RegistryAdapter):

    BASE = "https://datatracker.ietf.org/api/v1"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="ietf_rfc",
            domain="standards",
            base_url=self.BASE,
            auth_type="none",
            rate_limit=1.0,
            coverage="9,000+ RFCs + 10,000+ Internet-Drafts",
            entity_types=["standard"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "rfc_number":
            return None
        # Normalize: "RFC 8446" → "rfc8446"
        rfc_name = re.sub(r"\s+", "", id_value.lower())
        if not rfc_name.startswith("rfc"):
            rfc_name = f"rfc{rfc_name}"

        resp = self._get(
            f"{self.BASE}/doc/document/{rfc_name}/",
            headers={"Accept": "application/json"},
        )
        if not resp:
            return None
        try:
            return self._parse(resp.json())
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "title__contains": title,
            "type": "rfc",
            "format": "json",
            "limit": 5,
        }
        resp = self._get(f"{self.BASE}/doc/document/", params=params)
        if not resp:
            return []
        try:
            results = resp.json().get("objects", [])
        except (ValueError, AttributeError):
            return []
        return [e for r in results if (e := self._parse(r)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        title = data.get("title", "")
        name = data.get("name", "")
        if not title:
            return None

        rfc_number = name.upper() if name.startswith("rfc") else name

        # Extract authors from author list URI (would need additional API call)
        # For now, use the stream info
        entity = ICEntity(
            entity_type=EntityType.STANDARD,
            title=f"{rfc_number}: {title}" if rfc_number else title,
            year=str(data.get("time", ""))[:4],
            venue="IETF",
            metadata={
                "stream": data.get("stream", ""),
                "status": data.get("std_level", ""),
                "pages": data.get("pages", 0),
                "abstract": data.get("abstract", ""),
            },
            source_registries=["ietf_rfc"],
        )
        entity.add_external_id("ietf_rfc", "rfc_number", rfc_number)
        entity.normalize()
        return entity
