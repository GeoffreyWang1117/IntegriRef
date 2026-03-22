"""
benchmarks/export_hf_dataset.py
================================
Export IntegriRef benchmark data to HuggingFace-compatible format.

Reads the four primary JSON/JSONL source files in benchmarks/data/ and writes
one file per split to benchmarks/data/hf_export/ (default).  Supports JSONL
output and, when the `datasets` library is available, Arrow/Parquet output.

Usage
-----
    python -m benchmarks.export_hf_dataset
    python -m benchmarks.export_hf_dataset --output-dir benchmarks/data/hf_export
    python -m benchmarks.export_hf_dataset --format parquet
    python -m benchmarks.export_hf_dataset --format both

Splits produced
---------------
    reference_verification  — golden test set (retracted, real, hallucinated, chimera)
    signal_unit_tests       — per-signal unit test cases
    l1_intent               — L1 citing sentence × cited abstract pairs
    l2_nli                  — L2 claim × source-text NLI pairs
    graph_anomaly           — L3 citation-graph anomaly cases
    retracted_papers        — fetched retraction data from Crossref / PubMed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_THIS_DIR = Path(__file__).parent          # benchmarks/
_DATA_DIR = _THIS_DIR / "data"
_DEFAULT_OUT = _DATA_DIR / "hf_export"

# Source files
_GOLDEN = _DATA_DIR / "golden_test_set.json"
_SIGNALS = _DATA_DIR / "signal_test_cases.json"
_L1L2 = _DATA_DIR / "l1_l2_test_pairs.json"
_GRAPH = _DATA_DIR / "graph_anomaly_cases.json"
_RETRACTION_WATCH_DIR = _DATA_DIR / "retraction_watch"

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _jdump(obj: Any) -> str:
    """Compact JSON serialisation for flattened string fields."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _str_list(lst: Any) -> str:
    """Normalise author lists / DOI lists to a JSON string."""
    if lst is None:
        return "[]"
    if isinstance(lst, list):
        return _jdump(lst)
    return str(lst)


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val)


def _safe_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return bool(val)


# ---------------------------------------------------------------------------
# Split builders
# ---------------------------------------------------------------------------


def build_reference_verification(data_dir: Path) -> list[dict]:
    """
    Flatten golden_test_set.json into rows for the reference_verification split.

    Each row represents a single bibliographic reference to verify.
    Categories: retracted, real, hallucinated, chimera.
    """
    with open(data_dir / "golden_test_set.json", encoding="utf-8") as fh:
        raw = json.load(fh)

    rows: list[dict] = []

    # --- retracted_papers ---------------------------------------------------
    # Note: the source array contains both actually retracted papers AND hard
    # negative controls (controversial-but-not-retracted papers stored in the
    # same array for completeness). Category is derived from expected_retracted.
    for item in raw.get("retracted_papers", []):
        is_retracted = _safe_bool(item.get("expected_retracted"), True)
        category = "retracted" if is_retracted else "real"
        rows.append(
            {
                "id": item["id"],
                "split": "reference_verification",
                "category": category,
                "signal": "retracted_citation" if is_retracted else "none",
                "title": _safe_str(item.get("title")),
                "authors": _str_list(item.get("authors")),
                "year": _safe_str(item.get("year")),
                "doi": _safe_str(item.get("doi")),
                "venue": _safe_str(item.get("venue")),
                "expected_retracted": _safe_bool(item.get("expected_retracted"), True),
                "expected_found": _safe_bool(item.get("expected_found"), True),
                "expected_risk": _safe_str(item.get("expected_risk", "HIGH")),
                "subcategory": _safe_str(item.get("category")),
                "note": _safe_str(item.get("note")),
            }
        )

    # --- real_papers --------------------------------------------------------
    for item in raw.get("real_papers", []):
        rows.append(
            {
                "id": item["id"],
                "split": "reference_verification",
                "category": "real",
                "signal": "none",
                "title": _safe_str(item.get("title")),
                "authors": _str_list(item.get("authors")),
                "year": _safe_str(item.get("year")),
                "doi": _safe_str(item.get("doi")),
                "venue": _safe_str(item.get("venue")),
                "expected_retracted": False,
                "expected_found": _safe_bool(item.get("expected_found"), True),
                "expected_risk": _safe_str(item.get("expected_risk", "LOW")),
                "subcategory": "legitimate_control",
                "note": _safe_str(item.get("note")),
            }
        )

    # --- llm_hallucinated ---------------------------------------------------
    for item in raw.get("llm_hallucinated", []):
        rows.append(
            {
                "id": item["id"],
                "split": "reference_verification",
                "category": "hallucinated",
                "signal": "phantom_doi",
                "title": _safe_str(item.get("title")),
                "authors": _str_list(item.get("authors")),
                "year": _safe_str(item.get("year")),
                "doi": _safe_str(item.get("doi")),
                "venue": _safe_str(item.get("venue")),
                "expected_retracted": False,
                "expected_found": _safe_bool(item.get("expected_found"), False),
                "expected_risk": _safe_str(item.get("expected_risk", "HIGH")),
                "subcategory": "llm_fabrication",
                "note": _safe_str(item.get("note")),
            }
        )

    # --- metadata_chimera ---------------------------------------------------
    for item in raw.get("metadata_chimera", []):
        rows.append(
            {
                "id": item["id"],
                "split": "reference_verification",
                "category": "chimera",
                "signal": "metadata_mismatch",
                "title": _safe_str(item.get("title")),
                "authors": _str_list(item.get("authors")),
                "year": _safe_str(item.get("year")),
                "doi": _safe_str(item.get("doi")),
                "venue": _safe_str(item.get("venue")),
                "expected_retracted": False,
                "expected_found": _safe_bool(item.get("expected_found"), False),
                "expected_risk": _safe_str(item.get("expected_risk", "HIGH")),
                "subcategory": "metadata_chimera",
                "note": _safe_str(item.get("note")),
            }
        )

    return rows


