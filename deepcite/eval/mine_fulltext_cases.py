"""Mine citation claims whose evidence is in the full text but NOT in the abstract.

This is Part C of deepcite/eval/PLAN.md, and it is the only thing that can support
the feature's central claim: that reading the primary artifact catches errors an
abstract-level check misses. No public corpus labels those cases --
Citation-Integrity, the one labelled corpus for this task, ships abstracts only --
so the set has to be built.

Method, mirroring the NatChim mining already in the paper: mine aggressively,
confirm mechanically, keep few, and record what was discarded so the filter can be
audited instead of trusted.

Pipeline, ordered so the expensive step runs last:

  1. Stream ``saier/unarXive_citrec`` -- 2.5M paragraphs from arXiv papers, each
     with a citation marker and the cited work's OpenAlex id. Gives the citing side
     for free, with no e-print fetch.
  2. Keep paragraphs whose citing sentence ``select()`` fires on.
  3. Resolve the OpenAlex id to the cited work's **abstract** and arXiv id, in
     batches of 50.
  4. Score retrieval against the abstract alone. Keep only the claims the abstract
     answers **weakly or not at all** -- these are the candidates whose evidence may
     live deeper in the paper. This prunes before any arXiv traffic.
  5. Only now fetch the cited work's full text (one request per work, >=3 s apart)
     and score retrieval against it.

A candidate is kept when the abstract is weak and the full text is strong: the
claim is checkable, and only the primary artifact can check it. Those go to hand
labelling; everything rejected is written out with its reason.

Honest limits of this method: it finds claims whose *evidence* is full-text-only.
It does not know whether the citing paper described that evidence correctly -- that
is what the hand labelling decides. And it cannot reach the ChroKnowBench class at
all, where the fact lives in a released data file rather than in any paper.

Usage:
    python -m deepcite.eval.mine_fulltext_cases --limit 2000 --out candidates.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import fetch as F
from .. import retrieve as V
from .. import select as S
from ..contract import CITE_RE

OPENALEX = "https://api.openalex.org/works"
# No mailto: that would put the user's address into a third-party URL. OpenAlex
# serves unauthenticated traffic at lower priority, which is fine for a batch job.
UA = "integriref-deepcite/0.1 (+https://github.com/GeoffreyWang1117/IntegriRef)"

# unarXive marks citations as [1], [2]} etc. Rewrite to a \cite call so selection
# sees a citing sentence; deleting the marker would change what fires.
_MARKER = re.compile(r"\[\d+\]\}?|\{\{cite:[^}]*\}\}")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")

ABSTRACT_WEAK = 2.0      # pre-filter only; see the note on comparability below
FULLTEXT_STRONG = 4.0

# A sentence carrying this many citations is a background list, not a claim about
# any one of them. Mining them yields one candidate per cited work from the same
# uninformative sentence.
MAX_CITES_IN_SENTENCE = 2

# Deciding "the evidence is in the body, not the abstract" by comparing a BM25
# score over a 2-sentence abstract with one over a 400-sentence full text is
# unsound: idf depends on n and df *within* each corpus, so the two numbers are
# not on the same scale and the full text wins by being bigger. The decision is
# therefore made by ranking the abstract's sentences and the body's sentences
# **together, in one call**, and asking where the top passages come from. The
# ABSTRACT_WEAK threshold above survives only as a cheap pre-filter that avoids
# fetching an e-print when the abstract plainly already answers the claim; it
# never decides whether a candidate is kept.
BODY_ONLY_TOPK = 3       # no abstract sentence may appear this high up
# Ordering alone is not enough: a claim nothing in the paper matches also puts no
# abstract sentence in the top 3. Within the joint ranking the scores share one idf
# space, so a magnitude floor here is legitimate -- unlike the cross-corpus
# comparison it replaced.
JOINT_MIN = 4.0


@dataclass
class Candidate:
    unarxive_id: str
    openalex_id: str
    cited_arxiv_id: str | None
    cited_title: str
    citing_sentence: str
    family: str
    pattern_id: str
    claim_type: str
    search_terms: list[str]
    abstract_best: float
    fulltext_best: float
    fulltext_passage: str
    fulltext_locator: str


def citing_sentence(paragraph: str) -> str | None:
    """The sentence carrying the marker, rewritten to hold a \\cite call.

    unarXive gives whole paragraphs. Selection operates on one sentence, so pick
    the sentence the marker sits in -- which is also the unit whose hash the cache
    contract is defined over.
    """
    text = _MARKER.sub(r"\\cite{cited}", paragraph)
    if "\\cite{" not in text:
        return None
    for sent in _SENT_SPLIT.split(text):
        if "\\cite{" in sent:
            return sent.strip()
    return text.strip()


def _get(url: str, timeout: int = 60) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8", "replace"))
    except Exception:
        return None


def openalex_batch(ids: list[str]) -> dict[str, dict]:
    """Abstract + arXiv id for up to 50 OpenAlex works in one request."""
    short = [i.rsplit("/", 1)[-1] for i in ids]
    q = urllib.parse.urlencode({
        "filter": "openalex:" + "|".join(short),
        "select": "id,title,abstract_inverted_index,locations,doi",
        "per-page": len(short)})
    data = _get(f"{OPENALEX}?{q}") or {}
    out: dict[str, dict] = {}
    for w in data.get("results", []):
        out[w["id"].rsplit("/", 1)[-1]] = {
            "title": w.get("title") or "",
            "abstract": _deinvert(w.get("abstract_inverted_index")),
            "arxiv_id": _arxiv_from(w),
        }
    return out


def _deinvert(inv: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}."""
    if not inv:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inv.items():
        for p in positions:
            slots[p] = word
    return " ".join(slots[k] for k in sorted(slots))


