"""Retrieve candidate passages from the cited work's own text.

Default ranker is lexical and deterministic. NLI is opt-in (``--rank nli``) and
may only ORDER passages, never label them: on SciFact-matched pairs L2 scored
FPR 0.68 for {contradicted union unsupported}, and wrapping a claim in a citation
template flips supported to unsupported -- which is exactly the shape every
citing sentence has. See docs/DEEPCITE_SPEC_v1.1.md §6.

NLI scores are also not bit-reproducible on GPU, which is why rank_score never
enters any hash.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

MAX_PASSAGES = 5          # enforced here, in the tool, not asked of a prompt
MAX_WORDS = 60

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]+|\d+(?:\.\d+)?")
# Macros whose ARGUMENT is prose worth keeping.
_KEEP_ARG = re.compile(r"\\(?:text(?:bf|it|sc|tt|rm|sf)|emph|mbox|textnormal"
                       r"|section|subsection|subsubsection|caption|title)\*?"
                       r"(?:\[[^\]]*\])?\{([^{}]*)\}")
_DROP_CMD = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?")
_MATHY = re.compile(r"\$[^$]*\$")


@dataclass(frozen=True)
class Passage:
    file: str
    line_start: int
    line_end: int
    text: str            # display-normalized, <= MAX_WORDS words
    rank_score: float
    raw: str             # untouched source slice; quotes are checked against this


def display_text(s: str) -> str:
    """De-macro for human reading. Quotes are verified against RAW source, so
    this is cosmetic only -- a worklist full of \\textsc{MQuAKE}-CF is unreadable."""
    s = _MATHY.sub(" ", s)
    for _ in range(3):
        s, n = _KEEP_ARG.subn(lambda m: m.group(1), s)
        if not n:
            break
    s = _DROP_CMD.sub(" ", s)
    s = s.replace("~", " ").replace("\\&", "&").replace("\\%", "%")
    s = re.sub(r"[{}]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def clip_words(s: str, limit: int = MAX_WORDS) -> str:
    w = s.split()
    return s if len(w) <= limit else " ".join(w[:limit]) + " \u2026"


MIN_WINDOW_WORDS = 12     # below this a window is a title or a heading fragment

# Words that signal a window is *about* a given kind of claim. A window that
# discusses metrics outranks one that merely repeats the benchmark's name.
_TYPE_CUES = {
    "metric": ("metric", "metrics", "accuracy", "score", "evaluate", "evaluation",
               "measure", "report", "defined", "definition"),
    "protocol": ("protocol", "procedure", "we evaluate", "setting", "setup",
                 "follow", "pipeline"),
    "split": ("split", "splits", "train", "dev", "test", "validation", "fold"),
    "dataset": ("dataset", "benchmark", "instances", "examples", "statistics",
                "constructed", "collected", "contains"),
    "setup": ("setup", "setting", "hyperparameter", "configuration", "implementation",
              "architecture", "trained"),
    "number": ("total", "instances", "examples", "statistics", "contains", "consists"),
    # First person is a POSITIVE cue here and a NEGATIVE one in select.py, on
    # purpose: retrieval reads the CITED work, where "we show" is its authors
    # stating their own finding; selection reads the CITING sentence, where "we"
    # is the citing paper talking about itself. Do not make the two consistent.
    "finding": ("we show", "we find", "we found", "we observe", "our results",
                "results show", "results suggest", "we demonstrate", "significantly",
                "outperform", "compared", "increase", "decrease", "associated"),
}


def _windows(path: Path, root: Path, size: int = 6, stride: int = 3):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return
    rel = str(path.relative_to(root)) if root in path.parents or root == path.parent \
        else path.name
    for i in range(0, max(1, len(lines)), stride):
        chunk = lines[i:i + size]
        if not any(c.strip() for c in chunk):
            continue
        yield rel, i + 1, min(i + size, len(lines)), "\n".join(chunk)


def _terms(s: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(s)]


def _tabular_penalty(raw: str, dl: int) -> float:
    """Down-weight table and figure rows.

    A results table is dense in exactly the terms a metric claim searches for
    ("nDCG@10" five times in one row) while almost never being the sentence that
    defines a protocol or a metric. Observed on a real run: RankGPT's top two
    passages were both table rows.
    """
    cells = raw.count("&")
    rows = raw.count("\\\\")
    if cells >= 3 or rows >= 2:
        return 0.3
    if re.search(r"\\begin\{(?:tabular|table|figure|tikzpicture|lstlisting|align)", raw):
        return 0.6
    # Mostly punctuation and numbers with little prose.
    if dl and len(_WORD.findall(raw)) / max(1, dl) < 0.5:
        return 0.6
    return 1.0


def rank_units(terms: list[str], units: list[tuple[str, int, int, str]],
               top_k: int = MAX_PASSAGES, claim_type: str = "other",
               k1: float = 1.2, b: float = 0.75) -> list[Passage]:
    """Rank pre-segmented units instead of files.

    ``units`` are ``(label, line_start, line_end, raw_text)``. This is the entry
    point for evaluation against corpora that ship sentence-segmented cited
    documents (e.g. the Citation-Integrity corpus), where there is no artifact on
    disk to window over. The scoring is identical to ``rank``; only the source of
    the candidate units differs, so numbers from the two paths are comparable.
    """
    return _score(terms, units, top_k, claim_type, k1, b)


def rank(terms: list[str], artifact_files: list[Path], root: Path,
         top_k: int = MAX_PASSAGES, claim_type: str = "other",
         k1: float = 1.2, b: float = 0.75) -> list[Passage]:
    """Rank windows by BM25 over the claim's terms, plus a claim-type bonus.

    Proper BM25 length normalization matters here: a naive 1/sqrt(len) divisor
    sends a one-word window containing the benchmark's own name to the top, which
    is how the first run buried the passage that actually defined the metric.
    """
    want = {t.lower() for t in terms}
    if not want:
        return []
    windows = [w for f in artifact_files for w in _windows(f, root)]
    return _score(terms, windows, top_k, claim_type, k1, b)


def _score(terms: list[str], windows: list[tuple[str, int, int, str]],
           top_k: int, claim_type: str, k1: float, b: float) -> list[Passage]:
    want = {t.lower() for t in terms}
    if not want or not windows:
        return []

    disp = [display_text(raw) for _, _, _, raw in windows]
    toks = [_terms(d) for d in disp]
    keep = [i for i, tk in enumerate(toks) if len(tk) >= MIN_WINDOW_WORDS]
    if not keep:
        keep = list(range(len(windows)))       # tiny artifact: take what there is

    df: dict[str, int] = {}
    for i in keep:
        for t in set(toks[i]) & want:
            df[t] = df.get(t, 0) + 1
    n = len(keep)
    avgdl = sum(len(toks[i]) for i in keep) / max(1, n)
    cues = _TYPE_CUES.get(claim_type, ())

    scored: list[Passage] = []
    for i in keep:
        tk, dl = toks[i], len(toks[i])
        counts = {t: tk.count(t) for t in set(tk) & want}
        if not counts:
            continue
        score = 0.0
        for t, f in counts.items():
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        if cues:
            low = disp[i].lower()
            hits = sum(1 for c in cues if c in low)
            score *= 1.0 + 0.25 * min(hits, 4)
        score *= _tabular_penalty(windows[i][3], dl)
        rel, a, bb, raw = windows[i]
        scored.append(Passage(file=rel, line_start=a, line_end=bb,
                              text=clip_words(disp[i]),
                              rank_score=round(score, 4), raw=raw))
    scored.sort(key=lambda p: (-p.rank_score, p.file, p.line_start))
    return _dedupe(scored)[:top_k]


def _dedupe(ps: list[Passage]) -> list[Passage]:
    """Drop near-duplicates and windows overlapping a higher-ranked one.

    Windows are strided, so consecutive ones share lines; without this the top 5
    can be five views of the same paragraph.
    """
    seen: set[str] = set()
    taken: list[Passage] = []
    for p in ps:
        key = p.text[:80].lower()
        if key in seen:
            continue
        if any(q.file == p.file and p.line_start <= q.line_end
               and q.line_start <= p.line_end for q in taken):
            continue
        seen.add(key)
        taken.append(p)
    return taken


def nli_rerank(claim: str, passages: list[Passage]) -> list[Passage]:
    """Optional reordering. Returns the input unchanged if torch is unavailable.

    No label is produced or stored. Ordering errors cost passage order only.
    """
    try:
        from semantic.nli_verifier import NLIVerifier
    except Exception:
        return passages
    try:
        v = NLIVerifier()
        scored = []
        for p in passages:
            r = v.verify(claim, p.text)
            prob = float(getattr(r, "entailment_prob", 0.0) or 0.0)
            scored.append((prob, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored]
    except Exception:
        return passages