def build_signal_unit_tests(data_dir: Path) -> list[dict]:
    """
    Flatten signal_test_cases.json into rows for the signal_unit_tests split.

    Each row is a unit test for one Bayesian signal.
    """
    with open(data_dir / "signal_test_cases.json", encoding="utf-8") as fh:
        raw = json.load(fh)

    rows: list[dict] = []

    # Mapping from JSON key → (category label, signal name, input extractor fn)
    def _tortured(item: dict) -> tuple[str, str, dict]:
        return (
            "tortured_phrases",
            "tortured_phrases",
            {
                "input_text": item.get("text", ""),
                "extra": _jdump(
                    {
                        "tortured_phrase": item.get("tortured_phrase"),
                        "original_phrase": item.get("original_phrase"),
                    }
                ),
            },
        )

    def _email(item: dict) -> tuple[str, str, dict]:
        return (
            "suspicious_email",
            "suspicious_email",
            {
                "input_text": _jdump(item.get("authors", [])),
                "extra": "{}",
            },
        )

    def _grim(item: dict) -> tuple[str, str, dict]:
        return (
            "grim_test",
            "grim_test_failure",
            {
                "input_text": (
                    f"mean={item.get('reported_mean')} N={item.get('sample_size_n')} "
                    f"scale=[{item.get('scale_min')},{item.get('scale_max')}]"
                ),
                "extra": _jdump(
                    {
                        "reported_mean": item.get("reported_mean"),
                        "sample_size_n": item.get("sample_size_n"),
                        "scale_min": item.get("scale_min"),
                        "scale_max": item.get("scale_max"),
                        "scale_type": item.get("scale_type"),
                        "grim_check": item.get("grim_check"),
                    }
                ),
            },
        )

    def _statcheck(item: dict) -> tuple[str, str, dict]:
        return (
            "statcheck",
            "statcheck_error",
            {
                "input_text": _safe_str(item.get("stat_text")),
                "extra": _jdump(
                    {
                        "test_type": item.get("test_type"),
                        "df1": item.get("df1"),
                        "df2": item.get("df2"),
                        "test_statistic": item.get("test_statistic"),
                        "reported_p": item.get("reported_p"),
                        "computed_p_approx": item.get("computed_p_approx"),
                    }
                ),
            },
        )

    def _citation_ring(item: dict) -> tuple[str, str, dict]:
        return (
            "citation_ring",
            "citation_ring_detected",
            {
                "input_text": _safe_str(item.get("description", "")),
                "extra": _jdump(
                    {
                        "dois": item.get("dois", []),
                        "journal": item.get("journal"),
                    }
                ),
            },
        )

    def _self_cite(item: dict) -> tuple[str, str, dict]:
        return (
            "self_citation",
            "excessive_self_citation",
            {
                "input_text": (
                    f"doi={item.get('paper_doi')} "
                    f"self_rate={item.get('self_citation_rate')} "
                    f"n_self={item.get('self_citations')} "
                    f"n_total={item.get('total_citations')}"
                ),
                "extra": _jdump(
                    {
                        "paper_doi": item.get("paper_doi"),
                        "self_citation_rate": item.get("self_citation_rate"),
                        "self_citations": item.get("self_citations"),
                        "total_citations": item.get("total_citations"),
                    }
                ),
            },
        )

    def _temporal(item: dict) -> tuple[str, str, dict]:
        return (
            "temporal_anomaly",
            "temporal_anomaly",
            {
                "input_text": (
                    f"citing={item.get('citing_year')} cited={item.get('cited_year')}"
                ),
                "extra": _jdump(
                    {
                        "citing_doi": item.get("citing_paper", {}).get("doi"),
                        "cited_doi": item.get("cited_paper", {}).get("doi"),
                        "citing_year": item.get("citing_year"),
                        "cited_year": item.get("cited_year"),
                    }
                ),
            },
        )

    def _orphan(item: dict) -> tuple[str, str, dict]:
        return (
            "orphan_cluster",
            "orphan_cluster",
            {
                "input_text": (
                    f"cluster_size={item.get('cluster_size')} "
                    f"intra_rate={item.get('intra_cluster_citation_rate')}"
                ),
                "extra": _jdump(
                    {
                        "cluster_size": item.get("cluster_size"),
                        "total_references": item.get("total_references_in_cluster"),
                        "intra_citations": item.get("intra_cluster_citations"),
                        "external_citations": item.get("external_citations"),
                        "intra_rate": item.get("intra_cluster_citation_rate"),
                    }
                ),
            },
        )

    def _l1_intent(item: dict) -> tuple[str, str, dict]:
        return (
            "l1_intent",
            "citation_misrepresents_source",
            {
                "input_text": _safe_str(item.get("citing_sentence")),
                "extra": _jdump(
                    {
                        "doi": item.get("cited_paper", {}).get("doi"),
                        "expected_intent": item.get("expected_intent"),
                        "expected_misrepresents": item.get("expected_misrepresents"),
                    }
                ),
            },
        )

    def _l2_nli(item: dict) -> tuple[str, str, dict]:
        return (
            "l2_nli",
            "claim_contradicted" if item.get("expected_signal") == "claim_contradicted" else "claim_unsupported",
            {
                "input_text": _safe_str(item.get("claim")),
                "extra": _jdump(
                    {
                        "source_text": item.get("source_text"),
                        "expected_relation": item.get("expected_relation"),
                        "domain": item.get("domain"),
                    }
                ),
            },
        )

    def _ref_not_found(item: dict) -> tuple[str, str, dict]:
        return (
            "reference_not_found",
            "reference_not_found",
            {
                "input_text": _safe_str(item.get("title", "")),
                "extra": _jdump(
                    {
                        "doi": item.get("doi"),
                        "authors": item.get("authors"),
                        "year": item.get("year"),
                    }
                ),
            },
        )

    def _phantom_doi(item: dict) -> tuple[str, str, dict]:
        return (
            "phantom_doi",
            "phantom_doi",
            {
                "input_text": _safe_str(item.get("doi", "")),
                "extra": _jdump({"title": item.get("title")}),
            },
        )

    def _metadata_mismatch(item: dict) -> tuple[str, str, dict]:
        return (
            "metadata_mismatch",
            "metadata_mismatch",
            {
                "input_text": _safe_str(item.get("title", "")),
                "extra": _jdump(
                    {
                        "doi": item.get("doi"),
                        "submitted_authors": item.get("submitted_authors"),
                        "actual_authors": item.get("actual_authors"),
                    }
                ),
            },
        )

    def _retracted_cite(item: dict) -> tuple[str, str, dict]:
        return (
            "retracted_citation",
            "retracted_citation",
            {
                "input_text": _safe_str(item.get("title", "")),
                "extra": _jdump(
                    {
                        "doi": item.get("doi"),
                        "retraction_date": item.get("retraction_date"),
                    }
                ),
            },
        )

    _dispatch: dict[str, Any] = {
        "tortured_phrases_cases": _tortured,
        "suspicious_email_cases": _email,
        "grim_test_cases": _grim,
        "statcheck_cases": _statcheck,
        "citation_ring_cases": _citation_ring,
        "self_citation_cases": _self_cite,
        "temporal_anomaly_cases": _temporal,
        "orphan_cluster_cases": _orphan,
        "l1_intent_cases": _l1_intent,
        "l2_nli_cases": _l2_nli,
        "reference_not_found_cases": _ref_not_found,
        "phantom_doi_cases": _phantom_doi,
        "metadata_mismatch_cases": _metadata_mismatch,
        "retracted_citation_cases": _retracted_cite,
    }

    for key, fn in _dispatch.items():
        for item in raw.get(key, []):
            cat, sig, fields = fn(item)
            row = {
                "id": item.get("id", f"{key}_{len(rows)}"),
                "split": "signal_unit_tests",
                "category": cat,
                "signal": sig,
                "expected_fired": _safe_bool(item.get("expected_fired")),
                "note": _safe_str(item.get("note")),
            }
            row.update(fields)
            rows.append(row)

    return rows