_ARXIV_IN_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^v\s/]+(?:v\d+)?)", re.I)


def _arxiv_from(work: dict) -> str | None:
    for loc in work.get("locations") or []:
        for key in ("landing_page_url", "pdf_url"):
            m = _ARXIV_IN_URL.search(str((loc or {}).get(key) or ""))
            if m:
                return m.group(1)
    return None


# --- arXiv-only resolution -------------------------------------------------
#
# The unarXive route needs OpenAlex to turn its labels into ids and abstracts,
# and OpenAlex's unauthenticated budget is per-IP and per-day. This route needs
# neither: the title is already in the .bib, and one arXiv API query returns the
# versioned id and the abstract together, so step 3 costs no extra request.

_BIB_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n@|\Z)", re.S)
_BIB_TITLE = re.compile(r"\btitle\s*=\s*[{\"]+(.+?)[}\"]+\s*,?\s*$",
                        re.S | re.M | re.I)
_WS = re.compile(r"\s+")


def bib_titles(bib_path: Path) -> dict[str, str]:
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for key, body in _BIB_ENTRY.findall(text):
        m = _BIB_TITLE.search(body)
        if m:
            out[key] = _WS.sub(" ", re.sub(r"[{}]", "", m.group(1))).strip()
    return out


_T_NORM = re.compile(r"[^a-z0-9]+")


def arxiv_meta(title: str, timeout: int = 60) -> tuple[str, str] | None:
    """(versioned arXiv id, abstract) for a title, or None.

    Requires a tight title match: arXiv's relevance search returns neighbours
    happily, and mining the wrong artifact is worse than mining nothing.
    """
    if not title.strip():
        return None
    q = urllib.parse.urlencode({"search_query": f'ti:"{title}"', "max_results": 5})
    F.polite_wait()
    xml = None
    req = urllib.request.Request(f"http://export.arxiv.org/api/query?{q}",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            xml = fh.read().decode("utf-8", "replace")
    except Exception:
        return None
    want = _T_NORM.sub(" ", title.lower()).strip()
    for ent in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        t = re.search(r"<title>(.*?)</title>", ent, re.S)
        i = re.search(r"<id>https?://arxiv\.org/abs/([^<]+)</id>", ent)
        s = re.search(r"<summary>(.*?)</summary>", ent, re.S)
        if not (t and i):
            continue
        got = _T_NORM.sub(" ", _WS.sub(" ", t.group(1)).lower()).strip()
        if got != want and not (abs(len(got) - len(want)) < 15
                                and (want in got or got in want)):
            continue
        vid = i.group(1)
        if not re.search(r"v\d+$", vid):
            return None
        return vid, _WS.sub(" ", s.group(1)).strip() if s else ""
    return None


def mine_local(paper: Path, bib: Path, cache_dir: Path | None,
               max_fetch: int = 40) -> tuple[list[Candidate], list[dict]]:
    """Mine one local paper: .tex for claims, .bib for titles, arXiv for both sides."""
    from .. import texscan as T

    titles = bib_titles(bib)
    kept: list[Candidate] = []
    rejected: list[dict] = []
    fetches = 0
    seen: set[tuple[str, str]] = set()

    for rel in T.tex_files(paper):
        for cit in T.scan_file(paper, rel).citations:
            sent = cit.sentence.text
            sel = S.select(sent)
            if not sel:
                continue
            uid = f"{paper.name}:{rel}:{cit.sentence.line}:{cit.bib_key}"
            if (cit.bib_key, sent) in seen:
                continue
            seen.add((cit.bib_key, sent))
            if len(CITE_RE.findall(sent)) > MAX_CITES_IN_SENTENCE:
                rejected.append({"id": uid, "reason": "background citation list",
                                 "n_cites": len(CITE_RE.findall(sent))})
                continue
            title = titles.get(cit.bib_key)
            if not title:
                rejected.append({"id": uid, "reason": "bib key has no title"})
                continue
            terms = S.search_terms(sent)
            if not terms:
                rejected.append({"id": uid, "reason": "no search terms"})
                continue
            if fetches >= max_fetch:
                rejected.append({"id": uid, "reason": "fetch budget exhausted"})
                continue
            fetches += 1
            meta = arxiv_meta(title)
            if not meta:
                rejected.append({"id": uid, "reason": "cited work not on arXiv",
                                 "title": title[:90]})
                continue
            aid, abstract = meta
            a_score, _, _ = best_score(terms, abstract, sel.claim_type)
            if a_score > ABSTRACT_WEAK:
                rejected.append({"id": uid, "reason": "abstract answers it",
                                 "abstract_best": round(a_score, 3),
                                 "arxiv_id": aid})
                continue
            art = F.fetch_eprint(aid, cache_dir)
            if art is None or art.kind != F.KIND_TEX:
                rejected.append({"id": uid, "reason": "no LaTeX e-print",
                                 "arxiv_id": aid})
                continue
            full = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                             for p in art.files[:40])
            full = CITE_RE.sub(" ", full)
            ok, f_score, passage, loc = body_only(terms, abstract, full,
                                                  sel.claim_type)
            if not ok:
                rejected.append({"id": uid,
                                 "reason": "abstract ranks within the top passages",
                                 "abstract_best": round(a_score, 3),
                                 "top_score": round(f_score, 3),
                                 "top_from": loc.split(":")[0],
                                 "arxiv_id": aid})
                continue
            kept.append(Candidate(
                unarxive_id=uid, openalex_id="", cited_arxiv_id=aid,
                cited_title=title, citing_sentence=sent, family=sel.family,
                pattern_id=sel.pattern_id, claim_type=sel.claim_type,
                search_terms=terms, abstract_best=round(a_score, 3),
                fulltext_best=round(f_score, 3),
                fulltext_passage=V.clip_words(passage), fulltext_locator=loc))
    return kept, rejected


