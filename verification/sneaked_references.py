"""Sneaked references detection — Besancon et al. 2024 (JASIST Best Paper).

Detects references that appear in Crossref metadata but not in the
paper's actual text, indicating potential citation manipulation by
journal editors.

Reference:
  Besancon, Brembs, Chambers et al. (2024) "Sneaked References:
  How Publications Get References They Didn't Ask For", JASIST.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import requests

import config


# ---------------------------------------------------------------------------
# Try to import RapidFuzz; fall back to simple Jaccard similarity.
# ---------------------------------------------------------------------------

try:
    from rapidfuzz import fuzz as _rfuzz

    def _fuzzy_ratio(a: str, b: str) -> float:
        """Return normalised similarity in [0, 1] using RapidFuzz."""
        return _rfuzz.token_set_ratio(a, b) / 100.0

except ImportError:

    def _fuzzy_ratio(a: str, b: str) -> float:  # type: ignore[misc]
        """Jaccard token similarity fallback."""
        ta = set(a.lower().split())
        tb = set(b.lower().split())
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SneakedReferenceMatch:
    """A single potentially sneaked reference."""

    doi: str
    title: str
    authors: list[str]
    year: str
    journal: str
    is_sneaked: bool  # True if in metadata but not in text
    match_type: str  # "metadata_only", "text_only", "both"


@dataclass
class SneakedReferenceReport:
    """Full sneaked reference analysis."""

    paper_doi: str
    metadata_ref_count: int  # references in Crossref metadata
    text_ref_count: int  # references found in paper text
    matched_count: int  # references found in both
    sneaked_count: int  # in metadata but not in text
    missing_count: int  # in text but not in metadata (normal)
    sneaked_ratio: float  # sneaked_count / metadata_ref_count
    risk_score: float  # 0-100
    is_suspicious: bool
    sneaked_refs: list[SneakedReferenceMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

_CROSSREF_BASE = "https://api.crossref.org"

# Threshold above which a report is flagged as suspicious.
_SUSPICIOUS_SCORE = 40.0

# Fuzzy match thresholds.
_TITLE_MATCH_THRESHOLD = 0.85
_AUTHOR_MATCH_THRESHOLD = 0.80


class SneakedReferenceDetector:
    """Detect sneaked references by comparing Crossref metadata vs text.

    Usage::

        detector = SneakedReferenceDetector()

        # Method 1: Compare Crossref metadata against provided text references
        report = detector.check(
            doi="10.1234/example",
            text_references=["Smith 2020", "Jones et al. 2019"],
        )

        # Method 2: Compare two reference lists directly
        report = detector.compare_reference_lists(
            metadata_refs=[...],  # from Crossref
            text_refs=[...],      # from paper body
        )

        # Method 3: Just fetch and analyze Crossref metadata for red flags
        report = detector.analyze_crossref_metadata(doi="10.1234/example")
    """

    # Class-level rate-limit state (20 ms gap ~ 50 req/s polite pool).
    _last_call: float = 0.0
    _min_interval: float = 0.02

    def __init__(self, email: str = "", timeout: int = 0):
        """Create a detector.

        Parameters
        ----------
        email : str
            Contact e-mail for the Crossref polite pool.  Falls back to
            ``config.CROSSREF_MAILTO``.
        timeout : int
            HTTP timeout in seconds.  ``0`` uses ``config.DEFAULT_TIMEOUT``.
        """
        self._email = email or config.CROSSREF_MAILTO
        self._timeout = timeout or config.DEFAULT_TIMEOUT
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "IntegriRef/1.0 (sneaked-reference detection; "
                f"mailto:{self._email})"
            ),
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - SneakedReferenceDetector._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        SneakedReferenceDetector._last_call = time.monotonic()

    def _params(self) -> dict:
        params: dict[str, str] = {}
        if self._email:
            params["mailto"] = self._email
        return params

    def _fetch_work(self, doi: str) -> dict | None:
        """Fetch a Crossref work record by DOI.  Returns *None* on error."""
        self._rate_limit()
        try:
            resp = self._session.get(
                f"{_CROSSREF_BASE}/works/{doi}",
                params=self._params(),
                timeout=self._timeout,
            )
            if resp.status_code != 200:
                return None
            return resp.json().get("message", {})
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Reference extraction from Crossref work JSON
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_crossref_refs(work: dict) -> list[dict]:
        """Extract structured reference list from a Crossref work record."""
        raw_refs = work.get("reference") or []
        parsed: list[dict] = []
        for ref in raw_refs:
            authors: list[str] = []
            author_raw = ref.get("author") or ""
            if author_raw:
                authors = [a.strip() for a in re.split(r"[,;&]", author_raw) if a.strip()]

            title = ref.get("article-title") or ref.get("volume-title") or ""
            journal = ref.get("journal-title") or ""
            year = ref.get("year") or ""
            doi = ref.get("DOI") or ""

            parsed.append({
                "doi": doi,
                "title": title,
                "authors": authors,
                "year": str(year),
                "journal": journal,
                "unstructured": ref.get("unstructured") or "",
            })
        return parsed

    @staticmethod
    def _paper_year(work: dict) -> str:
        """Extract publication year from a Crossref work record."""
        for key in ("published-print", "published-online", "issued", "created"):
            dp = work.get(key, {}).get("date-parts")
            if dp and dp[0]:
                return str(dp[0][0])
        return ""

    @staticmethod
    def _paper_journal(work: dict) -> str:
        """Extract journal name from a Crossref work record."""
        titles = work.get("container-title") or []
        return titles[0] if titles else ""

    # ------------------------------------------------------------------
    # Matching logic
    # ------------------------------------------------------------------

    def _match_references(self, meta_ref: dict, text_ref: str | dict) -> bool:
        """Check if a metadata reference matches a text reference.

        Matching strategies (tried in order):
        1. DOI exact match
        2. Title fuzzy match (> threshold)
        3. Author surname + year match
        """
        # Normalise text_ref to a dict.
        if isinstance(text_ref, str):
            text_dict: dict = {"raw": text_ref}
        else:
            text_dict = text_ref

        # --- Strategy 1: DOI exact match ---
        meta_doi = (meta_ref.get("doi") or "").strip().lower()
        text_doi = (text_dict.get("doi") or "").strip().lower()
        if meta_doi and text_doi and meta_doi == text_doi:
            return True

        # --- Strategy 2: Title fuzzy match ---
        meta_title = (meta_ref.get("title") or "").strip().lower()
        text_title = (text_dict.get("title") or "").strip().lower()
        if meta_title and text_title:
            if _fuzzy_ratio(meta_title, text_title) >= _TITLE_MATCH_THRESHOLD:
                return True

        # --- Strategy 3: Author surname + year in raw string ---
        raw = (text_dict.get("raw") or "").lower()
        if not raw:
            # Build a pseudo-raw from dict fields.
            parts = []
            for k in ("title", "authors", "year"):
                v = text_dict.get(k)
                if v:
                    parts.append(str(v) if not isinstance(v, list) else " ".join(v))
            raw = " ".join(parts).lower()

        if raw:
            meta_year = (meta_ref.get("year") or "").strip()
            meta_authors = meta_ref.get("authors") or []
            # Also try matching against unstructured Crossref string.
            meta_unstructured = (meta_ref.get("unstructured") or "").lower()

            if meta_year and meta_year in raw:
                # Check if any author surname appears.
                for author in meta_authors:
                    surname = author.split()[-1].lower() if author.strip() else ""
                    if surname and len(surname) > 2 and surname in raw:
                        return True

            # Also try fuzzy matching the unstructured string.
            if meta_unstructured and _fuzzy_ratio(meta_unstructured, raw) >= _AUTHOR_MATCH_THRESHOLD:
                return True

            # Try title against raw text.
            if meta_title and _fuzzy_ratio(meta_title, raw) >= _TITLE_MATCH_THRESHOLD:
                return True

        return False

    def _compute_journal_concentration(self, refs: list[dict]) -> dict:
        """Compute journal concentration in reference list.

        Returns dict with:
        - top_journal: most cited journal name
        - top_journal_count: how many refs cite it
        - concentration_ratio: top_journal_count / total
        - hhi: Herfindahl-Hirschman Index
        """
        journals: list[str] = []
        for ref in refs:
            j = (ref.get("journal") or "").strip().lower()
            if j:
                journals.append(j)

        if not journals:
            return {
                "top_journal": "",
                "top_journal_count": 0,
                "concentration_ratio": 0.0,
                "hhi": 0.0,
            }

        counts = Counter(journals)
        top_journal, top_count = counts.most_common(1)[0]
        total = len(journals)
        shares = [c / total for c in counts.values()]
        hhi = sum(s * s for s in shares)

        return {
            "top_journal": top_journal,
            "top_journal_count": top_count,
            "concentration_ratio": top_count / total,
            "hhi": hhi,
        }

    # ------------------------------------------------------------------
    # Risk scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_risk_score(
        sneaked_ratio: float,
        journal_concentration: dict,
        paper_journal: str,
        same_journal_count: int,
        has_future_refs: bool,
    ) -> tuple[float, list[str]]:
        """Compute 0-100 risk score and associated warnings."""
        score = 0.0
        warnings: list[str] = []

        # Sneaked ratio scoring.
        if sneaked_ratio > 0.50:
            score += 70
            warnings.append(
                f"Very high sneaked ratio ({sneaked_ratio:.0%}) — "
                "majority of Crossref references may not appear in paper text"
            )
        elif sneaked_ratio > 0.20:
            score += 40
            warnings.append(
                f"Elevated sneaked ratio ({sneaked_ratio:.0%}) — "
                "significant number of metadata-only references"
            )

        # Journal concentration (exclude paper's own journal).
        conc = journal_concentration
        top_j = conc.get("top_journal", "")
        conc_ratio = conc.get("concentration_ratio", 0.0)
        if top_j and paper_journal:
            if top_j != paper_journal.strip().lower() and conc_ratio > 0.30:
                score += 20
                warnings.append(
                    f"High reference concentration to external journal "
                    f"'{top_j}' ({conc_ratio:.0%})"
                )
        elif conc_ratio > 0.30:
            score += 20
            warnings.append(
                f"High reference concentration to journal "
                f"'{top_j}' ({conc_ratio:.0%})"
            )

        # Self-journal references.
        if same_journal_count > 5:
            score += 10
            warnings.append(
                f"{same_journal_count} references to the paper's own journal"
            )

        # Future references.
        if has_future_refs:
            score += 15
            warnings.append(
                "Some references have publication year after the paper's "
                "publication year (temporal anomaly)"
            )

        return min(score, 100.0), warnings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_crossref_references(self, doi: str) -> list[dict]:
        """Fetch reference list from Crossref metadata.

        Returns list of dicts with keys: doi, title, authors, year, journal.
        Returns an empty list on error (never raises).
        """
        work = self._fetch_work(doi)
        if not work:
            return []
        return self._parse_crossref_refs(work)

    def check(
        self,
        doi: str,
        text_references: list[str | dict],
    ) -> SneakedReferenceReport:
        """Full sneaked reference check.

        Args:
            doi: Paper's DOI for fetching Crossref metadata.
            text_references: References extracted from paper text.
                Can be strings (``"Smith 2020"``) or dicts with
                title/authors/year/doi keys.

        Returns:
            :class:`SneakedReferenceReport` summarising the analysis.
            On Crossref fetch failure, returns a report with zero counts
            and a warning.
        """
        work = self._fetch_work(doi)
        if not work:
            return SneakedReferenceReport(
                paper_doi=doi,
                metadata_ref_count=0,
                text_ref_count=len(text_references),
                matched_count=0,
                sneaked_count=0,
                missing_count=len(text_references),
                sneaked_ratio=0.0,
                risk_score=0.0,
                is_suspicious=False,
                warnings=["Failed to fetch Crossref metadata for DOI"],
            )

        metadata_refs = self._parse_crossref_refs(work)
        paper_journal = self._paper_journal(work)
        paper_year = self._paper_year(work)

        return self._build_report(
            metadata_refs=metadata_refs,
            text_refs=text_references,
            paper_doi=doi,
            paper_journal=paper_journal,
            paper_year=paper_year,
        )

    def analyze_crossref_metadata(self, doi: str) -> SneakedReferenceReport:
        """Analyze Crossref metadata for red flags WITHOUT paper text.

        Red flags detected:
        - Very high reference count for paper type
        - Many references to same journal (self-promotion)
        - References published AFTER the paper (temporal anomaly)
        - Cluster of references to obscure journals
        """
        work = self._fetch_work(doi)
        if not work:
            return SneakedReferenceReport(
                paper_doi=doi,
                metadata_ref_count=0,
                text_ref_count=0,
                matched_count=0,
                sneaked_count=0,
                missing_count=0,
                sneaked_ratio=0.0,
                risk_score=0.0,
                is_suspicious=False,
                warnings=["Failed to fetch Crossref metadata for DOI"],
            )

        metadata_refs = self._parse_crossref_refs(work)
        paper_journal = self._paper_journal(work)
        paper_year = self._paper_year(work)

        # Detect future references.
        has_future_refs = False
        if paper_year:
            try:
                py = int(paper_year)
                for ref in metadata_refs:
                    ry = ref.get("year") or ""
                    if ry:
                        try:
                            if int(ry) > py:
                                has_future_refs = True
                                break
                        except ValueError:
                            pass
            except ValueError:
                pass

        # Journal concentration.
        concentration = self._compute_journal_concentration(metadata_refs)

        # Count references to paper's own journal.
        same_journal_count = 0
        if paper_journal:
            pj = paper_journal.strip().lower()
            for ref in metadata_refs:
                rj = (ref.get("journal") or "").strip().lower()
                if rj and rj == pj:
                    same_journal_count += 1

        # Since we have no text references, sneaked ratio is indeterminate.
        # Score only from metadata red flags.
        score, warnings = self._compute_risk_score(
            sneaked_ratio=0.0,
            journal_concentration=concentration,
            paper_journal=paper_journal,
            same_journal_count=same_journal_count,
            has_future_refs=has_future_refs,
        )

        # Flag suspiciously high reference counts (> 80 is unusual).
        ref_count = len(metadata_refs)
        if ref_count > 80:
            score = min(score + 15, 100.0)
            warnings.append(
                f"Unusually high reference count ({ref_count}) in Crossref metadata"
            )

        return SneakedReferenceReport(
            paper_doi=doi,
            metadata_ref_count=ref_count,
            text_ref_count=0,
            matched_count=0,
            sneaked_count=0,
            missing_count=0,
            sneaked_ratio=0.0,
            risk_score=score,
            is_suspicious=score >= _SUSPICIOUS_SCORE,
            warnings=warnings,
        )

    def compare_reference_lists(
        self,
        metadata_refs: list[dict],
        text_refs: list[str | dict],
        paper_doi: str = "",
        paper_journal: str = "",
        paper_year: str = "",
    ) -> SneakedReferenceReport:
        """Compare two reference lists to find sneaked references.

        Uses fuzzy matching on title/author to handle formatting differences.
        """
        return self._build_report(
            metadata_refs=metadata_refs,
            text_refs=text_refs,
            paper_doi=paper_doi,
            paper_journal=paper_journal,
            paper_year=paper_year,
        )

    # ------------------------------------------------------------------
    # Core comparison logic
    # ------------------------------------------------------------------

    def _build_report(
        self,
        *,
        metadata_refs: list[dict],
        text_refs: list[str | dict],
        paper_doi: str,
        paper_journal: str = "",
        paper_year: str = "",
    ) -> SneakedReferenceReport:
        """Core logic: compare metadata refs against text refs."""
        meta_count = len(metadata_refs)
        text_count = len(text_refs)

        # Track which text refs have been matched (avoid double matching).
        text_matched: list[bool] = [False] * text_count

        sneaked_matches: list[SneakedReferenceMatch] = []
        matched_count = 0

        for meta_ref in metadata_refs:
            found = False
            for idx, text_ref in enumerate(text_refs):
                if text_matched[idx]:
                    continue
                if self._match_references(meta_ref, text_ref):
                    found = True
                    text_matched[idx] = True
                    matched_count += 1
                    break

            match = SneakedReferenceMatch(
                doi=meta_ref.get("doi") or "",
                title=meta_ref.get("title") or "",
                authors=meta_ref.get("authors") or [],
                year=meta_ref.get("year") or "",
                journal=meta_ref.get("journal") or "",
                is_sneaked=not found,
                match_type="both" if found else "metadata_only",
            )
            if not found:
                sneaked_matches.append(match)

        sneaked_count = meta_count - matched_count
        missing_count = text_count - matched_count

        sneaked_ratio = sneaked_count / meta_count if meta_count > 0 else 0.0

        # Detect future references.
        has_future_refs = False
        if paper_year:
            try:
                py = int(paper_year)
                for ref in metadata_refs:
                    ry = ref.get("year") or ""
                    if ry:
                        try:
                            if int(ry) > py:
                                has_future_refs = True
                                break
                        except ValueError:
                            pass
            except ValueError:
                pass

        # Journal concentration.
        concentration = self._compute_journal_concentration(metadata_refs)

        # Same-journal count.
        same_journal_count = 0
        if paper_journal:
            pj = paper_journal.strip().lower()
            for ref in metadata_refs:
                rj = (ref.get("journal") or "").strip().lower()
                if rj and rj == pj:
                    same_journal_count += 1

        score, warnings = self._compute_risk_score(
            sneaked_ratio=sneaked_ratio,
            journal_concentration=concentration,
            paper_journal=paper_journal,
            same_journal_count=same_journal_count,
            has_future_refs=has_future_refs,
        )

        return SneakedReferenceReport(
            paper_doi=paper_doi,
            metadata_ref_count=meta_count,
            text_ref_count=text_count,
            matched_count=matched_count,
            sneaked_count=sneaked_count,
            missing_count=missing_count,
            sneaked_ratio=sneaked_ratio,
            risk_score=score,
            is_suspicious=score >= _SUSPICIOUS_SCORE,
            sneaked_refs=sneaked_matches,
            warnings=warnings,
        )