def build_l1_intent(data_dir: Path) -> list[dict]:
    """
    Flatten l1_l2_test_pairs.json l1_citing_pairs into the l1_intent split.
    """
    with open(data_dir / "l1_l2_test_pairs.json", encoding="utf-8") as fh:
        raw = json.load(fh)

    rows: list[dict] = []
    for item in raw.get("l1_citing_pairs", []):
        cited = item.get("cited_paper", {})
        rows.append(
            {
                "id": item["id"],
                "split": "l1_intent",
                "category": _safe_str(item.get("category")),
                "signal": (
                    "citation_misrepresents_source"
                    if _safe_bool(item.get("expected_misrepresents"))
                    else "none"
                ),
                "citing_sentence": _safe_str(item.get("citing_sentence")),
                "citation_key": _safe_str(item.get("citation_key")),
                "cited_doi": _safe_str(cited.get("doi")),
                "cited_title": _safe_str(cited.get("title")),
                "cited_abstract": _safe_str(cited.get("abstract")),
                "expected_intent": _safe_str(item.get("expected_intent")),
                "expected_misrepresents": _safe_bool(
                    item.get("expected_misrepresents")
                ),
                "note": _safe_str(item.get("note")),
            }
        )
    return rows


def build_l2_nli(data_dir: Path) -> list[dict]:
    """
    Flatten l1_l2_test_pairs.json l2_claim_source_pairs into the l2_nli split.
    """
    with open(data_dir / "l1_l2_test_pairs.json", encoding="utf-8") as fh:
        raw = json.load(fh)

    rows: list[dict] = []
    for item in raw.get("l2_claim_source_pairs", []):
        rel = _safe_str(item.get("expected_relation"))
        sig = (
            "claim_contradicted"
            if rel == "contradiction"
            else ("claim_unsupported" if rel == "neutral" else "none")
        )
        rows.append(
            {
                "id": item["id"],
                "split": "l2_nli",
                "category": rel,
                "signal": sig,
                "claim": _safe_str(item.get("claim")),
                "source_text": _safe_str(item.get("source_text")),
                "expected_relation": rel,
                "domain": _safe_str(item.get("domain")),
                "note": _safe_str(item.get("note")),
            }
        )
    return rows


