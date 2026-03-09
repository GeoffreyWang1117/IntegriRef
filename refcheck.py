#!/usr/bin/env python3
"""ref-check: 论文引用零幻觉验证工具

Four-source cascade verification (arXiv → Crossref → DBLP → Semantic Scholar)
with .tex cross-audit, duplicate detection, retraction checking, and auto-fix.
"""

import argparse
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Optional
from copy import deepcopy

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
import requests

# ── Rate limiting ──────────────────────────────────────────────────────────────

_last_call: dict[str, float] = {}
RATE_LIMITS = {"arxiv": 3.0, "crossref": 1.0, "dblp": 1.0, "s2": 1.0, "openalex": 0.5}


def _rate_limit(source: str):
    now = time.time()
    delay = RATE_LIMITS.get(source, 1.0)
    last = _last_call.get(source, 0)
    wait = delay - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_call[source] = time.time()


# ── LaTeX preprocessing ───────────────────────────────────────────────────────

_LATEX_ACCENTS = {
    "`": "\u0300", "'": "\u0301", "^": "\u0302", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", '"': "\u0308", "v": "\u030C",
    "c": "\u0327", "u": "\u0306",
}


def strip_latex(s: str) -> str:
    """Remove LaTeX markup, returning plain Unicode text."""
    if not s:
        return ""
    # Remove \textsuperscript{...}, \textbf{...}, \textit{...}, etc.
    s = re.sub(r"\\text\w+\{([^}]*)\}", r"\1", s)
    # Handle LaTeX accents: \'{e} or \'e → é
    def _accent_repl(m):
        cmd, char = m.group(1), m.group(2)
        combining = _LATEX_ACCENTS.get(cmd, "")
        base = char.strip("{}")
        if combining and base:
            return unicodedata.normalize("NFC", base + combining)
        return base
    s = re.sub(r"\\([`'^~\"=.vc])\{(\w)\}", _accent_repl, s)
    s = re.sub(r"\\([`'^~\"=.vc])(\w)", _accent_repl, s)
    # Remove remaining backslash commands (e.g. \&, \%)
    s = re.sub(r"\\(\W)", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\s*", "", s)
    # Remove braces and dollar signs (math mode)
    s = s.replace("{", "").replace("}", "").replace("$", "")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_arxiv_id(entry: dict) -> Optional[str]:
    """Extract arXiv ID from journal, eprint, url, or note fields."""
    patterns = [
        r"arXiv[:\s]*(\d{4}\.\d{4,5}(?:v\d+)?)",
        r"arXiv[:\s]*([a-z\-]+/\d{7}(?:v\d+)?)",
    ]
    for field in ("journal", "eprint", "url", "note", "archiveprefix"):
        val = entry.get(field, "")
        for pat in patterns:
            m = re.search(pat, val, re.IGNORECASE)
            if m:
                return m.group(1)
    # Also check eprint directly (common in newer bibtex)
    eprint = entry.get("eprint", "").strip()
    if re.match(r"\d{4}\.\d{4,5}", eprint):
        return eprint
    return None


# ── BibTeX parsing ─────────────────────────────────────────────────────────────

def parse_bib(path: str) -> list[dict]:
    """Parse .bib file and return list of standardized entries."""
    text = Path(path).read_text(encoding="utf-8")
    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    bib_db = bibtexparser.loads(text, parser=parser)
    entries = []
    for e in bib_db.entries:
        entry = {
            "key": e.get("ID", ""),
            "type": e.get("ENTRYTYPE", ""),
            "title": strip_latex(e.get("title", "")),
            "title_raw": e.get("title", ""),
            "author": e.get("author", ""),
            "author_clean": strip_latex(e.get("author", "")),
            "year": e.get("year", ""),
            "journal": e.get("journal", ""),
            "booktitle": e.get("booktitle", ""),
            "volume": e.get("volume", ""),
            "pages": e.get("pages", ""),
            "doi": e.get("doi", ""),
            "url": e.get("url", ""),
            "arxiv_id": extract_arxiv_id(e),
            "_raw": e,
        }
        entry["venue"] = entry["booktitle"] or entry["journal"]
        entries.append(entry)
    return entries


# ── API query functions ────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "ref-check/1.0 (academic citation verifier)"}


def query_arxiv(arxiv_id: str) -> Optional[dict]:
    """Query arXiv API by paper ID."""
    _rate_limit("arxiv")
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return None

    text = r.text
    # Parse XML manually (avoid lxml dependency)
    title_m = re.search(r"<title[^>]*>(.*?)</title>", text, re.DOTALL)
    # Skip the feed title, get the entry title
    titles = re.findall(r"<title[^>]*>(.*?)</title>", text, re.DOTALL)
    if len(titles) < 2:
        return None
    title = re.sub(r"\s+", " ", titles[1]).strip()
    if title.lower().startswith("error"):
        return None

    authors = re.findall(r"<name>(.*?)</name>", text)
    published = re.search(r"<published>(.*?)</published>", text)
    year = published.group(1)[:4] if published else None

    return {
        "source": "arxiv",
        "title": title,
        "authors": authors,
        "year": year,
        "arxiv_id": arxiv_id,
    }


def query_crossref(doi: str) -> Optional[dict]:
    """Query Crossref API by DOI."""
    _rate_limit("crossref")
    url = f"https://api.crossref.org/works/{doi}"
    try:
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        r.raise_for_status()
        data = r.json()["message"]
    except (requests.RequestException, KeyError, ValueError):
        return None

    title = data.get("title", [""])[0]
    authors = []
    for a in data.get("author", []):
        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
        if name:
            authors.append(name)
    year = None
    for date_field in ("published-print", "published-online", "created"):
        if date_field in data:
            parts = data[date_field].get("date-parts", [[]])[0]
            if parts:
                year = str(parts[0])
                break
    venue = ""
    for v in data.get("container-title", []):
        if v:
            venue = v
            break
    is_retracted = "retracted-article" in [u.get("type", "") for u in data.get("update-to", [])]

    return {
        "source": "crossref",
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "is_retracted": is_retracted,
    }


def _clean_dblp_author(name: str) -> str:
    """Strip DBLP disambiguation suffixes like '0001' from author names."""
    return re.sub(r"\s+\d{4}$", "", name).strip()


def _extract_first_surname(author_str: str) -> str:
    """Extract first author's surname from a bib author string.

    Handles both formats:
      - "First Last" → surname is "Last"
      - "Last, First" → surname is "Last"
    """
    clean = strip_latex(author_str)
    parts = re.split(r"\s+and\s+", clean, maxsplit=1)
    first = parts[0].strip()
    if not first:
        return ""
    # "Last, First" format (BibTeX standard)
    if "," in first:
        return first.split(",")[0].strip().split()[-1].lower()
    # "First Last" format
    return first.split()[-1].lower()


def query_dblp(title: str, bib_first_author: str = "") -> Optional[dict]:
    """Query DBLP API by title — the gold standard for CS papers.

    If bib_first_author is given, uses it to disambiguate among results.
    """
    _rate_limit("dblp")
    url = "https://dblp.org/search/publ/api"
    params = {"q": title, "format": "json", "h": 10}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None

    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    if not hits:
        return None

    bib_surname = _extract_first_surname(bib_first_author) if bib_first_author else ""

    # Score each hit by title similarity + author match bonus
    candidates = []
    for hit in hits:
        info = hit.get("info", {})
        hit_title = info.get("title", "").rstrip(".")
        title_score = _token_similarity(title, hit_title)
        if title_score < 0.6:
            continue

        # Extract authors
        authors_data = info.get("authors", {}).get("author", [])
        if isinstance(authors_data, dict):
            authors_data = [authors_data]
        authors = [_clean_dblp_author(a.get("text", a) if isinstance(a, dict) else str(a))
                    for a in authors_data]

        # Author match bonus
        author_bonus = 0
        if bib_surname and authors:
            api_surname = authors[0].split()[-1].lower() if authors[0].split() else ""
            if api_surname == bib_surname:
                author_bonus = 0.3

        candidates.append((title_score + author_bonus, info, authors))

    if not candidates:
        return None

    # Pick best candidate
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best, authors = candidates[0]

    if best_score < 0.6:
        return None

    venue = best.get("venue", "")
    if isinstance(venue, list):
        venue = venue[0] if venue else ""

    return {
        "source": "dblp",
        "title": best.get("title", "").rstrip("."),
        "authors": authors,
        "year": best.get("year"),
        "venue": venue,
        "pages": best.get("pages", ""),
        "doi": best.get("doi", ""),
        "url": best.get("ee", ""),
    }


def query_s2(title: str, bib_first_author: str = "") -> Optional[dict]:
    """Query Semantic Scholar API by title."""
    _rate_limit("s2")
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": title,
        "limit": 10,
        "fields": "title,authors,year,venue,citationCount,isOpenAccess,externalIds",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None

    papers = data.get("data", [])
    if not papers:
        return None

    bib_surname = _extract_first_surname(bib_first_author) if bib_first_author else ""

    # Find best match with author disambiguation
    candidates = []
    for p in papers:
        title_score = _token_similarity(title, p.get("title", ""))
        if title_score < 0.6:
            continue
        author_bonus = 0
        p_authors = [a.get("name", "") for a in p.get("authors", [])]
        if bib_surname and p_authors:
            api_surname = p_authors[0].split()[-1].lower() if p_authors[0].split() else ""
            if api_surname == bib_surname:
                author_bonus = 0.3
        candidates.append((title_score + author_bonus, p))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best = candidates[0]

    if best_score < 0.6:
        return None

    authors = [a.get("name", "") for a in best.get("authors", [])]
    ext_ids = best.get("externalIds", {}) or {}

    return {
        "source": "s2",
        "title": best.get("title", ""),
        "authors": authors,
        "year": str(best["year"]) if best.get("year") else None,
        "venue": best.get("venue", ""),
        "citation_count": best.get("citationCount"),
        "doi": ext_ids.get("DOI"),
        "arxiv_id": ext_ids.get("ArXiv"),
        "s2_id": best.get("paperId"),
    }


def query_openalex(title: str, bib_first_author: str = "") -> Optional[dict]:
    """Query OpenAlex API — covers all disciplines including old/non-CS papers."""
    _rate_limit("openalex")
    url = "https://api.openalex.org/works"
    params = {
        "search": title,
        "per_page": 5,
        "mailto": "ref-check@example.com",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None

    results = data.get("results", [])
    if not results:
        return None

    bib_surname = _extract_first_surname(bib_first_author) if bib_first_author else ""

    candidates = []
    for work in results:
        work_title = work.get("title", "") or ""
        title_score = _token_similarity(title, work_title)
        if title_score < 0.6:
            continue

        authorships = work.get("authorships", [])
        authors = []
        for a in authorships:
            author_obj = a.get("author", {})
            name = author_obj.get("display_name", "")
            if name:
                authors.append(name)

        author_bonus = 0
        if bib_surname and authors:
            api_surname = authors[0].split()[-1].lower() if authors[0].split() else ""
            if api_surname == bib_surname:
                author_bonus = 0.3

        candidates.append((title_score + author_bonus, work, authors))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best, authors = candidates[0]
    if best_score < 0.6:
        return None

    year = str(best["publication_year"]) if best.get("publication_year") else None
    venue = ""
    primary_loc = best.get("primary_location", {}) or {}
    source = primary_loc.get("source", {}) or {}
    venue = source.get("display_name", "")

    doi = best.get("doi", "")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    return {
        "source": "openalex",
        "title": best.get("title", ""),
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "cited_by_count": best.get("cited_by_count"),
        "is_retracted": best.get("is_retracted", False),
    }


# ── Field comparison ───────────────────────────────────────────────────────────

def _tokenize(s: str) -> set[str]:
    """Lowercase token set from a string."""
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _token_similarity(a: str, b: str) -> float:
    """Jaccard similarity on tokens after stripping."""
    ta, tb = _tokenize(strip_latex(a)), _tokenize(strip_latex(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match_title(bib_title: str, api_title: str) -> tuple[str, str]:
    """Compare titles. Returns (status, detail)."""
    score = _token_similarity(bib_title, api_title)
    if score >= 0.85:
        return "OK", f"title match ({score:.2f})"
    elif score >= 0.7:
        return "WARN", f"title partial match ({score:.2f}): '{api_title}'"
    else:
        return "FAIL", f"title mismatch ({score:.2f}): bib='{bib_title}' vs api='{api_title}'"


def match_authors(bib_author: str, api_authors: list[str]) -> tuple[str, str]:
    """Compare authors — check first author surname + count."""
    if not api_authors:
        return "WARN", "no authors from API"

    bib_clean = strip_latex(bib_author)
    # Extract first author surname from bib (handles both "First Last" and "Last, First")
    bib_surname = _extract_first_surname(bib_author)

    # API first author surname
    api_first = api_authors[0]
    api_surname = api_first.split()[-1].lower() if api_first.split() else ""

    if not bib_surname or not api_surname:
        return "WARN", "could not extract surnames"

    # Normalize Unicode for comparison (e.g. garcı́a vs garcía)
    # Also normalize dotless-i (ı U+0131) → i, common LaTeX artifact
    def _norm_name(s):
        """Normalize name for comparison: strip accents, handle dotless-i."""
        s = unicodedata.normalize("NFKD", s)
        s = s.replace("\u0131", "i")  # dotless i → i
        # Strip all combining characters (accents/diacritics)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return s.lower()
    bib_surname_n = _norm_name(bib_surname)
    api_surname_n = _norm_name(api_surname)
    surname_ok = bib_surname_n == api_surname_n

    # Count check
    bib_count = len(re.split(r"\s+and\s+", bib_clean))
    api_count = len(api_authors)
    has_others = "others" in bib_clean.lower()

    if has_others:
        # "and others" means bib shows fewer — this is always acceptable
        count_ok = True
    else:
        count_ok = abs(bib_count - api_count) <= 2

    if surname_ok and count_ok:
        return "OK", f"first author '{bib_surname}' matches, count bib={bib_count} api={api_count}"
    elif surname_ok:
        return "WARN", f"first author matches but count differs: bib={bib_count} api={api_count}"
    else:
        return "FAIL", f"first author mismatch: bib='{bib_surname}' api='{api_surname}'"


def match_year(bib_year: str, api_year: Optional[str]) -> tuple[str, str]:
    """Compare years — exact or ±1."""
    if not api_year:
        return "WARN", "no year from API"
    try:
        by, ay = int(bib_year), int(api_year)
    except (ValueError, TypeError):
        return "WARN", f"unparseable years: bib={bib_year} api={api_year}"
    if by == ay:
        return "OK", f"year exact match ({by})"
    elif abs(by - ay) <= 1:
        return "WARN", f"year off by 1: bib={by} api={ay}"
    else:
        return "FAIL", f"year mismatch: bib={by} api={ay}"


# Common venue abbreviations for matching
_VENUE_ABBREVS = {
    "icml": "international conference on machine learning",
    "neurips": "advances in neural information processing systems",
    "nips": "advances in neural information processing systems",
    "iclr": "international conference on learning representations",
    "aaai": "association for the advancement of artificial intelligence",
    "ijcai": "international joint conference on artificial intelligence",
    "cvpr": "computer vision and pattern recognition",
    "acl": "association for computational linguistics",
    "emnlp": "empirical methods in natural language processing",
    "jmlr": "journal of machine learning research",
    "pnas": "proceedings of the national academy of sciences",
    "jair": "journal of artificial intelligence research",
    "iros": "intelligent robots and systems",
    "kbs": "knowledge-based systems",
}

# DBLP-style abbreviations → canonical forms
_DBLP_VENUE_MAP = {
    "j. mach. learn. res.": "journal of machine learning research",
    "nat.": "nature",
    "acm comput. surv.": "acm computing surveys",
    "knowl. inf. syst.": "knowledge and information systems",
    "psychol. bull.": "psychological bulletin",
    "proc. natl. acad. sci.": "proceedings of the national academy of sciences",
    "artif. intell.": "artificial intelligence",
    "j. artif. intell. res.": "journal of artificial intelligence research",
    "mach. learn.": "machine learning",
    "corr": "arxiv",
}


def _normalize_venue(v: str) -> str:
    """Normalize a venue string for comparison."""
    v = strip_latex(v).lower().strip()
    # Map DBLP abbreviations to canonical forms
    for abbrev, full in _DBLP_VENUE_MAP.items():
        if v == abbrev or v.startswith(abbrev):
            return full
    return v


def match_venue(bib_venue: str, api_venue: str) -> tuple[str, str]:
    """Compare venues with abbreviation awareness."""
    if not api_venue:
        return "WARN", "no venue from API"
    if not bib_venue:
        return "WARN", "no venue in bib"

    bib_v = _normalize_venue(bib_venue)
    api_v = _normalize_venue(api_venue)

    # arXiv preprint ↔ CoRR / arXiv
    bib_is_arxiv = "arxiv" in bib_v or "corr" in bib_v
    api_is_arxiv = "arxiv" in api_v or "corr" in api_v
    if bib_is_arxiv and api_is_arxiv:
        return "OK", "venue match (both arXiv/CoRR)"

    # Direct token similarity (on normalized forms)
    score = _token_similarity(bib_v, api_v)
    if score >= 0.5:
        return "OK", f"venue match ({score:.2f})"

    # Check abbreviation matches
    for abbrev, full in _VENUE_ABBREVS.items():
        bib_has = abbrev in bib_v or full in bib_v
        api_has = abbrev in api_v or full in api_v
        if bib_has and api_has:
            return "OK", f"venue match via abbreviation '{abbrev}'"

    # Substring containment (e.g. "Nature" in "Nat.")
    if bib_v in api_v or api_v in bib_v:
        return "OK", f"venue match (substring)"

    return "WARN", f"venue unclear: bib='{bib_venue[:50]}' api='{api_venue[:50]}'"


# ── TeX cross-audit ────────────────────────────────────────────────────────────

def _read_tex_with_includes(tex_path: str) -> str:
    """Read a .tex file and recursively resolve \\input{} and \\include{} directives."""
    p = Path(tex_path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    base_dir = p.parent

    def _resolve(m):
        inc_path = m.group(1).strip()
        if not inc_path.endswith(".tex"):
            inc_path += ".tex"
        full = base_dir / inc_path
        if full.exists():
            return full.read_text(encoding="utf-8")
        return m.group(0)  # keep original if file not found

    # Resolve \input{...} and \include{...}
    text = re.sub(r"\\(?:input|include)\{([^}]+)\}", _resolve, text)
    return text


def audit_tex_bib(tex_path: str, bib_entries: list[dict]) -> list[dict]:
    """Cross-audit .tex citations against .bib entries."""
    text = _read_tex_with_includes(tex_path)
    # Remove comments
    text = re.sub(r"(?<!\\)%.*", "", text)

    # Find all \cite variants
    cite_pattern = r"\\(?:cite[tp]?|citealp|citeauthor|citeyear|Cite[tp]?)\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"
    cited_keys: set[str] = set()
    for m in re.finditer(cite_pattern, text):
        keys = m.group(1).split(",")
        for k in keys:
            k = k.strip()
            if k:
                cited_keys.add(k)

    bib_keys = {e["key"] for e in bib_entries}
    issues = []

    # Phantom references (cited but not in bib)
    for k in sorted(cited_keys - bib_keys):
        issues.append({
            "type": "FAIL",
            "category": "phantom_ref",
            "key": k,
            "message": f"Phantom reference: \\cite{{{k}}} used in .tex but not defined in .bib",
        })

    # Orphan entries (in bib but not cited)
    for k in sorted(bib_keys - cited_keys):
        issues.append({
            "type": "WARN",
            "category": "orphan_entry",
            "key": k,
            "message": f"Orphan entry: '{k}' defined in .bib but never cited in .tex",
        })

    return issues


def detect_duplicates(entries: list[dict]) -> list[dict]:
    """Detect potential duplicate entries (different keys, same paper)."""
    issues = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            score = _token_similarity(a["title"], b["title"])
            if score >= 0.85:
                issues.append({
                    "type": "WARN",
                    "category": "duplicate",
                    "key": f"{a['key']} / {b['key']}",
                    "message": f"Possible duplicate: '{a['key']}' and '{b['key']}' "
                               f"(title similarity {score:.2f})",
                })
    return issues


# ── Main verification engine ──────────────────────────────────────────────────

class VerificationResult:
    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.sources_tried: list[str] = []
        self.sources_hit: list[str] = []
        self.checks: list[dict] = []  # {"field", "status", "detail", "source"}
        self.api_data: dict = {}  # merged data from all APIs
        self.overall = "OK"
        self.suggested_fixes: dict = {}

    def add_check(self, field: str, status: str, detail: str, source: str = ""):
        self.checks.append({"field": field, "status": status, "detail": detail, "source": source})
        if status == "FAIL" and self.overall != "FAIL":
            self.overall = "FAIL"
        elif status == "WARN" and self.overall == "OK":
            self.overall = "WARN"


def _strip_accents(s: str) -> str:
    """Strip all accents/diacritics from a string for fuzzy comparison."""
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("\u0131", "i")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _validate_api_match(entry: dict, data: dict) -> bool:
    """Check if an API result is a plausible match for the bib entry.

    Returns False if the result is clearly a different paper (wrong author + wrong year).
    """
    if not data:
        return False
    # Check first author
    api_authors = data.get("authors", [])
    if api_authors:
        bib_surname = _extract_first_surname(entry["author"])
        api_first = api_authors[0]
        api_surname = api_first.split()[-1].lower() if api_first.split() else ""
        author_match = _strip_accents(bib_surname) == _strip_accents(api_surname)
        # If author doesn't match AND year is off by more than 2, reject
        if not author_match:
            api_year = data.get("year")
            if api_year:
                try:
                    year_diff = abs(int(entry["year"]) - int(api_year))
                except (ValueError, TypeError):
                    year_diff = 0
                if year_diff >= 2:
                    return False
            # Also check title similarity — reject if < 0.85 and author mismatch
            api_title = data.get("title", "")
            if _token_similarity(entry.get("title", ""), api_title) < 0.85:
                return False
    return True


def _has_perfect_title_author_match(result: VerificationResult) -> bool:
    """Check if any source has perfect title AND author match."""
    title_ok = any(c["field"] == "title" and c["status"] == "OK" for c in result.checks)
    author_ok = any(c["field"] == "authors" and c["status"] == "OK" for c in result.checks)
    return title_ok and author_ok


def verify_entry(entry: dict, verbose: bool = False) -> VerificationResult:
    """Verify a single bib entry against all available sources."""
    result = VerificationResult(entry["key"], entry["title"])

    # ── Source 1: arXiv ──
    if entry["arxiv_id"]:
        result.sources_tried.append("arxiv")
        data = query_arxiv(entry["arxiv_id"])
        if data:
            result.sources_hit.append("arxiv")
            result.api_data["arxiv"] = data
            s, d = match_title(entry["title"], data["title"])
            result.add_check("title", s, d, "arxiv")
            s, d = match_authors(entry["author"], data["authors"])
            result.add_check("authors", s, d, "arxiv")
            s, d = match_year(entry["year"], data["year"])
            result.add_check("year", s, d, "arxiv")

    # ── Source 2: Crossref ──
    doi = entry.get("doi", "")
    if doi:
        result.sources_tried.append("crossref")
        data = query_crossref(doi)
        if data:
            result.sources_hit.append("crossref")
            result.api_data["crossref"] = data
            s, d = match_title(entry["title"], data["title"])
            result.add_check("title", s, d, "crossref")
            s, d = match_authors(entry["author"], data["authors"])
            result.add_check("authors", s, d, "crossref")
            s, d = match_year(entry["year"], data["year"])
            result.add_check("year", s, d, "crossref")
            s, d = match_venue(entry["venue"], data.get("venue", ""))
            result.add_check("venue", s, d, "crossref")
            if data.get("is_retracted"):
                result.add_check("retraction", "FAIL", "RETRACTED PAPER!", "crossref")

    # Build search variants for titles with special characters (e.g. "RL2" → "RL")
    search_title = entry["title"]
    # Also try without trailing digits stuck to words (RL2 → RL)
    alt_title = re.sub(r"(\b[A-Z]+)(\d+)\b", r"\1", entry["title"])
    search_titles = [search_title]
    if alt_title != search_title:
        search_titles.append(alt_title)

    # ── Source 3: DBLP ──
    result.sources_tried.append("dblp")
    data = None
    for st in search_titles:
        data = query_dblp(st, bib_first_author=entry["author"])
        if data and _validate_api_match(entry, data):
            break
        data = None
    if data:
        result.sources_hit.append("dblp")
        result.api_data["dblp"] = data
        s, d = match_title(entry["title"], data["title"])
        result.add_check("title", s, d, "dblp")
        s, d = match_authors(entry["author"], data["authors"])
        result.add_check("authors", s, d, "dblp")
        s, d = match_year(entry["year"], data["year"])
        result.add_check("year", s, d, "dblp")
        s, d = match_venue(entry["venue"], data.get("venue", ""))
        result.add_check("venue", s, d, "dblp")
        # Collect DOI for auto-fix
        if data.get("doi") and not entry.get("doi"):
            result.suggested_fixes["doi"] = data["doi"]

    # ── Source 4: Semantic Scholar ──
    result.sources_tried.append("s2")
    data = None
    for st in search_titles:
        data = query_s2(st, bib_first_author=entry["author"])
        if data and _validate_api_match(entry, data):
            break
        data = None
    if data:
        result.sources_hit.append("s2")
        result.api_data["s2"] = data
        s, d = match_title(entry["title"], data["title"])
        result.add_check("title", s, d, "s2")
        s, d = match_authors(entry["author"], data["authors"])
        result.add_check("authors", s, d, "s2")
        s, d = match_year(entry["year"], data["year"])
        result.add_check("year", s, d, "s2")
        if data.get("venue"):
            s, d = match_venue(entry["venue"], data["venue"])
            result.add_check("venue", s, d, "s2")
        # Collect DOI/arXiv for auto-fix
        if data.get("doi") and not entry.get("doi") and "doi" not in result.suggested_fixes:
            result.suggested_fixes["doi"] = data["doi"]
        if data.get("arxiv_id") and not entry.get("arxiv_id"):
            result.suggested_fixes["eprint"] = data["arxiv_id"]
        if data.get("citation_count") is not None:
            result.add_check("citations", "OK", f"citation count: {data['citation_count']}", "s2")

    # ── Source 5: OpenAlex (fallback for non-CS / old papers) ──
    if not result.sources_hit:
        result.sources_tried.append("openalex")
        data = None
        for st in search_titles:
            data = query_openalex(st, bib_first_author=entry["author"])
            if data and _validate_api_match(entry, data):
                break
            data = None
        if data:
            result.sources_hit.append("openalex")
            result.api_data["openalex"] = data
            s, d = match_title(entry["title"], data["title"])
            result.add_check("title", s, d, "openalex")
            s, d = match_authors(entry["author"], data["authors"])
            result.add_check("authors", s, d, "openalex")
            s, d = match_year(entry["year"], data["year"])
            result.add_check("year", s, d, "openalex")
            if data.get("venue"):
                s, d = match_venue(entry["venue"], data["venue"])
                result.add_check("venue", s, d, "openalex")
            if data.get("doi") and not entry.get("doi") and "doi" not in result.suggested_fixes:
                result.suggested_fixes["doi"] = data["doi"]
            if data.get("is_retracted"):
                result.add_check("retraction", "FAIL", "RETRACTED PAPER!", "openalex")
            if data.get("cited_by_count") is not None:
                result.add_check("citations", "OK",
                                 f"citation count: {data['cited_by_count']}", "openalex")

    # ── No source hit ──
    if not result.sources_hit:
        result.add_check("verification", "FAIL",
                         f"NO API MATCH — paper may be hallucinated or title differs significantly "
                         f"(tried: {', '.join(result.sources_tried)})")
        result.overall = "FAIL"

    # ── Post-processing: source-aware overall status ──
    # If ANY source has title+author OK, the paper is confirmed.
    # Wrong matches from other sources should not override a confirmed match.

    # Compute per-source status
    source_statuses = {}
    for src in result.sources_hit:
        src_checks = [c for c in result.checks if c["source"] == src]
        title_ok = any(c["field"] == "title" and c["status"] == "OK" for c in src_checks)
        author_ok = any(c["field"] == "authors" and c["status"] in ("OK", "WARN") for c in src_checks)
        year_ok = any(c["field"] == "year" and c["status"] in ("OK", "WARN") for c in src_checks)
        if title_ok and author_ok:
            source_statuses[src] = "confirmed"
            # If year FAILs but title+author match, downgrade year to WARN
            for c in result.checks:
                if c["source"] == src and c["field"] == "year" and c["status"] == "FAIL":
                    c["status"] = "WARN"
                    c["detail"] += " (downgraded: title+author match)"

    # Recompute overall: if any source confirmed, use only confirmed sources for status
    if any(v == "confirmed" for v in source_statuses.values()):
        confirmed_sources = {s for s, v in source_statuses.items() if v == "confirmed"}
        result.overall = "OK"
        for c in result.checks:
            if c["source"] not in confirmed_sources:
                continue  # ignore checks from non-confirmed sources
            if c["status"] == "FAIL":
                result.overall = "FAIL"
                break
            elif c["status"] == "WARN" and result.overall == "OK":
                result.overall = "WARN"
    else:
        # No confirmed source — use all checks
        result.overall = "OK"
        for c in result.checks:
            if c["status"] == "FAIL":
                result.overall = "FAIL"
                break
            elif c["status"] == "WARN" and result.overall == "OK":
                result.overall = "WARN"

    return result


# ── Report generation ──────────────────────────────────────────────────────────

_STATUS_ICON = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}


def generate_report(results: list[VerificationResult],
                    tex_issues: list[dict],
                    dup_issues: list[dict]) -> str:
    """Generate Markdown verification report."""
    lines = ["# ref-check Verification Report\n"]

    # Summary
    ok = sum(1 for r in results if r.overall == "OK")
    warn = sum(1 for r in results if r.overall == "WARN")
    fail = sum(1 for r in results if r.overall == "FAIL")
    total = len(results)
    lines.append(f"**Total entries:** {total}  |  "
                 f"✅ OK: {ok}  |  ⚠️ WARN: {warn}  |  ❌ FAIL: {fail}\n")

    # API coverage
    any_hit = sum(1 for r in results if r.sources_hit)
    lines.append(f"**API coverage:** {any_hit}/{total} entries matched at least one source\n")

    source_counts = defaultdict(int)
    for r in results:
        for s in r.sources_hit:
            source_counts[s] += 1
    lines.append("**Source breakdown:** " + ", ".join(
        f"{s}: {c}" for s, c in sorted(source_counts.items())) + "\n")

    # TeX cross-audit
    if tex_issues:
        lines.append("## TeX Cross-Audit\n")
        for iss in tex_issues:
            icon = _STATUS_ICON.get(iss["type"], "❓")
            lines.append(f"- {icon} **{iss['category']}** [{iss['key']}]: {iss['message']}")
        lines.append("")

    # Duplicates
    if dup_issues:
        lines.append("## Duplicate Detection\n")
        for iss in dup_issues:
            lines.append(f"- ⚠️ {iss['message']}")
        lines.append("")

    # Per-entry details (FAILs first, then WARNs, then OKs)
    lines.append("## Per-Entry Verification\n")
    sorted_results = sorted(results, key=lambda r: {"FAIL": 0, "WARN": 1, "OK": 2}[r.overall])

    for r in sorted_results:
        icon = _STATUS_ICON[r.overall]
        sources = ", ".join(r.sources_hit) if r.sources_hit else "none"
        lines.append(f"### {icon} `{r.key}`")
        lines.append(f"**Title:** {r.title}  ")
        lines.append(f"**Verified by:** {sources}\n")

        for c in r.checks:
            ci = _STATUS_ICON.get(c["status"], "❓")
            src = f" [{c['source']}]" if c["source"] else ""
            lines.append(f"- {ci} {c['field']}: {c['detail']}{src}")

        if r.suggested_fixes:
            lines.append(f"\n**Suggested fixes:** {r.suggested_fixes}")
        lines.append("")

    return "\n".join(lines)


# ── Auto-fix .bib ──────────────────────────────────────────────────────────────

def generate_fixed_bib(bib_path: str, results: list[VerificationResult]) -> str:
    """Generate a fixed .bib file with corrections applied."""
    text = Path(bib_path).read_text(encoding="utf-8")

    fixes_by_key = {r.key: r for r in results if r.suggested_fixes}
    if not fixes_by_key:
        return text

    lines = text.split("\n")
    output = []
    current_key = None
    brace_depth = 0
    last_field_line = -1

    for i, line in enumerate(lines):
        # Detect entry start
        entry_match = re.match(r"@\w+\{(\w+),", line)
        if entry_match:
            current_key = entry_match.group(1)
            brace_depth = 1
            last_field_line = i
            output.append(line)
            continue

        if current_key:
            brace_depth += line.count("{") - line.count("}")
            # Detect entry end
            if brace_depth <= 0:
                # Insert fixes before closing brace
                if current_key in fixes_by_key:
                    # Ensure last field line has a trailing comma
                    for j in range(len(output) - 1, -1, -1):
                        prev = output[j].rstrip()
                        if prev and not prev.isspace():
                            if prev[-1] not in (",", "{"):
                                output[j] = prev + ","
                            break
                    r = fixes_by_key[current_key]
                    for field, value in r.suggested_fixes.items():
                        fix_line = f'  {field} = "{value}",'
                        output.append(fix_line)
                current_key = None
            else:
                last_field_line = i

        output.append(line)

    return "\n".join(output)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ref-check: 论文引用零幻觉验证工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--bib", required=True, help=".bib file path")
    parser.add_argument("--tex", help=".tex file path (enables cross-audit)")
    parser.add_argument("--out", help="Output Markdown report path")
    parser.add_argument("--fix", help="Output auto-fixed .bib path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Parse bib
    print(f"📖 Parsing {args.bib}...")
    entries = parse_bib(args.bib)
    print(f"   Found {len(entries)} entries\n")

    # Verify each entry
    results = []
    for i, entry in enumerate(entries):
        print(f"[{i+1}/{len(entries)}] Verifying '{entry['key']}'...", end=" ", flush=True)
        r = verify_entry(entry, verbose=args.verbose)
        results.append(r)
        icon = _STATUS_ICON[r.overall]
        sources = ", ".join(r.sources_hit) if r.sources_hit else "no match"
        print(f"{icon} ({sources})")

    # TeX cross-audit
    tex_issues = []
    if args.tex:
        print(f"\n📄 Cross-auditing {args.tex}...")
        tex_issues = audit_tex_bib(args.tex, entries)
        for iss in tex_issues:
            icon = _STATUS_ICON.get(iss["type"], "❓")
            print(f"   {icon} {iss['message']}")

    # Duplicate detection
    print("\n🔍 Checking for duplicates...")
    dup_issues = detect_duplicates(entries)
    for iss in dup_issues:
        print(f"   ⚠️ {iss['message']}")
    if not dup_issues:
        print("   No duplicates found")

    # Generate report
    report = generate_report(results, tex_issues, dup_issues)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n📝 Report saved to {args.out}")
    else:
        print("\n" + "=" * 60)
        print(report)

    # Auto-fix
    if args.fix:
        fixed = generate_fixed_bib(args.bib, results)
        Path(args.fix).write_text(fixed, encoding="utf-8")
        n_fixes = sum(1 for r in results if r.suggested_fixes)
        print(f"\n🔧 Fixed .bib saved to {args.fix} ({n_fixes} entries with fixes)")

    # Exit code
    has_fail = any(r.overall == "FAIL" for r in results)
    has_fail = has_fail or any(i["type"] == "FAIL" for i in tex_issues)
    sys.exit(1 if has_fail else 0)


if __name__ == "__main__":
    main()
