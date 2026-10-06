"""deepcite command line.

``run`` is fully deterministic and never calls an LLM. It produces a worklist of
candidate evidence; a human (or an agent, via ``annotate``) does the judging.

Exit codes (§4):
  0  ran -- including "nothing was selected", which still writes a cache so the
     reader can tell "checked, nothing in scope" from "never ran"
  2  bib missing or unparseable (an invocation error)
  3  bibguard too old; we fail loudly rather than resolve ids ourselves
  4  every record failed (only when there is at least one record)
  5  annotate rejected an opinion (a quote did not resolve, or context is stale)
  1  unexpected failure (reserved; never used deliberately)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import cache as K
from . import contract as C
from . import fetch as F
from . import resolve as R
from . import retrieve as V
from . import select as S
from . import report as REP
from . import texscan as T
from .annotate import QuoteRejected, locate, verify_opinion
from .net import SourceRefused

# A monthly backup snapshot is not a scratch directory. Running with --paper
# pointed at one would write .cache/ into the backup, and the snapshot tree is
# writable, so nothing else stops it.
PROTECTED = ("ProjectsBackup/snapshots",)
MAX_URL_BYTES = 64 * 1024 * 1024


def _refuse_protected(path: Path, what: str) -> None:
    s = str(path.resolve())
    for frag in PROTECTED:
        if frag in s:
            sys.exit(f"deepcite: refusing to write {what} under a backup snapshot:\n"
                     f"  {s}\n"
                     f"Copy the fixture to a scratch directory and run there, or "
                     f"pass --cache-dir pointing outside the snapshot.")


def _warn_gitignore(paper: Path, cache_dir: Path | None) -> None:
    target = Path(cache_dir) if cache_dir else paper / ".cache"
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(target)],
                           cwd=str(paper), capture_output=True, timeout=30)
    except Exception:
        return
    if r.returncode != 0:
        rel = target.name if cache_dir else ".cache/"
        print(f"  warning: {target} is not git-ignored. Add this line to "
              f".gitignore:\n           {rel}", file=sys.stderr)


# --- artifact location ------------------------------------------------------

def _fetch_url(url: str, dest: Path) -> bool:
    """Fetch a user-supplied --artifact URL. http(s) only, size capped."""
    if not url.lower().startswith(("http://", "https://")):
        return False

    class _NoScheme(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.lower().startswith(("http://", "https://")):
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    op = urllib.request.build_opener(_NoScheme)
    try:
        with op.open(urllib.request.Request(
                url, headers={"User-Agent": F.USER_AGENT}), timeout=120) as fh:
            data = fh.read(MAX_URL_BYTES + 1)
    except Exception:
        return False
    if len(data) > MAX_URL_BYTES:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def _manual_artifact(spec: str, cache_dir: Path) -> tuple[dict, list[Path], Path] | None:
    """A user-supplied artifact (path or URL). A PDF is searched through its text."""
    p = Path(spec).expanduser()
    if p.exists():
        got = F.artifact_files(p, cache_dir / "manual_text")
        if not got:
            return None
        files, root = got
        return ({"kind": "data_file", "ref": str(p),
                 "sha256": C.file_sha256(p.read_bytes()) if p.is_file() else None},
                files, root)
    dest = cache_dir / "manual" / (spec.replace("/", "_")[-80:] or "artifact")
    if _fetch_url(spec, dest):
        got = F.artifact_files(dest, cache_dir / "manual_text")
        if not got:
            return None
        files, root = got
        return ({"kind": "url", "ref": spec,
                 "sha256": C.file_sha256(dest.read_bytes())}, files, root)
    return None


# --- run --------------------------------------------------------------------

_BIBLIO = re.compile(r"\\bibliography\s*\{([^}]+)\}")
_ADDBIB = re.compile(r"\\addbibresource(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def _detect_bibs(paper: Path, rels: list[str]) -> list[Path]:
    """The .bib files the paper names (\\bibliography / \\addbibresource), else
    every .bib in the paper directory."""
    out: list[Path] = []
    for rel in rels:
        try:
            txt = C.strip_comments((paper / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        names = [n.strip() for m in _BIBLIO.finditer(txt) for n in m.group(1).split(",")]
        names += [m.group(1).strip() for m in _ADDBIB.finditer(txt)]
        for n in names:
            for base in (paper, (paper / rel).parent):
                cand = base / (n if n.endswith(".bib") else n + ".bib")
                if cand.is_file() and cand.resolve() not in {o.resolve() for o in out}:
                    out.append(cand)
                    break
    return out or sorted(paper.glob("*.bib"))


@dataclass
class Located:
    cited_id: str | None = None
    cited_title: str | None = None
    artifact: dict | None = None
    files: list = field(default_factory=list)
    root: Path | None = None
    status: str | None = None
    error: str | None = None


def _locate(key: str, res, entry: str, manual_spec: str | None, art_cache: Path,
            memo: dict) -> Located:
    """Find and fetch one cited work's primary artifact. Called once per bib key."""
    title = (res.title if res and res.title else "") or R.bib_field(entry, "title")
    loc = Located(cited_title=re.sub(r"[{}]", "", title) or None)
    doi = (res.doi if res else None) or R.bib_field(entry, "doi") or None
    if manual_spec:
        got = _manual_artifact(manual_spec, art_cache)
        if got:
            loc.artifact, loc.files, loc.root = got
            loc.cited_id = f"doi:{doi}" if doi else None
        else:
            loc.status, loc.error = K.UNRESOLVED, f"--artifact not usable: {manual_spec}"
        return loc

    refused: list[str] = []
    note = ""
    aid = (R.normalize_arxiv_id(res.arxiv_id) if res and res.arxiv_id else None) \
        or R.bib_arxiv_id(entry)
    try:
        if aid:
            aid = R.versioned(aid, title, memo)
            if not aid:
                note = "the arXiv id in the bib has no resolvable version"
        elif title:
            # bibguard only queries arXiv when the entry already carries an arXiv
            # id, so for venue-cited works we must locate it ourselves.
            aid, note = R.locate_arxiv(title, memo=memo)
    except SourceRefused as e:
        refused.append(f"{e.source}: {e.reason}")
        aid = None

    if aid:
        loc.cited_id = f"arXiv:{aid}"
        try:
            art = F.fetch_eprint(aid, art_cache)
        except SourceRefused as e:
            loc.status = K.SOURCE_UNAVAILABLE
            loc.error = f"arXiv did not answer when fetching {aid} ({e.reason}); re-run later"
            return loc
        if art is None:
            loc.status, loc.error = K.NO_FULLTEXT, f"e-print not retrievable: {aid}"
            return loc
        if not art.files:
            loc.status = K.WRONG_ARTIFACT_KIND
            loc.error = (f"{aid} is PDF-only and its PDF has no text layer; supply "
                         f"--artifact {key}=<path-or-url>")
            return loc
        loc.artifact = {"kind": art.kind, "ref": loc.cited_id,
                        "sha256": C.file_sha256(art.archive.read_bytes())}
        loc.files, loc.root = art.files, art.root
        return loc

    acl = F.acl_id(doi)
    if acl:
        loc.cited_id = f"doi:{doi}"
        try:
            art = F.fetch_acl(acl, art_cache)
        except SourceRefused as e:
            refused.append(f"{e.source}: {e.reason}")
            art = None
        if art and art.files:
            loc.artifact = {"kind": art.kind, "ref": f"https://aclanthology.org/{acl}.pdf",
                            "sha256": C.file_sha256(art.archive.read_bytes())}
            loc.files, loc.root = art.files, art.root
            return loc

    if title:
        try:
            orv = R.openreview_lookup(title, memo=memo)
            art = F.fetch_openreview(*orv, cache_dir=art_cache) if orv else None
        except SourceRefused as e:
            refused.append(f"{e.source}: {e.reason}")
            orv, art = None, None
        if orv and art and art.files:
            loc.cited_id = f"openreview:{orv[0]}"
            loc.artifact = {"kind": art.kind, "ref": f"https://openreview.net/forum?id={orv[0]}",
                            "sha256": C.file_sha256(art.archive.read_bytes())}
            loc.files, loc.root = art.files, art.root
            return loc

    if refused:
        loc.status = K.SOURCE_UNAVAILABLE
        loc.error = ("could not look the work up -- " + "; ".join(refused)
                     + ". Not evidence about the citation; re-run later (successful "
                       "lookups are cached)")
    else:
        loc.status = K.UNRESOLVED
        loc.error = (f"not found on arXiv, ACL Anthology or OpenReview"
                     + (f" ({note})" if note else "")
                     + f"; supply one with --artifact {key}=<path-or-url>")
    return loc


