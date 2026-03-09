"""Base registry adapter — the contract all registry adapters implement.

Design principles:
  - No content storage — only verification results and metadata
  - Built-in rate limiting per registry
  - Unified error handling
  - Returns ICEntity objects
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .entity import ICEntity


@dataclass
class RegistryInfo:
    """Metadata about a registry adapter."""
    name: str               # e.g. "crossref"
    domain: str             # e.g. "academic"
    base_url: str           # e.g. "https://api.crossref.org"
    auth_type: str          # "none" | "api_key_free" | "oauth"
    rate_limit: float       # seconds between requests
    coverage: str           # human-readable description
    entity_types: list[str] # what entity types this registry covers


class RegistryAdapter(abc.ABC):
    """Abstract base class for all registry adapters.

    Subclasses must implement:
      - info() → RegistryInfo
      - query_by_id(id_type, id_value) → Optional[ICEntity]
      - search(title, ...) → list[ICEntity]
    """

    _last_call: float = 0.0

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "IntegriRef/1.0 (cross-domain reference verification)"
        })

    @abc.abstractmethod
    def info(self) -> RegistryInfo:
        """Return metadata about this registry."""
        ...

    @abc.abstractmethod
    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        """Look up a specific entity by its identifier.

        Args:
            id_type: e.g. "doi", "patent_number", "case_cite"
            id_value: the actual identifier value

        Returns:
            ICEntity if found, None otherwise.
        """
        ...

    @abc.abstractmethod
    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        """Search for entities by metadata fields.

        Returns a list of candidate ICEntity objects (best matches first).
        """
        ...

    # ── Rate limiting ─────────────────────────────────────────────────────

    def _rate_limit(self):
        """Enforce per-adapter rate limiting."""
        now = time.time()
        delay = self.info().rate_limit
        wait = delay - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self.__class__._last_call = time.time()

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _get(self, url: str, params: dict = None,
             timeout: int = 20, **kwargs) -> Optional[requests.Response]:
        """Rate-limited GET request with error handling."""
        self._rate_limit()
        try:
            resp = self._session.get(url, params=params, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            return None

    def _post(self, url: str, data: dict = None, json: dict = None,
              timeout: int = 30, **kwargs) -> Optional[requests.Response]:
        """Rate-limited POST request with error handling."""
        self._rate_limit()
        try:
            resp = self._session.post(url, data=data, json=json,
                                      timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            return None

    # ── Convenience ───────────────────────────────────────────────────────

    def verify_exists(self, id_type: str, id_value: str) -> bool:
        """Quick existence check — does this ID resolve in this registry?"""
        return self.query_by_id(id_type, id_value) is not None

    def __repr__(self) -> str:
        i = self.info()
        return f"<{i.name} [{i.domain}] {i.base_url}>"
