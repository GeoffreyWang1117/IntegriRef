"""
tests/test_dataset_export.py
============================
Pytest test suite for the HuggingFace dataset export pipeline.

Tests verify that:
  - All source JSON/JSONL files parse correctly.
  - The export produces the expected number of rows per split.
  - Every row carries the required fields.
  - There are no duplicate IDs within or across splits.
  - Category labels are drawn from valid sets.
  - Boolean fields are actual Python booleans (not strings).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BENCH_DIR = Path(__file__).parent.parent / "benchmarks"
_DATA_DIR = _BENCH_DIR / "data"
_RW_DIR = _DATA_DIR / "retraction_watch"

# Source files
_GOLDEN = _DATA_DIR / "golden_test_set.json"
_SIGNALS = _DATA_DIR / "signal_test_cases.json"
_L1L2 = _DATA_DIR / "l1_l2_test_pairs.json"
_GRAPH = _DATA_DIR / "graph_anomaly_cases.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))  # raises on bad JSON
    return rows


def _build_all_splits() -> dict[str, list[dict]]:
    """Import and run the export pipeline, returning all split row-lists."""
    # Import lazily so a SyntaxError in export module is caught as a test failure
    from benchmarks.export_hf_dataset import build_all_splits  # noqa: PLC0415
    return build_all_splits(_DATA_DIR)


# ---------------------------------------------------------------------------
# Source file parse tests
# ---------------------------------------------------------------------------


class TestSourceFilesParse:
    """Verify that every source data file is valid JSON / JSONL."""

    def test_golden_test_set_is_valid_json(self):
        data = _load_json(_GOLDEN)
        assert isinstance(data, dict), "golden_test_set.json must be a JSON object"

    def test_signal_test_cases_is_valid_json(self):
        data = _load_json(_SIGNALS)
        assert isinstance(data, dict), "signal_test_cases.json must be a JSON object"

    def test_l1_l2_test_pairs_is_valid_json(self):
        data = _load_json(_L1L2)
        assert isinstance(data, dict), "l1_l2_test_pairs.json must be a JSON object"

    def test_graph_anomaly_cases_is_valid_json(self):
        data = _load_json(_GRAPH)
        assert isinstance(data, dict), "graph_anomaly_cases.json must be a JSON object"

    @pytest.mark.parametrize(
        "filename",
        [
            "crossref_retracted.jsonl",
            "crossref_real_deep_learning.jsonl",
            "crossref_real_genomics.jsonl",
            "crossref_real_clinical_trial.jsonl",
            "crossref_real_natural_language_processing.jsonl",
            "pubmed_retracted.jsonl",
        ],
    )
    def test_retraction_watch_jsonl_is_valid(self, filename: str):
        path = _RW_DIR / filename
        assert path.exists(), f"Expected JSONL file not found: {path}"
        rows = _load_jsonl(path)
        assert len(rows) > 0, f"{filename} must contain at least one record"

    def test_golden_test_set_has_required_top_level_keys(self):
        data = _load_json(_GOLDEN)
        required = {"retracted_papers", "real_papers", "llm_hallucinated", "metadata_chimera"}
        missing = required - set(data.keys())
        assert not missing, f"golden_test_set.json missing keys: {missing}"

    def test_signal_test_cases_has_required_top_level_keys(self):
        data = _load_json(_SIGNALS)
        required = {
            "tortured_phrases_cases",
            "suspicious_email_cases",
            "grim_test_cases",
            "statcheck_cases",
        }
        missing = required - set(data.keys())
        assert not missing, f"signal_test_cases.json missing keys: {missing}"

    def test_l1l2_has_required_top_level_keys(self):
        data = _load_json(_L1L2)
        required = {"l1_citing_pairs", "l2_claim_source_pairs"}
        missing = required - set(data.keys())
        assert not missing, f"l1_l2_test_pairs.json missing keys: {missing}"

    def test_graph_anomaly_has_required_top_level_keys(self):
        data = _load_json(_GRAPH)
        required = {
            "citation_rings",
            "excessive_self_citation",
            "temporal_anomalies",
            "orphan_clusters",
        }
        missing = required - set(data.keys())
        assert not missing, f"graph_anomaly_cases.json missing keys: {missing}"


# ---------------------------------------------------------------------------
# Source record count tests
# ---------------------------------------------------------------------------


class TestSourceCounts:
    """Verify expected record counts in raw source files."""

    def test_golden_retracted_papers_count(self):
        data = _load_json(_GOLDEN)
        assert len(data["retracted_papers"]) == 28

    def test_golden_real_papers_count(self):
        data = _load_json(_GOLDEN)
        assert len(data["real_papers"]) == 10

    def test_golden_llm_hallucinated_count(self):
        data = _load_json(_GOLDEN)
        assert len(data["llm_hallucinated"]) >= 8

    def test_golden_metadata_chimera_count(self):
        data = _load_json(_GOLDEN)
        assert len(data["metadata_chimera"]) == 5

    def test_l1_citing_pairs_count(self):
        data = _load_json(_L1L2)
        assert len(data["l1_citing_pairs"]) == 20

    def test_l2_claim_source_pairs_count(self):
        data = _load_json(_L1L2)
        assert len(data["l2_claim_source_pairs"]) == 20

    def test_retraction_watch_total(self):
        total = 0
        for path in _RW_DIR.glob("*.jsonl"):
            total += len(_load_jsonl(path))
        assert total == 250, f"Expected 250 retraction-watch records, got {total}"

    def test_pubmed_retracted_count(self):
        rows = _load_jsonl(_RW_DIR / "pubmed_retracted.jsonl")
        assert len(rows) == 200

    def test_crossref_retracted_count(self):
        rows = _load_jsonl(_RW_DIR / "crossref_retracted.jsonl")
        assert len(rows) == 30


# ---------------------------------------------------------------------------
# Export row count tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def all_splits() -> dict[str, list[dict]]:
    """Build all export splits once per test session."""
    return _build_all_splits()


class TestExportRowCounts:
    """Verify expected row counts in each exported split."""

    def test_reference_verification_count(self, all_splits):
        rows = all_splits["reference_verification"]
        # Base: 58 hand-curated + crawled real/hallucinated/chimera
        assert len(rows) >= 58

    def test_signal_unit_tests_count(self, all_splits):
        rows = all_splits["signal_unit_tests"]
        # Base: 86 hand-curated + crawled GRIM/StatCheck
        assert len(rows) >= 86

    def test_l1_intent_count(self, all_splits):
        rows = all_splits["l1_intent"]
        assert len(rows) >= 20

    def test_l2_nli_count(self, all_splits):
        rows = all_splits["l2_nli"]
        assert len(rows) >= 20

    def test_graph_anomaly_count(self, all_splits):
        rows = all_splits["graph_anomaly"]
        # Base: 30 hand-curated + crawled temporal anomalies
        assert len(rows) >= 30

    def test_retracted_papers_count(self, all_splits):
        rows = all_splits["retracted_papers"]
        # Base: 250 + crawled from Crossref/PubMed
        assert len(rows) >= 250

    def test_total_count(self, all_splits):
        total = sum(len(v) for v in all_splits.values())
        # Base: 464 + crawled data
        assert total >= 464


# ---------------------------------------------------------------------------
# No duplicate ID tests
# ---------------------------------------------------------------------------


class TestNoDuplicateIds:
    """Verify ID uniqueness within each split and across the full dataset."""

    def test_no_duplicates_within_reference_verification(self, all_splits):
        ids = [r["id"] for r in all_splits["reference_verification"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs in reference_verification"

    def test_no_duplicates_within_signal_unit_tests(self, all_splits):
        ids = [r["id"] for r in all_splits["signal_unit_tests"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs in signal_unit_tests"

    def test_no_duplicates_within_l1_intent(self, all_splits):
        ids = [r["id"] for r in all_splits["l1_intent"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs in l1_intent"

    def test_no_duplicates_within_l2_nli(self, all_splits):
        ids = [r["id"] for r in all_splits["l2_nli"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs in l2_nli"

    def test_no_duplicates_within_graph_anomaly(self, all_splits):
        ids = [r["id"] for r in all_splits["graph_anomaly"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs in graph_anomaly"

    def test_no_duplicates_within_retracted_papers(self, all_splits):
        ids = [r["id"] for r in all_splits["retracted_papers"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs in retracted_papers"

    def test_no_duplicate_ids_across_all_splits(self, all_splits):
        from benchmarks.export_hf_dataset import validate_no_duplicate_ids  # noqa: PLC0415
        dupes = validate_no_duplicate_ids(all_splits)
        assert not dupes, "Duplicate IDs found across splits:\n" + "\n".join(dupes)


# ---------------------------------------------------------------------------
# Required fields tests
# ---------------------------------------------------------------------------

# Required fields common to every split
_COMMON_REQUIRED = {"id", "split", "category", "signal"}

# Per-split required fields (in addition to common)
_SPLIT_REQUIRED: dict[str, set[str]] = {
    "reference_verification": {
        "title", "authors", "year", "doi", "venue",
        "expected_retracted", "expected_found", "expected_risk",
        "subcategory", "note",
    },
    "signal_unit_tests": {
        "input_text", "expected_fired", "note", "extra",
    },
    "l1_intent": {
        "citing_sentence", "citation_key", "cited_doi", "cited_title",
        "cited_abstract", "expected_intent", "expected_misrepresents", "note",
    },
    "l2_nli": {
        "claim", "source_text", "expected_relation", "domain", "note",
    },
    "graph_anomaly": {
        "description", "dois", "expected_anomaly", "note", "extra",
    },
    "retracted_papers": {
        "source", "doi", "pmid", "title", "authors", "year",
        "venue", "expected_retracted", "expected_risk",
    },
}


class TestRequiredFields:
    """Every row must contain the common and split-specific required fields."""

    @pytest.mark.parametrize("split_name", list(_SPLIT_REQUIRED.keys()))
    def test_required_fields_present(self, all_splits, split_name: str):
        rows = all_splits[split_name]
        required = _COMMON_REQUIRED | _SPLIT_REQUIRED[split_name]
        failures: list[str] = []
        for row in rows:
            missing = required - set(row.keys())
            if missing:
                failures.append(f"  row id={row.get('id')} missing fields: {sorted(missing)}")
        assert not failures, (
            f"Missing required fields in split '{split_name}':\n"
            + "\n".join(failures[:20])  # cap output
        )


# ---------------------------------------------------------------------------
# Category label tests
# ---------------------------------------------------------------------------

_VALID_CATEGORIES: dict[str, set[str]] = {
    "reference_verification": {"retracted", "real", "hallucinated", "chimera"},
    "l1_intent": {"supporting", "contrasting", "mentioning", "misrepresents", "all_mentioning"},
    "l2_nli": {"entailment", "contradiction", "neutral"},
    "graph_anomaly": {"citation_ring", "self_citation", "temporal_anomaly", "orphan_cluster"},
    "retracted_papers": {"retracted", "real_control"},
}


class TestCategoryLabels:
    """Category values must come from a defined vocabulary for each split."""

    @pytest.mark.parametrize("split_name", list(_VALID_CATEGORIES.keys()))
    def test_category_labels_valid(self, all_splits, split_name: str):
        valid = _VALID_CATEGORIES[split_name]
        invalid: list[str] = []
        for row in all_splits[split_name]:
            cat = row.get("category", "")
            if cat not in valid:
                invalid.append(
                    f"  row id={row['id']} has category={cat!r} (valid: {sorted(valid)})"
                )
        assert not invalid, (
            f"Invalid category values in split '{split_name}':\n"
            + "\n".join(invalid[:20])
        )


# ---------------------------------------------------------------------------
# Type correctness tests
# ---------------------------------------------------------------------------


class TestFieldTypes:
    """Boolean fields must be actual Python booleans, not strings."""

    def test_expected_retracted_is_bool_in_reference_verification(self, all_splits):
        for row in all_splits["reference_verification"]:
            assert isinstance(row["expected_retracted"], bool), (
                f"expected_retracted not bool in row {row['id']}: "
                f"{type(row['expected_retracted'])}"
            )

    def test_expected_found_is_bool_in_reference_verification(self, all_splits):
        for row in all_splits["reference_verification"]:
            assert isinstance(row["expected_found"], bool), (
                f"expected_found not bool in row {row['id']}: "
                f"{type(row['expected_found'])}"
            )

    def test_expected_fired_is_bool_in_signal_unit_tests(self, all_splits):
        for row in all_splits["signal_unit_tests"]:
            assert isinstance(row["expected_fired"], bool), (
                f"expected_fired not bool in row {row['id']}: "
                f"{type(row['expected_fired'])}"
            )

    def test_expected_misrepresents_is_bool_in_l1_intent(self, all_splits):
        for row in all_splits["l1_intent"]:
            assert isinstance(row["expected_misrepresents"], bool), (
                f"expected_misrepresents not bool in row {row['id']}: "
                f"{type(row['expected_misrepresents'])}"
            )

    def test_expected_anomaly_is_bool_in_graph_anomaly(self, all_splits):
        for row in all_splits["graph_anomaly"]:
            assert isinstance(row["expected_anomaly"], bool), (
                f"expected_anomaly not bool in row {row['id']}: "
                f"{type(row['expected_anomaly'])}"
            )

    def test_id_is_string_in_all_splits(self, all_splits):
        for split_name, rows in all_splits.items():
            for row in rows:
                assert isinstance(row["id"], str), (
                    f"id is not a string in split {split_name}: {type(row['id'])}"
                )

    def test_extra_is_valid_json_in_signal_unit_tests(self, all_splits):
        """The 'extra' field must be a JSON-parseable string."""
        for row in all_splits["signal_unit_tests"]:
            extra = row.get("extra", "")
            try:
                json.loads(extra)
            except (json.JSONDecodeError, TypeError) as exc:
                pytest.fail(
                    f"signal_unit_tests row {row['id']}: 'extra' is not valid JSON: {exc}"
                )

    def test_dois_is_valid_json_list_in_graph_anomaly(self, all_splits):
        """The 'dois' field must be a JSON-parseable list."""
        for row in all_splits["graph_anomaly"]:
            dois_str = row.get("dois", "[]")
            try:
                parsed = json.loads(dois_str)
            except (json.JSONDecodeError, TypeError) as exc:
                pytest.fail(
                    f"graph_anomaly row {row['id']}: 'dois' is not valid JSON: {exc}"
                )
            assert isinstance(parsed, list), (
                f"graph_anomaly row {row['id']}: 'dois' must decode to a list"
            )


# ---------------------------------------------------------------------------
# Content sanity tests
# ---------------------------------------------------------------------------


class TestContentSanity:
    """Spot-check specific well-known entries survive the export correctly."""

    def test_wakefield_retraction_present(self, all_splits):
        """The Wakefield MMR paper (retracted_001) must appear in reference_verification."""
        rows = all_splits["reference_verification"]
        wakefield = next((r for r in rows if r["id"] == "retracted_001"), None)
        assert wakefield is not None, "retracted_001 (Wakefield) missing from reference_verification"
        assert wakefield["expected_retracted"] is True
        assert wakefield["expected_risk"] == "HIGH"
        assert wakefield["category"] == "retracted"

    def test_attention_is_all_you_need_present(self, all_splits):
        """The Vaswani Transformer paper (real_001) must appear as a non-retracted real paper."""
        rows = all_splits["reference_verification"]
        transformer = next((r for r in rows if r["id"] == "real_001"), None)
        assert transformer is not None, "real_001 (Attention Is All You Need) missing"
        assert transformer["expected_retracted"] is False
        assert transformer["category"] == "real"

    def test_l1_pair_001_supporting(self, all_splits):
        """l1_pair_001 should be labelled 'supporting' with no misrepresentation."""
        rows = all_splits["l1_intent"]
        pair = next((r for r in rows if r["id"] == "l1_pair_001"), None)
        assert pair is not None, "l1_pair_001 missing from l1_intent"
        assert pair["expected_intent"] == "supporting"
        assert pair["expected_misrepresents"] is False

    def test_l2_pair_contradiction_present(self, all_splits):
        """At least one l2_nli row must have expected_relation == 'contradiction'."""
        rows = all_splits["l2_nli"]
        contradictions = [r for r in rows if r["expected_relation"] == "contradiction"]
        assert len(contradictions) >= 3, (
            f"Expected at least 3 contradiction pairs, found {len(contradictions)}"
        )

    def test_reference_verification_retracted_labels(self, all_splits):
        """All rows with category='retracted' must have expected_retracted=True.

        Note: the source retracted_papers array contains 5 hard-negative entries
        (controversial-but-not-retracted papers).  The export maps these to
        category='real' based on their expected_retracted=False flag.
        """
        rows = all_splits["reference_verification"]
        bad = [
            r for r in rows
            if r["category"] == "retracted" and r["expected_retracted"] is not True
        ]
        assert not bad, f"Retracted rows with expected_retracted != True: {[r['id'] for r in bad]}"

    def test_reference_verification_real_labels(self, all_splits):
        """All rows with category='real' must have expected_retracted=False."""
        rows = all_splits["reference_verification"]
        bad = [
            r for r in rows
            if r["category"] == "real" and r["expected_retracted"] is not False
        ]
        assert not bad, f"Real rows with expected_retracted != False: {[r['id'] for r in bad]}"

    def test_golden_retracted_papers_contains_hard_negatives(self, all_splits):
        """The source retracted_papers array includes controversial-not-retracted entries.

        These should be exported as category='real' with expected_retracted=False.
        Facebook contagion study (retracted_008) is a known hard negative.
        """
        rows = all_splits["reference_verification"]
        facebook = next((r for r in rows if r["id"] == "retracted_008"), None)
        assert facebook is not None, "retracted_008 (Facebook contagion study) missing"
        assert facebook["category"] == "real", (
            "retracted_008 is not actually retracted and should have category='real'"
        )
        assert facebook["expected_retracted"] is False

    def test_graph_anomaly_has_all_four_subtypes(self, all_splits):
        rows = all_splits["graph_anomaly"]
        cats = {r["category"] for r in rows}
        expected = {"citation_ring", "self_citation", "temporal_anomaly", "orphan_cluster"}
        missing = expected - cats
        assert not missing, f"graph_anomaly is missing category subtypes: {missing}"

    def test_retracted_papers_has_both_labels(self, all_splits):
        rows = all_splits["retracted_papers"]
        cats = {r["category"] for r in rows}
        assert "retracted" in cats, "retracted_papers split has no 'retracted' category rows"
        assert "real_control" in cats, "retracted_papers split has no 'real_control' category rows"

    def test_signal_unit_tests_has_positive_and_negative_examples(self, all_splits):
        rows = all_splits["signal_unit_tests"]
        fired = [r for r in rows if r["expected_fired"] is True]
        not_fired = [r for r in rows if r["expected_fired"] is False]
        assert len(fired) > 0, "signal_unit_tests has no positive (expected_fired=True) examples"
        assert len(not_fired) > 0, "signal_unit_tests has no negative (expected_fired=False) examples"

    def test_all_split_names_correct(self, all_splits):
        """Every row's 'split' field must match its actual split key."""
        for split_name, rows in all_splits.items():
            bad = [r for r in rows if r.get("split") != split_name]
            assert not bad, (
                f"Rows in '{split_name}' have wrong 'split' field: "
                + str([r["id"] for r in bad[:5]])
            )