def _records_for(selected, resolved, entries, manual, art_cache, memo, rank_mode):
    """Locate each cited work once, then rank passages per citing sentence."""
    located: dict[str, Located] = {}
    records = []
    for rel, cit, sel in selected:
        key = cit.bib_key
        if key not in located:
            located[key] = _locate(key, resolved.get(key), entries.get(key, ""),
                                   manual.get(key), art_cache, memo)
        loc = located[key]
        sent = cit.sentence
        ctx = C.context_sha256(sent.text)
        terms = S.search_terms(sent.text)
        status, passages = loc.status, []
        if loc.files:
            ps = V.rank(terms, loc.files, loc.root, claim_type=sel.claim_type)
            if rank_mode == "nli":
                ps = V.nli_rerank(sent.text, ps)
            passages = [{"file": p.file, "line_start": p.line_start,
                         "line_end": p.line_end, "text": p.text,
                         "rank_score": p.rank_score} for p in ps[:V.MAX_PASSAGES]]
            status = K.CANDIDATE_EVIDENCE if passages else K.CLAIM_ABSENT_FROM_ARTIFACT
        quotes = _check_quotes(sent.text, loc)
        records.append(K.record(
            bib_key=key, cited_id=loc.cited_id,
            artifact=loc.artifact or {"kind": "none", "ref": "", "sha256": None},
            citing={"file": rel, "line": sent.line,
                    "byte_start": sent.byte_start, "byte_end": sent.byte_end,
                    "sentence": sent.text, "context_sha256": ctx},
            selection={"family": sel.family, "pattern_id": sel.pattern_id,
                       "claim_type": sel.claim_type},
            status=status or K.UNRESOLVED, search_terms=terms, passages=passages,
            record_id=C.record_id(key, loc.cited_id, rel, ctx), error=loc.error,
            cited_title=loc.cited_title))
        if quotes is not None:
            records[-1]["quotes_checked"] = quotes
    return records