def build_graph_anomaly(data_dir: Path) -> list[dict]:
    """
    Flatten graph_anomaly_cases.json into the graph_anomaly split.

    Covers citation rings, excessive self-citation, temporal anomalies,
    and orphan clusters.
    """
    with open(data_dir / "graph_anomaly_cases.json", encoding="utf-8") as fh:
        raw = json.load(fh)

    rows: list[dict] = []

    # --- citation_rings -----------------------------------------------------
    for item in raw.get("citation_rings", []):
        papers = item.get("papers", [])
        dois = [p.get("doi", "") for p in papers]
        rows.append(
            {
                "id": item["id"],
                "split": "graph_anomaly",
                "category": "citation_ring",
                "signal": "citation_ring_detected",
                "description": _safe_str(item.get("description")),
                "dois": _jdump(dois),
                "expected_anomaly": _safe_bool(item.get("expected_ring")),
                "note": _safe_str(item.get("note")),
                "extra": _jdump(
                    {
                        "ring_size": item.get("ring_size"),
                        "citation_matrix": item.get("citation_matrix"),
                        "papers": papers,
                    }
                ),
            }
        )

    # --- excessive_self_citation --------------------------------------------
    for item in raw.get("excessive_self_citation", []):
        rows.append(
            {
                "id": item["id"],
                "split": "graph_anomaly",
                "category": "self_citation",
                "signal": "excessive_self_citation",
                "description": _safe_str(item.get("paper_title", "")),
                "dois": _jdump([item.get("paper_doi", "")]),
                "expected_anomaly": _safe_bool(item.get("expected_fired")),
                "note": _safe_str(item.get("note")),
                "extra": _jdump(
                    {
                        "self_citation_rate": item.get("self_citation_rate"),
                        "total_citations": item.get("total_citations"),
                        "self_citations": item.get("self_citations"),
                    }
                ),
            }
        )

    # --- temporal_anomalies -------------------------------------------------
    for item in raw.get("temporal_anomalies", []):
        citing = item.get("citing_paper", {})
        cited = item.get("cited_paper", {})
        rows.append(
            {
                "id": item["id"],
                "split": "graph_anomaly",
                "category": "temporal_anomaly",
                "signal": "temporal_anomaly",
                "description": (
                    f"citing {citing.get('year', '?')} cites "
                    f"{cited.get('year', '?')}: {cited.get('title', '')}"
                ),
                "dois": _jdump(
                    [citing.get("doi", ""), cited.get("doi", "")]
                ),
                "expected_anomaly": _safe_bool(item.get("expected_anomaly")),
                "note": _safe_str(item.get("note")),
                "extra": _jdump(
                    {
                        "citing_doi": citing.get("doi"),
                        "citing_year": citing.get("year"),
                        "citing_published": citing.get("published"),
                        "cited_doi": cited.get("doi"),
                        "cited_year": cited.get("year"),
                        "cited_published": cited.get("published"),
                        "time_gap_months": item.get("time_gap_months"),
                    }
                ),
            }
        )

    # --- orphan_clusters ----------------------------------------------------
    for item in raw.get("orphan_clusters", []):
        cluster_papers = item.get("cluster_papers", [])
        dois = [p.get("doi", "") for p in cluster_papers]
        rows.append(
            {
                "id": item["id"],
                "split": "graph_anomaly",
                "category": "orphan_cluster",
                "signal": "orphan_cluster",
                "description": _safe_str(item.get("description", "")),
                "dois": _jdump(dois),
                "expected_anomaly": _safe_bool(item.get("expected_orphan")),
                "note": _safe_str(item.get("note")),
                "extra": _jdump(
                    {
                        "internal_citations": item.get("internal_citations"),
                        "external_citations_from_cluster": item.get(
                            "external_citations_from_cluster"
                        ),
                        "external_citations_to_cluster": item.get(
                            "external_citations_to_cluster"
                        ),
                        "insularity_score": item.get("insularity_score"),
                        "cluster_size": len(cluster_papers),
                    }
                ),
            }
        )

    return rows


