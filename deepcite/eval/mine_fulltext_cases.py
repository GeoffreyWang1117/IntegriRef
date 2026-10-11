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
from .. import resolve as R
from ..net import SourceRefused, http_get
from .. import select as S
from ..contract import CITE_RE, strip_comments

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
# "The abstract does not answer this" has to be expressed in a way that does not
# depend on how many sentences the documents happen to have. An earlier version
# required no abstract sentence in the absolute top 3, which is unsatisfiable for a
# short paper -- with six sentences in the joint pool, the top 3 always contains
# one -- and trivially satisfied for a long one. Same flaw as comparing BM25 scores
# across two corpora: the criterion moved with the size rather than the content.
#
# Instead: the best body passage must clear JOINT_MIN, and the best abstract
# sentence must score below this fraction of it.
ABSTRACT_RATIO = 0.5
# Ordering alone is not enough: a claim nothing in the paper matches also puts no
# abstract sentence in the top 3. Within the joint ranking the scores share one idf
# space, so a magnitude floor here is legitimate -- unlike the cross-corpus
# comparison it replaced.
JOINT_MIN = 4.0


def _emit(sink, cand) -> None:
    """Append a candidate to disk immediately.

    A batch large enough to be useful runs for hours, and arXiv rate limiting is
    now machine-wide: another tool's 429 becomes a shared cooldown that raises
    SourceRefused here. Keeping results only in memory until the end means one
    refusal, or any crash, throws away the whole run.
    """
    if sink is None:
        return
    sink.write(json.dumps(asdict(cand), ensure_ascii=False) + "\n")
    sink.flush()


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


def _memo_file() -> Path:
    return (F.default_cache_dir().parent / "mine_title_memo.json")


def _memo() -> dict:
    try:
        return json.loads(_memo_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _memo_put(key: str, value) -> None:
    """Persist a title lookup, including the negative result.

    Title lookups dominate a batch -- about 430 of them for 60 fetches in the
    first real run, roughly 21 of its 26 minutes -- and they are what earned this
    machine an arXiv 429. Caching them on disk means a throttled run costs its
    work once rather than every retry, and negatives are cached too because a
    title that is not on arXiv stays not on arXiv.
    """
    m = _memo()
    m[key] = value
    try:
        f = _memo_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(m), encoding="utf-8")
    except OSError:
        pass


def arxiv_meta(title: str, timeout: int = 60) -> tuple[str, str] | None:
    """(versioned arXiv id, abstract) for a title, or None.

    Requires a tight title match: arXiv's relevance search returns neighbours
    happily, and mining the wrong artifact is worse than mining nothing.
    """
    if not title.strip():
        return None
    key = _T_NORM.sub(" ", title.lower()).strip()
    cached = _memo().get(key, "__miss__")
    if cached != "__miss__":
        return tuple(cached) if cached else None
    q = urllib.parse.urlencode({"search_query": f'ti:"{title}"', "max_results": 5})
    # http_get, not a bare urlopen with a blanket except: swallowing the error
    # turned arXiv's 429 into "cited work not on arXiv by title", so a throttled
    # run quietly recorded hundreds of works as absent that are in fact there.
    # A refusal has to be distinguishable from an answer of "not present".
    body = http_get(f"http://export.arxiv.org/api/query?{q}", "arxiv",
                    timeout=timeout, wait=F.polite_wait)
    if body is None:
        return None
    xml = body.decode("utf-8", "replace")
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
            _memo_put(key, None)
            return None
        out = (vid, _WS.sub(" ", s.group(1)).strip() if s else "")
        _memo_put(key, list(out))
        return out
    _memo_put(key, None)
    return None