def _check_quotes(sentence: str, loc: Located) -> list[dict] | None:
    """Every quotation in the citing sentence, looked up verbatim in the artifact.

    Deterministic, and the one place deepcite states a fact about a citation: a
    quotation either occurs in the cited work's text or it does not. Whitespace,
    dashes, LaTeX commands and line-end hyphens are normalized; words are not.
    """
    spans = S.quoted_spans(sentence)
    if not spans or not loc.files:
        return None
    out = []
    for q in spans:
        hit = locate(q, loc.files, loc.root)
        out.append({"text": q, "found": bool(hit),
                    "file": hit.file if hit else None, "line": hit.line if hit else None})
    return out


def _finish(paper: Path, payload: dict, cache_dir: Path | None, records: list) -> int:
    out = K.write(paper, payload, cache_dir)
    _warn_gitignore(paper, cache_dir)
    md = out.with_suffix(".md")
    md.write_text(REP.render(payload), encoding="utf-8")
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    for st in sorted(by_status):
        print(f"    {st:28} {by_status[st]}")
    print(f"  wrote {out}\n  report {md}")
    if by_status.get(K.SOURCE_UNAVAILABLE):
        print(f"  {by_status[K.SOURCE_UNAVAILABLE]} record(s) not checked because a source "
              f"refused (rate limit / timeout): re-run later; nothing is wrong with them yet.")
    if records and all(r["status"] in K.FAILED_STATUSES for r in records):
        print("  every record failed to resolve or fetch", file=sys.stderr)
        return C.EXIT_ALL_RECORDS_FAILED
    return C.EXIT_OK


