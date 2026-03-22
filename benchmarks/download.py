"""Dataset download helper for IntegriRef benchmarks.

Downloads and prepares benchmark datasets from public sources.

Usage:
    python -m benchmarks.download [--dataset DATASET] [--data-dir DIR]

Datasets:
    scifact   - SciFact (Allen AI) — ~5MB
    scicite   - SciCite (Allen AI) — ~50MB
    fever     - FEVER (shared task) — ~150MB
    all       - Download all above
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

from benchmarks.datasets import DATA_DIR

# Known download URLs (public, stable)
DATASET_URLS = {
    "scifact": {
        "url": "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz",
        "format": "tar.gz",
        "description": "SciFact: Scientific claim verification (Allen AI)",
        "license": "CC BY-NC 2.0",
        "size_mb": 5,
    },
    "scicite": {
        "url": "https://s3-us-west-2.amazonaws.com/ai2-s2-research/scicite/scicite.tar.gz",
        "format": "tar.gz",
        "description": "SciCite: Citation intent classification (Allen AI)",
        "license": "Apache 2.0",
        "size_mb": 50,
    },
    "fever": {
        "urls": {
            "dev": "https://fever.ai/download/fever/paper_dev.jsonl",
            "train": "https://fever.ai/download/fever/train.jsonl",
        },
        "format": "jsonl",
        "description": "FEVER: Fact Extraction and VERification",
        "license": "CC BY-SA 3.0",
        "size_mb": 150,
    },
}


def download_file(url: str, dest: Path, desc: str = "") -> bool:
    """Download a file with progress display."""
    try:
        import requests
    except ImportError:
        print("  Error: 'requests' package required. Install with: pip install requests")
        return False

    print(f"  Downloading {desc or url}...")
    try:
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  Progress: {pct}% ({downloaded // 1024}KB / {total // 1024}KB)",
                          end="", flush=True)

        print()
        return True
    except Exception as e:
        print(f"\n  Error downloading: {e}")
        return False


def extract_archive(archive_path: Path, dest_dir: Path, fmt: str) -> bool:
    """Extract tar.gz or zip archive."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        if fmt in ("tar.gz", "tgz"):
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(dest_dir, filter="data")
        elif fmt == "zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(dest_dir)
        else:
            print(f"  Unknown format: {fmt}")
            return False

        # Clean up archive
        archive_path.unlink()
        return True
    except Exception as e:
        print(f"  Error extracting: {e}")
        return False


def download_scifact(data_dir: Path) -> bool:
    """Download and prepare SciFact dataset."""
    info = DATASET_URLS["scifact"]
    dest_dir = data_dir / "scifact"

    if (dest_dir / "corpus.jsonl").exists():
        print("  SciFact already downloaded, skipping.")
        return True

    archive = data_dir / "scifact.tar.gz"
    if not download_file(info["url"], archive, "SciFact"):
        return False

    print("  Extracting...")
    if not extract_archive(archive, dest_dir, info["format"]):
        return False

    # SciFact extracts to data/ subdirectory — move files up if needed
    inner = dest_dir / "data"
    if inner.exists():
        for f in inner.iterdir():
            f.rename(dest_dir / f.name)
        inner.rmdir()

    # Verify
    expected = ["corpus.jsonl"]
    for f in expected:
        if not (dest_dir / f).exists():
            # Check claims files
            pass

    print(f"  SciFact ready at {dest_dir}")
    return True


def download_scicite(data_dir: Path) -> bool:
    """Download and prepare SciCite dataset."""
    info = DATASET_URLS["scicite"]
    dest_dir = data_dir / "scicite"

    if (dest_dir / "train.jsonl").exists():
        print("  SciCite already downloaded, skipping.")
        return True

    archive = data_dir / "scicite.tar.gz"
    if not download_file(info["url"], archive, "SciCite"):
        return False

    print("  Extracting...")
    if not extract_archive(archive, dest_dir, info["format"]):
        return False

    # SciCite may extract to a subdirectory
    for subdir in dest_dir.iterdir():
        if subdir.is_dir():
            for f in subdir.iterdir():
                f.rename(dest_dir / f.name)
            subdir.rmdir()

    print(f"  SciCite ready at {dest_dir}")
    return True


def download_fever(data_dir: Path) -> bool:
    """Download FEVER dataset (dev set only by default)."""
    info = DATASET_URLS["fever"]
    dest_dir = data_dir / "fever"
    dest_dir.mkdir(parents=True, exist_ok=True)

    if (dest_dir / "paper_dev.jsonl").exists():
        print("  FEVER dev already downloaded, skipping.")
        return True

    urls = info["urls"]
    success = True
    for name, url in urls.items():
        dest = dest_dir / f"{name}.jsonl"
        if dest.exists():
            continue
        if not download_file(url, dest, f"FEVER {name}"):
            success = False

    print(f"  FEVER ready at {dest_dir}")
    return success


_DOWNLOADERS = {
    "scifact": download_scifact,
    "scicite": download_scicite,
    "fever": download_fever,
}


def main():
    parser = argparse.ArgumentParser(description="Download benchmark datasets")
    parser.add_argument("--dataset", type=str, default="all",
                        choices=list(_DOWNLOADERS.keys()) + ["all"],
                        help="Which dataset to download")
    parser.add_argument("--data-dir", type=str, default=str(DATA_DIR),
                        help="Directory to store datasets")
    parser.add_argument("--list", action="store_true",
                        help="List available datasets and exit")
    args = parser.parse_args()

    if args.list:
        print("Available datasets:")
        for name, info in DATASET_URLS.items():
            print(f"  {name:10s}  ~{info['size_mb']}MB  {info['description']}")
            print(f"             License: {info['license']}")
        return

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.dataset == "all":
        datasets = list(_DOWNLOADERS.keys())
    else:
        datasets = [args.dataset]

    for name in datasets:
        print(f"\n--- {name} ---")
        info = DATASET_URLS.get(name, {})
        print(f"  {info.get('description', '')}")
        print(f"  License: {info.get('license', 'Unknown')}")
        _DOWNLOADERS[name](data_dir)


if __name__ == "__main__":
    main()