def mine_local(paper: Path, bib: Path, cache_dir: Path | None,
               max_fetch: int = 40, sink=None
               ) -> tuple[list[Candidate], list[dict]]:
    """Mine one local paper: .tex for claims, .bib for titles, arXiv for both sides."""
    from .. import texscan as T

    titles = bib_titles(bib)
    kept: list[Candidate] = []
    rejected: list[dict] = []
    fetches = 0
    seen: set[tuple[str, str]] = set()
    stop = [False]

    for rel in T.tex_files(paper):
        if stop[0]:
            break
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
            try:
                meta = arxiv_meta(title)
            except SourceRefused as e:
                rejected.append({"id": uid, "reason": f"source refused: {e}"})
                stop[0] = True
                break
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
            try:
                art = F.fetch_eprint(aid, cache_dir)
            except SourceRefused as e:
                # Shared cooldown: every later fetch would refuse too, so stop
                # instead of burning through the remaining rows.
                rejected.append({"id": uid, "reason": f"source refused: {e}"})
                stop[0] = True
                break
            if art is None or art.kind != F.KIND_TEX:
                rejected.append({"id": uid, "reason": "no LaTeX e-print",
                                 "arxiv_id": aid})
                continue
            full = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                             for p in art.files[:40])
            full = CITE_RE.sub(" ", strip_comments(full))   # comments are not evidence
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
            _emit(sink, kept[-1])
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
    if not units:
        return False, 0.0, "", ""
    # Rank the whole pool, not a top slice: the criterion needs the best abstract
    # sentence as well as the best body one, in the same idf space.
    got = V.rank_units(terms, units, top_k=len(units), claim_type=claim_type)
    if not got:
        return False, 0.0, "", ""
    best_body = next((p for p in got if p.file == "body"), None)
    best_abs = next((p for p in got if p.file == "abstract"), None)
    if best_body is None:
        return False, 0.0, "", ""
    ok = (best_body.rank_score >= JOINT_MIN
          and (best_abs is None
               or best_abs.rank_score < ABSTRACT_RATIO * best_body.rank_score))
    return (ok, best_body.rank_score, best_body.text,
            f"{best_body.file}:{best_body.line_start}")


CITREC_URL = ("https://huggingface.co/datasets/saier/unarXive_citrec/"
              "resolve/main/data/train.jsonl")
CITREC_LOCAL = (Path(__file__).resolve().parents[2] / "benchmarks" / "data"
                / "unarxive" / "citrec_head.jsonl")


def citrec_rows(local: Path | None = None):
    """Iterate unarXive citrec rows from a locally cached prefix of train.jsonl.

    The HF `datasets` streaming path is not used: train.jsonl is a single 5.8 GB
    file and unauthenticated range-less reads of it time out repeatedly. An HTTP
    range request for the first few hundred MB is reliable, gives ~100k rows --
    far more than the arXiv fetch budget can consume -- and keeps this module
    free of the `datasets` dependency. Fetch it with:

        curl -L -r 0-314572800 <CITREC_URL> -o benchmarks/data/unarxive/citrec_head.jsonl

    The final line of a range download is truncated, so undecodable lines are
    skipped rather than repaired.
    """
    path = Path(local) if local else CITREC_LOCAL
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Range-download a prefix of {CITREC_URL} first; "
            f"see this function's docstring.")
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue      # truncated tail of the range request


