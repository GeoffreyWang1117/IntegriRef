"""e-Stat registry — Japanese government statistics.

API: https://api.e-stat.go.jp/rest/3.0/app/json/
Auth: API key (ESTAT_API_KEY env var), parameter appId
Rate: 1 req/s
Coverage: Japanese government statistics (all ministries and agencies)
"""

from __future__ import annotations

import os
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class EStatJapanRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="estat_japan",
            domain="government",
            base_url="https://api.e-stat.go.jp/rest/3.0/app/json",
            auth_type="api_key_free",
            rate_limit=1.0,
            coverage="Japanese government statistics (all ministries and agencies)",
            entity_types=["dataset"],
        )

    def _api_key(self) -> Optional[str]:
        return os.environ.get("ESTAT_API_KEY")

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "stat_id":
            return None
        api_key = self._api_key()
        if not api_key:
            return None
        resp = self._get(
            "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList",
            params={
                "appId": api_key,
                "statsCode": id_value,
            },
        )
        if not resp:
            return None
        try:
            result = resp.json().get("GET_STATS_LIST", {})
            datalist = result.get("DATALIST_INF", {})
            items = datalist.get("TABLE_INF", [])
        except (ValueError, AttributeError):
            return None
        if not items:
            return None
        if isinstance(items, dict):
            items = [items]
        return self._parse(items[0])

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        api_key = self._api_key()
        if not api_key:
            return []
        params = {
            "appId": api_key,
            "searchWord": title,
        }
        if year:
            params["surveyYears"] = year
        resp = self._get(
            "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList",
            params=params,
        )
        if not resp:
            return []
        try:
            result = resp.json().get("GET_STATS_LIST", {})
            datalist = result.get("DATALIST_INF", {})
            items = datalist.get("TABLE_INF", [])
        except (ValueError, AttributeError):
            return []
        if isinstance(items, dict):
            items = [items]
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict) -> Optional[ICEntity]:
        # STATISTICS_NAME can be a dict with $ key or a plain string
        stat_name_raw = data.get("STATISTICS_NAME", "")
        if isinstance(stat_name_raw, dict):
            stat_name = stat_name_raw.get("$", "")
        else:
            stat_name = str(stat_name_raw)

        title_raw = data.get("TITLE", "")
        if isinstance(title_raw, dict):
            title = title_raw.get("$", "")
        else:
            title = str(title_raw)

        combined_title = f"{stat_name}: {title}" if stat_name and title else (stat_name or title)
        if not combined_title:
            return None

        survey_date = str(data.get("SURVEY_DATE", ""))
        year = survey_date[:4] if len(survey_date) >= 4 else survey_date
        stat_id = str(data.get("@id", ""))

        entity = ICEntity(
            entity_type=EntityType.DATASET,
            title=combined_title,
            authors=[],
            year=year,
            venue="e-Stat (Japan)",
            metadata={
                "statistics_name": stat_name,
                "survey_date": survey_date,
                "stat_id": stat_id,
            },
            source_registries=["estat_japan"],
        )

        if stat_id:
            entity.add_external_id("estat_japan", "stat_id", stat_id)

        entity.normalize()
        return entity
