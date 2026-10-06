"""Scan a submission PDF into citing sentences mapped to its own reference list.

Review mode: the paper under review arrives as a PDF, with no .tex and no .bib.
This module rebuilds what ``texscan`` gets for free from LaTeX -- sentences with
offsets, and for each citation the reference-list entry it points at -- so the
rest of deepcite can treat the PDF like a scanned .tex file.

Layout comes from ``pdftotext -tsv`` (word boxes), not from the text stream:

  * margin line numbers (review mode), page numbers and running heads are
    dropped by position and repetition, not by guessing at the text;
  * two-column pages are ordered column by column, and small-font blocks
    (footnotes, captions, table cells) are moved to the end of their page so a
    sentence that continues from one column into the next is not cut in two;
  * reference entries are split on the hanging indent (author-year) or on their
    printed labels (numeric), which survives entries that run across columns.

Mapping a marker to an entry is held to PRECISION over recall: a numeric
bracket maps only if every label in it exists, an author-year marker only if
exactly one entry matches surname + year (+ a/b suffix). Anything else is
counted in ``warnings`` and produces no citation -- a wrong mapping would send
a human to check a claim against the wrong paper.

Stdlib only, plus the poppler ``pdftotext`` binary.
"""

from __future__ import annotations

import bisect
import hashlib
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .texscan import Sentence, segment

CITE_TOKEN = "CITEREF"


@dataclass(frozen=True)
class RefEntry:
    index: int            # 1-based position in the reference list
    label: str | None     # "12" for numeric styles, else None
    raw: str              # the entry text, whitespace-collapsed, dehyphenated
    title: str            # best title guess ("" if none)
    surnames: list[str]   # author surnames in order, lowercased, ascii-folded
    year: str | None
    arxiv_id: str | None
    doi: str | None


@dataclass(frozen=True)
class PdfCitation:
    ref_index: int        # RefEntry.index
    marker: str           # the in-text marker as printed
    sentence: Sentence    # offsets (char + UTF-8 byte) into ScannedPdf.body
    masked: str           # sentence with every citation marker -> "CITEREF"


@dataclass
class ScannedPdf:
    pdf_sha256: str
    body: str                 # main text, then any appendix after the references
    refs: list[RefEntry]
    citations: list[PdfCitation]
    style: str                # "numeric" | "author-year" | "unknown"
    warnings: list[str]
    # (char offset in body, page number) at each block start; see page_of().
    page_spans: list[tuple[int, int]] = field(default_factory=list, repr=False)
    # ``body`` continues with any appendix printed after the reference list, from
    # this char offset on (-1: none). Its citations count: a reviewer reads it.
    appendix_start: int = -1

    def page_of(self, char_offset: int) -> int | None:
        """1-based PDF page on which the body text at ``char_offset`` starts."""
        if not self.page_spans:
            return None
        i = bisect.bisect_right([s for s, _ in self.page_spans], char_offset) - 1
        return self.page_spans[max(i, 0)][1]


# --- layout from pdftotext -tsv ---------------------------------------------

@dataclass
class _Line:
    page: int
    x0: float
    x1: float
    y0: float
    y1: float
    h: float
    text: str


