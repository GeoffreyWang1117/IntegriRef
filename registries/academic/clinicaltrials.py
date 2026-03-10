"""ClinicalTrials.gov registry — clinical trial registrations.

API: https://clinicaltrials.gov/api/v2
Auth: None
Rate: 1.0s (10 req/s)
Coverage: 530K+ clinical trials
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class ClinicalTrialsRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="clinicaltrials",
            domain="academic",
            base_url="https://clinicaltrials.gov/api/v2",
            auth_type="none",
            rate_limit=1.0,
            coverage="530K+ clinical trials",
            entity_types=["dataset"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "nct_id":
            return None
        nct_id = id_value.strip().upper()
        if not nct_id.startswith("NCT"):
            nct_id = f"NCT{nct_id}"

        resp = self._get(f"https://clinicaltrials.gov/api/v2/studies/{nct_id}")
        if not resp:
            return None
        try:
            return self._parse(resp.json())
        except (ValueError, KeyError):
            return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        params = {
            "query.titles": title,
            "pageSize": 5,
        }
        resp = self._get(
            "https://clinicaltrials.gov/api/v2/studies",
            params=params,
        )
        if not resp:
            return []
        try:
            studies = resp.json().get("studies", [])
        except (ValueError, AttributeError):
            return []
        return [e for s in studies if (e := self._parse(s)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        protocol = data.get("protocolSection", {})
        ident_module = protocol.get("identificationModule", {})
        status_module = protocol.get("statusModule", {})
        desc_module = protocol.get("descriptionModule", {})
        design_module = protocol.get("designModule", {})
        conditions_module = protocol.get("conditionsModule", {})
        sponsor_module = protocol.get("sponsorCollaboratorsModule", {})

        nct_id = ident_module.get("nctId", "")
        title = ident_module.get("officialTitle", "") or ident_module.get("briefTitle", "")
        if not title:
            return None

        # Use lead sponsor as "author"
        authors = []
        lead_sponsor = sponsor_module.get("leadSponsor", {})
        if lead_sponsor.get("name"):
            authors.append(lead_sponsor["name"])

        year = ""
        start_date = status_module.get("startDateStruct", {})
        if start_date.get("date"):
            m = re.match(r"(\d{4})", start_date["date"])
            if m:
                year = m.group(1)

        phases = design_module.get("phases", [])
        conditions = conditions_module.get("conditions", [])

        entity = ICEntity(
            entity_type=EntityType.DATASET,
            title=title,
            authors=authors,
            year=year,
            venue="ClinicalTrials.gov",
            metadata={
                "nct_id": nct_id,
                "overall_status": status_module.get("overallStatus", ""),
                "conditions": conditions,
                "brief_summary": desc_module.get("briefSummary", ""),
                "study_type": design_module.get("studyType", ""),
                "phases": phases,
                "enrollment": design_module.get("enrollmentInfo", {}).get("count"),
            },
            source_registries=["clinicaltrials"],
        )

        if nct_id:
            entity.add_external_id("clinicaltrials", "nct_id", nct_id)

        entity.normalize()
        return entity