def mine(limit: int, cache_dir: Path | None, batch: int = 50,
         max_fetch: int = 40, local: Path | None = None, sink=None
         ) -> tuple[list[Candidate], list[dict]]:
    ds = citrec_rows(local)
    stop = [False]
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
            abstract = info["abstract"]
            # Resolve the artifact by TITLE, not by OpenAlex's arXiv link.
            #
            # Using the link directly produced a wrong artifact on a 355-row
            # sample: a paragraph about projecting point clouds into 3D voxels
            # resolved to 1810.04805 (BERT), because that work's OpenAlex
            # locations carry an arXiv url that does not belong to it. Mining the
            # wrong paper manufactures a candidate that a human would then be
            # asked to label -- the exact failure this project exists to catch.
            #
            # arxiv_meta requires a tight title match, so a hit validates the id
            # by construction, and it returns the abstract in the same request.
            try:
                axm = arxiv_meta(info["title"])  # not `meta`: that is the batch dict
            except SourceRefused as e:
                rejected.append({"id": uid, "reason": f"source refused: {e}"})
                stop[0] = True
                break
            if not axm:
                rejected.append({"id": uid,
                                 "reason": "cited work not found on arXiv by title",
                                 "title": (info["title"] or "")[:90]})
                continue
            aid, arxiv_abstract = axm
            # Prefer arXiv's abstract: it is the artifact we are about to search.
            abstract = arxiv_abstract or info["abstract"]
            if fetches >= max_fetch:
                rejected.append({"id": uid, "reason": "fetch budget exhausted"})
                continue
            fetches += 1
            try:
                art = F.fetch_eprint(aid, cache_dir)
            except SourceRefused as e:
                # Shared cooldown: every later fetch would refuse too, so stop
                # instead of burning through the remaining rows.
                rejected.append({"id": uid, "reason": f"source refused: {e}"})
                stop[0] = True
                break
            if art is None or art.kind != F.KIND_TEX:
                rejected.append({"id": uid, "reason": "no LaTeX e-print",
                                 "arxiv_id": aid})
                continue
            full = "\n".join(
                p.read_text(encoding="utf-8", errors="replace")
                for p in art.files[:40])
            full = CITE_RE.sub(" ", strip_comments(full))   # comments are not evidence
            ok, f_score, passage, loc = body_only(terms, abstract, full,
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
            _emit(sink, kept[-1])
        pending.clear()

    for row in ds:
        if stop[0]:
            break
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


# --- arxiv-bulk: both sides of the citation from a local corpus --------------
#
# With an S3 corpus slice on disk there is no network step at all, and the one
# that used to dominate -- the title search that cost 430 requests per 60 fetches
# and earned a 429 -- is not merely cheaper but unnecessary. arXiv ids are written
# down in the citing paper's own bibliography, in .bib fields (eprint,
# archivePrefix, url) and literally as "arXiv:2305.14795" in a .bbl. Reading them
# is exact, where a title search was a guess that had to be validated.

_BIBITEM = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}")
_ARXIV_IN_TEXT = re.compile(
    r"ar[Xx]iv[:\s/]*((?:\d{4}\.\d{4,5})(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)")
_ABSTRACT = re.compile(
    r"\\begin\{abstract\}(.*?)\\end\{abstract\}|\\abstract\{(.*?)\n\s*\n", re.S)