def _units(text: str, tag: str) -> list[tuple[str, int, int, str]]:
    return [(tag, i, i, s)
            for i, s in enumerate(s for s in _SENT_SPLIT.split(text) if s.strip())]


def best_score(terms: list[str], text: str, claim_type: str,
               tag: str = "abstract") -> tuple[float, str, str]:
    """Top score over one text's sentences. Only comparable within one corpus."""
    got = V.rank_units(terms, _units(text, tag), top_k=1, claim_type=claim_type)
    if not got:
        return 0.0, "", ""
    p = got[0]
    return p.rank_score, p.text, f"{p.file}:{p.line_start}"


def body_only(terms: list[str], abstract: str, body: str, claim_type: str
              ) -> tuple[bool, float, str, str]:
    """Rank abstract and body sentences together; is the evidence body-only?

    One ranking call, one idf space, so "the abstract does not answer this but the
    body does" is a statement about ordering rather than about two incomparable
    scores.
    """
    units = _units(abstract, "abstract") + _units(body, "body")
    got = V.rank_units(terms, units, top_k=BODY_ONLY_TOPK, claim_type=claim_type)
    if not got:
        return False, 0.0, "", ""
    top = got[0]
    is_body_only = all(p.file == "body" for p in got[:BODY_ONLY_TOPK])
    ok = is_body_only and top.file == "body" and top.rank_score >= JOINT_MIN
    return ok, top.rank_score, top.text, f"{top.file}:{top.line_start}"


