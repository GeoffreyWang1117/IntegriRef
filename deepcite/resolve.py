"""Identifier resolution: bibguard for metadata ids, arXiv search for artifacts.

bibguard is the single source of METADATA id resolution -- deepcite shells out to
its CLI and reads ``resolved_ids`` rather than importing its internals, so the
public JSON stays the only contract between the two tools.

Locating the arXiv e-print is a separate job and deepcite does it itself. This is
not a second metadata resolver: bibguard only queries arXiv when the bib entry
already carries an arXiv id, so for any work cited by its published venue
(MQuAKE@EMNLP, TALLRec@RecSys) ``resolved_ids["arxiv_id"]`` comes back null and
there is no artifact to fetch. Artifact location is deepcite's own concern.

Spec: docs/DEEPCITE_SPEC_v1.1.md §2.5, §3.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .contract import MIN_BIBGUARD, parse_version
from .net import SourceRefused, http_get, polite_wait

ARXIV_API = "https://export.arxiv.org/api/query"


class BibguardTooOld(RuntimeError):
    def __init__(self, found: str):
        super().__init__(
            f"bibguard {found} is too old; deepcite needs "
            f"{'.'.join(map(str, MIN_BIBGUARD))}+ for resolved_ids. "
            f"Pass --bibguard to point at a newer build; deepcite will not fall "
            f"back to resolving ids itself.")
        self.found = found


@dataclass
class Resolved:
    bib_key: str
    title: str = ""
    resolved_ids: dict = field(default_factory=dict)
    overall: str = ""

    @property
    def arxiv_id(self) -> str | None:
        return (self.resolved_ids or {}).get("arxiv_id")

    @property
    def doi(self) -> str | None:
        return (self.resolved_ids or {}).get("doi")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def bibguard_version(bibguard_cmd: list[str]) -> str:
    """Version string of the bibguard that will actually be invoked."""
    for probe in (bibguard_cmd + ["--version"], bibguard_cmd + ["-V"]):
        r = _run(probe, timeout=120)
        m = re.search(r"(\d+\.\d+\.\d+)", (r.stdout or "") + (r.stderr or ""))
        if m:
            return m.group(1)
    raise RuntimeError(f"cannot determine bibguard version from {bibguard_cmd!r}")


def check_bibguard(bibguard_cmd: list[str]) -> str:
    v = bibguard_version(bibguard_cmd)
    if parse_version(v) < MIN_BIBGUARD:
        raise BibguardTooOld(v)
    return v


def resolve_bib(bib_path: Path, bibguard_cmd: list[str],
                timeout: int = 900) -> dict[str, Resolved]:
    """Run bibguard once over the whole .bib and collect resolved_ids per key."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "bibguard.json"
        # bibguard exits 1 when any entry FAILs; that is information, not an error.
        _run(bibguard_cmd + [str(bib_path), "--json", "--out", str(out)],
             timeout=timeout)
        if not out.exists():
            raise RuntimeError(f"bibguard produced no JSON at {out}")
        data = json.loads(out.read_text(encoding="utf-8"))
    results = {}
    for r in data.get("results", []):
        results[r["key"]] = Resolved(bib_key=r["key"], title=r.get("title", ""),
                                     resolved_ids=r.get("resolved_ids") or {},
                                     overall=r.get("overall", ""))
    return results


# --- arXiv artifact location ------------------------------------------------
#
# Two key-free sources, tried in this order:
#   1. DataCite, which registers every arXiv paper's DOI (10.48550/arXiv.<id>) and
#      records its latest version. Checked 2026-10-06 against the arXiv abs pages of
#      four papers: the version matched every time. It also answers when arXiv's own
#      API is rate-limiting this machine.
#   2. arXiv's query API (title phrase search), as before.
# A source that does not answer raises SourceRefused. Until 2026-10-06 that came
# back as None, so a 429 from arXiv was reported as "no arXiv artifact found;
# supply one with --artifact" -- the same conflation bibguard had before 0.6.0.

DATACITE_API = "https://api.datacite.org/dois"
_NORM_T = re.compile(r"[^a-z0-9]+")
def _tnorm(s: str) -> str:
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", s or "")       # LaTeX macros in bib titles
    # Case-protecting braces sit inside words ({MQ}u{AKE}); drop them, do not split.
    s = s.replace("{", "").replace("}", "")
    return _NORM_T.sub(" ", s.lower()).strip()


def _titles_match(want: str, got: str) -> bool:
    # Require a tight match: relevance search will happily return a different
    # paper, and fetching the wrong artifact is worse than none.
    return got == want or ((want in got or got in want) and abs(len(got) - len(want)) < 15)