def build_retracted_papers(rw_dir: Path) -> list[dict]:
    """
    Read all JSONL files under retraction_watch/ into the retracted_papers split.

    Files with 'real' in their name are treated as clean controls.
    """
    rows: list[dict] = []
    seen_ids: set[str] = set()

    for jsonl_path in sorted(rw_dir.glob("*.jsonl")):
        source_name = jsonl_path.stem  # e.g. crossref_retracted
        is_real = "real" in source_name

        with open(jsonl_path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(
                        f"  [warn] {jsonl_path.name}:{lineno}: JSON parse error: {exc}",
                        file=sys.stderr,
                    )
                    continue

                # Derive a stable unique ID
                doi = _safe_str(item.get("doi", ""))
                pmid = _safe_str(item.get("pmid", ""))
                if doi:
                    raw_id = doi.replace("/", "_").replace(".", "-")
                elif pmid:
                    raw_id = f"pmid_{pmid}"
                else:
                    raw_id = f"{source_name}_{lineno}"

                row_id = f"rw_{source_name}_{raw_id}"
                # Truncate to avoid excessively long IDs
                row_id = row_id[:120]

                # Deduplicate within this split
                if row_id in seen_ids:
                    row_id = f"{row_id}_{lineno}"
                seen_ids.add(row_id)

                category = "real_control" if is_real else "retracted"
                rows.append(
                    {
                        "id": row_id,
                        "split": "retracted_papers",
                        "category": category,
                        "signal": "retracted_citation",
                        "source": source_name,
                        "doi": doi,
                        "pmid": pmid,
                        "title": _safe_str(item.get("title")),
                        "authors": _str_list(item.get("authors")),
                        "year": _safe_str(item.get("year")),
                        "venue": _safe_str(item.get("venue")),
                        "expected_retracted": _safe_bool(
                            item.get("expected_retracted"), not is_real
                        ),
                        "expected_risk": _safe_str(item.get("expected_risk", "HIGH" if not is_real else "LOW")),
                    }
                )

    return rows


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def write_jsonl(rows: list[dict], path: Path) -> None:
    """Write a list of row dicts as newline-delimited JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_parquet(rows: list[dict], path: Path) -> bool:
    """
    Write rows as a Parquet file using the HuggingFace datasets library.

    Returns True on success, False if the library is unavailable.
    """
    try:
        from datasets import Dataset  # type: ignore
    except ImportError:
        return False

    ds = Dataset.from_list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_parquet(str(path))
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SPLITS = {
    "reference_verification": build_reference_verification,
    "signal_unit_tests": build_signal_unit_tests,
    "l1_intent": build_l1_intent,
    "l2_nli": build_l2_nli,
    "graph_anomaly": build_graph_anomaly,
}


def build_crawled_retracted(crawled_dir: Path) -> list[dict]:
    """Build retracted_papers rows from crawled data (S-tier)."""
    rows: list[dict] = []
    seen_ids: set[str] = set()

    for jsonl_path in sorted(crawled_dir.glob("*.jsonl")):
        name = jsonl_path.stem
        # Only process retracted/real paper files
        if not (name.startswith("crossref_re") or name.startswith("pubmed_re")
                or name.startswith("crossref_real")):
            continue

        is_real = "real" in name
        with open(jsonl_path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue

                doi = _safe_str(item.get("doi", ""))
                pmid = _safe_str(item.get("pmid", ""))
                if doi:
                    raw_id = doi.replace("/", "_").replace(".", "-")
                elif pmid:
                    raw_id = f"pmid_{pmid}"
                else:
                    raw_id = f"{name}_{lineno}"

                row_id = f"cr_{name}_{raw_id}"[:120]
                if row_id in seen_ids:
                    row_id = f"{row_id}_{lineno}"
                seen_ids.add(row_id)

                category = "real_control" if is_real else "retracted"
                rows.append({
                    "id": row_id,
                    "split": "retracted_papers",
                    "category": category,
                    "signal": "retracted_citation",
                    "source": name,
                    "doi": doi,
                    "pmid": pmid,
                    "title": _safe_str(item.get("title")),
                    "authors": _str_list(item.get("authors")),
                    "year": _safe_str(item.get("year")),
                    "venue": _safe_str(item.get("venue")),
                    "expected_retracted": _safe_bool(
                        item.get("expected_retracted"), not is_real),
                    "expected_risk": _safe_str(
                        item.get("expected_risk", "HIGH" if not is_real else "LOW")),
                })

    return rows


def build_crawled_reference_verification(crawled_dir: Path) -> list[dict]:
    """Build reference_verification rows from crawled data (S-tier)."""
    rows: list[dict] = []

    for fname, category, signal in [
        ("real_papers.jsonl", "real", "none"),
        ("hallucinated_papers.jsonl", "hallucinated", "phantom_doi"),
        ("chimera_papers.jsonl", "chimera", "metadata_mismatch"),
    ]:
        fpath = crawled_dir / fname
        if not fpath.exists():
            continue
        with open(fpath, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.append({
                    "id": item.get("id", f"cr_ref_{len(rows)}"),
                    "split": "reference_verification",
                    "category": category,
                    "signal": signal,
                    "title": _safe_str(item.get("title")),
                    "authors": _str_list(item.get("authors")),
                    "year": _safe_str(item.get("year")),
                    "doi": _safe_str(item.get("doi")),
                    "venue": _safe_str(item.get("venue")),
                    "expected_retracted": _safe_bool(item.get("expected_retracted")),
                    "expected_found": _safe_bool(item.get("expected_found"),
                                                  category == "real"),
                    "expected_risk": _safe_str(item.get("expected_risk")),
                    "subcategory": _safe_str(item.get("subcategory")),
                    "note": _safe_str(item.get("note")),
                })

    return rows


def build_crawled_grim(crawled_dir: Path) -> list[dict]:
    """Build signal_unit_tests rows from crawled GRIM data (A-tier)."""
    fpath = crawled_dir / "grim_cases.jsonl"
    if not fpath.exists():
        return []

    rows = []
    with open(fpath, encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line.strip())
            rows.append({
                "id": item.get("id", f"grim_pmc_{len(rows)}"),
                "split": "signal_unit_tests",
                "category": "grim_test",
                "signal": "grim_test_failure",
                "expected_fired": _safe_bool(item.get("expected_fired")),
                "input_text": f"mean={item.get('reported_mean')} N={item.get('sample_size_n')}",
                "extra": _jdump({
                    "reported_mean": item.get("reported_mean"),
                    "sample_size_n": item.get("sample_size_n"),
                    "scale": item.get("scale"),
                    "source_pmcid": item.get("source_pmcid"),
                }),
                "note": _safe_str(item.get("note")),
            })
    return rows


def build_crawled_statcheck(crawled_dir: Path) -> list[dict]:
    """Build signal_unit_tests rows from crawled StatCheck data (A-tier)."""
    fpath = crawled_dir / "statcheck_cases.jsonl"
    if not fpath.exists():
        return []

    rows = []
    with open(fpath, encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line.strip())
            rows.append({
                "id": item.get("id", f"statcheck_pmc_{len(rows)}"),
                "split": "signal_unit_tests",
                "category": "statcheck",
                "signal": "statcheck_error",
                "expected_fired": _safe_bool(item.get("expected_fired")),
                "input_text": _safe_str(item.get("stat_text")),
                "extra": _jdump({
                    "test_type": item.get("test_type"),
                    "df1": item.get("df1"),
                    "df2": item.get("df2"),
                    "test_statistic": item.get("test_statistic"),
                    "reported_p": item.get("reported_p"),
                    "computed_p": item.get("computed_p"),
                    "source_pmcid": item.get("source_pmcid"),
                }),
                "note": _safe_str(item.get("note")),
            })
    return rows


def build_crawled_temporal(crawled_dir: Path) -> list[dict]:
    """Build graph_anomaly rows from crawled temporal data (A-tier)."""
    fpath = crawled_dir / "temporal_anomaly_cases.jsonl"
    if not fpath.exists():
        return []

    rows = []
    with open(fpath, encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line.strip())
            citing = item.get("citing_paper", {})
            cited = item.get("cited_paper", {})
            rows.append({
                "id": item.get("id", f"temporal_oa_{len(rows)}"),
                "split": "graph_anomaly",
                "category": "temporal_anomaly",
                "signal": "temporal_anomaly",
                "description": f"citing {citing.get('year', '?')} → cited {cited.get('year', '?')}",
                "dois": _jdump([citing.get("doi", ""), cited.get("doi", "")]),
                "expected_anomaly": _safe_bool(item.get("expected_anomaly")),
                "note": _safe_str(item.get("note")),
                "extra": _jdump({
                    "citing_year": item.get("citing_year"),
                    "cited_year": item.get("cited_year"),
                    "time_gap_years": item.get("time_gap_years"),
                }),
            })
    return rows


def build_crawled_scifact(crawled_dir: Path) -> list[dict]:
    """Build l2_nli rows from converted SciFact data (A-tier)."""
    fpath = crawled_dir / "scifact_l2_nli.jsonl"
    if not fpath.exists():
        return []

    rows = []
    with open(fpath, encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line.strip())
            rows.append(item)  # Already in correct schema
    return rows


def build_all_splits(data_dir: Path) -> dict[str, list[dict]]:
    """Build all splits and return a mapping of split_name → row list.

    Merges hand-curated data with crawled data when available.
    """
    result: dict[str, list[dict]] = {}
    for name, builder in SPLITS.items():
        result[name] = builder(data_dir)

    # Retraction Watch (original hand-curated)
    rw_dir = data_dir / "retraction_watch"
    if rw_dir.is_dir():
        result["retracted_papers"] = build_retracted_papers(rw_dir)
    else:
        print(f"  [warn] retraction_watch directory not found at {rw_dir}", file=sys.stderr)
        result["retracted_papers"] = []

    # Merge crawled data if available
    crawled_dir = data_dir / "crawled"
    if crawled_dir.is_dir():
        print(f"  Merging crawled data from {crawled_dir}")

        # S-tier: crawled retracted papers
        crawled_retracted = build_crawled_retracted(crawled_dir)
        if crawled_retracted:
            result["retracted_papers"].extend(crawled_retracted)
            print(f"    +{len(crawled_retracted)} crawled retracted papers")

        # S-tier: crawled reference verification
        crawled_refs = build_crawled_reference_verification(crawled_dir)
        if crawled_refs:
            result["reference_verification"].extend(crawled_refs)
            print(f"    +{len(crawled_refs)} crawled reference verification cases")

        # A-tier: GRIM cases
        crawled_grim = build_crawled_grim(crawled_dir)
        if crawled_grim:
            result["signal_unit_tests"].extend(crawled_grim)
            print(f"    +{len(crawled_grim)} crawled GRIM cases")

        # A-tier: StatCheck cases
        crawled_sc = build_crawled_statcheck(crawled_dir)
        if crawled_sc:
            result["signal_unit_tests"].extend(crawled_sc)
            print(f"    +{len(crawled_sc)} crawled StatCheck cases")

        # A-tier: Temporal anomalies
        crawled_temp = build_crawled_temporal(crawled_dir)
        if crawled_temp:
            result["graph_anomaly"].extend(crawled_temp)
            print(f"    +{len(crawled_temp)} crawled temporal anomaly cases")

        # A-tier: SciFact L2 NLI
        crawled_sf = build_crawled_scifact(crawled_dir)
        if crawled_sf:
            result["l2_nli"].extend(crawled_sf)
            print(f"    +{len(crawled_sf)} SciFact L2 NLI cases")

    return result


def validate_no_duplicate_ids(all_splits: dict[str, list[dict]]) -> list[str]:
    """
    Check for duplicate IDs *across all splits*.

    Returns a list of duplicate ID strings (empty = clean).
    """
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for split_name, rows in all_splits.items():
        for row in rows:
            row_id = row["id"]
            if row_id in seen:
                duplicates.append(
                    f"  duplicate id '{row_id}' in {split_name} (first seen in {seen[row_id]})"
                )
            else:
                seen[row_id] = split_name
    return duplicates


def print_summary(all_splits: dict[str, list[dict]]) -> None:
    """Print per-split row counts and category breakdowns."""
    print("\n=== IntegriRef-Bench Export Summary ===\n")
    grand_total = 0
    for split_name, rows in all_splits.items():
        n = len(rows)
        grand_total += n
        # Category breakdown
        cats: dict[str, int] = {}
        for row in rows:
            c = row.get("category", "unknown")
            cats[c] = cats.get(c, 0) + 1
        cat_str = "  ".join(f"{c}={v}" for c, v in sorted(cats.items()))
        print(f"  {split_name:<28} {n:>4} rows   [{cat_str}]")
    print(f"\n  {'TOTAL':<28} {grand_total:>4} rows")


def export(
    data_dir: Path,
    output_dir: Path,
    fmt: str,
) -> None:
    """
    Build all splits and write output files.

    Parameters
    ----------
    data_dir:   directory containing the source JSON/JSONL files
    output_dir: destination directory for exported files
    fmt:        'jsonl', 'parquet', or 'both'
    """
    print(f"Source data dir : {data_dir}")
    print(f"Output dir      : {output_dir}")
    print(f"Format          : {fmt}")
    print()

    output_dir.mkdir(parents=True, exist_ok=True)

    print("Building splits …")
    all_splits = build_all_splits(data_dir)

    # Validate IDs
    dupes = validate_no_duplicate_ids(all_splits)
    if dupes:
        print("\n[WARN] Duplicate IDs found across splits:", file=sys.stderr)
        for d in dupes:
            print(d, file=sys.stderr)
    else:
        print("  ID uniqueness check: PASSED (no cross-split duplicate IDs)")

    # Write outputs
    print("\nWriting files …")
    for split_name, rows in all_splits.items():
        if not rows:
            print(f"  [skip] {split_name}: no rows")
            continue

        if fmt in ("jsonl", "both"):
            out_path = output_dir / f"{split_name}.jsonl"
            write_jsonl(rows, out_path)
            print(f"  JSONL  → {out_path}  ({len(rows)} rows)")

        if fmt in ("parquet", "both"):
            out_path = output_dir / f"{split_name}.parquet"
            ok = write_parquet(rows, out_path)
            if ok:
                print(f"  Parquet→ {out_path}  ({len(rows)} rows)")
            else:
                print(
                    f"  [skip] Parquet output skipped for {split_name} "
                    "(install `datasets` library to enable Arrow/Parquet export)"
                )

    print_summary(all_splits)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export IntegriRef benchmark data to HuggingFace-compatible format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"Destination directory (default: {_DEFAULT_OUT})",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_DATA_DIR,
        help=f"Source data directory (default: {_DATA_DIR})",
    )
    parser.add_argument(
        "--format",
        choices=["jsonl", "parquet", "both"],
        default="jsonl",
        help="Output format (default: jsonl)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    export(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        fmt=args.format,
    )