def bbl_arxiv_ids(text: str) -> dict[str, str]:
    """``{bibitem key: arXiv id}`` from a compiled .bbl.

    Most arXiv submissions ship a .bbl rather than a .bib, so without this the
    corpus route would only work for the minority that include their .bib.
    Splitting on \\bibitem keeps an id attached to the entry that printed it
    instead of to whichever entry happens to be nearest.
    """
    out: dict[str, str] = {}
    marks = list(_BIBITEM.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        hit = _ARXIV_IN_TEXT.search(text[m.end():end])
        if hit:
            out[m.group(1)] = hit.group(1)
    return out


def corpus_cited_ids(paper_dir: Path) -> dict[str, str]:
    """``{bib key: arXiv id}`` for one paper, from its .bib if present else .bbl."""
    ids: dict[str, str] = {}
    for bib in sorted(paper_dir.glob("*.bib")):
        text = bib.read_text(encoding="utf-8", errors="replace")
        for key, entry in R.split_bib(text).items():
            if key.startswith("@"):
                continue
            got = R.bib_arxiv_id(entry)
            if got:
                ids[key] = got
    for bbl in sorted(paper_dir.glob("*.bbl")):
        for key, got in bbl_arxiv_ids(
                bbl.read_text(encoding="utf-8", errors="replace")).items():
            ids.setdefault(key, got)
    return ids


def corpus_abstract(paper_dir: Path) -> str:
    """The cited paper's own abstract, from its source.

    Taken from the artifact rather than from a metadata API: it is the same
    document the body comes from, so "the abstract does not answer this but the
    body does" is a statement about one text, not a comparison across sources.
    """
    for tex in sorted(paper_dir.glob("*.tex")):
        hit = _ABSTRACT.search(tex.read_text(encoding="utf-8", errors="replace"))
        if hit:
            return V.display_text(
                strip_comments(hit.group(1) or hit.group(2) or ""))
    return ""


def corpus_body(paper_dir: Path, max_files: int = 40) -> str:
    r"""The cited paper's prose with the abstract removed.

    Two things this has to get right, both found by the synthetic-corpus test:

    * **The abstract must be excluded.** It lives in the same .tex, so returning
      the whole file made "the evidence is in the body, not the abstract"
      self-contradictory -- the abstract was in both sides of the comparison.
    * **Segmentation needs prose, not LaTeX.** Sentences are split on a
      terminator followed by a capital, and `\end{abstract}` or `\section{...}`
      starts with a backslash, so splitting raw source merged whole papers into
      two or three enormous units and the ranking became meaningless.

    The locator this produces is therefore a sentence index, not a file line.
    """
    texts = []
    for tex in sorted(paper_dir.glob("*.tex"))[:max_files]:
        texts.append(tex.read_text(encoding="utf-8", errors="replace"))
    raw = strip_comments("\n".join(texts))
    raw = _ABSTRACT.sub(" ", raw)
    return V.display_text(CITE_RE.sub(" ", raw))


def mine_bulk(root: Path | None = None, max_papers: int = 0, sink=None
              ) -> tuple[list[Candidate], list[dict]]:
    """Mine an S3 corpus slice. No network, no rate limit, no id resolution."""
    from . import arxiv_bulk as B
    from .. import texscan as T

    index = B.load_index(Path(root) if root else B.DEFAULT_ROOT)
    if not index:
        raise FileNotFoundError(
            "no corpus index; run `python -m deepcite.eval.arxiv_bulk fetch` then "
            "`index` first")
    kept: list[Candidate] = []
    rejected: list[dict] = []
    papers = sorted(index.items())
    if max_papers:
        papers = papers[:max_papers]

    for citing_id, citing_dir in papers:
        cdir = Path(citing_dir)
        try:
            cited_ids = corpus_cited_ids(cdir)
        except OSError:
            continue
        if not cited_ids:
            rejected.append({"id": citing_id, "reason": "no arXiv ids in its bibliography"})
            continue
        seen: set[tuple[str, str]] = set()
        for rel in T.tex_files(cdir):
            for cit in T.scan_file(cdir, rel).citations:
                sent = cit.sentence.text
                sel = S.select(sent)
                if not sel:
                    continue
                uid = f"{citing_id}:{rel}:{cit.sentence.line}:{cit.bib_key}"
                if (cit.bib_key, sent) in seen:
                    continue
                seen.add((cit.bib_key, sent))
                if len(CITE_RE.findall(sent)) > MAX_CITES_IN_SENTENCE:
                    rejected.append({"id": uid, "reason": "background citation list"})
                    continue
                aid = cited_ids.get(cit.bib_key)
                if not aid:
                    rejected.append({"id": uid, "reason": "cited work has no arXiv id in the bib"})
                    continue
                base = re.sub(r"v\d+$", "", aid)
                cited_dir = index.get(aid) or index.get(base)
                if not cited_dir:
                    # Expected and not a failure: the corpus is a slice, so most
                    # cited works simply are not in it. Widening the slice is the
                    # only fix, and it costs only S3 bytes.
                    rejected.append({"id": uid, "reason": "cited work outside the corpus slice",
                                     "arxiv_id": aid})
                    continue
                terms = S.search_terms(sent)
                if not terms:
                    rejected.append({"id": uid, "reason": "no search terms"})
                    continue
                pdir = Path(cited_dir)
                abstract, body = corpus_abstract(pdir), corpus_body(pdir)
                if not body.strip():
                    rejected.append({"id": uid, "reason": "cited work has no LaTeX body",
                                     "arxiv_id": aid})
                    continue
                ok, score, passage, loc = body_only(terms, abstract, body, sel.claim_type)
                if not ok:
                    rejected.append({"id": uid,
                                     "reason": "abstract ranks within the top passages",
                                     "top_score": round(score, 3), "arxiv_id": aid})
                    continue
                kept.append(Candidate(
                    unarxive_id=uid, openalex_id="", cited_arxiv_id=aid,
                    cited_title="", citing_sentence=sent, family=sel.family,
                    pattern_id=sel.pattern_id, claim_type=sel.claim_type,
                    search_terms=terms, abstract_best=0.0,
                    fulltext_best=round(score, 3),
                    fulltext_passage=V.clip_words(passage), fulltext_locator=loc))
                _emit(sink, kept[-1])
    return kept, rejected


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m deepcite.eval.mine_fulltext_cases")
    ap.add_argument("--source", choices=("local", "unarxive", "arxiv-bulk"),
                    default="local",
                    help="local reads a paper's .tex+.bib and uses arXiv only; "
                         "unarxive streams 2.5M contexts but needs OpenAlex, whose "
                         "unauthenticated budget is per-IP per-day")
    ap.add_argument("--paper", default=None, help="--source local: paper directory")
    ap.add_argument("--bib", default=None, help="--source local: .bib file")
    ap.add_argument("--limit", type=int, default=2000,
                    help="unarXive rows to read")
    ap.add_argument("--max-papers", type=int, default=0,
                    help="--source arxiv-bulk: cap citing papers scanned (0 = all)")
    ap.add_argument("--corpus-root", default=None,
                    help="--source arxiv-bulk: corpus root (default the "
                         "arxiv_bulk cache)")
    ap.add_argument("--citrec", default=None,
                    help="locally cached prefix of unarXive citrec train.jsonl "
                         "(default benchmarks/data/unarxive/citrec_head.jsonl)")
    ap.add_argument("--max-fetch", type=int, default=40,
                    help="cap on arXiv e-print fetches (>=3 s apart)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rejects", default=None)
    ap.add_argument("--cache-dir", default=None)
    a = ap.parse_args(argv)

    t0 = time.time()
    cache = Path(a.cache_dir) if a.cache_dir else None
    # Opened before mining, not after: a multi-hour batch must survive a crash or
    # a shared rate-limit refusal with its candidates already on disk.
    sink = open(a.out, "w", encoding="utf-8") if a.out else None
    if a.source == "local":
        if not (a.paper and a.bib):
            print("--source local needs --paper and --bib")
            return 2
        kept, rejected = mine_local(Path(a.paper).expanduser().resolve(),
                                    Path(a.bib).expanduser(), cache,
                                    max_fetch=a.max_fetch, sink=sink)
        print(f"  {a.paper} -> {len(kept)} candidates, {len(rejected)} rejected, "
              f"{time.time() - t0:.0f}s")
    elif a.source == "arxiv-bulk":
        kept, rejected = mine_bulk(a.corpus_root, max_papers=a.max_papers,
                                   sink=sink)
        print(f"  corpus -> {len(kept)} candidates, {len(rejected)} rejected, "
              f"{time.time() - t0:.0f}s")
    else:
        kept, rejected = mine(a.limit, cache, max_fetch=a.max_fetch,
                              local=Path(a.citrec) if a.citrec else None,
                              sink=sink)
        print(f"  streamed {a.limit} rows -> {len(kept)} candidates, "
              f"{len(rejected)} rejected, {time.time() - t0:.0f}s")
    reasons: dict[str, int] = {}
    for r in rejected:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    for why in sorted(reasons, key=lambda k: -reasons[k]):
        print(f"    {why:34} {reasons[why]}")
    if sink:
        sink.close()
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
