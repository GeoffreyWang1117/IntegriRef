"""CheckIfExist baseline — existence-only verification via CrossRef→S2→OpenAlex.

Reimplements the core methodology of Abbonato et al. (2025) "CheckIfExist":
  1. Query CrossRef by DOI or title search
  2. Fallback to Semantic Scholar
  3. Fallback to OpenAlex
  4. Levenshtein-based title matching (threshold 0.85)
  5. Author family-name overlap check

This baseline does NOT do:
  - Bayesian risk scoring (L4)
  - Citation intent classification (L1)
  - NLI semantic verification (L2)
  - Graph anomaly detection (L3)
  - Retraction checking
  - Hallucination scoring

It classifies a reference as:
  - "VERIFIED" if found with title similarity ≥ 0.85 in any source
  - "SUSPICIOUS" if found but similarity < 0.85
  - "NOT_FOUND" if not found in any source

Reference:
  Abbonato et al. (2025) "CheckIfExist: A Reference Validation Tool for
  AI-Generated Academic Content", arXiv:2602.15871
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# API endpoints
CROSSREF_API = "https://api.crossref.org/works"
S2_API = "https://api.semanticscholar.org/graph/v1/paper"
OPENALEX_API = "https://api.openalex.org/works"

# Polite contact for CrossRef
CROSSREF_MAILTO = "integriref-benchmark@example.com"


@dataclass
class CheckIfExistResult:
    """Result from CheckIfExist baseline verification."""
    title: str = ""
    status: str = "NOT_FOUND"   # VERIFIED / SUSPICIOUS / NOT_FOUND
    best_title_sim: float = 0.0
    best_source: str = ""
    found_title: str = ""
    found_authors: list[str] = field(default_factory=list)
    author_overlap: float = 0.0
    sources_tried: list[str] = field(default_factory=list)
    sources_hit: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


def _levenshtein_similarity(a: str, b: str) -> float:
    """Normalized Levenshtein similarity (0-1) using SequenceMatcher."""
    if not a or not b:
        return 0.0
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    return SequenceMatcher(None, a_lower, b_lower).ratio()


def _extract_family_names(authors: list[str]) -> set[str]:
    """Extract family names from author strings."""
    names = set()
    for a in authors:
        parts = a.replace(",", " ").split()
        if parts:
            # Take the longest part as family name (heuristic)
            family = max(parts, key=len).lower().strip(".")
            if len(family) > 1:
                names.add(family)
    return names


def _author_family_overlap(ref_authors: list[str],
                           found_authors: list[str]) -> float:
    """Fraction of reference family names found in result."""
    ref_names = _extract_family_names(ref_authors)
    found_names = _extract_family_names(found_authors)
    if not ref_names:
        return 0.0
    overlap = ref_names & found_names
    return len(overlap) / len(ref_names)


def _query_crossref_doi(doi: str, timeout: float = 10.0) -> Optional[dict]:
    """Query CrossRef by DOI."""
    if not doi:
        return None
    try:
        url = f"{CROSSREF_API}/{doi}"
        r = requests.get(url, params={"mailto": CROSSREF_MAILTO},
                         timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            msg = data.get("message", {})
            title = msg.get("title", [""])[0] if msg.get("title") else ""
            authors = []
            for a in msg.get("author", []):
                name = a.get("family", "")
                given = a.get("given", "")
                if given:
                    name = f"{given} {name}"
                if name.strip():
                    authors.append(name.strip())
            return {"title": title, "authors": authors, "source": "crossref"}
    except Exception as e:
        logger.debug("CrossRef DOI query failed: %s", e)
    return None


def _query_crossref_search(title: str, timeout: float = 10.0) -> Optional[dict]:
    """Query CrossRef by title search."""
    if not title:
        return None
    try:
        r = requests.get(CROSSREF_API, params={
            "query.bibliographic": title,
            "rows": 3,
            "mailto": CROSSREF_MAILTO,
        }, timeout=timeout)
        if r.status_code == 200:
            items = r.json().get("message", {}).get("items", [])
            if items:
                best = items[0]
                t = best.get("title", [""])[0] if best.get("title") else ""
                authors = []
                for a in best.get("author", []):
                    name = a.get("family", "")
                    given = a.get("given", "")
                    if given:
                        name = f"{given} {name}"
                    if name.strip():
                        authors.append(name.strip())
                return {"title": t, "authors": authors, "source": "crossref"}
    except Exception as e:
        logger.debug("CrossRef search failed: %s", e)
    return None


def _query_semantic_scholar(title: str, timeout: float = 10.0) -> Optional[dict]:
    """Query Semantic Scholar by title search."""
    if not title:
        return None
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "limit": 3,
                    "fields": "title,authors"},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                best = data[0]
                t = best.get("title", "")
                authors = [a.get("name", "") for a in best.get("authors", [])]
                return {"title": t, "authors": authors,
                        "source": "semantic_scholar"}
    except Exception as e:
        logger.debug("Semantic Scholar search failed: %s", e)
    return None


def _query_openalex(title: str, timeout: float = 10.0) -> Optional[dict]:
    """Query OpenAlex by title search."""
    if not title:
        return None
    try:
        r = requests.get(OPENALEX_API, params={
            "search": title,
            "per_page": 3,
            "mailto": CROSSREF_MAILTO,
        }, timeout=timeout)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                best = results[0]
                t = best.get("title", "") or ""
                authors = []
                for auth in best.get("authorships", []):
                    name = auth.get("author", {}).get("display_name", "")
                    if name:
                        authors.append(name)
                return {"title": t, "authors": authors, "source": "openalex"}
    except Exception as e:
        logger.debug("OpenAlex search failed: %s", e)
    return None


def check_if_exist(
    ref: dict,
    title_threshold: float = 0.85,
    timeout: float = 10.0,
) -> CheckIfExistResult:
    """Run CheckIfExist verification on a single reference.

    Implements the CrossRef → Semantic Scholar → OpenAlex cascade
    with Levenshtein title matching.

    Args:
        ref: Reference dict with keys: title, authors, year, doi, venue.
        title_threshold: Minimum title similarity for VERIFIED (default 0.85).
        timeout: Per-API request timeout in seconds.

    Returns:
        CheckIfExistResult with status and match details.
    """
    start = time.monotonic()
    result = CheckIfExistResult(title=ref.get("title", ""))
    query_title = ref.get("title", "")
    ref_authors = ref.get("authors", [])
    doi = ref.get("doi", "")

    candidates = []

    # Step 1: CrossRef DOI lookup
    if doi:
        result.sources_tried.append("crossref_doi")
        cr_doi = _query_crossref_doi(doi, timeout=timeout)
        if cr_doi:
            candidates.append(cr_doi)
            result.sources_hit.append("crossref_doi")

    # Step 2: CrossRef title search (always, as fallback/validation)
    result.sources_tried.append("crossref_search")
    cr_search = _query_crossref_search(query_title, timeout=timeout)
    if cr_search:
        candidates.append(cr_search)
        result.sources_hit.append("crossref_search")

    # Step 3: Semantic Scholar
    result.sources_tried.append("semantic_scholar")
    s2 = _query_semantic_scholar(query_title, timeout=timeout)
    if s2:
        candidates.append(s2)
        result.sources_hit.append("semantic_scholar")

    # Step 4: OpenAlex
    result.sources_tried.append("openalex")
    oa = _query_openalex(query_title, timeout=timeout)
    if oa:
        candidates.append(oa)
        result.sources_hit.append("openalex")

    # Find best match by title similarity
    best_sim = 0.0
    best_candidate = None
    for c in candidates:
        sim = _levenshtein_similarity(query_title, c["title"])
        if sim > best_sim:
            best_sim = sim
            best_candidate = c

    if best_candidate:
        result.best_title_sim = best_sim
        result.best_source = best_candidate["source"]
        result.found_title = best_candidate["title"]
        result.found_authors = best_candidate["authors"]
        result.author_overlap = _author_family_overlap(
            ref_authors, best_candidate["authors"])

        if best_sim >= title_threshold:
            result.status = "VERIFIED"
        else:
            result.status = "SUSPICIOUS"
    else:
        result.status = "NOT_FOUND"

    result.latency_ms = (time.monotonic() - start) * 1000
    return result


def check_if_exist_batch(
    refs: list[dict],
    title_threshold: float = 0.85,
    timeout: float = 10.0,
) -> list[CheckIfExistResult]:
    """Run CheckIfExist on a batch of references (sequential)."""
    return [check_if_exist(r, title_threshold, timeout) for r in refs]