def datacite_lookup(title: str, timeout: int = 60) -> str | None:
    """Versioned arXiv id for a title via DataCite, or None if it has no match."""
    want = _tnorm(title)
    words = [w for w in want.split() if len(w) > 2][:12]
    if not words:
        return None
    q = urllib.parse.urlencode({
        "query": "titles.title:(" + " AND ".join(words) + ")",
        "client-id": "arxiv.content", "page[size]": 10})
    body = http_get(f"{DATACITE_API}?{q}", "datacite", timeout)
    if body is None:
        return None
    try:
        data = json.loads(body.decode("utf-8", "replace")).get("data") or []
    except ValueError:
        raise SourceRefused("datacite", "unreadable response")
    for item in data:
        a = item.get("attributes") or {}
        titles = [x.get("title", "") for x in a.get("titles") or []]
        if not any(_titles_match(want, _tnorm(x)) for x in titles):
            continue
        m = re.search(r"arxiv\.(.+)$", (a.get("doi") or "").lower())
        ver = str(a.get("version") or "").strip()
        if m and ver.isdigit():
            return f"{m.group(1)}v{ver}"
    return None


def datacite_version(arxiv_id: str, timeout: int = 60) -> str | None:
    """``<id>v<n>`` for an unversioned arXiv id (latest version), or None."""
    base = re.sub(r"v\d+$", "", arxiv_id)
    body = http_get(f"{DATACITE_API}/10.48550/arXiv.{base}", "datacite", timeout)
    if body is None:
        return None
    try:
        ver = str(((json.loads(body).get("data") or {}).get("attributes") or {})
                  .get("version") or "")
    except ValueError:
        raise SourceRefused("datacite", "unreadable response")
    return f"{base}v{ver}" if ver.isdigit() else None


def arxiv_api_lookup(title: str, timeout: int = 60) -> str | None:
    """Versioned arXiv id via arXiv's own title search, or None."""
    if not title.strip():
        return None
    q = urllib.parse.urlencode({
        "search_query": f'ti:"{_tnorm(title)}"', "start": 0, "max_results": 5})
    body = http_get(f"{ARXIV_API}?{q}", "arxiv", timeout, wait=polite_wait)
    if body is None:
        return None
    xml = body.decode("utf-8", "replace")
    want = _tnorm(title)
    for ent in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        t = re.search(r"<title>(.*?)</title>", ent, re.S)
        i = re.search(r"<id>https?://arxiv\.org/abs/([^<]+)</id>", ent)
        if not (t and i):
            continue
        if _titles_match(want, _tnorm(re.sub(r"\s+", " ", t.group(1)))):
            vid = i.group(1)
            return vid if re.search(r"v\d+$", vid) else None
    return None


# --- lookup memo ---------------------------------------------------------------
# Successful lookups are kept so a re-run after a refusal only asks again for what
# was refused. A versioned id names immutable content, so a hit is safe to reuse;
# it is kept 30 days so a newer version is eventually noticed. A definite "no such
# paper" is kept 7 days (it may be posted later). Refusals are never stored.

_MEMO_TTL = {"hit": 30 * 86400, "miss": 7 * 86400}


def _memo_path() -> Path:
    from .fetch import default_cache_dir
    return default_cache_dir().parent / "lookup_memo.json"


