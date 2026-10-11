"""arXiv bulk source access over S3, so mining stops depending on the API.

Mining through the arXiv API does not scale and should not: the first real batch
spent 430 title lookups and 60 e-print fetches to produce 48 candidates, took 26
minutes, and earned this machine an HTTP 429. arXiv publishes every submission's
LaTeX source in a requester-pays S3 bucket precisely so that bulk consumers stop
hammering the public endpoints. Using it is both the sanctioned route and, by a
wide margin, the faster one.

    s3://arxiv/src/arXiv_src_manifest.xml   the index
    s3://arxiv/src/arXiv_src_YYMM_NNN.tar   ~500 MB each, grouped by submission
                                            month, NOT by paper id

Because tarballs are grouped by month, this is the right tool for "give me many
papers" and the wrong one for "give me this one paper". Single-citation checks in
`deepcite run` should keep using the e-print endpoint politely; only mining moves
here.

What it buys, beyond the absence of rate limits: a local corpus holds **both**
sides of a citation. The citing paper's .tex gives the claim, its .bib or .bbl
often carries the cited work's arXiv id outright, and the cited paper's own source
gives both its abstract and its body. The title-search round trip -- the step that
caused the 429 and that silently mislabelled throttling as "not on arXiv" --
disappears entirely.

Credentials come from boto3's default chain: an instance role, a named profile, or
the environment. None are read, written or logged here. On EC2 in us-east-1 the
transfer is same-region and therefore free, which is the reason to run the job
there rather than pulling terabytes to a laptop.

Usage:
    python -m deepcite.eval.arxiv_bulk plan  --since 2305 --until 2312 --budget-gb 8
    python -m deepcite.eval.arxiv_bulk fetch --since 2305 --until 2312 --budget-gb 8
    python -m deepcite.eval.arxiv_bulk index
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

BUCKET = "arxiv"
MANIFEST_KEY = "src/arXiv_src_manifest.xml"
# us-east-1 is where the bucket lives; asking for it elsewhere adds a cross-region
# hop that the requester pays for.
REGION = "us-east-1"

DEFAULT_ROOT = Path.home() / ".cache" / "integriref" / "arxiv_bulk"
# S3 egress to the internet, dollars per GB, for the plan's estimate only. Free
# when the caller runs inside us-east-1. Not authoritative -- AWS pricing changes
# and the real bill is AWS's, not this estimate's.
EGRESS_USD_PER_GB = 0.09
# Requester-pays also bills per request; a few hundred GETs is cents, so the
# estimate tracks bytes only and says so.


@dataclass(frozen=True)
class Tarball:
    filename: str          # e.g. "src/arXiv_src_2305_001.tar"
    size: int
    num_items: int
    yymm: str
    md5sum: str

    @property
    def gb(self) -> float:
        return self.size / 1_000_000_000


def _client():
    try:
        import boto3
    except ImportError:
        sys.exit("boto3 is required: pip install boto3")
    return boto3.client("s3", region_name=REGION)


def manifest(root: Path = DEFAULT_ROOT, refresh: bool = False) -> list[Tarball]:
    """Fetch and parse the source manifest, caching it locally.

    The manifest itself is a requester-pays GET, so it is cached: re-planning a
    download should not cost anything.
    """
    local = root / "arXiv_src_manifest.xml"
    if refresh or not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        obj = _client().get_object(Bucket=BUCKET, Key=MANIFEST_KEY,
                                   RequestPayer="requester")
        local.write_bytes(obj["Body"].read())
    tree = ET.fromstring(local.read_text(encoding="utf-8", errors="replace"))
    out: list[Tarball] = []
    for f in tree.iter("file"):
        def _t(tag: str, default: str = "") -> str:
            el = f.find(tag)
            return (el.text or default).strip() if el is not None else default
        try:
            out.append(Tarball(filename=_t("filename"), size=int(_t("size", "0")),
                               num_items=int(_t("num_items", "0")),
                               yymm=_t("yymm"), md5sum=_t("md5sum")))
        except ValueError:
            continue
    return out


def select(tars: list[Tarball], since: str = "", until: str = "",
           budget_gb: float = 8.0) -> list[Tarball]:
    """Pick tarballs inside a YYMM range, newest first, up to a size budget.

    Newest first because citation claims in recent papers cite papers that are
    also likely to be in the corpus, which is what makes both-sides-local work.
    """
    def in_range(t: Tarball) -> bool:
        return ((not since or t.yymm >= since) and (not until or t.yymm <= until))

    picked, total = [], 0.0
    for t in sorted((t for t in tars if in_range(t)),
                    key=lambda t: (t.yymm, t.filename), reverse=True):
        if total + t.gb > budget_gb:
            continue
        picked.append(t)
        total += t.gb
    return picked


def plan(since: str, until: str, budget_gb: float,
         root: Path = DEFAULT_ROOT) -> dict:
    """Dry run: what would be downloaded, how big, and the rough egress cost."""
    tars = manifest(root)
    picked = select(tars, since, until, budget_gb)
    gb = sum(t.gb for t in picked)
    return {
        "manifest_tarballs": len(tars),
        "selected": len(picked),
        "yymm_range": [picked[-1].yymm, picked[0].yymm] if picked else [],
        "total_gb": round(gb, 2),
        "papers": sum(t.num_items for t in picked),
        "egress_usd_estimate": round(gb * EGRESS_USD_PER_GB, 2),
        "egress_note": ("bytes only; requester-pays also bills per request (cents "
                        "at this scale), and transfer is free from EC2 in "
                        f"{REGION}"),
        "files": [t.filename for t in picked],
    }


def fetch(since: str, until: str, budget_gb: float, root: Path = DEFAULT_ROOT,
          verify: bool = True) -> list[Path]:
    """Download the selected tarballs, skipping any already present and intact."""
    picked = select(manifest(root), since, until, budget_gb)
    dest = root / "tars"
    dest.mkdir(parents=True, exist_ok=True)
    client = _client()
    got: list[Path] = []
    for t in picked:
        out = dest / Path(t.filename).name
        if out.exists() and (not verify or _md5(out) == t.md5sum):
            print(f"  have {out.name}")
            got.append(out)
            continue
        print(f"  get  {out.name}  ({t.gb:.2f} GB, {t.num_items} papers)")
        client.download_file(BUCKET, t.filename, str(out),
                             ExtraArgs={"RequestPayer": "requester"})
        if verify and _md5(out) != t.md5sum:
            # A silently truncated tarball would become "paper not in corpus",
            # which is the same class of lie as a 429 read as "not on arXiv".
            out.unlink(missing_ok=True)
            print(f"  BAD  {out.name}: md5 mismatch, removed", file=sys.stderr)
            continue
        got.append(out)
    return got


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


_ID_IN_MEMBER = re.compile(r"(\d{4}\.\d{4,5})|([a-z-]+(?:\.[A-Z]{2})?\d{7})")


def unpack_submission(blob: Path, dest: Path) -> str | None:
    """Unpack one paper's submission package, the way fetch_eprint does.

    Each member of an arXiv_src tarball is itself a submission archive -- usually
    a gzipped tar, sometimes a single gzipped .tex, occasionally a PDF-only
    deposit. Extracting the outer tar alone leaves those packages unopened and the
    corpus unreadable, so the same content sniffing used for e-prints is applied
    here. Returns the kind, or None when nothing usable came out.
    """
    from .. import fetch as F

    kind = F.sniff(blob)
    if kind is None:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "tar":
            with tarfile.open(blob, "r:gz") as tf:
                F._safe_extract(tf, dest)
        elif kind == "gz":
            import gzip
            with gzip.open(blob, "rb") as fh:
                (dest / "main.tex").write_bytes(fh.read(F.MAX_BYTES))
        else:                       # pdf: kept so the record says why, not mined
            blob.replace(dest / "paper.pdf")
    except Exception:
        return None
    return kind


def build_index(root: Path = DEFAULT_ROOT) -> dict[str, str]:
    """Extract every tarball and map arXiv id -> extracted directory.

    arXiv ids are taken from member paths rather than parsed out of the sources,
    because the archive layout is authoritative and the .tex is not.
    """
    tars = sorted((root / "tars").glob("*.tar"))
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}
    skipped = 0
    for tar in tars:
        print(f"  index {tar.name}")
        with tarfile.open(tar) as tf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                hit = _ID_IN_MEMBER.search(Path(m.name).stem)
                if not hit:
                    continue
                aid = hit.group(0)
                out = src / aid
                if out.exists():
                    index[aid] = str(out)
                    continue
                data = tf.extractfile(m)
                if data is None:
                    continue
                blob = src / f".{aid}.blob"
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_bytes(data.read())
                kind = unpack_submission(blob, out)
                blob.unlink(missing_ok=True)
                if kind is None:
                    skipped += 1
                    continue
                index[aid] = str(out)
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    print(f"  {len(index)} papers indexed, {skipped} unusable "
          f"-> {root / 'index.json'}")
    return index


def load_index(root: Path = DEFAULT_ROOT) -> dict[str, str]:
    try:
        return json.loads((root / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m deepcite.eval.arxiv_bulk",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("plan", "fetch", "index"))
    ap.add_argument("--since", default="", help="earliest YYMM, e.g. 2305")
    ap.add_argument("--until", default="", help="latest YYMM")
    ap.add_argument("--budget-gb", type=float, default=8.0,
                    help="cap on bytes downloaded; the estimate is in `plan`")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--no-verify", action="store_true",
                    help="skip md5 verification (not advised: a truncated tarball "
                         "reads downstream as 'paper not in corpus')")
    a = ap.parse_args(argv)
    root = Path(a.root).expanduser()

    if a.action == "plan":
        print(json.dumps(plan(a.since, a.until, a.budget_gb, root), indent=1))
    elif a.action == "fetch":
        got = fetch(a.since, a.until, a.budget_gb, root, verify=not a.no_verify)
        print(f"  {len(got)} tarball(s) in {root / 'tars'}")
    else:
        build_index(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
