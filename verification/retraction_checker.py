"""Retraction Watch integration via Crossref API.

Checks whether cited papers have been retracted by querying the Crossref
REST API, which now includes Retraction Watch data.  Supports single-DOI
and batch lookups, caches results in-memory, and respects Crossref rate
limits (polite pool via ``mailto`` parameter).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import requests

import config


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RetractionInfo:
    """Structured result for a retraction check."""

    doi: str
    is_retracted: bool
    retraction_date: str = ""
    retraction_notice_doi: str = ""
    retraction_reason: str = ""
    source: str = "crossref"  # or "retraction_watch"


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

_CROSSREF_BASE = "https://api.crossref.org"


class RetractionChecker:
    """Check DOIs for retraction status via the Crossref API.

    Parameters
    ----------
    email : str, optional
        Contact e-mail for the Crossref polite pool.  Falls back to
        ``config.CROSSREF_MAILTO`` when empty.
    timeout : int
        HTTP request timeout in seconds.
    """

    # Class-level rate-limit state (20 ms gap ≈ 50 req/s polite pool).
    _last_call: float = 0.0
    _min_interval: float = 0.02  # 20 ms

    def __init__(self, email: str = "", timeout: int | None = None):
        self._email = email or config.CROSSREF_MAILTO
        self._timeout = timeout or config.DEFAULT_TIMEOUT
        self._cache: dict[str, RetractionInfo] = {}
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "IntegriRef/1.0 (cross-domain reference verification; "
                          f"mailto:{self._email})"
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Ensure minimum interval between Crossref requests."""
        now = time.monotonic()
        elapsed = now - RetractionChecker._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        RetractionChecker._last_call = time.monotonic()

    def _params(self) -> dict:
        """Common query parameters (polite pool mailto)."""
        params: dict[str, str] = {}
        if self._email:
            params["mailto"] = self._email
        return params

    @staticmethod
    def _parse_retraction(doi: str, work: dict) -> RetractionInfo:
        """Extract retraction info from a Crossref work JSON object."""
        updates = work.get("update-to") or []
        for update in updates:
            label = (update.get("label") or "").lower()
            update_type = (update.get("type") or "").lower()
            if "retract" in label or "retract" in update_type:
                return RetractionInfo(
                    doi=doi,
                    is_retracted=True,
                    retraction_date=_format_date_parts(
                        update.get("updated", {}).get("date-parts")
                    ),
                    retraction_notice_doi=update.get("DOI", ""),
                    retraction_reason=update.get("label", ""),
                    source="retraction_watch"
                        if "retraction watch" in label
                        else "crossref",
                )

        # Also inspect the "relation" field for retractions.
        relation = work.get("relation") or {}
        for rel_type in ("is-retracted-by", "is-replaced-by"):
            entries = relation.get(rel_type) or []
            for entry in entries:
                if entry.get("id-type") == "doi":
                    return RetractionInfo(
                        doi=doi,
                        is_retracted=True,
                        retraction_notice_doi=entry.get("id", ""),
                        source="crossref",
                    )

        return RetractionInfo(doi=doi, is_retracted=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_doi(self, doi: str) -> RetractionInfo | None:
        """Check a single DOI for retraction status.

        Returns *None* on network / API errors (never raises).
        """
        doi = doi.strip()
        if not doi:
            return None

        if doi in self._cache:
            return self._cache[doi]

        self._rate_limit()
        try:
            resp = self._session.get(
                f"{_CROSSREF_BASE}/works/{doi}",
                params=self._params(),
                timeout=self._timeout,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        work = data.get("message", {})
        info = self._parse_retraction(doi, work)
        self._cache[doi] = info
        return info

    def check_batch(self, dois: list[str]) -> list[RetractionInfo]:
        """Check multiple DOIs in one call (where possible).

        Uses the Crossref ``filter=doi:`` parameter to fetch up to 100
        works per request.  Falls back to single-DOI lookups for any
        DOIs the batch response misses.

        Returns a list of :class:`RetractionInfo` — one per input DOI.
        DOIs that could not be resolved are included with
        ``is_retracted=False`` (conservative default).
        """
        if not dois:
            return []

        cleaned = [d.strip() for d in dois if d.strip()]
        # Return cached results immediately; collect uncached DOIs.
        results: dict[str, RetractionInfo] = {}
        uncached: list[str] = []
        for doi in cleaned:
            if doi in self._cache:
                results[doi] = self._cache[doi]
            else:
                uncached.append(doi)

        # Process uncached DOIs in chunks of 100 (Crossref batch limit).
        CHUNK = 100
        for start in range(0, len(uncached), CHUNK):
            chunk = uncached[start : start + CHUNK]
            self._rate_limit()
            try:
                resp = self._session.get(
                    f"{_CROSSREF_BASE}/works",
                    params={
                        **self._params(),
                        "filter": "doi:" + ",doi:".join(chunk),
                        "rows": str(len(chunk)),
                    },
                    timeout=self._timeout,
                )
                if resp.status_code != 200:
                    raise ValueError("batch request failed")
                items = resp.json().get("message", {}).get("items", [])
            except Exception:
                # Fall back to individual lookups.
                for doi in chunk:
                    info = self.check_doi(doi)
                    if info is not None:
                        results[doi] = info
                continue

            seen: set[str] = set()
            for work in items:
                doi_val = work.get("DOI", "")
                info = self._parse_retraction(doi_val, work)
                self._cache[doi_val] = info
                results[doi_val] = info
                seen.add(doi_val.lower())

            # Individual fallback for any DOIs missing from the batch.
            for doi in chunk:
                if doi.lower() not in seen and doi not in results:
                    info = self.check_doi(doi)
                    if info is not None:
                        results[doi] = info

        # Build output list preserving input order.
        out: list[RetractionInfo] = []
        for doi in cleaned:
            out.append(
                results.get(doi, RetractionInfo(doi=doi, is_retracted=False))
            )
        return out

    def check_reference(self, ref: dict) -> RetractionInfo | None:
        """Check a reference dictionary (as produced by the verification engine).

        Looks for a ``doi`` key first.  If absent, returns *None* — title-
        based retraction search is not supported (Crossref retraction data
        is DOI-indexed).
        """
        doi = ref.get("doi") or ref.get("DOI") or ""
        doi = doi.strip()
        if not doi:
            return None
        return self.check_doi(doi)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_date_parts(date_parts: list | None) -> str:
    """Convert Crossref ``date-parts`` (e.g. ``[[2023, 5, 12]]``) to ISO string."""
    if not date_parts:
        return ""
    try:
        parts = date_parts[0]
        if len(parts) >= 3:
            return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
        if len(parts) == 2:
            return f"{parts[0]:04d}-{parts[1]:02d}"
        if len(parts) == 1:
            return f"{parts[0]:04d}"
    except (IndexError, TypeError):
        pass
    return ""