def mine(limit: int, cache_dir: Path | None, batch: int = 50,
         max_fetch: int = 40) -> tuple[list[Candidate], list[dict]]:
    from datasets import load_dataset

    ds = load_dataset("saier/unarXive_citrec", name="default", split="train",
                      streaming=True)
    kept: list[Candidate] = []
    rejected: list[dict] = []
    pending: list[tuple[str, str, str, object]] = []   # id, oa, sentence, selection
    fetches = 0
    seen = 0

    def flush() -> None:
        nonlocal fetches
        if not pending:
            return
        meta = openalex_batch([p[1] for p in pending])
        for uid, oa, sent, sel in pending:
            info = meta.get(oa.rsplit("/", 1)[-1])
            if not info:
                rejected.append({"id": uid, "reason": "openalex lookup failed"})
                continue
            terms = S.search_terms(sent)
            if not terms:
                rejected.append({"id": uid, "reason": "no search terms"})
                continue
            a_score, _, _ = best_score(terms, info["abstract"], sel.claim_type)
            if a_score > ABSTRACT_WEAK:
                rejected.append({"id": uid, "reason": "abstract answers it",
                                 "abstract_best": round(a_score, 3)})
                continue
            aid = info["arxiv_id"]
            if not aid:
                rejected.append({"id": uid, "reason": "cited work not on arXiv"})
                continue
            if not re.search(r"v\d+$", aid):
                rejected.append({"id": uid, "reason": f"unversioned arXiv id {aid}"})
                continue
            if fetches >= max_fetch:
                rejected.append({"id": uid, "reason": "fetch budget exhausted"})
                continue
            fetches += 1
            art = F.fetch_eprint(aid, cache_dir)
            if art is None or art.kind != F.KIND_TEX:
                rejected.append({"id": uid, "reason": "no LaTeX e-print",
                                 "arxiv_id": aid})
                continue
            full = "\n".join(
                p.read_text(encoding="utf-8", errors="replace")
                for p in art.files[:40])
            full = CITE_RE.sub(" ", full)
            ok, f_score, passage, loc = body_only(terms, info["abstract"], full,
                                                  sel.claim_type)
            if not ok:
                rejected.append({"id": uid,
                                 "reason": "abstract ranks within the top passages",
                                 "abstract_best": round(a_score, 3),
                                 "top_score": round(f_score, 3)})
                continue
            kept.append(Candidate(
                unarxive_id=uid, openalex_id=oa, cited_arxiv_id=aid,
                cited_title=info["title"], citing_sentence=sent,
                family=sel.family, pattern_id=sel.pattern_id,
                claim_type=sel.claim_type, search_terms=terms,
                abstract_best=round(a_score, 3), fulltext_best=round(f_score, 3),
                fulltext_passage=V.clip_words(passage), fulltext_locator=loc))
        pending.clear()

    for row in ds:
        seen += 1
        if seen > limit:
            break
        sent = citing_sentence(row.get("text") or "")
        if not sent:
            continue
        sel = S.select(sent)
        if not sel:
            continue
        label = row.get("label") or ""
        if not label.startswith("https://openalex.org/"):
            rejected.append({"id": row.get("_id"), "reason": "no OpenAlex label"})
            continue
        if len(CITE_RE.findall(sent)) > MAX_CITES_IN_SENTENCE:
            rejected.append({"id": row.get("_id"),
                             "reason": "background citation list"})
            continue
        pending.append((row.get("_id") or "", label, sent, sel))
        if len(pending) >= batch:
            flush()
        if fetches >= max_fetch:
            break
    flush()
    return kept, rejected


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m deepcite.eval.mine_fulltext_cases")
    ap.add_argument("--source", choices=("local", "unarxive"), default="local",
                    help="local reads a paper's .tex+.bib and uses arXiv only; "
                         "unarxive streams 2.5M contexts but needs OpenAlex, whose "
                         "unauthenticated budget is per-IP per-day")
    ap.add_argument("--paper", default=None, help="--source local: paper directory")
    ap.add_argument("--bib", default=None, help="--source local: .bib file")
    ap.add_argument("--limit", type=int, default=2000,
                    help="unarXive rows to stream")
    ap.add_argument("--max-fetch", type=int, default=40,
                    help="cap on arXiv e-print fetches (>=3 s apart)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rejects", default=None)
    ap.add_argument("--cache-dir", default=None)
    a = ap.parse_args(argv)

    t0 = time.time()
    cache = Path(a.cache_dir) if a.cache_dir else None
    if a.source == "local":
        if not (a.paper and a.bib):
            print("--source local needs --paper and --bib")
            return 2
        kept, rejected = mine_local(Path(a.paper).expanduser().resolve(),
                                    Path(a.bib).expanduser(), cache,
                                    max_fetch=a.max_fetch)
        print(f"  {a.paper} -> {len(kept)} candidates, {len(rejected)} rejected, "
              f"{time.time() - t0:.0f}s")
    else:
        kept, rejected = mine(a.limit, cache, max_fetch=a.max_fetch)
        print(f"  streamed {a.limit} rows -> {len(kept)} candidates, "
              f"{len(rejected)} rejected, {time.time() - t0:.0f}s")
    reasons: dict[str, int] = {}
    for r in rejected:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    for why in sorted(reasons, key=lambda k: -reasons[k]):
        print(f"    {why:34} {reasons[why]}")
    if a.out:
        Path(a.out).write_text(
            "".join(json.dumps(asdict(c), ensure_ascii=False) + "\n" for c in kept),
            encoding="utf-8")
        print(f"  candidates -> {a.out}")
    if a.rejects:
        Path(a.rejects).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejected),
            encoding="utf-8")
        print(f"  rejects -> {a.rejects}")
    for c in kept[:3]:
        print(f"\n  [{c.family}/{c.claim_type}] {c.citing_sentence[:130]}")
        print(f"    cited {c.cited_arxiv_id} abstract={c.abstract_best} "
              f"fulltext={c.fulltext_best}")
        print(f"    {c.fulltext_locator}: {c.fulltext_passage[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
