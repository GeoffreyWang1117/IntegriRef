"""Verify an LLM opinion mechanically. No SUPPORTED label is ever produced.

An opinion is accepted only if every quote in it resolves, after normalization,
to text actually present in the cached artifact -- and the file and line are
recorded. A single unresolved quote rejects the whole opinion (exit 5).

This is the guard against the ChroKnowBench failure: that error came from a
secondary summary, and only a quote that must resolve against the primary
artifact forces the primary artifact to be read.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


class QuoteRejected(RuntimeError):
    pass


def qnorm(s: str) -> str:
    """Normalization for quote matching: tolerant of whitespace and LaTeX noise,
    intolerant of different words."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?", " ", s)
    s = re.sub(r"[{}$~\\]", " ", s)
    s = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", s)
    s = re.sub(r"[^0-9a-z%'\s.-]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class VerifiedQuote:
    text: str
    file: str
    line: int


def locate(quote: str, files: list[Path], root: Path) -> VerifiedQuote | None:
    """Find a quote in the artifact, returning its file and 1-based line."""
    needle = qnorm(quote)
    if len(needle) < 12:
        return None            # too short to be evidence of anything
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle not in qnorm(text):
            continue
        lines = text.split("\n")
        # Report the first line of the smallest window that contains the quote.
        for size in (1, 2, 3, 5, 8):
            for i in range(len(lines)):
                if needle in qnorm("\n".join(lines[i:i + size])):
                    rel = str(path.relative_to(root)) if path != root else path.name
                    return VerifiedQuote(text=quote, file=rel, line=i + 1)
    return None


ASSESSMENTS = ("mismatch", "no_mismatch_found", "unclear")


def verify_opinion(opinion: dict, files: list[Path], root: Path) -> dict:
    """Return the opinion with every quote verified, or raise QuoteRejected.

    ``assessment`` (optional) is one of mismatch / no_mismatch_found / unclear.
    There is no "supported": a reader of the quotes can see that they do not
    contradict the citing sentence, never that the citation is right. "unclear"
    alone may come without a quote, and then must list what was searched for --
    an absence cannot be quoted, but it can be audited.
    """
    assessment = opinion.get("assessment")
    if assessment is not None and assessment not in ASSESSMENTS:
        raise QuoteRejected(f"assessment must be one of {', '.join(ASSESSMENTS)}, "
                            f"not {assessment!r}")
    quotes = opinion.get("quotes") or []
    if not quotes:
        if assessment == "unclear" and opinion.get("searched"):
            return {"text": opinion.get("text", ""), "assessment": assessment,
                    "quotes": [], "searched": list(opinion["searched"])}
        raise QuoteRejected("opinion cites no passage; a judgment must quote the "
                            "cited work's own text (only 'unclear' may instead list "
                            "the terms it searched for)")
    out = []
    for q in quotes:
        text = (q or {}).get("text", "") if isinstance(q, dict) else str(q)
        found = locate(text, files, root)
        if not found:
            raise QuoteRejected(
                f"quote does not appear in the cached artifact: {text[:90]!r}")
        out.append({"text": found.text, "file": found.file, "line": found.line,
                    "verified": True})
    res = {"text": opinion.get("text", ""), "quotes": out}
    if assessment is not None:
        res["assessment"] = assessment
    return res