def cmd_run(a: argparse.Namespace) -> int:
    paper = Path(a.paper).expanduser().resolve()
    if not paper.is_dir():
        print(f"deepcite: --paper is not a directory: {paper}", file=sys.stderr)
        return C.EXIT_NO_BIB

    cache_dir = Path(a.cache_dir).expanduser().resolve() if a.cache_dir else None
    _refuse_protected(cache_dir or paper, "the cache file")
    art_cache = Path(a.artifact_cache).expanduser() if a.artifact_cache \
        else F.default_cache_dir()
    _refuse_protected(art_cache, "fetched artifacts")

    main_tex = a.main or T.find_main(paper)
    # 1. Scan the .tex the paper actually includes -- the full selection input set
    #    goes into files_sha256, so a citing file absent from the map reads as
    #    unscanned, not clean.
    rels = T.tex_files(paper, main_tex)
    bibs = [Path(b).expanduser() for b in (a.bib or [])] or _detect_bibs(paper, rels)
    missing = [b for b in bibs if not b.is_file()]
    if not bibs or missing:
        print(f"deepcite: bib file not found: {missing[0] if missing else '(none named or present)'}"
              f" -- pass --bib", file=sys.stderr)
        return C.EXIT_NO_BIB

    bibguard_cmd = a.bibguard.split() if a.bibguard else ["bibguard"]
    try:
        bg_version = R.check_bibguard(bibguard_cmd)
    except R.BibguardTooOld as e:
        print(f"deepcite: {e}", file=sys.stderr)
        return C.EXIT_BIBGUARD_TOO_OLD
    except Exception as e:
        print(f"deepcite: {e}", file=sys.stderr)
        return C.EXIT_BIBGUARD_TOO_OLD

    files_sha, selected = {}, []
    for rel in rels:
        sf = T.scan_file(paper, rel)
        files_sha[rel] = sf.raw_sha256
        for cit in sf.citations:
            sel = S.select(cit.sentence.text)
            if sel:
                selected.append((rel, cit, sel))

    print(f"  main {main_tex or '(not found; scanned every .tex)'}; scanned {len(rels)} "
          f".tex file(s); {len(selected)} citation(s) make a checkable claim")

    manual = dict(kv.split("=", 1) for kv in (a.artifact or []) if "=" in kv)

    if not selected:
        # Not an error: a paper may simply make no checkable claims. Write the
        # cache anyway so the reader can distinguish this from "never ran".
        payload = K.build(paper, main_tex, files_sha, [], S.patterns_sha256(), bg_version)
        return _finish(paper, payload, cache_dir, [])

    # 2. Resolve ids for the SELECTED keys only. bibguard over the whole .bib was
    #    the slowest step (55 s for a 20-key paper) and most keys are never used.
    entries: dict[str, str] = {}
    for b in bibs:
        try:
            for k, v in R.split_bib(b.read_text(encoding="utf-8", errors="replace")).items():
                entries.setdefault(k, v)
        except OSError as e:
            print(f"deepcite: cannot read {b}: {e}", file=sys.stderr)
            return C.EXIT_NO_BIB
    keys = sorted({cit.bib_key for _, cit, _ in selected})
    unknown = [k for k in keys if k not in entries]
    if unknown:
        print(f"  note: {len(unknown)} cited key(s) not in the .bib: {', '.join(unknown[:5])}")
    with tempfile.TemporaryDirectory() as td:
        subset = Path(td) / "selected.bib"
        subset.write_text("\n\n".join(([entries["@strings"]] if "@strings" in entries else [])
                                      + [entries[k] for k in keys if k in entries]) + "\n",
                          encoding="utf-8")
        try:
            resolved = R.resolve_bib(subset, bibguard_cmd)
        except Exception as e:
            print(f"deepcite: bibguard failed: {e}", file=sys.stderr)
            return C.EXIT_NO_BIB

    memo = R._memo_load()
    records = _records_for(selected, resolved, entries, manual, art_cache, memo, a.rank)
    R._memo_save(memo)
    payload = K.build(paper, main_tex, files_sha, records, S.patterns_sha256(), bg_version)
    return _finish(paper, payload, cache_dir, records)


# --- review: someone else's submission, PDF only -------------------------------

class _PdfRef:
    """What _locate needs from a reference-list entry (bibguard is not involved:
    there is no .bib, only the printed list)."""

    def __init__(self, e):
        self.title = e.title
        self.arxiv_id = e.arxiv_id
        self.doi = e.doi


