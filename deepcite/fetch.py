"""Fetch the cited work's primary artifact from arXiv, with a disk cache.

Content sniffing happens INSIDE the integrity check, not as a fallback after it.
arXiv's /e-print endpoint serves three different things and the obvious reuse of
final-reviewer's ``_archive_ok`` (gzip + tarfile only) rejects two of them, then
retries three times with growing timeouts before mislabelling them NO_FULLTEXT --
about fifteen wasted minutes per PDF-only reference.

Spec: docs/DEEPCITE_SPEC_v1.1.md §3, pitfall 4 in §7.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .net import MIN_INTERVAL, USER_AGENT, SourceRefused, http_get, polite_wait  # noqa: F401

EPRINT_URL = "https://arxiv.org/e-print/{id}"
MAX_BYTES = 80 * 1024 * 1024


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "integriref/deepcite/eprints"


# --- integrity + sniffing ---------------------------------------------------

KIND_TAR = "arxiv_source"
KIND_TEX = "arxiv_source"       # single-file e-print, still LaTeX source
KIND_PDF = "arxiv_pdf"


def sniff(path: Path) -> str | None:
    """Return 'tar' | 'gz' | 'pdf' | None by reading the file, not its name.

    None means the bytes are not a complete artifact of any kind we handle --
    truncated download, HTML error page, or something new.
    """
    try:
        head = path.open("rb").read(5)
    except OSError:
        return None
    if head[:4] == b"%PDF":
        return "pdf" if path.stat().st_size > 1024 else None
    if head[:2] != b"\x1f\x8b":
        return None
    # Read the whole stream so gzip's trailing CRC is actually checked; a
    # truncated download passes a header-only test.
    try:
        with gzip.open(path, "rb") as fh:
            while fh.read(1 << 20):
                pass
    except Exception:
        return None
    try:
        with tarfile.open(path, "r:gz") as tf:
            tf.getmembers()
        return "tar"
    except Exception:
        return "gz"          # valid gzip, not a tar: a single-file .tex e-print


@dataclass(frozen=True)
class Artifact:
    kind: str            # arxiv_source | arxiv_pdf
    root: Path           # directory of extracted sources / of the PDF's text
    files: list[Path]    # .tex files, or [the PDF's text file]
    archive: Path        # the downloaded blob, for sha256


def _download(url: str, dest: Path, timeout: int) -> bool:
    """True when downloaded, False when arXiv answered that there is nothing
    (404). Raises SourceRefused when arXiv did not answer."""
    body = http_get(url, "arxiv", timeout, wait=polite_wait, max_bytes=MAX_BYTES)
    if body is None:
        return False
    dest.write_bytes(body)
    return True


def pdf_to_text(pdf: Path, out: Path) -> bool:
    """Text of a PDF via poppler's pdftotext, cached beside it.

    Quotes in an opinion are verified against this text file, and retrieval
    reads it, so both see exactly the same characters. False when pdftotext is
    missing or the PDF has no text layer (a scan).
    """
    if out.exists() and out.stat().st_mtime >= pdf.stat().st_mtime and out.stat().st_size:
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), str(out)],
                           capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0 or not out.exists():
        return False
    if len(out.read_text(encoding="utf-8", errors="replace").split()) < 50:
        out.unlink(missing_ok=True)
        return False
    return True


def artifact_files(path: Path, text_dir: Path) -> tuple[list[Path], Path] | None:
    """Files to search for a user-supplied artifact (file or directory).

    A PDF is searched through its text (written under ``text_dir``), never as
    bytes: before 2026-10-06 a ``--artifact KEY=paper.pdf`` was read with
    errors="replace", so retrieval ranked binary garbage. Returns (files, root).
    """
    if path.is_dir():
        files = []
        for q in sorted(x for x in path.rglob("*") if x.is_file()):
            if _is_pdf(q):
                txt = text_dir / (q.relative_to(path).as_posix().replace("/", "_") + ".txt")
                if pdf_to_text(q, txt):
                    files.append(txt)
            else:
                files.append(q)
        files = unique_files(files)
        return (files, path) if files else None
    if _is_pdf(path):
        txt = text_dir / (path.name + ".txt")
        return ([txt], text_dir) if pdf_to_text(path, txt) else None
    return [path], path.parent


def _is_pdf(p: Path) -> bool:
    try:
        return p.open("rb").read(4) == b"%PDF"
    except OSError:
        return False


def fetch_eprint(arxiv_id: str, cache_dir: Path | None = None,
                 tries: int = 2, timeout: int = 180) -> Artifact | None:
    """Fetch and unpack arXiv ``<id>v<n>``. Returns None if nothing usable.

    The id must be versioned: artifacts are immutable per version, which is what
    makes the on-disk cache safe to share across papers.
    """
    if not re.search(r"v\d+$", arxiv_id):
        raise ValueError(f"refusing to fetch unversioned arXiv id {arxiv_id!r}")
    cache_dir = Path(cache_dir or default_cache_dir())
    slot = cache_dir / arxiv_id.replace("/", "_")
    slot.mkdir(parents=True, exist_ok=True)
    blob = slot / "eprint.bin"

    kind = sniff(blob) if blob.exists() else None
    for _ in range(tries):
        if kind:
            break
        blob.unlink(missing_ok=True)
        # SourceRefused propagates: "arXiv did not answer" is not "no e-print".
        if _download(EPRINT_URL.format(id=arxiv_id), blob, timeout):
            kind = sniff(blob)
        else:
            return None
    if not kind:
        return None

    src = slot / "src"
    stamp = slot / ".unpacked_from_size"
    size = str(blob.stat().st_size)
    # The unpacked tree lives and dies with the blob: a re-download must not
    # leave a half-extracted directory behind to be silently reused.
    if src.exists() and (not stamp.exists() or stamp.read_text().strip() != size):
        shutil.rmtree(src, ignore_errors=True)

    if kind == "pdf":
        # No LaTeX source on arXiv: search the PDF's text instead of giving up
        # (it used to end as WRONG_ARTIFACT_KIND).
        txt = slot / "text" / "eprint.txt"
        if not pdf_to_text(blob, txt):
            return Artifact(KIND_PDF, slot, [], blob)
        return Artifact(KIND_PDF, txt.parent, [txt], blob)

    if not src.exists():
        src.mkdir(parents=True, exist_ok=True)
        try:
            if kind == "tar":
                with tarfile.open(blob, "r:gz") as tf:
                    _safe_extract(tf, src)
            else:
                with gzip.open(blob, "rb") as fh:
                    (src / "main.tex").write_bytes(fh.read(MAX_BYTES))
        except Exception:
            shutil.rmtree(src, ignore_errors=True)
            return None
        stamp.write_text(size)

    tex = unique_files([p for p in src.rglob("*.tex") if p.is_file()])
    if not tex:
        return None
    return Artifact(KIND_TEX, src, tex, blob)


def unique_files(paths: list[Path]) -> list[Path]:
    """Drop byte-identical copies, keeping the first in sorted order.

    Some e-prints ship the paper twice (ReAct 2210.03629v3 has src/iclr2023/ and
    src/iclr2023 2_arXiv/): scored twice, a rare term's df doubles and the top
    five fill with the same passage. Found by the IntegriRef session, 2026-10-06.
    """
    import hashlib
    seen: set[str] = set()
    out = []
    for p in sorted(paths):
        try:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        if h not in seen:
            seen.add(h)
            out.append(p)
    return out


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract without letting a member escape dest (absolute paths, ../)."""
    dest = dest.resolve()
    for m in tf.getmembers():
        if not (m.isfile() or m.isdir()):
            continue
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            continue
        tf.extract(m, dest, filter="data")


