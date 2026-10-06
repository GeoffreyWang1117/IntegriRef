"""Scan .tex files into citing sentences with byte offsets.

Sentence segmentation lives here and ONLY here: the cache stores byte offsets so
the reader never has to reproduce these boundaries. That is deliberate -- two
independent segmenters would disagree on "et al." or "Fig. 3" and every
disagreement would surface as a spurious STALE. See docs/DEEPCITE_SPEC_v1.1.md §5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .contract import (CITE_KEYS_RE, byte_span, file_sha256, strip_comments)

# Tokens whose trailing period does not end a sentence.
_ABBREV = {
    "al", "e.g", "i.e", "cf", "etc", "resp", "approx", "vs", "viz",
    "fig", "figs", "tab", "tabs", "sec", "secs", "eq", "eqs", "ref", "refs",
    "app", "alg", "thm", "lem", "def", "prop", "cor", "ch", "chap",
    "no", "nos", "vol", "vols", "pp", "p", "dr", "prof", "mr", "ms", "mrs",
    "st", "jr", "inc", "ltd", "ca", "cp", "ibid", "pers", "comm",
}
_SENT_END = re.compile(r"[.!?]")


@dataclass(frozen=True)
class Sentence:
    text: str          # raw slice of the comment-stripped text
    char_start: int
    char_end: int
    byte_start: int
    byte_end: int
    line: int          # 1-based line on which the sentence starts


@dataclass(frozen=True)
class Citation:
    bib_key: str
    sentence: Sentence


def _is_boundary(text: str, i: int) -> bool:
    """Is the terminator at index i a real sentence end?"""
    ch = text[i]
    nxt = text[i + 1:i + 2]
    # A period between digits is a decimal point, not a boundary.
    if ch == "." and nxt.isdigit():
        return False
    if ch == ".":
        # Walk back over the word immediately before the period.
        j = i - 1
        while j >= 0 and (text[j].isalnum() or text[j] in ".-"):
            j -= 1
        word = text[j + 1:i].lower().rstrip(".")
        if word in _ABBREV:
            return False
        # A single letter before the period is an initial ("C. D. Manning").
        if len(word) == 1 and word.isalpha():
            return False
    # Must be followed by whitespace (or end of text) to close a sentence.
    if nxt and not nxt.isspace():
        return False
    return True


# A line consisting only of a structural LaTeX command is a hard block boundary:
# without this, a preamble with no period runs into the first body sentence.
_STRUCTURAL = re.compile(
    r"^\s*\\(?:documentclass|usepackage|begin|end|input|include|includeonly"
    r"|section|subsection|subsubsection|paragraph|subparagraph|chapter|part"
    r"|title|author|institute|date|maketitle|item|caption|label|bibliography"
    r"|bibliographystyle|newcommand|renewcommand|def|setlength|setcounter"
    r"|keywords|titlerunning|authorrunning|tableofcontents|clearpage|newpage"
    r"|vspace|hspace|noindent|centering|footnotesize|small|normalsize)\b[^.!?]*$"
)


def _prose_blocks(stripped: str) -> list[tuple[int, int]]:
    """Maximal runs of consecutive prose lines, as char spans.

    Blank lines (paragraph breaks) and structural-command-only lines are
    excluded, which both prevents preamble text from merging into body prose and
    keeps junk "sentences" out of the candidate pool.
    """
    blocks: list[tuple[int, int]] = []
    pos = 0
    cur: int | None = None
    for line in stripped.split("\n"):
        end = pos + len(line)
        prose = bool(line.strip()) and not _STRUCTURAL.match(line)
        if prose:
            cur = pos if cur is None else cur
        elif cur is not None:
            blocks.append((cur, pos - 1 if pos > cur else pos))
            cur = None
        pos = end + 1          # +1 for the newline
    if cur is not None:
        blocks.append((cur, len(stripped)))
    return blocks


def segment(stripped: str) -> list[Sentence]:
    """Split comment-stripped text into sentences carrying char and byte spans."""
    out: list[Sentence] = []
    for b_start, b_end in _prose_blocks(stripped):
        start = b_start
        for m in _SENT_END.finditer(stripped, b_start, b_end):
            i = m.start()
            if not _is_boundary(stripped, i):
                continue
            out.append(_mk(stripped, start, i + 1))
            start = i + 1
        if start < b_end and stripped[start:b_end].strip():
            out.append(_mk(stripped, start, b_end))
    return [s for s in out if s.text.strip()]


def _mk(stripped: str, a: int, b: int) -> Sentence:
    # Trim surrounding whitespace but keep the span pointing at real characters,
    # so the stored offsets slice back to exactly the stored sentence.
    while a < b and stripped[a].isspace():
        a += 1
    while b > a and stripped[b - 1].isspace():
        b -= 1
    bs, be = byte_span(stripped, a, b)
    return Sentence(text=stripped[a:b], char_start=a, char_end=b,
                    byte_start=bs, byte_end=be,
                    line=stripped.count("\n", 0, a) + 1)


@dataclass(frozen=True)
class ScannedFile:
    relpath: str
    raw_sha256: str
    stripped: str
    citations: list[Citation]


def scan_file(root: Path, relpath: str) -> ScannedFile:
    raw = (root / relpath).read_bytes()
    stripped = strip_comments(raw.decode("utf-8", "replace"))
    cits: list[Citation] = []
    for sent in segment(stripped):
        for m in CITE_KEYS_RE.finditer(sent.text):
            for key in m.group(1).split(","):
                key = key.strip()
                if key:
                    cits.append(Citation(bib_key=key, sentence=sent))
    return ScannedFile(relpath=relpath, raw_sha256=file_sha256(raw),
                       stripped=stripped, citations=cits)


# Directories and filename prefixes that hold material which is not the paper.
# Scanning them fills the worklist with claims from drafts and old versions.
_SKIP_DIRS = {"archive", "archives", "old", "drafts", "backup", "backups",
              ".git", "build", "out", "_minted", "supplementary_archive"}
_SKIP_PREFIX = ("draft_", "old_", "unused_", "scratch_")


_INPUT = re.compile(r"\\(?:input|include|subfile|InputIfFileExists)\s*\{([^}]+)\}")
_DOCCLASS = re.compile(r"^[^%\n]*\\documentclass", re.MULTILINE)


def find_main(root: Path) -> str | None:
    """The top-level file that is the paper: main.tex if it is a document, else
    the only top-level .tex with \\documentclass and \\begin{document}."""
    cands = []
    for p in sorted(root.glob("*.tex")):
        if p.name.lower().startswith(_SKIP_PREFIX):
            continue
        try:
            txt = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if _DOCCLASS.search(txt) and "\\begin{document}" in txt:
            cands.append(p.name)
    if "main.tex" in cands:
        return "main.tex"
    return cands[0] if len(cands) == 1 else None


def reachable(root: Path, main_tex: str) -> list[str]:
    """main_tex and every file it pulls in through \\input/\\include, in order.

    Commented-out inputs are not followed. Paths resolve against the paper root
    (LaTeX's working directory) first, then the including file's directory.
    """
    out: list[str] = []
    seen: set[Path] = set()

    def visit(path: Path, depth: int) -> None:
        rp = path.resolve()
        if rp in seen or depth > 12 or not path.is_file():
            return
        seen.add(rp)
        try:
            out.append(str(path.relative_to(root)))
        except ValueError:
            return                      # outside the paper directory
        txt = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for m in _INPUT.finditer(txt):
            name = m.group(1).strip()
            for base in (root, path.parent):
                cand = base / name
                if cand.suffix != ".tex":
                    cand = cand.with_name(cand.name + ".tex") if not cand.is_file() else cand
                if cand.is_file():
                    visit(cand, depth + 1)
                    break

    visit(root / main_tex, 0)
    return out


def tex_files(root: Path, main_tex: str | None = None,
              include_all: bool = False) -> list[str]:
    """The .tex files to scan, main first.

    This is the selection input set recorded in files_sha256, so a citing file
    missing from the map reads as unscanned rather than clean. With a main file
    (given or found) it is exactly what the paper \\inputs: on 2026-10-06 the
    ICML template's example file, sitting beside the paper, put "Use the et al.
    construct ..." on the worklist. Without one, every .tex outside draft and
    archive material is scanned; include_all scans everything.
    """
    def wanted(p: Path) -> bool:
        if include_all:
            return True
        rel = p.relative_to(root)
        if any(part.lower() in _SKIP_DIRS for part in rel.parts[:-1]):
            return False
        return not rel.name.lower().startswith(_SKIP_PREFIX)

    if not include_all:
        main = main_tex or find_main(root)
        if main and (root / main).is_file():
            got = reachable(root, main)
            if got:
                return got

    found = sorted(str(p.relative_to(root)) for p in root.rglob("*.tex")
                   if wanted(p))
    if main_tex and main_tex in found:
        found.remove(main_tex)
        found.insert(0, main_tex)
    return found
