"""Legifrance registry — French law database.

API: https://api.legifrance.gouv.fr/
Auth: OAuth2 (requires LEGIFRANCE_CLIENT_ID and LEGIFRANCE_CLIENT_SECRET env vars)
Rate: 1 req/s
Coverage: French legislation, codes, decrees, and legal texts
"""

from __future__ import annotations

import os
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class LegifranceRegistry(RegistryAdapter):

    BASE_URL = "https://api.legifrance.gouv.fr"
    TOKEN_URL = "https://oauth.piste.gouv.fr/api/oauth/token"

    def __init__(self):
        super().__init__()
        self._client_id = os.environ.get("LEGIFRANCE_CLIENT_ID", "")
        self._client_secret = os.environ.get("LEGIFRANCE_CLIENT_SECRET", "")
        self._access_token: Optional[str] = None

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="legifrance",
            domain="legal",
            base_url=self.BASE_URL,
            auth_type="oauth2",
            rate_limit=1.0,
            coverage="French legislation, codes, decrees, and legal texts",
            entity_types=["statute"],
        )

    def _authenticate(self) -> bool:
        """Obtain an OAuth2 access token from the PISTE platform."""
        if not self._client_id or not self._client_secret:
            return False
        resp = self._post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "openid",
            },
        )
        if not resp:
            return False
        try:
            self._access_token = resp.json()["access_token"]
            self._session.headers.update(
                {"Authorization": f"Bearer {self._access_token}"}
            )
            return True
        except (KeyError, ValueError):
            return False

    def _ensure_auth(self) -> bool:
        """Ensure we have a valid access token, refreshing if needed."""
        if self._access_token:
            return True
        return self._authenticate()

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type not in ("celex", "eli"):
            return None
        if not self._ensure_auth():
            return None
        resp = self._post(
            f"{self.BASE_URL}/consult/getArticle",
            json={"id": id_value},
        )
        if not resp:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return self._parse(data, id_type=id_type, id_value=id_value)

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        if not self._ensure_auth():
            return []
        payload = {
            "recherche": {
                "champs": [
                    {"typeChamp": "TITLE", "criteres": [
                        {"typeRecherche": "CONTIENT", "valeur": title}
                    ]}
                ],
                "pageNumber": 1,
                "pageSize": 5,
            }
        }
        resp = self._post(
            f"{self.BASE_URL}/search",
            json=payload,
        )
        if not resp:
            return []
        try:
            items = resp.json().get("results", [])
        except (ValueError, AttributeError):
            return []
        return [e for item in items if (e := self._parse(item)) is not None]

    def _parse(self, data: dict, id_type: str = "",
               id_value: str = "") -> Optional[ICEntity]:
        title = data.get("title") or data.get("titre", "")
        if not title:
            return None

        date_pub = data.get("datePubli", "") or data.get("dateTexte", "")
        year = date_pub[:4] if len(date_pub) >= 4 else ""

        nature = data.get("natureTexte", "") or data.get("nature", "")
        num_texte = data.get("numTexte", "") or data.get("num", "")

        entity = ICEntity(
            entity_type=EntityType.STATUTE,
            title=title,
            authors=[],
            year=year,
            venue="Legifrance",
            metadata={
                "nature_texte": nature,
                "num_texte": num_texte,
                "date_publication": date_pub,
                "jurisdiction": "France",
            },
            source_registries=["legifrance"],
        )

        if id_type and id_value:
            entity.add_external_id("legifrance", id_type, id_value)

        cid = data.get("cid", "") or data.get("id", "")
        if cid:
            entity.add_external_id("legifrance", "cid", str(cid))

        entity.normalize()
        return entity