def cmd_review(a: argparse.Namespace) -> int:
    """Build the worklist for a submission PDF you are reviewing.

    Only the titles and identifiers of the CITED works leave this machine (to
    DataCite, arXiv, the ACL Anthology and OpenReview); the submission's text
    does not. Judging the worklist with an LLM is a separate step (packet /
    annotate) and is subject to the venue's reviewing policy on LLM use.
    """
    from . import pdfscan as P
    pdf = Path(a.pdf).expanduser().resolve()
    if not pdf.is_file():
        print(f"deepcite: not a file: {pdf}", file=sys.stderr)
        return C.EXIT_NO_BIB
    out_dir = Path(a.out).expanduser().resolve() if a.out else pdf.with_name(pdf.stem + "_deepcite")
    _refuse_protected(out_dir, "the review worklist")
    art_cache = Path(a.artifact_cache).expanduser() if a.artifact_cache \
        else F.default_cache_dir()
    _refuse_protected(art_cache, "fetched artifacts")

    scan = P.scan_pdf(pdf)
    out_dir.mkdir(parents=True, exist_ok=True)
    body = out_dir / "body.txt"
    body.write_text(scan.body, encoding="utf-8")
    for w in scan.warnings:
        print(f"  note: {w}")
    refs = {e.index: e for e in scan.refs}
    # Keys per marker, for the long-list guard ("[3-9]" is background).
    per_marker: dict[tuple[int, str], int] = {}
    for c in scan.citations:
        k = (c.sentence.byte_start, c.marker)
        per_marker[k] = per_marker.get(k, 0) + 1
    selected = []
    for c in scan.citations:
        sel = S.select(c.sentence.text, masked=c.masked,
                       max_keys_per_marker=per_marker[(c.sentence.byte_start, c.marker)])
        if sel:
            selected.append((c, sel))
    print(f"  {len(scan.refs)} references ({scan.style}), {len(scan.citations)} citation(s); "
          f"{len(selected)} make a checkable claim")

    def key_of(e) -> str:
        return f"ref{e.index}" + (f"[{e.label}]" if e.label and e.label != str(e.index) else "")

    manual = dict(kv.split("=", 1) for kv in (a.artifact or []) if "=" in kv)
    memo = R._memo_load()
    located: dict[int, Located] = {}
    records = []
    for c, sel in selected:
        e = refs.get(c.ref_index)
        if e is None:
            continue
        key = key_of(e)
        if e.index not in located:
            located[e.index] = _locate(key, _PdfRef(e), "", manual.get(key) or manual.get(str(e.index)),
                                       art_cache, memo)
            if not located[e.index].cited_title:
                located[e.index].cited_title = e.title or e.raw[:120]
        loc = located[e.index]
        sent = c.sentence
        ctx = C.context_sha256(sent.text)
        terms = S.search_terms(c.masked.replace(S.CITE_TOKEN, " "))
        status, passages = loc.status, []
        if loc.files:
            ps = V.rank(terms, loc.files, loc.root, claim_type=sel.claim_type)
            passages = [{"file": q.file, "line_start": q.line_start, "line_end": q.line_end,
                         "text": q.text, "rank_score": q.rank_score} for q in ps[:V.MAX_PASSAGES]]
            status = K.CANDIDATE_EVIDENCE if passages else K.CLAIM_ABSENT_FROM_ARTIFACT
        rec = K.record(
            bib_key=key, cited_id=loc.cited_id,
            artifact=loc.artifact or {"kind": "none", "ref": "", "sha256": None},
            citing={"file": "body.txt", "line": sent.line, "page": scan.page_of(sent.char_start),
                    "byte_start": sent.byte_start, "byte_end": sent.byte_end,
                    "sentence": sent.text, "context_sha256": ctx, "marker": c.marker},
            selection={"family": sel.family, "pattern_id": sel.pattern_id,
                       "claim_type": sel.claim_type},
            status=status or K.UNRESOLVED, search_terms=terms, passages=passages,
            record_id=C.record_id(key, loc.cited_id, "body.txt", ctx), error=loc.error,
            cited_title=loc.cited_title)
        q = _check_quotes(sent.text, loc)
        if q is not None:
            rec["quotes_checked"] = q
        records.append(rec)
    R._memo_save(memo)

    payload = K.build(out_dir, None, {"body.txt": C.file_sha256(body.read_bytes())}, records,
                      S.patterns_sha256(), "not used (review of a PDF)")
    payload["paper"].update({"kind": "pdf", "pdf": str(pdf), "pdf_sha256": scan.pdf_sha256,
                             "body_text": str(body), "reference_style": scan.style,
                             "references": len(scan.refs)})
    out = K.write(out_dir, payload, out_dir)
    md = out.with_suffix(".md")
    md.write_text(REP.render(payload), encoding="utf-8")
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    for st in sorted(by_status):
        print(f"    {st:28} {by_status[st]}")
    print(f"  wrote {out}\n  report {md}")
    if records and all(r["status"] in K.FAILED_STATUSES for r in records):
        return C.EXIT_ALL_RECORDS_FAILED
    return C.EXIT_OK