# --- ACL Anthology (non-arXiv NLP papers) -------------------------------------

KIND_ACL = "acl_anthology_pdf"
_ACL_DOI = re.compile(r"^10\.18653/v1/(.+)$", re.IGNORECASE)


def acl_id(doi: str | None) -> str | None:
    m = _ACL_DOI.match((doi or "").strip())
    return m.group(1) if m else None


def fetch_acl(anthology_id: str, cache_dir: Path | None = None,
              timeout: int = 120) -> Artifact | None:
    """PDF from aclanthology.org for an ACL DOI, searched as text.

    Many EMNLP/ACL papers are cited by venue and never posted to arXiv; the
    Anthology serves every one of them at a stable URL. Raises SourceRefused
    when the Anthology does not answer; None when it has no such paper.
    """
    cache_dir = Path(cache_dir or default_cache_dir())
    slot = cache_dir / ("acl_" + anthology_id.replace("/", "_"))
    slot.mkdir(parents=True, exist_ok=True)
    blob = slot / "paper.pdf"
    if not (blob.exists() and sniff(blob) == "pdf"):
        body = http_get(f"https://aclanthology.org/{anthology_id}.pdf", "aclanthology",
                        timeout, max_bytes=MAX_BYTES)
        if body is None or body[:4] != b"%PDF":
            return None
        blob.write_bytes(body)
    txt = slot / "text" / "paper.txt"
    if not pdf_to_text(blob, txt):
        return Artifact(KIND_ACL, slot, [], blob)
    return Artifact(KIND_ACL, txt.parent, [txt], blob)


