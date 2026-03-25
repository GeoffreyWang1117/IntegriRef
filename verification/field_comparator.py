"""Field comparison utilities — title, author, year, venue matching.

Extracted and enhanced from refcheck.py for use across the verification engine.
Supports LaTeX stripping, accent normalization, venue abbreviation maps,
RapidFuzz similarity metrics, and configurable thresholds.

When the Rust accelerator (integriref_core) is available, hot-path functions
(strip_latex, strip_accents, tokenize, jaccard_similarity, token_set_ratio,
token_similarity, author_name_similarity) are delegated to compiled Rust code
for 10-50x speedup.  Falls back to pure Python transparently.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ── Rust accelerator (optional) ──────────────────────────────────────────
try:
    import integriref_core as _rc
    HAS_RUST = True
except ImportError:
    _rc = None  # type: ignore
    HAS_RUST = False

try:
    from rapidfuzz import fuzz as _rf_fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

# ── LaTeX handling ─────────────────────────────────────────────────────────

_LATEX_ACCENTS = {
    "`": "\u0300", "'": "\u0301", "^": "\u0302", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", '"': "\u0308", "v": "\u030C",
    "c": "\u0327", "u": "\u0306",
}


def strip_latex(s: str) -> str:
    """Remove LaTeX markup, returning plain Unicode text."""
    if not s:
        return ""
    if HAS_RUST:
        return _rc.strip_latex(s)
    s = re.sub(r"\\text\w+\{([^}]*)\}", r"\1", s)

    def _accent_repl(m):
        cmd, char = m.group(1), m.group(2)
        combining = _LATEX_ACCENTS.get(cmd, "")
        base = char.strip("{}")
        if combining and base:
            return unicodedata.normalize("NFC", base + combining)
        return base

    s = re.sub(r"\\([`'^~\"=.vc])\{(\w)\}", _accent_repl, s)
    s = re.sub(r"\\([`'^~\"=.vc])(\w)", _accent_repl, s)
    s = re.sub(r"\\(\W)", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\s*", "", s)
    s = s.replace("{", "").replace("}", "").replace("$", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_accents(s: str) -> str:
    """Strip all accents/diacritics for fuzzy comparison."""
    if HAS_RUST:
        return _rc.strip_accents(s)
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("\u0131", "i")  # Turkish dotless i
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


# ── Tokenization ──────────────────────────────────────────────────────────

def tokenize(s: str) -> set[str]:
    """Lowercase alphanumeric token set."""
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity on tokens after LaTeX stripping."""
    if HAS_RUST:
        return _rc.jaccard_similarity(a, b)
    ta, tb = tokenize(strip_latex(a)), tokenize(strip_latex(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def token_set_ratio(a: str, b: str) -> float:
    """RapidFuzz token_set_ratio (0-1 scale). Falls back to Rust, then Jaccard."""
    if HAS_RAPIDFUZZ:
        return _rf_fuzz.token_set_ratio(
            strip_latex(a).lower(), strip_latex(b).lower()) / 100.0
    if HAS_RUST:
        return _rc.token_set_ratio(a, b)
    return jaccard_similarity(a, b)


def token_similarity(a: str, b: str) -> float:
    """Best available token similarity (RapidFuzz > Rust > Jaccard fallback).

    Uses max(token_set_ratio, jaccard) for robustness — token_set_ratio
    handles reordering/subsets better; Jaccard handles exact token overlap.
    """
    if HAS_RAPIDFUZZ:
        tsr = _rf_fuzz.token_set_ratio(
            strip_latex(a).lower(), strip_latex(b).lower()) / 100.0
        jac = jaccard_similarity(a, b)
        return max(tsr, jac)
    if HAS_RUST:
        return _rc.token_similarity(a, b)
    return jaccard_similarity(a, b)


def author_name_similarity(a: str, b: str) -> float:
    """Jaro-Winkler similarity for author names (0-1).
    Falls back to exact accent-stripped comparison."""
    a_clean = strip_accents(strip_latex(a))
    b_clean = strip_accents(strip_latex(b))
    if a_clean == b_clean:
        return 1.0
    if HAS_RAPIDFUZZ:
        from rapidfuzz.distance import JaroWinkler
        return JaroWinkler.similarity(a_clean, b_clean)
    if HAS_RUST:
        return _rc.author_name_similarity(a, b)
    # Simple fallback: character overlap ratio
    if not a_clean or not b_clean:
        return 0.0
    common = sum(1 for c in a_clean if c in b_clean)
    return common / max(len(a_clean), len(b_clean))


# ── Venue abbreviation maps ──────────────────────────────────────────────

VENUE_ABBREVS = {
    # CS conferences
    "icml": "international conference on machine learning",
    "neurips": "advances in neural information processing systems",
    "nips": "advances in neural information processing systems",
    "iclr": "international conference on learning representations",
    "aaai": "association for the advancement of artificial intelligence",
    "ijcai": "international joint conference on artificial intelligence",
    "cvpr": "computer vision and pattern recognition",
    "iccv": "international conference on computer vision",
    "eccv": "european conference on computer vision",
    "acl": "association for computational linguistics",
    "naacl": "north american chapter of the association for computational linguistics",
    "emnlp": "empirical methods in natural language processing",
    "coling": "international conference on computational linguistics",
    "sigir": "special interest group on information retrieval",
    "www": "world wide web",
    "kdd": "knowledge discovery and data mining",
    "icde": "international conference on data engineering",
    "vldb": "very large data bases",
    "sigmod": "management of data",
    "fse": "foundations of software engineering",
    "icse": "international conference on software engineering",
    "issta": "international symposium on software testing and analysis",
    "osdi": "operating systems design and implementation",
    "sosp": "symposium on operating systems principles",
    "nsdi": "networked systems design and implementation",
    "usenix": "usenix security symposium",
    "ccs": "computer and communications security",
    "sp": "ieee symposium on security and privacy",
    "iros": "intelligent robots and systems",
    "icra": "international conference on robotics and automation",
    # Journals
    "jmlr": "journal of machine learning research",
    "pnas": "proceedings of the national academy of sciences",
    "jair": "journal of artificial intelligence research",
    "tpami": "transactions on pattern analysis and machine intelligence",
    "tkde": "transactions on knowledge and data engineering",
    "tse": "transactions on software engineering",
    "tosem": "transactions on software engineering and methodology",
    "kbs": "knowledge-based systems",
    "aij": "artificial intelligence",
    # Science journals
    "prl": "physical review letters",
    "jacs": "journal of the american chemical society",
    "angew": "angewandte chemie",
    "bmj": "british medical journal",
    "nejm": "new england journal of medicine",
    "lancet": "the lancet",
    "jama": "journal of the american medical association",
    # Scientometrics / Library science (synced from bibguard v0.3.0)
    "jasist": "journal of the association for information science and technology",
    "scientometrics": "scientometrics",
    "qss": "quantitative science studies",
    "jcdl": "joint conference on digital libraries",
    "joi": "journal of informetrics",
    "wsdm": "web search and data mining",
    "cikm": "conference on information and knowledge management",
}

# DBLP-style abbreviations → canonical forms
DBLP_VENUE_MAP = {
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
    "ieee trans. pattern anal. mach. intell.": "transactions on pattern analysis and machine intelligence",
    "ieee trans. knowl. data eng.": "transactions on knowledge and data engineering",
    "ieee trans. softw. eng.": "transactions on software engineering",
    # Synced from bibguard v0.3.0
    "sci. rep.": "scientific reports",
    "inf. process. manag.": "information processing and management",
    "j. assoc. inf. sci. technol.": "journal of the association for information science and technology",
    "j. informetr.": "journal of informetrics",
    "account. res.": "accountability in research",
    "learn. publ.": "learned publishing",
}


def normalize_venue(v: str) -> str:
    """Normalize a venue string for comparison."""
    v = strip_latex(v).lower().strip()
    for abbrev, full in DBLP_VENUE_MAP.items():
        if v == abbrev or v.startswith(abbrev):
            return full
    return v


# ── Main comparator class ────────────────────────────────────────────────

class FieldComparator:
    """Compare reference fields between bib entries and API results.

    Each match method returns (status, detail) where status is
    "OK", "WARN", or "FAIL".
    """

    # Configurable thresholds
    TITLE_OK_THRESHOLD = 0.85
    TITLE_WARN_THRESHOLD = 0.70
    SEARCH_MATCH_THRESHOLD = 0.60

    @staticmethod
    def match_title(bib_title: str, api_title: str) -> tuple[str, str]:
        """Compare titles using Jaccard token similarity."""
        score = token_similarity(bib_title, api_title)
        if score >= FieldComparator.TITLE_OK_THRESHOLD:
            return "OK", f"title match ({score:.2f})"
        elif score >= FieldComparator.TITLE_WARN_THRESHOLD:
            return "WARN", f"title partial match ({score:.2f}): '{api_title}'"
        else:
            return "FAIL", f"title mismatch ({score:.2f}): bib='{bib_title[:60]}' vs api='{api_title[:60]}'"

    @staticmethod
    def match_authors(bib_authors: list[str] | str,
                      api_authors: list[str]) -> tuple[str, str]:
        """Compare authors — first author surname + count heuristic.

        bib_authors can be a raw BibTeX author string or a list of names.
        """
        if not api_authors:
            return "WARN", "no authors from API"

        # Parse bib authors
        if isinstance(bib_authors, str):
            bib_list = FieldComparator._parse_bib_authors(bib_authors)
        else:
            bib_list = bib_authors

        if not bib_list:
            return "WARN", "no authors in reference"

        # Compare first author surnames
        bib_surname = FieldComparator._extract_surname(bib_list[0])
        api_surname = FieldComparator._extract_surname(api_authors[0])

        if not bib_surname or not api_surname:
            return "WARN", "could not extract surnames"

        surname_sim = author_name_similarity(bib_surname, api_surname)
        surname_ok = surname_sim >= 0.9

        # Count check
        bib_count = len(bib_list)
        api_count = len(api_authors)
        has_others = any("others" in a.lower() or "et al" in a.lower()
                         for a in bib_list)
        count_ok = has_others or abs(bib_count - api_count) <= 2

        if surname_ok and count_ok:
            return "OK", (f"first author '{bib_surname}' matches, "
                          f"count bib={bib_count} api={api_count}")
        elif surname_ok:
            return "WARN", (f"first author matches but count differs: "
                            f"bib={bib_count} api={api_count}")
        else:
            return "FAIL", (f"first author mismatch: "
                            f"bib='{bib_surname}' api='{api_surname}'")

    @staticmethod
    def match_year(bib_year: str, api_year: Optional[str]) -> tuple[str, str]:
        """Compare years — exact, ±1, ±2 (preprint vs published)."""
        if not api_year:
            return "WARN", "no year from API"
        try:
            by, ay = int(bib_year), int(api_year)
        except (ValueError, TypeError):
            return "WARN", f"unparseable years: bib={bib_year} api={api_year}"
        diff = abs(by - ay)
        if diff == 0:
            return "OK", f"year exact match ({by})"
        elif diff <= 1:
            return "WARN", f"year off by 1: bib={by} api={ay}"
        elif diff <= 2:
            return "WARN", f"year off by 2: bib={by} api={ay} (preprint vs published?)"
        else:
            return "FAIL", f"year mismatch: bib={by} api={ay}"

    @staticmethod
    def match_venue(bib_venue: str, api_venue: str) -> tuple[str, str]:
        """Compare venues with abbreviation awareness."""
        if not api_venue:
            return "WARN", "no venue from API"
        if not bib_venue:
            return "WARN", "no venue in bib"

        bib_v = normalize_venue(bib_venue)
        api_v = normalize_venue(api_venue)

        # arXiv preprint ↔ CoRR
        bib_is_arxiv = "arxiv" in bib_v or "corr" in bib_v
        api_is_arxiv = "arxiv" in api_v or "corr" in api_v
        if bib_is_arxiv and api_is_arxiv:
            return "OK", "venue match (both arXiv/CoRR)"

        # Direct token similarity
        score = token_similarity(bib_v, api_v)
        if score >= 0.5:
            return "OK", f"venue match ({score:.2f})"

        # Abbreviation matches
        for abbrev, full in VENUE_ABBREVS.items():
            bib_has = abbrev in bib_v or full in bib_v
            api_has = abbrev in api_v or full in api_v
            if bib_has and api_has:
                return "OK", f"venue match via abbreviation '{abbrev}'"

        # Substring containment
        if len(bib_v) > 3 and len(api_v) > 3:
            if bib_v in api_v or api_v in bib_v:
                return "OK", "venue match (substring)"

        return "WARN", f"venue unclear: bib='{bib_venue[:50]}' api='{api_venue[:50]}'"

    @staticmethod
    def validate_api_match(ref: dict, api_data: dict) -> bool:
        """Check if an API result is a plausible match for the reference.

        Rejects matches where author AND year are clearly wrong,
        preventing false positive matches.
        """
        if not api_data:
            return False

        api_authors = api_data.get("authors", [])
        if not api_authors:
            return True  # can't reject without author data

        # Extract surnames for comparison
        ref_authors = ref.get("authors", [])
        if isinstance(ref_authors, str):
            ref_authors = FieldComparator._parse_bib_authors(ref_authors)

        if not ref_authors:
            return True

        bib_surname = FieldComparator._extract_surname(ref_authors[0])
        api_surname = FieldComparator._extract_surname(api_authors[0])

        author_match = (strip_accents(bib_surname) == strip_accents(api_surname)
                        if bib_surname and api_surname else True)

        if not author_match:
            # Author doesn't match — check year too
            api_year = api_data.get("year")
            ref_year = ref.get("year", "")
            if api_year and ref_year:
                try:
                    if abs(int(ref_year) - int(api_year)) >= 2:
                        return False
                except (ValueError, TypeError):
                    pass
            # Also check title similarity
            api_title = api_data.get("title", "")
            ref_title = ref.get("title", "")
            if token_similarity(ref_title, api_title) < 0.85:
                return False

        return True

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_bib_authors(author_str: str) -> list[str]:
        """Parse BibTeX author string into a list of names."""
        clean = strip_latex(author_str)
        parts = re.split(r"\s+and\s+", clean)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _extract_surname(name: str) -> str:
        """Extract surname from a name string.

        Handles:
          - "First Last" → "Last"
          - "Last, First" → "Last"
          - "First Middle Last" → "Last"
        """
        name = strip_latex(name).strip()
        if not name:
            return ""
        if "," in name:
            return name.split(",")[0].strip().split()[-1]
        return name.split()[-1]

    @staticmethod
    def compute_match_score(checks: list[tuple[str, str]]) -> float:
        """Compute an aggregate match score from a list of (status, detail) checks.

        Returns 0-100 score.
        """
        if not checks:
            return 0.0
        score_map = {"OK": 100, "WARN": 60, "FAIL": 0}
        total = sum(score_map.get(status, 0) for status, _ in checks)
        return total / len(checks)