# --- annotate ---------------------------------------------------------------

def _cache_dir_for(paper: Path, cache_dir: Path | None) -> Path | None:
    """Where the cache is: --cache-dir, else <paper>/.cache, else <paper> itself
    (a `review` output directory holds ref_check_deep.json at its top)."""
    if cache_dir:
        return cache_dir
    if not K.cache_path(paper).exists() and (paper / "ref_check_deep.json").exists():
        return paper
    return None


def _apply_opinion(paper: Path, data: dict, rec: dict, opinion: dict,
                   art_cache: Path) -> str | None:
    """Verify one opinion and attach it. Returns None, or why it was rejected."""
    # An opinion must not outlive the sentence it judged.
    ci = rec["citing"]
    src = (paper / ci["file"]) if data["paper"].get("kind", "tex") == "tex" else None
    if src is not None:
        if not src.exists():
            return f"citing file is gone: {src}"
        state = C.classify_staleness(
            data["paper"]["files_sha256"].get(ci["file"]), src.read_bytes(),
            ci["context_sha256"], ci["byte_start"], ci["byte_end"], ci["sentence"])
        if state == C.STALE:
            return ("STALE -- the citing sentence has changed since it was produced. "
                    "Re-run `deepcite run` first.")
    # Quotes are checked against the artifact on disk, located the same way the
    # run that produced this record located it.
    got = F.local_files(rec.get("artifact") or {}, rec.get("cited_id"), art_cache)
    if not got:
        return (f"cached artifact for {rec.get('cited_id') or rec.get('bib_key')} is not "
                f"on disk; re-run `deepcite run`")
    files, root = got
    try:
        rec["llm_opinion"] = verify_opinion(opinion, files, root)
    except QuoteRejected as e:
        return f"opinion rejected -- {e}"
    return None


def cmd_annotate(a: argparse.Namespace) -> int:
    paper = Path(a.paper).expanduser().resolve()
    cache_dir = _cache_dir_for(paper, Path(a.cache_dir).expanduser().resolve() if a.cache_dir else None)
    try:
        data = K.read(paper, cache_dir)
    except Exception as e:
        print(f"deepcite: {e}", file=sys.stderr)
        return C.EXIT_NO_BIB
    art_cache = Path(a.artifact_cache).expanduser() if a.artifact_cache \
        else F.default_cache_dir()
    by_id = {r["record_id"]: r for r in data["records"]}

    if a.opinions:
        lines = Path(a.opinions).expanduser().read_text(encoding="utf-8").splitlines()
        batch = [json.loads(x) for x in lines if x.strip()]
    elif a.record and a.opinion_file:
        batch = [{**json.loads(Path(a.opinion_file).expanduser().read_text(encoding="utf-8")),
                  "record": a.record}]
    else:
        print("deepcite: give --opinions FILE.jsonl, or --record ID --opinion-file F",
              file=sys.stderr)
        return C.EXIT_NO_BIB

    rejected = 0
    for op in batch:
        rid = op.get("record")
        rec = by_id.get(rid)
        why = f"no record {rid}" if rec is None else _apply_opinion(paper, data, rec, op, art_cache)
        if why:
            rejected += 1
            print(f"  REJECTED {rid}: {why}", file=sys.stderr)
        else:
            n = len(rec["llm_opinion"]["quotes"])
            print(f"  accepted {rid}: {rec['llm_opinion'].get('assessment', '-')}, "
                  f"{n} quote(s) verified against {rec.get('cited_id')}")
    out = K.write(paper, data, cache_dir)
    out.with_suffix(".md").write_text(REP.render(data), encoding="utf-8")
    return C.EXIT_QUOTE_REJECTED if rejected else C.EXIT_OK