@dataclass
class _Block:
    page: int
    lines: list[_Line]
    col: int = 0          # 0 / 1, or -1 spanning
    rank: int = 0         # ordering bucket within the page

    @property
    def x0(self) -> float:
        return min(l.x0 for l in self.lines)

    @property
    def x1(self) -> float:
        return max(l.x1 for l in self.lines)

    @property
    def y0(self) -> float:
        return min(l.y0 for l in self.lines)

    @property
    def y1(self) -> float:
        return max(l.y1 for l in self.lines)

    @property
    def h(self) -> float:
        hs = sorted(l.h for l in self.lines)
        return hs[len(hs) // 2]

    @property
    def text(self) -> str:
        return " ".join(l.text for l in self.lines)


def _run_tsv(pdf: Path) -> str:
    try:
        r = subprocess.run(["pdftotext", "-tsv", str(pdf), "-"], capture_output=True,
                           text=True, errors="replace", timeout=300)
    except FileNotFoundError as e:
        raise RuntimeError("pdftotext (poppler-utils) is required for PDF review mode") from e
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext failed on {pdf}: {r.stderr.strip()[:200]}")
    return r.stdout


_PURE_NUMBER = re.compile(r"^\d{1,4}$")


def _layout(tsv: str) -> tuple[dict[int, tuple[float, float]], list[_Block]]:
    """Pages (width, height) and blocks of lines, in pdftotext's block grouping."""
    sizes: dict[int, tuple[float, float]] = {}
    words: dict[tuple, list[tuple[int, float, float, float, float, str]]] = defaultdict(list)
    for row in tsv.split("\n")[1:]:
        f = row.split("\t")
        if len(f) < 12:
            continue
        level, page = f[0], int(f[1])
        if level == "1":
            sizes[page] = (float(f[8]), float(f[9]))
        elif level == "5":
            text = f[11]
            if not text.strip():
                continue
            # (page, par, block) is a block; line_num orders lines inside it.
            words[(page, int(f[2]), int(f[3]), int(f[4]))].append(
                (int(f[5]), float(f[6]), float(f[7]), float(f[8]), float(f[9]), text))
    blocks: dict[tuple, list[_Line]] = defaultdict(list)
    for (page, par, blk, ln), ws in words.items():
        ws.sort()
        text = " ".join(w[5] for w in ws)
        if _PURE_NUMBER.match(text.strip()):
            continue        # page numbers, review-mode margin line numbers, table cells
        hs = sorted(w[4] for w in ws)
        blocks[(page, par, blk)].append(_Line(
            page=page, x0=min(w[1] for w in ws), x1=max(w[1] + w[3] for w in ws),
            y0=min(w[2] for w in ws), y1=max(w[2] + w[4] for w in ws),
            h=hs[len(hs) // 2], text=text))
    out = []
    for (page, _, _), lines in blocks.items():
        lines.sort(key=lambda l: (l.y0, l.x0))
        merged: list[_Line] = []
        for l in lines:
            p = merged[-1] if merged else None
            # poppler sometimes splits one printed line into two "lines" whose
            # tops differ by a fraction of a point ("buffer.py," / "2023.");
            # sorting those by y alone reverses them.
            if p and abs(l.y0 - p.y0) < 0.4 * min(l.h, p.h):
                a, b = (p, l) if p.x0 <= l.x0 else (l, p)
                merged[-1] = _Line(page=page, x0=a.x0, x1=max(a.x1, b.x1),
                                   y0=min(a.y0, b.y0), y1=max(a.y1, b.y1),
                                   h=max(a.h, b.h), text=a.text + " " + b.text)
            else:
                merged.append(l)
        out.append(_Block(page=page, lines=merged))
    return sizes, out


def _drop_running_heads(blocks: list[_Block], sizes: dict) -> list[_Block]:
    """Remove text repeated at the top or bottom of several pages."""
    npages = max(1, len(sizes))
    sig_pages: dict[str, set[int]] = defaultdict(set)

    def sig(b: _Block) -> str | None:
        _, hgt = sizes.get(b.page, (612.0, 792.0))
        if b.y0 < 0.09 * hgt or b.y1 > 0.93 * hgt:
            return re.sub(r"\d+", "#", b.text.lower()).strip()
        return None

    for b in blocks:
        s = sig(b)
        if s:
            sig_pages[s].add(b.page)
    need = max(2, int(0.25 * npages))
    return [b for b in blocks if not (sig(b) and len(sig_pages[sig(b)]) >= need)]


def _order(blocks: list[_Block], sizes: dict) -> list[_Block]:
    """Geometric reading order: per page, top spanning, column 0, column 1, rest."""
    by_page: dict[int, list[_Block]] = defaultdict(list)
    for b in blocks:
        by_page[b.page].append(b)
    out: list[_Block] = []
    for page in sorted(by_page):
        bs = by_page[page]
        w, _ = sizes.get(page, (612.0, 792.0))
        for b in bs:
            b.col = 1 if b.x0 >= 0.45 * w else (0 if b.x1 <= 0.55 * w else -1)
        right = sum(len(b.text) for b in bs if b.col == 1)
        total = sum(len(b.text) for b in bs) or 1
        if right < 0.15 * total:
            for b in bs:                      # single-column page
                b.col, b.rank = 0, 1
            bs.sort(key=lambda b: (b.y0, b.x0))
            out.extend(bs)
            continue
        cols = [b for b in bs if b.col >= 0]
        top = min((b.y0 for b in cols), default=0.0)
        for b in bs:
            if b.col == -1:
                b.rank = 0 if b.y0 <= top + 1 else 3
            else:
                b.rank = 1 + b.col
        bs.sort(key=lambda b: (b.rank, b.y0, b.x0))
        out.extend(bs)
    return out


# --- text assembly ------------------------------------------------------------

_COMPOUND_FIRST = {
    "self", "non", "multi", "cross", "well", "high", "low", "long", "short",
    "large", "small", "open", "zero", "few", "one", "two", "three", "four",
    "five", "six", "seven", "eight", "nine", "ten", "single", "double", "real",
    "state", "time", "data", "task", "model", "human", "rule", "user", "third",
    "first", "second", "last", "top", "end", "fine", "full", "half", "semi",
    "context", "domain", "token", "query", "key", "out", "in-context",
}
_WORDS = re.compile(r"[A-Za-z][A-Za-z']+")
_HYPHENATED = re.compile(r"\b([A-Za-z]+)-([A-Za-z]+)\b")


class _Vocab:
    """Words seen in the document, for deciding what a line-end hyphen was."""

    def __init__(self, lines: list[str]):
        self.words: set[str] = set()
        self.hyph: set[str] = set()
        for t in lines:
            body = t[:-1] if t.endswith("-") else t
            self.words.update(w.lower() for w in _WORDS.findall(body))
            self.hyph.update(f"{a}-{b}".lower() for a, b in _HYPHENATED.findall(body))


def _join_hyphen(left: str, right: str, vocab: _Vocab) -> str:
    """Join two line pieces where ``left`` ends with '-'."""
    m = re.search(r"([A-Za-z0-9]*)-$", left)
    lw = m.group(1) if m else ""
    rm = re.match(r"([A-Za-z0-9]*)", right)
    rw = rm.group(1) if rm else ""
    keep = (
        not lw or not rw
        or any(c.isdigit() for c in lw) or rw[:1].isdigit()
        or f"{lw}-{rw}".lower() in vocab.hyph
    )
    if not keep and (lw + rw).lower() in vocab.words:
        return left[:-1] + right
    if not keep:
        keep = (rw[:1].isupper() or lw.isupper() and len(lw) > 1
                or any(c.isupper() for c in lw[1:]) or lw.lower() in _COMPOUND_FIRST
                # "instruction-|following": both halves occur as words of their
                # own and the joined form never does -> a compound, keep the hyphen.
                or len(lw) >= 3 and len(rw) >= 3 and lw.lower() in vocab.words
                and rw.lower() in vocab.words)
    return left + right if keep else left[:-1] + right


def _join_lines(lines: list[str], vocab: _Vocab) -> str:
    out = ""
    for t in lines:
        t = t.strip()
        if not t:
            continue
        if not out:
            out = t
        elif out.endswith("-") and len(out) > 1 and out[-2].isalnum():
            out = _join_hyphen(out, t, vocab)
        else:
            out += "\n" + t
    return out


_TERMINAL = re.compile(r"[.!?:;][\"'”’)\]]*$")


def _continues(prev: str, nxt: str) -> bool:
    """Does block ``nxt`` continue the sentence that ``prev`` left open?"""
    if not prev or not nxt:
        return False
    if prev.endswith("-") and nxt[:1].isalpha():
        return True
    return not _TERMINAL.search(prev) and nxt[:1].islower()


# --- references heading and section boundaries --------------------------------

_REF_WORDS = {"references", "bibliography", "referencesandnotes", "literaturecited"}
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVXL]+\.)\s*")


def _is_ref_heading(b: _Block) -> bool:
    if len(b.lines) > 2 or len(b.text) > 40:
        return False
    t = _NUMBERING.sub("", b.text.strip())
    return re.sub(r"\s+", "", t).lower() in _REF_WORDS


_APPENDIX = re.compile(
    r"^(?:Appendi(?:x|ces)\b|APPENDI(?:X|CES)\b|Supplementary\b|SUPPLEMENTARY\b"
    r"|[A-H](?:\.\d+)*\.?\s+[A-Z][A-Za-z]|Checklist\b|NeurIPS Paper Checklist)")
_YEAR_ANY = re.compile(r"(?:19|20)\d{2}")


def _is_appendix_heading(b: _Block, body_h: float) -> bool:
    if len(b.lines) != 1 or len(b.text) > 90 or _YEAR_ANY.search(b.text):
        return False
    if b.h < 0.97 * body_h:
        return False
    return bool(_APPENDIX.match(b.text.strip()))


# --- reference entries ----------------------------------------------------------

_LABEL_BRACKET = re.compile(r"^\[(\d{1,4})\]\s*")
_LABEL_DOT = re.compile(r"^(\d{1,4})\.\s+(?=\S)")
_ARXIV = re.compile(r"(?:arXiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
_DOI = re.compile(r"(?:doi\s*:\s*|doi\.org/)(10\.\d{4,9}/[^\s,;]+)", re.I)
_YEAR = re.compile(r"(?<![\d.:/])((?:19|20)\d{2})([a-z]?)(?![\d]|\.\d|-\d)")
_INITIAL_END = re.compile(r"(?:^|[\s,(\-])(?:[A-Z][a-z]?\.(?:-[A-Z]\.)?|al\.)$")
_NM = r"[A-Z][\w'’\-]+"
# A piece that continues an author list, whatever the style.
_CONT_ANY = re.compile(r"^(?:[A-Z][a-z]?\.|and\b|&|et al\.)")
# Surname-first ("Cover, T. M. and Thomas, J. A."): the next author is "Surname, I.".
_CONT_SURNAME_FIRST = re.compile(rf"^{_NM}(?:\s{_NM})?,\s*[A-Z][a-z]?\.")
# First-last ("David F. Ransohoff and Alvan R. Feinstein."): the list goes on while a
# piece ends on an initial, is a lone surname, or opens with "Name," / "Name and".
_CONT_FIRST_LAST = re.compile(rf"^(?:{_NM}\.$|{_NM}(?:,|\s+and\s|\s+&\s)|[A-Z].*\s[A-Z]\.$)")
_SURNAME_FIRST = re.compile(r"^[^,.]+,\s*[A-Z][a-z]?\.")
_PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "del", "la", "le", "du", "dos", "das"}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z\s\-]", "", s.lower()).strip()


def _pieces(entry: str) -> list[str]:
    return [p for p in re.split(r"(?<=\.)\s+", entry) if p]


def _author_block(entry: str) -> tuple[str, list[str]]:
    """Split an entry into (authors, the remaining '. '-separated pieces)."""
    # LNCS: "Schick, T., Dwivedi-Yu, J.: Toolformer: ..." -- authors end at "I.:".
    m = re.match(r"^(.*?[A-Z]\.(?:\s*et al\.)?):\s+(.*)$", entry)
    if m and "," in m.group(1):
        return m.group(1), _pieces(m.group(2))
    # IEEE / ACM numeric: authors, “Title,” venue -- the quote ends the authors.
    q = re.match(r"^(.*?),\s*[\u201c\"]", entry)
    if q and len(q.group(1)) < 400 and "(" not in q.group(1):
        return q.group(1), _pieces(entry[q.end() - 1:])
    ps = _pieces(entry)
    if not ps:
        return "", []
    authors, i = ps[0], 1
    cont = _CONT_SURNAME_FIRST if _SURNAME_FIRST.match(entry) else _CONT_FIRST_LAST
    while i < len(ps) and _INITIAL_END.search(authors) and \
            (_CONT_ANY.match(ps[i]) or cont.match(ps[i])):
        authors += " " + ps[i]
        i += 1
    return authors, ps[i:]


def _surnames(authors: str) -> list[str]:
    a = re.sub(r",?\s*\bet al\.?", "", authors).strip().rstrip(", ")
    # Surname-first: "Cover, T. M. and Thomas, J. A." / "Devarangadi Sunil, B."
    pairs = re.findall(r"(?:^|,\s*|\band\s+|&\s*)([^,]+?),\s*((?:[A-Z][a-z]?\.\s?-?)+)", a)
    if pairs and re.match(r"^[^,.]+,\s*[A-Z][a-z]?\.", a):
        return [_fold(re.sub(r"^(?:and|&)\s+", "", s)) for s, _ in pairs]
    out = []
    for name in re.split(r",\s*(?:and\s+|&\s*)?|\s+and\s+|\s+&\s+", a.rstrip(".")):
        name = re.sub(r"\b[A-Z][a-z]?\.\s*-?", " ", name).strip()     # drop initials
        toks = name.split()
        if not toks:
            continue
        # keep lowercase particles with the surname: "Aaron van den Oord" -> "van den oord"
        j = len(toks) - 1
        while j > 0 and toks[j - 1].lower() in _PARTICLES:
            j -= 1
        out.append(_fold(" ".join(toks[j:])))
    return [s for s in out if s]


_NOT_TITLE = re.compile(r"^(?:In\b|In:|Proceedings\b|Proc\.|arXiv\b|https?:|URL\b|Accessed\b"
                        r"|Technical report|PhD thesis|Master|pp\.|vol\.|Advances in)", re.I)


def _title(entry: str, rest: list[str]) -> str:
    q = re.search(r"[“\"](.+?)[,.]?[”\"]", entry)          # IEEE: "Title,"
    if q and len(q.group(1).split()) >= 2:
        return q.group(1).strip().rstrip(",.")
    for p in rest:
        p = p.strip()
        if _YEAR.fullmatch(p.rstrip(".")) or re.fullmatch(r"\(?(?:19|20)\d{2}[a-z]?\)?\.?", p):
            continue                                                  # ACL: "Authors. 2023. Title."
        if _NOT_TITLE.match(p) or len(p.split()) < 2:
            continue
        return re.sub(r"\s+", " ", p).rstrip(".").strip()
    return ""


def _entry_years(raw: str) -> list[tuple[str, str]]:
    return [(y, s) for y, s in _YEAR.findall(raw)]


def _make_entry(index: int, label: str | None, raw: str) -> RefEntry:
    raw = re.sub(r"\s+", " ", raw).strip()
    authors, rest = _author_block(raw)
    years = _entry_years(raw)
    year = None
    if years:
        suffixed = [y for y, s in years if s]
        lead = _YEAR.fullmatch(rest[0].strip().strip("().")) if rest else None
        # ACL/ACM: "Authors. 2023. Title." -- the year right after the authors is it.
        year = lead.group(1) if lead else (suffixed[0] if suffixed else years[-1][0])
    ax = _ARXIV.search(raw)
    doi = _DOI.search(raw)
    return RefEntry(index=index, label=label, raw=raw, title=_title(raw, rest),
                    surnames=_surnames(authors), year=year,
                    arxiv_id=ax.group(1) if ax else None,
                    doi=doi.group(1).rstrip(".") if doi else None)


def _is_label_run(nums: list[int]) -> bool:
    """[1, 2, 3, ...] as printed labels -- not years or page numbers at line starts."""
    if len(nums) < 3 or nums[0] > 2 or max(nums) > 999:
        return False
    steps = sum(1 for a, b in zip(nums, nums[1:]) if b == a + 1)
    return steps >= 0.8 * (len(nums) - 1)


def _split_refs(lines: list[_Line], vocab: _Vocab) -> tuple[list[RefEntry], str]:
    """Reference lines (in reading order) -> entries, and the label style."""
    texts = [l.text.strip() for l in lines]
    rx, style = None, "author-year"
    for cand in (_LABEL_BRACKET, _LABEL_DOT):
        if _is_label_run([int(m.group(1)) for t in texts if (m := cand.match(t))]):
            rx, style = cand, "numeric"
            break

    groups: list[tuple[str | None, list[str]]] = []
    if rx is not None:
        for t in texts:
            m = rx.match(t)
            if m:
                groups.append((m.group(1), [t[m.end():]]))
            elif groups:
                groups[-1][1].append(t)
    else:
        # Hanging indent: an entry's first line sits at the column's left edge,
        # its continuation lines are indented. Margins per column, across pages.
        margin: dict[int, float] = {}
        for l, t in zip(lines, texts):
            c = getattr(l, "_col", 0)
            margin[c] = min(margin.get(c, 1e9), l.x0)
        indented = sum(1 for l in lines if l.x0 > margin.get(getattr(l, "_col", 0), 0) + 2.5)
        hanging = indented >= 0.2 * max(1, len(lines))
        for l, t in zip(lines, texts):
            c = getattr(l, "_col", 0)
            starts = (l.x0 <= margin[c] + 2.5) if hanging else \
                (not groups or getattr(l, "_block_start", False))
            if starts or not groups:
                groups.append((None, [t]))
            else:
                groups[-1][1].append(t)
    entries = []
    for i, (label, ls) in enumerate(groups, 1):
        raw = _join_lines(ls, vocab).replace("\n", " ")
        if len(raw) < 12:
            continue
        entries.append(_make_entry(len(entries) + 1, label, raw))
    return entries, style


# --- in-text markers --------------------------------------------------------------

_NUM_MARKER = re.compile(
    r"\[(\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*[,;]\s*\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)*)\]")
_PART = r"(?:(?:van|von|de|der|den|da|di|del|la|le|du)\s+)*"
_NAME = rf"{_PART}[A-Z][\w'’\-]+(?:\s+[A-Za-z][\w'’\-]+)?"
_AUTHORS = rf"(?P<authors>{_NAME}(?:\s+et\s+al\.?|\s+(?:and|&)\s+{_NAME})?)"
_YEARS = r"(?P<years>(?:19|20)\d{2}[a-z]?(?:\s*[,;]\s*(?:(?:19|20)\d{2})?[a-z]?)*)"
_TEXTUAL = re.compile(rf"{_AUTHORS}\s*[(\[]\s*{_YEARS}\s*[)\]]")
_PAREN = re.compile(r"\(([^()]*?(?:19|20)\d{2}[a-z]?[^()]*?)\)|\[([^\[\]]*?[A-Za-z][^\[\]]*?(?:19|20)\d{2}[a-z]?[^\[\]]*?)\]")
_PART_RX = re.compile(rf"{_AUTHORS}\s*,?\s*{_YEARS}\s*(?:[,;]|$)")
_TAIL_AUTHORS = re.compile(rf"{_AUTHORS}\s*,?\s*$")
_YEAR_TOKEN = re.compile(r"((?:19|20)\d{2})?([a-z]?)")


@dataclass
class _Marker:
    start: int
    end: int
    text: str
    indices: list[int]


def _expand_numeric(spec: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"\s*[,;]\s*", spec):
        m = re.match(r"(\d+)\s*[-–—]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b < a or b - a > 50:
                return []
            out.extend(str(i) for i in range(a, b + 1))
        elif part.isdigit():
            out.append(part)
        else:
            return []
    return out


def _years_of(spec: str) -> list[tuple[str, str]]:
    """'2023a,b' -> [(2023,a),(2023,b)]; '2022; 2023' -> [(2022,''),(2023,'')]."""
    out: list[tuple[str, str]] = []
    base = ""
    for tok in re.split(r"\s*[,;]\s*", spec.strip()):
        m = _YEAR_TOKEN.fullmatch(tok)
        if not m or not (m.group(1) or base):
            continue
        base = m.group(1) or base
        out.append((base, m.group(2)))
    return out


class _AuthorYearIndex:
    def __init__(self, refs: list[RefEntry]):
        self.refs = refs
        self.years = {e.index: set(_entry_years(e.raw)) for e in refs}

    @staticmethod
    def _last(s: str) -> str:
        return s.split()[-1] if s.split() else ""

    def lookup(self, authors: str, year: str, suffix: str) -> list[int]:
        a = re.sub(r"\s+et\s+al\.?$", "", authors.strip())
        etal = a != authors.strip()
        second = None
        m = re.match(rf"^(.*?)\s+(?:and|&)\s+({_NAME})$", a)
        if m:
            a, second = m.group(1), _fold(m.group(2))
        first = _fold(a)
        cands = [e for e in self.refs if (year, suffix) in self.years[e.index] and e.surnames]
        hit = [e for e in cands if e.surnames[0] == first]
        if not hit:
            hit = [e for e in cands if self._last(e.surnames[0]) == self._last(first)]
        if len(hit) > 1 and second:
            hit = [e for e in hit if len(e.surnames) > 1
                   and self._last(e.surnames[1]) == self._last(second)]
        if len(hit) > 1 and etal:          # "Russo et al." is not "Russo and Van Roy"
            hit = [e for e in hit if len(e.surnames) >= 3] or hit
        if len(hit) > 1 and second and not etal:   # "Russo and Van Roy": two authors
            hit = [e for e in hit if len(e.surnames) == 2] or hit
        if len(hit) > 1 and not etal and not second:
            hit = [e for e in hit if len(e.surnames) == 1] or hit
        return [hit[0].index] if len(hit) == 1 else []


def _interval_context(body: str, pos: int, is_interval) -> bool:
    """Is the bracket at ``pos`` among numbers rather than prose?

    A results table prints confidence intervals as "[44,59]"; when both ends
    happen to be valid reference labels ("[1,5]") the bracket reads like a
    citation. Two signals mark the paragraph as numeric: another bracket in it
    that cannot be a citation (contains 0 or exceeds the list), or a token mix
    that is mostly numbers.
    """
    # "3.3 [2,7]" / "95.0 [91,97]": a value followed by its interval.
    if re.search(r"\d(?:\.\d+)?\s*%?\s*$", body[max(0, pos - 12):pos]):
        return True
    a = body.rfind("\n\n", 0, pos)
    b = body.find("\n\n", pos)
    para = body[a + 2 if a >= 0 else 0: b if b >= 0 else len(body)]
    if any(is_interval(_expand_numeric(m.group(1))) for m in _NUM_MARKER.finditer(para)):
        return True
    toks = re.findall(r"[A-Za-z]{2,}|\d+(?:\.\d+)?", _NUM_MARKER.sub(" ", para))
    nums = sum(1 for x in toks if x[0].isdigit())
    return (len(toks) >= 6 and nums / len(toks) > 0.45) or (len(toks) >= 2 and nums / len(toks) > 0.6)


def _find_markers(body: str, refs: list[RefEntry], style: str) -> tuple[list[_Marker], list[str]]:
    markers: list[_Marker] = []
    unmatched: list[str] = []
    if style == "numeric":
        by_label = {e.label: e.index for e in refs if e.label}

        def is_interval(labels: list[str]) -> bool:
            return not labels or not all(0 < int(l) <= len(refs) + 5 for l in labels)

        for m in _NUM_MARKER.finditer(body):
            labels = _expand_numeric(m.group(1))
            if len(labels) >= 2 and _interval_context(body, m.start(), is_interval):
                continue        # "[44,59] ... [1,5]" in a results table: intervals
            if labels and all(l in by_label for l in labels):
                markers.append(_Marker(m.start(), m.end(), m.group(0),
                                       [by_label[l] for l in labels]))
            elif labels and all(0 < int(l) <= len(refs) + 5 for l in labels):
                unmatched.append(m.group(0))
            # else: "[0, 1]", "[75,85]" -- an interval, not a citation; ignore silently
        return markers, unmatched

    idx = _AuthorYearIndex(refs)
    taken: list[tuple[int, int]] = []
    for m in _PAREN.finditer(body):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        found: list[int] = []
        prev_authors = None
        any_cite = False
        for part in re.split(r"\s*;\s*", inner):
            pm = _PART_RX.search(part)
            if pm:
                authors, years = pm.group("authors"), pm.group("years")
                any_cite = True
            elif prev_authors and _YEAR_TOKEN.fullmatch(part.strip()):
                authors, years = prev_authors, part.strip()     # "(Gravitas, 2023a;b)"
            else:
                continue
            prev_authors = authors
            for y, s in _years_of(years):
                got = idx.lookup(authors, y, s)
                if got:
                    found.extend(got)
                else:
                    unmatched.append(f"{authors}, {y}{s}")
        if any_cite:
            taken.append((m.start(), m.end()))
            if found:
                markers.append(_Marker(m.start(), m.end(),
                                       re.sub(r"\s+", " ", m.group(0).strip("()[]")),
                                       list(dict.fromkeys(found))))
    for m in _TEXTUAL.finditer(body):
        if any(a <= m.start() < b for a, b in taken):
            continue
        found = []
        for y, s in _years_of(m.group("years")):
            got = idx.lookup(m.group("authors"), y, s)
            if not got:
                # "Recently Zhong et al. (2023)": retry on the last name alone.
                tail = _TAIL_AUTHORS.search(m.group("authors").split(None, 1)[-1]) \
                    if " " in m.group("authors") else None
                got = idx.lookup(tail.group("authors"), y, s) if tail else []
            if got:
                found.extend(got)
            else:
                unmatched.append(f"{m.group('authors')} ({y}{s})")
        if found:
            markers.append(_Marker(m.start(), m.end(), re.sub(r"\s+", " ", m.group(0)),
                                   list(dict.fromkeys(found))))
    markers.sort(key=lambda k: k.start)
    return markers, unmatched


# --- driver ---------------------------------------------------------------------

def scan_pdf(pdf: Path) -> ScannedPdf:
    pdf = Path(pdf)
    raw_bytes = pdf.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    warnings: list[str] = []

    sizes, blocks = _layout(_run_tsv(pdf))
    blocks = _drop_running_heads(blocks, sizes)
    ordered = _order(blocks, sizes)
    if not ordered:
        return ScannedPdf(sha, "", [], [], "unknown", ["no text extracted (scanned image PDF?)"])

    weight: Counter = Counter()
    for b in ordered:
        for l in b.lines:
            weight[round(l.h, 1)] += len(l.text)
    body_h = weight.most_common(1)[0][0]
    vocab = _Vocab([l.text for b in ordered for l in b.lines])

    # Where the reference list starts and stops.
    head = next((i for i, b in enumerate(ordered) if _is_ref_heading(b)
                 and i > len(ordered) // 4), None)
    if head is None:
        head = next((i for i, b in enumerate(ordered) if _is_ref_heading(b)), None)
    if head is None:
        warnings.append("no References heading found; reference list not parsed")
        body_blocks, ref_blocks, appendix_blocks = ordered, [], []
    else:
        body_blocks = ordered[:head]
        after = ordered[head + 1:]
        hs = sorted(l.h for b in after[:12] for l in b.lines) or [body_h]
        ref_h = hs[len(hs) // 2]
        stop = len(ordered)
        for j in range(head + 1, len(ordered)):
            b = ordered[j]
            heading = (len(b.lines) <= 2 and b.h >= 1.1 * ref_h and len(b.text) < 90
                       and not _YEAR_ANY.search(b.text))
            if j > head + 1 and (heading or _is_appendix_heading(b, body_h)):
                stop = j
                break
        ref_blocks = ordered[head + 1:stop]
        # Appendices printed after the reference list: a reviewer reads them.
        appendix_blocks = ordered[stop:]

    # Body: per page, small-font blocks (footnotes, captions, table cells) go
    # last so they do not split a sentence running from one column to the next.
    def parts(blocks_: list[_Block]) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        by_page: dict[int, list[_Block]] = defaultdict(list)
        for b in blocks_:
            by_page[b.page].append(b)
        for page in sorted(by_page):
            bs = by_page[page]
            main = [b for b in bs if b.h >= 0.88 * body_h]
            small = [b for b in bs if b.h < 0.88 * body_h]
            for b in main + small:
                out.append((page, _join_lines([l.text for l in b.lines], vocab)))
        return out

    body = ""
    page_spans: list[tuple[int, int]] = []
    appendix_start = -1
    for section, blocks_ in (("main", body_blocks), ("appendix", appendix_blocks)):
        for k, (page, text) in enumerate(parts(blocks_)):
            if not text:
                continue
            if body and k and _continues(body, text):
                body = _join_hyphen(body, text, vocab) if body.endswith("-") else body + "\n" + text
            else:
                start = len(body) + (2 if body else 0)
                if section == "appendix" and appendix_start < 0:
                    appendix_start = start
                page_spans.append((start, page))
                body = body + "\n\n" + text if body else text

    # References.
    ref_lines: list[_Line] = []
    for b in ref_blocks:
        for k, l in enumerate(b.lines):
            l._col = b.col if b.col >= 0 else 0           # type: ignore[attr-defined]
            l._block_start = k == 0                        # type: ignore[attr-defined]
            ref_lines.append(l)
    refs, ref_style = _split_refs(ref_lines, vocab) if ref_lines else ([], "unknown")
    if head is not None and not refs:
        warnings.append("References heading found but no entries parsed")

    markers, unmatched = _find_markers(body, refs, ref_style) if refs else ([], [])
    style = ref_style if refs and markers else ("unknown" if not refs else ref_style)
    if refs and not markers:
        warnings.append(f"{len(refs)} reference entries parsed but no in-text marker "
                        f"mapped to one ({ref_style} style assumed)")
    if unmatched:
        ex = "; ".join(dict.fromkeys(unmatched))
        warnings.append(f"{len(unmatched)} citation marker(s) matched no single reference "
                        f"entry and were skipped: {ex[:300]}")

    sentences = segment(body)
    starts = [s.char_start for s in sentences]
    by_sentence: dict[int, list[_Marker]] = defaultdict(list)
    for mk in markers:
        i = bisect.bisect_right(starts, mk.start) - 1
        if i >= 0 and sentences[i].char_start <= mk.start < sentences[i].char_end:
            by_sentence[i].append(mk)
    citations: list[PdfCitation] = []
    for i in sorted(by_sentence):
        s = sentences[i]
        masked = s.text
        for mk in sorted(by_sentence[i], key=lambda k: -k.start):
            a = mk.start - s.char_start
            b = min(mk.end, s.char_end) - s.char_start
            masked = masked[:a] + CITE_TOKEN + masked[b:]
        seen: set[int] = set()
        for mk in by_sentence[i]:
            for ri in mk.indices:
                if ri in seen:
                    continue
                seen.add(ri)
                citations.append(PdfCitation(ref_index=ri, marker=mk.text,
                                             sentence=s, masked=masked))
    return ScannedPdf(pdf_sha256=sha, body=body, refs=refs, citations=citations,
                      style=style, warnings=warnings, page_spans=page_spans,
                      appendix_start=appendix_start)