def _memo_load() -> dict:
    try:
        return json.loads(_memo_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _memo_save(memo: dict) -> None:
    p = _memo_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(memo, indent=0, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def locate_arxiv(title: str, timeout: int = 60,
                 memo: dict | None = None) -> tuple[str | None, str]:
    """Find the versioned arXiv id for a title: ``(id or None, note)``.

    Returns ``<id>v<n>`` -- always versioned, because /e-print/<id> without a
    version serves whatever is latest and a cache keyed on an unversioned id
    would lie. Raises SourceRefused only if NO source answered. DataCite holds
    the DOI of every arXiv paper, so its "no match" is treated as an answer even
    when arXiv's search refused; the note says so, and that miss is not memoized
    so a later run asks arXiv again.
    """
    import time
    if not title.strip():
        return None, ""
    key = "t:" + _tnorm(title)
    store = memo if memo is not None else _memo_load()
    hit = store.get(key)
    if hit and time.time() - hit.get("at", 0) < _MEMO_TTL[hit.get("kind", "miss")]:
        return hit.get("id"), "cached lookup"
    refused: list[SourceRefused] = []
    for fn in (datacite_lookup, arxiv_api_lookup):
        try:
            got = fn(title, timeout)
        except SourceRefused as e:
            refused.append(e)
            continue
        if got:
            store[key] = {"id": got, "kind": "hit", "at": time.time()}
            if memo is None:
                _memo_save(store)
            return got, ""
    if len(refused) == 2:
        raise SourceRefused("datacite+arxiv",
                            "; ".join(f"{e.source} {e.reason}" for e in refused))
    if refused:
        return None, (f"no match where asked; {refused[0].source} search did not "
                      f"answer ({refused[0].reason})")
    store[key] = {"id": None, "kind": "miss", "at": time.time()}
    if memo is None:
        _memo_save(store)
    return None, "no arXiv paper with this title (DataCite and arXiv search)"


def arxiv_lookup(title: str, timeout: int = 60, memo: dict | None = None) -> str | None:
    return locate_arxiv(title, timeout, memo)[0]


def versioned(arxiv_id: str, title: str = "", memo: dict | None = None) -> str | None:
    """Make an arXiv id versioned: keep a printed version, else ask DataCite for the
    latest, else fall back to a title lookup."""
    if re.search(r"v\d+$", arxiv_id):
        return arxiv_id
    try:
        got = datacite_version(arxiv_id)
    except SourceRefused:
        got = None
    if got:
        return got
    return locate_arxiv(title, memo=memo)[0] if title else None


# --- OpenReview (ICLR / NeurIPS / TMLR papers never posted to arXiv) ---------

OPENREVIEW_API = "https://api2.openreview.net/notes/search"


def _or_value(v):
    return v.get("value") if isinstance(v, dict) else v


def openreview_lookup(title: str, timeout: int = 60,
                      memo: dict | None = None) -> tuple[str, str] | None:
    """``(note_id, pdf_path)`` for a title on OpenReview, or None.

    Prefers the accepted version ("ICLR 2025 Spotlight") over a rejected or
    withdrawn submission of the same paper ("Submitted to NeurIPS 2024"), and
    only returns a note that has a PDF. Raises SourceRefused when OpenReview
    does not answer. Found 2026-10-06: MQuAKE-Remastered (ICLR 2025) is on
    OpenReview and not on arXiv.
    """
    import time
    want = _tnorm(title)
    if not want:
        return None
    key = "or:" + want
    if memo is not None:
        hit = memo.get(key)
        if hit and time.time() - hit.get("at", 0) < _MEMO_TTL[hit.get("kind", "miss")]:
            return tuple(hit["id"]) if hit.get("id") else None
    q = urllib.parse.urlencode({"term": title.replace("{", "").replace("}", ""),
                                "type": "terms", "content": "title", "limit": 10})
    body = http_get(f"{OPENREVIEW_API}?{q}", "openreview", timeout)
    notes = []
    if body is not None:
        try:
            notes = json.loads(body.decode("utf-8", "replace")).get("notes") or []
        except ValueError:
            raise SourceRefused("openreview", "unreadable response")
    best = None
    for n in notes:
        c = n.get("content") or {}
        if not _titles_match(want, _tnorm(_or_value(c.get("title")) or "")):
            continue
        pdf = _or_value(c.get("pdf"))
        if not pdf or not str(pdf).startswith("/pdf"):
            continue
        venue = str(_or_value(c.get("venue")) or "")
        rank = 0 if venue and not venue.lower().startswith(("submitted", "withdrawn", "desk")) else 1
        if best is None or rank < best[0]:
            best = (rank, n.get("id"), str(pdf))
    got = (best[1], best[2]) if best else None
    if memo is not None:
        memo[key] = {"id": list(got) if got else None,
                     "kind": "hit" if got else "miss", "at": time.time()}
    return got


# --- the bib itself ----------------------------------------------------------

_ENTRY_HEAD = re.compile(r"@(\w+)\s*[{(]\s*([^,\s]+)\s*,", re.IGNORECASE)


def split_bib(text: str) -> dict[str, str]:
    """``{key: entry_text}`` by brace matching. @string/@preamble/@comment are
    collected under the pseudo-key ``@strings`` so a subset keeps its macros."""
    out: dict[str, str] = {}
    strings: list[str] = []
    i = 0
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        m = re.match(r"@(\w+)\s*([{(])", text[at:])
        if not m:
            i = at + 1
            continue
        open_ch = m.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        depth, j = 0, at + m.end() - 1
        while j < len(text):
            c = text[j]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        entry = text[at:j + 1]
        kind = m.group(1).lower()
        if kind in ("string", "preamble"):
            strings.append(entry)
        elif kind != "comment":
            h = _ENTRY_HEAD.match(entry)
            if h:
                out.setdefault(h.group(2), entry)
        i = j + 1
    if strings:
        out["@strings"] = "\n".join(strings)
    return out


def bib_field(entry: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*=\s*", entry, re.IGNORECASE)
    if not m:
        return ""
    s = entry[m.end():]
    if s[:1] == "{":
        depth, j = 0, 0
        for j, c in enumerate(s):
            depth += c == "{"
            depth -= c == "}"
            if depth == 0:
                break
        return re.sub(r"\s+", " ", s[1:j]).strip()
    if s[:1] == '"':
        k = s.find('"', 1)
        return re.sub(r"\s+", " ", s[1:k]).strip()
    return re.split(r"[,}\n]", s, maxsplit=1)[0].strip()


def bib_arxiv_id(entry: str) -> str | None:
    """An arXiv id printed in the entry itself (eprint, journal, url, note).

    Read locally so that bibguard not reaching arXiv (rate limit) does not lose
    an id the author already wrote down."""
    for f in ("eprint", "arxiv", "journal", "url", "note", "howpublished", "volume"):
        v = bib_field(entry, f)
        if not v:
            continue
        if f == "eprint" or "arxiv" in v.lower():
            got = normalize_arxiv_id(v)
            if got:
                return got
    return None


def normalize_arxiv_id(raw: str) -> str | None:
    """Strip prefixes from whatever bibguard or a bib entry gave us."""
    if not raw:
        return None
    m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
                  str(raw))
    return m.group(1) if m else None