KIND_OPENREVIEW = "openreview_pdf"


class NeedsBrowser(RuntimeError):
    """The source has the paper but serves it only to a browser that passes a
    challenge. deepcite does not work around that; the user downloads the file."""

    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


def fetch_openreview(note_id: str, pdf_path: str, cache_dir: Path | None = None,
                     timeout: int = 120) -> Artifact | None:
    """The PDF an OpenReview note links, searched as text. Raises SourceRefused."""
    cache_dir = Path(cache_dir or default_cache_dir())
    slot = cache_dir / ("or_" + re.sub(r"[^A-Za-z0-9_-]", "_", note_id))
    slot.mkdir(parents=True, exist_ok=True)
    blob = slot / "paper.pdf"
    if not (blob.exists() and sniff(blob) == "pdf"):
        try:
            body = http_get(f"https://openreview.net{pdf_path}", "openreview", timeout,
                            max_bytes=MAX_BYTES)
        except SourceRefused as e:
            # Since 2026-10 OpenReview answers every non-browser PDF request with 403
            # "Challenge verification required"; its search API still answers. That is
            # not a rate limit, and re-running will not help.
            if e.reason.startswith("HTTP 403"):
                raise NeedsBrowser(f"https://openreview.net/forum?id={note_id}")
            raise
        if body is None or body[:4] != b"%PDF":
            return None
        blob.write_bytes(body)
    txt = slot / "text" / "paper.txt"
    if not pdf_to_text(blob, txt):
        return Artifact(KIND_OPENREVIEW, slot, [], blob)
    return Artifact(KIND_OPENREVIEW, txt.parent, [txt], blob)


def local_files(artifact: dict, cited_id: str | None,
                cache_dir: Path | None = None) -> tuple[list[Path], Path] | None:
    """The files a record's passages and quotes refer to, found again on disk.

    One function for annotate, packet and report, so a quote is always checked
    against the same text retrieval searched.
    """
    cache_dir = Path(cache_dir or default_cache_dir())
    kind = (artifact or {}).get("kind")
    ref = (artifact or {}).get("ref") or ""
    if kind in ("data_file", "url"):
        p = Path(ref).expanduser()
        if not p.exists():
            p = cache_dir / "manual" / (ref.replace("/", "_")[-80:] or "artifact")
        if not p.exists():
            return None
        return artifact_files(p, cache_dir / "manual_text")
    if kind == KIND_ACL:
        aid = acl_id((cited_id or "").removeprefix("doi:"))
        root = cache_dir / ("acl_" + (aid or "").replace("/", "_")) / "text"
        files = sorted(root.glob("*.txt")) if root.is_dir() else []
        return (files, root) if files else None
    if kind == KIND_OPENREVIEW:
        nid = (cited_id or "").removeprefix("openreview:")
        root = cache_dir / ("or_" + re.sub(r"[^A-Za-z0-9_-]", "_", nid)) / "text"
        files = sorted(root.glob("*.txt")) if root.is_dir() else []
        return (files, root) if files else None
    aid = (cited_id or "").removeprefix("arXiv:")
    slot = cache_dir / aid.replace("/", "_")
    if kind == KIND_PDF:
        root = slot / "text"
        files = sorted(root.glob("*.txt")) if root.is_dir() else []
    else:
        root = slot / "src"
        files = unique_files([p for p in root.rglob("*.tex") if p.is_file()]) if root.is_dir() else []
    return (files, root) if files else None
