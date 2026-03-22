"""Dataset loaders for IntegriRef benchmarks.

Supported datasets:
  - SciFact: Scientific claim verification (claims ↔ abstracts)
  - SciCite: Citation intent classification (background/method/result)
  - FEVER: Large-scale fact verification (Wikipedia evidence)
  - Synthetic: Generated negative samples for L0 threshold tuning

Each loader returns standardized records that can be consumed by
the benchmark runners (bench_l0.py, bench_l1.py, bench_l2.py).
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Data directory ────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"


# ── Common record types ──────────────────────────────────────────────────

@dataclass
class L0Sample:
    """A single L0 (existence verification) test case."""
    ref_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    doi: str = ""
    venue: str = ""
    expected_found: bool = True  # True = should be found, False = fake
    source: str = ""             # dataset origin


@dataclass
class L1Sample:
    """A single L1 (citation intent) test case."""
    sample_id: str
    citing_sentence: str
    cited_title: str = ""
    expected_intent: str = ""    # supporting / contrasting / mentioning
    source: str = ""


@dataclass
class L2Sample:
    """A single L2 (semantic claim verification) test case."""
    claim_id: str
    claim: str
    evidence: list[str] = field(default_factory=list)
    expected_label: str = ""     # SUPPORTS / REFUTES / NOT_ENOUGH_INFO
    cited_doc_ids: list[int] = field(default_factory=list)
    source: str = ""


# ── SciFact loader ────────────────────────────────────────────────────────

def load_scifact(data_dir: Optional[str] = None) -> dict:
    """Load SciFact dataset (Allen AI).

    Expected directory structure:
      {data_dir}/scifact/
        claims_train.jsonl
        claims_dev.jsonl  (or claims_test.jsonl)
        corpus.jsonl

    Download from: https://github.com/allenai/scifact
    or: https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz

    Returns dict with keys: 'claims', 'corpus', 'samples_l2'
    """
    base = Path(data_dir) if data_dir else DATA_DIR / "scifact"

    result = {"claims": [], "corpus": {}, "samples_l2": []}

    # Load corpus (abstracts)
    corpus_path = base / "corpus.jsonl"
    if corpus_path.exists():
        with open(corpus_path) as f:
            for line in f:
                doc = json.loads(line)
                doc_id = doc.get("doc_id", doc.get("id"))
                result["corpus"][doc_id] = {
                    "title": doc.get("title", ""),
                    "abstract": doc.get("abstract", []),
                    "structured": doc.get("structured", False),
                }

    # Load claims
    for split in ["claims_train.jsonl", "claims_dev.jsonl", "claims_test.jsonl"]:
        claim_path = base / split
        if not claim_path.exists():
            continue
        with open(claim_path) as f:
            for line in f:
                claim = json.loads(line)
                result["claims"].append(claim)

                # Convert to L2Sample
                evidence_texts = []
                cited_ids = []
                label = "NOT_ENOUGH_INFO"

                evidence = claim.get("evidence", {})
                if evidence:
                    for doc_id_str, rationales in evidence.items():
                        doc_id = int(doc_id_str)
                        cited_ids.append(doc_id)
                        # Get abstract sentences as evidence
                        doc = result["corpus"].get(doc_id, {})
                        abstract = doc.get("abstract", [])
                        for rat in rationales:
                            sent_ids = rat.get("sentences", [])
                            for sid in sent_ids:
                                if sid < len(abstract):
                                    evidence_texts.append(abstract[sid])
                            # Use first rationale's label
                            if rat.get("label"):
                                label = rat["label"]

                result["samples_l2"].append(L2Sample(
                    claim_id=str(claim.get("id", "")),
                    claim=claim.get("claim", ""),
                    evidence=evidence_texts,
                    expected_label=label,
                    cited_doc_ids=cited_ids,
                    source="scifact",
                ))

    return result


# ── SciCite loader ────────────────────────────────────────────────────────

# SciCite label mapping to our 3-class taxonomy
_SCICITE_LABEL_MAP = {
    "background": "mentioning",
    "method": "supporting",       # Using a method is functionally supporting
    "result": "supporting",       # Comparing results is supporting/contrasting
    "result comparison": "supporting",
    "motivation": "mentioning",
    "extension": "supporting",
}


def load_scicite(data_dir: Optional[str] = None) -> list[L1Sample]:
    """Load SciCite dataset (Allen AI).

    Expected directory structure:
      {data_dir}/scicite/
        train.jsonl
        dev.jsonl
        test.jsonl

    Download from: https://github.com/allenai/scicite
    or via allennlp: allennlp train ...

    Each line: {"string": "citing sentence...", "label": "background|method|result",
                "citeStart": N, "citeEnd": N, "sectionName": "..."}

    Returns list of L1Sample.
    """
    base = Path(data_dir) if data_dir else DATA_DIR / "scicite"
    samples = []

    for split in ["train.jsonl", "dev.jsonl", "test.jsonl"]:
        fpath = base / split
        if not fpath.exists():
            continue
        with open(fpath) as f:
            for i, line in enumerate(f):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                raw_label = rec.get("label", "").lower().strip()
                mapped = _SCICITE_LABEL_MAP.get(raw_label, "mentioning")

                samples.append(L1Sample(
                    sample_id=f"scicite_{split.split('.')[0]}_{i}",
                    citing_sentence=rec.get("string", ""),
                    cited_title=rec.get("citedPaperTitle", ""),
                    expected_intent=mapped,
                    source="scicite",
                ))

    return samples


# ── FEVER loader ──────────────────────────────────────────────────────────

# FEVER label mapping
_FEVER_LABEL_MAP = {
    "SUPPORTS": "SUPPORTS",
    "REFUTES": "REFUTES",
    "NOT ENOUGH INFO": "NOT_ENOUGH_INFO",
}


def load_fever(data_dir: Optional[str] = None,
               max_samples: int = 0) -> list[L2Sample]:
    """Load FEVER dataset.

    Expected directory structure:
      {data_dir}/fever/
        train.jsonl  (or paper_dev.jsonl, paper_test.jsonl)

    Download from: https://fever.ai/resources.html

    Each line: {"id": N, "label": "SUPPORTS|REFUTES|NOT ENOUGH INFO",
                "claim": "...", "evidence": [...]}

    Returns list of L2Sample.
    """
    base = Path(data_dir) if data_dir else DATA_DIR / "fever"
    samples = []

    for split in ["paper_dev.jsonl", "paper_test.jsonl",
                   "train.jsonl", "shared_task_dev.jsonl"]:
        fpath = base / split
        if not fpath.exists():
            continue
        with open(fpath) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                label = _FEVER_LABEL_MAP.get(
                    rec.get("label", ""), "NOT_ENOUGH_INFO")

                # FEVER evidence is [[annotation_id, evidence_id, wiki_title, sent_id], ...]
                evidence_items = rec.get("evidence", [])
                evidence_texts = []
                for ev_set in evidence_items:
                    for ev in ev_set:
                        if len(ev) >= 4 and ev[2]:
                            evidence_texts.append(
                                f"{ev[2]} (sentence {ev[3]})")

                samples.append(L2Sample(
                    claim_id=str(rec.get("id", "")),
                    claim=rec.get("claim", ""),
                    evidence=evidence_texts,
                    expected_label=label,
                    source="fever",
                ))

                if max_samples and len(samples) >= max_samples:
                    return samples

    return samples


# ── Synthetic negative sample generator ──────────────────────────────────

def _perturb_title(title: str) -> str:
    """Generate a plausible but fake title by word swapping/insertion."""
    words = title.split()
    if len(words) < 3:
        return title + " revisited"

    op = random.choice(["swap", "insert", "drop", "replace"])
    if op == "swap" and len(words) >= 4:
        i, j = random.sample(range(1, len(words) - 1), 2)
        words[i], words[j] = words[j], words[i]
    elif op == "insert":
        filler = random.choice([
            "novel", "improved", "scalable", "robust", "efficient",
            "adaptive", "generalized", "unified", "optimal"])
        pos = random.randint(1, len(words) - 1)
        words.insert(pos, filler)
    elif op == "drop" and len(words) >= 4:
        words.pop(random.randint(1, len(words) - 2))
    elif op == "replace":
        replacements = {
            "learning": "inference", "network": "architecture",
            "model": "framework", "analysis": "synthesis",
            "detection": "recognition", "classification": "segmentation",
            "method": "approach", "algorithm": "technique",
        }
        for i, w in enumerate(words):
            wl = w.lower()
            if wl in replacements:
                words[i] = replacements[wl]
                break

    return " ".join(words)


def _shift_year(year: str) -> str:
    """Shift year by 2-5 to create a mismatch."""
    try:
        y = int(year)
        shift = random.choice([-5, -4, -3, -2, 2, 3, 4, 5])
        return str(y + shift)
    except (ValueError, TypeError):
        return "2099"


def _swap_author(authors: list[str]) -> list[str]:
    """Replace first author with a plausible fake."""
    fake_surnames = [
        "Zhang", "Smith", "Johnson", "Kim", "Lee", "Wang", "Chen",
        "Garcia", "Kumar", "Müller", "Nakamura", "Petrov", "Silva",
    ]
    fake_firstnames = [
        "A.", "B.", "C.", "J.", "K.", "M.", "S.", "X.", "Y.", "Z.",
    ]
    if authors:
        result = list(authors)
        result[0] = f"{random.choice(fake_firstnames)} {random.choice(fake_surnames)}"
        return result
    return [f"{random.choice(fake_firstnames)} {random.choice(fake_surnames)}"]


def generate_synthetic_negatives(
    positive_samples: list[L0Sample],
    n_negatives: int = 0,
    seed: int = 42,
) -> list[L0Sample]:
    """Generate synthetic negative L0 samples from real positives.

    Perturbation types:
      - Title perturbation (word swap/insert/drop/replace)
      - Year shift (±2-5 years)
      - Author swap (replace first author)
      - Combined (title + year or title + author)

    Args:
        positive_samples: Real reference samples to perturb.
        n_negatives: Number of negatives to generate. 0 = same count as positives.
        seed: Random seed for reproducibility.

    Returns list of L0Sample with expected_found=False.
    """
    rng = random.Random(seed)
    random.seed(seed)

    if n_negatives <= 0:
        n_negatives = len(positive_samples)

    negatives = []
    for i in range(n_negatives):
        src = rng.choice(positive_samples)
        perturbation = rng.choice([
            "title", "year", "author", "title+year", "title+author",
            "chimera",
        ])

        title = src.title
        authors = list(src.authors)
        year = src.year
        doi = ""  # Negatives never have valid DOIs

        if perturbation in ("title", "title+year", "title+author"):
            title = _perturb_title(title)
        if perturbation in ("year", "title+year"):
            year = _shift_year(year)
        if perturbation in ("author", "title+author"):
            authors = _swap_author(authors)
        if perturbation == "chimera":
            # Mix metadata from two different papers
            other = rng.choice(positive_samples)
            title = src.title
            authors = list(other.authors) if other.authors else authors
            year = other.year if other.year != src.year else _shift_year(year)

        negatives.append(L0Sample(
            ref_id=f"syn_neg_{i}",
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            venue=src.venue,
            expected_found=False,
            source=f"synthetic_{perturbation}",
        ))

    return negatives


# ── OpenAlex snapshot sampler ─────────────────────────────────────────────

def load_openalex_sample(
    snapshot_path: Optional[str] = None,
    n_samples: int = 500,
    seed: int = 42,
) -> list[L0Sample]:
    """Load a random sample from OpenAlex snapshot for L0 benchmarking.

    Expected: JSONL file with OpenAlex work records.
    Each line: {"id": "...", "title": "...", "authorships": [...],
                "publication_year": N, "doi": "...", ...}

    These are known-good records → expected_found=True.

    Download from: https://docs.openalex.org/download-all-data/openalex-snapshot
    """
    if not snapshot_path:
        snapshot_path = str(DATA_DIR / "openalex" / "works_sample.jsonl")

    if not Path(snapshot_path).exists():
        return []

    all_records = []
    with open(snapshot_path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            all_records.append(rec)

    rng = random.Random(seed)
    if len(all_records) > n_samples:
        all_records = rng.sample(all_records, n_samples)

    samples = []
    for rec in all_records:
        authors = []
        for auth in rec.get("authorships", []):
            name = auth.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)

        doi = rec.get("doi", "") or ""
        if doi.startswith("https://doi.org/"):
            doi = doi[16:]

        samples.append(L0Sample(
            ref_id=rec.get("id", ""),
            title=rec.get("title", "") or "",
            authors=authors,
            year=str(rec.get("publication_year", "")),
            doi=doi,
            venue=rec.get("primary_location", {}).get(
                "source", {}).get("display_name", "") if rec.get("primary_location") else "",
            expected_found=True,
            source="openalex",
        ))

    return samples


# ── Utility: dataset info ────────────────────────────────────────────────

def available_datasets() -> dict[str, dict]:
    """Return info about which datasets are available locally."""
    info = {}

    for name, subdir, files in [
        ("scifact", "scifact", ["corpus.jsonl", "claims_train.jsonl"]),
        ("scicite", "scicite", ["train.jsonl", "dev.jsonl"]),
        ("fever", "fever", ["paper_dev.jsonl", "train.jsonl"]),
        ("openalex", "openalex", ["works_sample.jsonl"]),
    ]:
        base = DATA_DIR / subdir
        found = [f for f in files if (base / f).exists()]
        info[name] = {
            "path": str(base),
            "available": len(found) > 0,
            "files_found": found,
            "files_expected": files,
        }

    return info
