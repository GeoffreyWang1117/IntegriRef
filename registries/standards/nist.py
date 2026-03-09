"""NIST registries — National Institute of Standards and Technology.

Covers two APIs:
1. NVD (National Vulnerability Database): https://services.nvd.nist.gov/rest/json
2. NIST CSRC (Computer Security Resource Center): publications

Auth: Free API key for NVD (higher rate), None for CSRC
Rate: NVD 5 req/30s without key, 50 req/30s with key
Coverage: 250K+ CVEs, 1,000+ NIST special publications
"""

from __future__ import annotations

from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class NISTRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="nist",
            domain="standards",
            base_url="https://services.nvd.nist.gov/rest/json",
            auth_type="api_key_free",
            rate_limit=6.0,
            coverage="250K+ CVEs, 1,000+ NIST special publications",
            entity_types=["standard"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "cve":
            return self._query_cve(id_value)
        if id_type == "nist_sp":
            return self._query_publication(id_value)
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # Search NVD by keyword
        resp = self._get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"keywordSearch": title, "resultsPerPage": 5},
        )
        if not resp:
            return []
        try:
            vulns = resp.json().get("vulnerabilities", [])
        except (ValueError, AttributeError):
            return []
        return [e for v in vulns if (e := self._parse_cve(v)) is not None]

    def _query_cve(self, cve_id: str) -> Optional[ICEntity]:
        resp = self._get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"cveId": cve_id},
        )
        if not resp:
            return None
        try:
            vulns = resp.json().get("vulnerabilities", [])
            if vulns:
                return self._parse_cve(vulns[0])
        except (ValueError, KeyError):
            pass
        return None

    def _query_publication(self, sp_number: str) -> Optional[ICEntity]:
        """Look up a NIST Special Publication by number (e.g. 'SP 800-53')."""
        # NIST CSRC doesn't have a clean JSON API, construct a basic entity
        entity = ICEntity(
            entity_type=EntityType.STANDARD,
            title=f"NIST {sp_number}",
            venue="NIST",
            metadata={"document_type": "special_publication"},
            source_registries=["nist"],
        )
        entity.add_external_id("nist", "nist_sp", sp_number)
        entity.normalize()
        return entity

    def _parse_cve(self, data: dict) -> Optional[ICEntity]:
        cve = data.get("cve", {})
        cve_id = cve.get("id", "")

        descriptions = cve.get("descriptions", [])
        title = ""
        for d in descriptions:
            if d.get("lang") == "en":
                title = d.get("value", "")[:200]
                break

        published = cve.get("published", "")
        year = published[:4] if published else ""

        entity = ICEntity(
            entity_type=EntityType.STANDARD,
            title=f"{cve_id}: {title}" if title else cve_id,
            year=year,
            venue="NIST NVD",
            metadata={
                "cvss_score": cve.get("metrics", {}).get("cvssMetricV31", [{}])[0]
                    .get("cvssData", {}).get("baseScore") if cve.get("metrics") else None,
                "severity": cve.get("metrics", {}).get("cvssMetricV31", [{}])[0]
                    .get("cvssData", {}).get("baseSeverity") if cve.get("metrics") else None,
            },
            source_registries=["nist"],
        )
        entity.add_external_id("nist", "cve", cve_id)
        entity.normalize()
        return entity