# --- report / packet ---------------------------------------------------------

def cmd_report(a: argparse.Namespace) -> int:
    paper = Path(a.paper).expanduser().resolve()
    cache_dir = _cache_dir_for(paper, Path(a.cache_dir).expanduser().resolve() if a.cache_dir else None)
    try:
        data = K.read(paper, cache_dir)
    except Exception as e:
        print(f"deepcite: {e}", file=sys.stderr)
        return C.EXIT_NO_BIB
    text = REP.render(data)
    if a.out:
        Path(a.out).expanduser().write_text(text, encoding="utf-8")
        print(f"  wrote {a.out}")
    else:
        print(text)
    return C.EXIT_OK


def cmd_packet(a: argparse.Namespace) -> int:
    paper = Path(a.paper).expanduser().resolve()
    cache_dir = _cache_dir_for(paper, Path(a.cache_dir).expanduser().resolve() if a.cache_dir else None)
    try:
        data = K.read(paper, cache_dir)
    except Exception as e:
        print(f"deepcite: {e}", file=sys.stderr)
        return C.EXIT_NO_BIB
    art_cache = Path(a.artifact_cache).expanduser() if a.artifact_cache \
        else F.default_cache_dir()
    text = REP.packet(data, art_cache, include_judged=a.all)
    if a.out:
        Path(a.out).expanduser().write_text(text, encoding="utf-8")
        print(f"  wrote {a.out}")
    else:
        print(text)
    return C.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m deepcite", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="build the worklist (deterministic, no LLM)")
    r.add_argument("--paper", default=".", help="paper directory (default: .)")
    r.add_argument("--bib", action="append", default=None,
                   help="bib file; repeatable. Default: what \\bibliography / "
                        "\\addbibresource name, else every .bib in --paper")
    r.add_argument("--main", default=None,
                   help="main .tex (default: main.tex, or the only top-level document); "
                        "only files it \\inputs are scanned")
    r.add_argument("--artifact", action="append", metavar="KEY=PATH_OR_URL",
                   help="primary artifact for a bib key whose claim is not in a "
                        "paper (e.g. a released data file)")
    r.add_argument("--cache-dir", default=None,
                   help="where ref_check_deep.json goes (default <paper>/.cache)")
    r.add_argument("--artifact-cache", default=None,
                   help=f"fetched e-prints (default {F.default_cache_dir()})")
    r.add_argument("--rank", choices=("lexical", "nli"), default="lexical",
                   help="lexical is deterministic; nli only reorders and needs torch")
    r.add_argument("--bibguard", default=None,
                   help="command to invoke bibguard (default: bibguard)")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("review", help="worklist for a submission PDF (no .tex/.bib needed)")
    v.add_argument("pdf")
    v.add_argument("--out", default=None, help="output directory (default <pdf stem>_deepcite/ beside it)")
    v.add_argument("--artifact", action="append", metavar="KEY=PATH_OR_URL",
                   help="primary artifact for a reference, KEY = ref<N> or N")
    v.add_argument("--artifact-cache", default=None)
    v.set_defaults(func=cmd_review)

    n = sub.add_parser("annotate", help="attach opinions; every quote is verified")
    n.add_argument("--paper", default=".")
    n.add_argument("--record", default=None)
    n.add_argument("--opinion-file", default=None)
    n.add_argument("--opinions", default=None,
                   help="JSONL, one opinion per line with a \"record\" field")
    n.add_argument("--cache-dir", default=None)
    n.add_argument("--artifact-cache", default=None)
    n.set_defaults(func=cmd_annotate)

    rp = sub.add_parser("report", help="the worklist as Markdown")
    rp.add_argument("--paper", default=".")
    rp.add_argument("--cache-dir", default=None)
    rp.add_argument("--out", default=None)
    rp.set_defaults(func=cmd_report)

    pk = sub.add_parser("packet", help="what a judge (human or agent) needs per record")
    pk.add_argument("--paper", default=".")
    pk.add_argument("--cache-dir", default=None)
    pk.add_argument("--artifact-cache", default=None)
    pk.add_argument("--all", action="store_true", help="include records already judged")
    pk.add_argument("--out", default=None)
    pk.set_defaults(func=cmd_packet)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.func(a)
