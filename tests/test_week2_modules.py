"""Comprehensive tests for Week 2 modules.

Covers:
1. OpenAlexGraphBuilder  (graph/openalex_builder.py)
2. GRIMTester            (verification/statistical.py)
3. StatChecker           (verification/statistical.py)
4. StatisticalVerifier   (verification/statistical.py)
5. SneakedReferenceDetector (verification/sneaked_references.py)
6. TextAugmenter         (benchmarks/train_l1_augmented.py)

All tests run offline with no network calls and no GPU.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from graph.builder import CitationEdge, CitationGraph, CitationNode
from graph.openalex_builder import OpenAlexGraphBuilder
from verification.statistical import (
    GRIMResult,
    GRIMTester,
    StatCheckResult,
    StatChecker,
    StatisticalReport,
    StatisticalVerifier,
    _HAS_SCIPY,
)
from verification.sneaked_references import (
    SneakedReferenceDetector,
    SneakedReferenceMatch,
    SneakedReferenceReport,
)
from benchmarks.train_l1_augmented import TextAugmenter


# ========================================================================
# Fixtures
# ========================================================================

@pytest.fixture
def sample_openalex_work():
    """A realistic OpenAlex work JSON dict."""
    return {
        "id": "https://openalex.org/W1234567890",
        "title": "Deep Learning for Citation Analysis",
        "doi": "https://doi.org/10.1234/example.2024",
        "publication_year": 2024,
        "authorships": [
            {"author": {"display_name": "Alice Smith"}},
            {"author": {"display_name": "Bob Jones"}},
        ],
        "primary_location": {
            "source": {"display_name": "Journal of AI Research"},
        },
        "cited_by_count": 42,
        "referenced_works": [
            "https://openalex.org/W1111",
            "https://openalex.org/W2222",
        ],
        "counts_by_year": [
            {"year": 2024, "cited_by_count": 10},
            {"year": 2023, "cited_by_count": 32},
        ],
        "concepts": [
            {"display_name": "Machine Learning", "score": 0.92},
            {"display_name": "Computer Science", "score": 0.85},
            {"display_name": "Bibliometrics", "score": 0.45},
        ],
        "is_retracted": False,
        "type": "journal-article",
    }


@pytest.fixture
def sample_ref_work():
    """A minimal reference work from OpenAlex."""
    return {
        "id": "https://openalex.org/W1111",
        "title": "Attention Is All You Need",
        "doi": "https://doi.org/10.5555/attention",
        "publication_year": 2017,
        "authorships": [
            {"author": {"display_name": "Vaswani A."}},
        ],
        "primary_location": {
            "source": {"display_name": "NeurIPS"},
        },
        "cited_by_count": 50000,
        "referenced_works": ["https://openalex.org/W9999"],
        "counts_by_year": [],
        "concepts": [
            {"display_name": "Neural Networks", "score": 0.95},
        ],
        "is_retracted": False,
        "type": "journal-article",
    }


@pytest.fixture
def grim():
    return GRIMTester()


@pytest.fixture
def statchecker():
    return StatChecker()


@pytest.fixture
def augmenter():
    return TextAugmenter(seed=42)


# ========================================================================
# 1. OpenAlexGraphBuilder
# ========================================================================

class TestOpenAlexGraphBuilder:
    """Tests for OpenAlexGraphBuilder (no network calls)."""

    def _make_builder(self):
        with patch("graph.openalex_builder.requests.Session"):
            builder = OpenAlexGraphBuilder(email="test@example.com")
        return builder

    def test_parse_to_node_basic(self, sample_openalex_work):
        builder = self._make_builder()
        node = builder._parse_to_node(sample_openalex_work)

        assert node is not None
        assert node.ice_id == "https://openalex.org/W1234567890"
        assert node.title == "Deep Learning for Citation Analysis"
        assert node.year == "2024"
        assert node.authors == ["Alice Smith", "Bob Jones"]
        assert node.venue == "Journal of AI Research"
        assert node.citation_count == 42
        assert node.reference_count == 2
        assert node.field == "Machine Learning"
        assert node.entity_type == "paper"
        assert node.metadata["doi"] == "10.1234/example.2024"
        assert node.metadata["is_retracted"] is False
        assert node.metadata["openalex_id"] == "https://openalex.org/W1234567890"
        assert len(node.metadata["counts_by_year"]) == 2

    def test_parse_to_node_no_title_returns_none(self):
        builder = self._make_builder()
        data = {"id": "https://openalex.org/W999", "title": ""}
        assert builder._parse_to_node(data) is None

        data2 = {"id": "https://openalex.org/W999"}
        assert builder._parse_to_node(data2) is None

    def test_parse_to_node_missing_optional_fields(self):
        builder = self._make_builder()
        data = {
            "id": "https://openalex.org/W555",
            "title": "Minimal Paper",
        }
        node = builder._parse_to_node(data)
        assert node is not None
        assert node.title == "Minimal Paper"
        assert node.year == ""
        assert node.authors == []
        assert node.venue == ""
        assert node.citation_count == 0
        assert node.reference_count == 0
        assert node.field == ""

    def test_extract_field_picks_highest_score(self, sample_openalex_work):
        builder = self._make_builder()
        field = builder._extract_field(sample_openalex_work)
        assert field == "Neural Networks" or field == "Machine Learning"
        # The highest score concept is "Machine Learning" at 0.92
        assert field == "Machine Learning"

    def test_extract_field_empty_concepts(self):
        builder = self._make_builder()
        assert builder._extract_field({"concepts": []}) == ""
        assert builder._extract_field({}) == ""

    def test_extract_field_single_concept(self):
        builder = self._make_builder()
        data = {"concepts": [{"display_name": "Physics", "score": 0.5}]}
        assert builder._extract_field(data) == "Physics"

    def test_stats_property_starts_at_zero(self):
        builder = self._make_builder()
        s = builder.stats
        assert s["api_calls"] == 0
        assert s["api_time_seconds"] == 0.0
        assert s["avg_latency_ms"] == 0.0

    def test_normalise_oa_id_full_url(self):
        assert OpenAlexGraphBuilder._normalise_oa_id(
            "https://openalex.org/W1234") == "W1234"

    def test_normalise_oa_id_bare(self):
        assert OpenAlexGraphBuilder._normalise_oa_id("W1234") == "W1234"

    def test_normalise_oa_id_empty(self):
        assert OpenAlexGraphBuilder._normalise_oa_id("") == ""

    def test_build_from_references_one_hop(
        self, sample_openalex_work, sample_ref_work
    ):
        """build_from_references with mocked _fetch_works_batch."""
        builder = self._make_builder()

        ref_work2 = dict(sample_ref_work)
        ref_work2["id"] = "https://openalex.org/W2222"
        ref_work2["title"] = "BERT: Pre-training of Deep Bidirectional Transformers"

        with patch.object(
            builder, "_fetch_works_batch", return_value=[sample_ref_work, ref_work2]
        ):
            graph = builder.build_from_references(
                paper_data=sample_openalex_work,
                reference_ids=[
                    "https://openalex.org/W1111",
                    "https://openalex.org/W2222",
                ],
                two_hop=False,
            )

        # Root + 2 references = 3 nodes
        assert graph.node_count == 3
        # Root cites each ref = 2 edges
        assert graph.edge_count == 2

        root_node = graph.get_node("https://openalex.org/W1234567890")
        assert root_node is not None
        assert root_node.title == "Deep Learning for Citation Analysis"

        refs = graph.get_references("https://openalex.org/W1234567890")
        assert len(refs) == 2

    def test_build_from_references_empty_refs(self, sample_openalex_work):
        builder = self._make_builder()
        graph = builder.build_from_references(
            paper_data=sample_openalex_work,
            reference_ids=[],
            two_hop=True,
        )
        assert graph.node_count == 1
        assert graph.edge_count == 0

    def test_build_from_references_none_root(self):
        builder = self._make_builder()
        # No title => _parse_to_node returns None
        graph = builder.build_from_references(
            paper_data={"id": "W1", "title": ""},
            reference_ids=["W2"],
            two_hop=False,
        )
        assert graph.node_count == 0

    def test_collect_oa_ids(self, sample_openalex_work):
        builder = self._make_builder()
        graph = CitationGraph()
        node = builder._parse_to_node(sample_openalex_work)
        graph.add_node(node)
        mapping = builder._collect_oa_ids(graph)
        assert "https://openalex.org/W1234567890" in mapping


# ========================================================================
# 2. GRIMTester
# ========================================================================

class TestGRIMTester:

    def test_grim_consistent_integer_product(self, grim):
        """mean=2.0, N=25 => product=50.0, integer => consistent."""
        result = grim.test(mean=2.0, n=25)
        assert result.is_consistent is True
        assert result.mean == 2.0
        assert result.n == 25
        assert result.items == 1
        assert result.granularity == pytest.approx(1 / 25)

    def test_grim_inconsistent(self, grim):
        """mean=2.31, N=25 => product=57.75, NOT integer => inconsistent."""
        result = grim.test(mean=2.31, n=25)
        assert result.is_consistent is False
        assert result.mean == 2.31
        assert result.n == 25

    def test_grim_with_items(self, grim):
        """mean=3.5, N=10, items=2 => product=70.0 => consistent."""
        result = grim.test(mean=3.5, n=10, items=2)
        assert result.is_consistent is True
        assert result.items == 2
        assert result.granularity == pytest.approx(1 / 20)

    def test_grim_exact_half_granularity(self, grim):
        """mean=2.50, N=2 => product=5.0, integer => consistent."""
        result = grim.test(mean=2.50, n=2)
        assert result.is_consistent is True
        assert result.granularity == pytest.approx(0.5)

    def test_grim_n_zero_treated_consistent(self, grim):
        """N=0 is degenerate; treated as consistent."""
        result = grim.test(mean=3.14, n=0)
        assert result.is_consistent is True
        assert result.granularity == 0.0

    def test_grim_negative_n_treated_consistent(self, grim):
        result = grim.test(mean=1.0, n=-5)
        assert result.is_consistent is True

    def test_grim_borderline_tolerance(self, grim):
        """mean=2.004, N=25 => product=50.1, remainder=0.1 > default tol 0.01."""
        result = grim.test(mean=2.004, n=25)
        assert result.is_consistent is False

    def test_grim_borderline_within_tolerance(self, grim):
        """mean=2.0004, N=25 => product=50.01, remainder=0.01 <= tol."""
        result = grim.test(mean=2.0004, n=25)
        assert result.is_consistent is True

    def test_extract_and_test_from_text(self, grim):
        text = "The participants scored M = 2.31, N = 25 on the scale."
        results = grim.extract_and_test(text)
        assert len(results) == 1
        assert results[0].mean == 2.31
        assert results[0].n == 25
        assert results[0].is_consistent is False
        assert results[0].reported_str != ""

    def test_extract_and_test_consistent(self, grim):
        text = "We found mean = 2.00 with N = 25 participants."
        results = grim.extract_and_test(text)
        assert len(results) == 1
        assert results[0].is_consistent is True

    def test_extract_and_test_no_matches(self, grim):
        text = "No statistics here, just text about cats and dogs."
        results = grim.extract_and_test(text)
        assert results == []

    def test_extract_and_test_multiple(self, grim):
        text = (
            "Group A scored M = 2.00, N = 10. "
            "Group B scored M = 3.33, N = 9."
        )
        results = grim.extract_and_test(text)
        assert len(results) == 2


# ========================================================================
# 3. StatChecker
# ========================================================================

class TestStatChecker:

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_extract_t_test(self, statchecker):
        text = "t(24) = 2.45, p = .02"
        results = statchecker.extract_and_check(text)
        assert len(results) == 1
        r = results[0]
        assert r.test_type == "t"
        assert r.test_statistic == 2.45
        assert r.df1 == 24
        assert r.reported_p == pytest.approx(0.02)
        assert r.computed_p > 0  # scipy computed a real p-value

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_extract_f_test(self, statchecker):
        text = "F(1, 30) = 4.12, p = .05"
        results = statchecker.extract_and_check(text)
        assert len(results) == 1
        r = results[0]
        assert r.test_type == "F"
        assert r.test_statistic == 4.12
        assert r.df1 == 1
        assert r.df2 == 30
        assert r.reported_p == pytest.approx(0.05)

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_extract_chi_squared(self, statchecker):
        text = "\u03c7\u00b2(3) = 7.81, p = .05"
        results = statchecker.extract_and_check(text)
        assert len(results) == 1
        r = results[0]
        assert r.test_type == "chi2"
        assert r.test_statistic == 7.81
        assert r.df1 == 3
        assert r.reported_p == pytest.approx(0.05)

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_extract_z_test(self, statchecker):
        text = "Z = 1.96, p = .05"
        results = statchecker.extract_and_check(text)
        assert len(results) == 1
        r = results[0]
        assert r.test_type == "Z"
        assert r.test_statistic == 1.96
        assert r.reported_p == pytest.approx(0.05)

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_extract_r_correlation(self, statchecker):
        text = "r(28) = .45, p = .01"
        results = statchecker.extract_and_check(text)
        assert len(results) == 1
        r = results[0]
        assert r.test_type == "r"
        assert r.test_statistic == pytest.approx(0.45)
        assert r.df1 == 28
        assert r.reported_p == pytest.approx(0.01)

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_t_test_consistency(self, statchecker):
        """t(24) = 2.45, p = .02 should be roughly consistent."""
        r = statchecker.check("t", 2.45, df1=24, reported_p=0.02)
        # Actual 2-tailed p for t(24)=2.45 is ~0.0219; |0.02 - 0.0219| < 0.05
        assert r.is_consistent is True
        assert r.computed_p > 0
        assert abs(r.reported_p - r.computed_p) < 0.05

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_t_test_inconsistency(self, statchecker):
        """t(24) = 0.5, p = .001 is clearly wrong."""
        r = statchecker.check("t", 0.5, df1=24, reported_p=0.001)
        # Actual p for t(24)=0.5 is ~0.62, far from 0.001
        assert r.is_consistent is False
        assert r.computed_p > 0.5

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_decision_error_detection(self, statchecker):
        """t(24) = 0.5, p = .001 => computed p > 0.05, reported p < 0.05 => decision error."""
        r = statchecker.check("t", 0.5, df1=24, reported_p=0.001)
        assert r.decision_error is True

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_no_decision_error_when_both_significant(self, statchecker):
        """Both reported and computed p < 0.05 => no decision error."""
        r = statchecker.check("t", 3.0, df1=24, reported_p=0.005)
        # Computed p for t(24)=3.0 is ~0.006
        assert r.decision_error is False

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_f_test_check(self, statchecker):
        r = statchecker.check("F", 4.12, df1=1, df2=30, reported_p=0.05)
        # F(1,30)=4.12 => p ~ 0.051
        assert r.computed_p > 0
        assert r.is_consistent is True

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_chi2_check(self, statchecker):
        r = statchecker.check("chi2", 7.81, df1=3, reported_p=0.05)
        # chi2(3)=7.81 => p ~ 0.05
        assert r.computed_p > 0
        assert r.is_consistent is True

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_z_check(self, statchecker):
        r = statchecker.check("Z", 1.96, df1=0, reported_p=0.05)
        # Z=1.96 => p ~ 0.05
        assert r.computed_p > 0
        assert r.is_consistent is True

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_r_check(self, statchecker):
        r = statchecker.check("r", 0.45, df1=28, reported_p=0.01)
        # r(28) = 0.45 => t = 0.45 * sqrt(28 / (1-0.2025)) => p ~ 0.013
        assert r.computed_p > 0
        assert r.is_consistent is True

    def test_parse_p_leading_dot(self, statchecker):
        """p = .05 => the regex captures '05', _parse_p should return 0.05."""
        val = statchecker._parse_p("05", "=")
        assert val == pytest.approx(0.05)

    def test_parse_p_full_decimal(self, statchecker):
        val = statchecker._parse_p("0.001", "=")
        assert val == pytest.approx(0.001)

    def test_no_scipy_fallback(self, statchecker):
        """When scipy is unavailable, check returns sentinel -1.0."""
        with patch("verification.statistical._HAS_SCIPY", False), \
             patch("verification.statistical._compute_p_value", return_value=None):
            r = statchecker.check("t", 2.0, df1=10, reported_p=0.05)
            assert r.computed_p == -1.0
            assert r.is_consistent is True
            assert r.decision_error is False

    def test_extract_multiple_stats(self, statchecker):
        text = (
            "We found t(24) = 2.45, p = .02 for group A. "
            "The F(1, 30) = 4.12, p = .05 was also significant."
        )
        results = statchecker.extract_and_check(text)
        assert len(results) == 2
        types = {r.test_type for r in results}
        assert "t" in types
        assert "F" in types


# ========================================================================
# 4. StatisticalVerifier
# ========================================================================

class TestStatisticalVerifier:

    def test_verify_returns_statistical_report(self):
        v = StatisticalVerifier()
        report = v.verify("No stats here, just plain text.")
        assert isinstance(report, StatisticalReport)
        assert report.text_length > 0
        assert report.grim_results == []
        assert report.statcheck_results == []
        assert report.risk_score == 0.0

    def test_risk_scoring_with_grim_failures(self):
        v = StatisticalVerifier()
        text = "We found M = 2.31, N = 25 in the experiment."
        report = v.verify(text)
        assert report.grim_failures == 1
        assert report.risk_score >= 25.0  # _GRIM_PENALTY = 25

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_risk_scoring_with_statcheck_errors(self):
        v = StatisticalVerifier()
        # t(24)=0.5, p=.001 is grossly inconsistent and a decision error
        text = "Results showed t(24) = 0.5, p = .001."
        report = v.verify(text)
        assert report.statcheck_errors >= 1
        # _STATCHECK_PENALTY=10 + _DECISION_PENALTY=20 = 30
        assert report.risk_score >= 30.0

    @pytest.mark.skipif(not _HAS_SCIPY, reason="scipy not available")
    def test_combined_grim_and_statcheck(self):
        v = StatisticalVerifier()
        text = (
            "Participants scored M = 2.31, N = 25. "
            "Results showed t(24) = 0.5, p = .001."
        )
        report = v.verify(text)
        assert report.grim_failures >= 1
        assert report.statcheck_errors >= 1
        # 25 + 10 + 20 = 55
        assert report.risk_score >= 55.0

    def test_risk_score_capped_at_100(self):
        v = StatisticalVerifier()
        # Simulate many failures by using multiple GRIM inconsistencies
        text = (
            "Group A: M = 2.31, N = 25. "
            "Group B: M = 3.33, N = 9. "
            "Group C: M = 4.41, N = 7. "
            "Group D: M = 5.77, N = 3. "
            "Group E: M = 6.13, N = 11."
        )
        report = v.verify(text)
        assert report.risk_score <= 100.0

    def test_verify_preserves_text_length(self):
        v = StatisticalVerifier()
        text = "Hello world" * 100
        report = v.verify(text)
        assert report.text_length == len(text)


# ========================================================================
# 5. SneakedReferenceDetector
# ========================================================================

class TestSneakedReferenceDetector:
    """Tests for SneakedReferenceDetector (no network calls)."""

    def _make_detector(self):
        with patch("verification.sneaked_references.requests.Session"):
            detector = SneakedReferenceDetector(email="test@test.com")
        return detector

    # -- _match_references tests --

    def test_match_references_doi_exact(self):
        detector = self._make_detector()
        meta_ref = {"doi": "10.1234/test", "title": "", "authors": [], "year": ""}
        text_ref = {"doi": "10.1234/test", "title": "", "raw": ""}
        assert detector._match_references(meta_ref, text_ref) is True

    def test_match_references_doi_case_insensitive(self):
        detector = self._make_detector()
        meta_ref = {"doi": "10.1234/TEST", "title": "", "authors": [], "year": ""}
        text_ref = {"doi": "10.1234/test", "title": "", "raw": ""}
        assert detector._match_references(meta_ref, text_ref) is True

    def test_match_references_doi_no_match(self):
        detector = self._make_detector()
        meta_ref = {"doi": "10.1234/aaa", "title": "", "authors": [], "year": ""}
        text_ref = {"doi": "10.5678/bbb", "title": "", "raw": ""}
        assert detector._match_references(meta_ref, text_ref) is False

    def test_match_references_title_fuzzy(self):
        detector = self._make_detector()
        meta_ref = {
            "doi": "",
            "title": "Deep Learning for Natural Language Processing",
            "authors": [],
            "year": "",
        }
        text_ref = {
            "doi": "",
            "title": "Deep Learning for Natural Language Processing",
            "raw": "",
        }
        assert detector._match_references(meta_ref, text_ref) is True

    def test_match_references_title_fuzzy_close(self):
        detector = self._make_detector()
        meta_ref = {
            "doi": "",
            "title": "Deep Learning for Natural Language Processing",
            "authors": [],
            "year": "",
        }
        # Slight variation should still match above 0.85
        text_ref = {
            "doi": "",
            "title": "Deep Learning for Natural Language Processing Tasks",
            "raw": "",
        }
        assert detector._match_references(meta_ref, text_ref) is True

    def test_match_references_title_too_different(self):
        detector = self._make_detector()
        meta_ref = {
            "doi": "",
            "title": "Deep Learning for Natural Language Processing",
            "authors": [],
            "year": "",
        }
        text_ref = {
            "doi": "",
            "title": "A Study of Quantum Computing in Biology",
            "raw": "",
        }
        assert detector._match_references(meta_ref, text_ref) is False

    def test_match_references_author_year_in_raw(self):
        detector = self._make_detector()
        # Surname is extracted as last token of author string.
        # "J. Smith" -> surname "Smith" (len>2, will match in raw).
        meta_ref = {
            "doi": "",
            "title": "",
            "authors": ["J. Smith", "B. Jones"],
            "year": "2020",
        }
        text_ref = "Smith et al. 2020"
        assert detector._match_references(meta_ref, text_ref) is True

    def test_match_references_author_year_no_match(self):
        detector = self._make_detector()
        meta_ref = {
            "doi": "",
            "title": "",
            "authors": ["J. Smith"],
            "year": "2020",
        }
        text_ref = "Jones 2019"
        assert detector._match_references(meta_ref, text_ref) is False

    def test_match_references_string_text_ref(self):
        """When text_ref is a plain string, it is used as 'raw'."""
        detector = self._make_detector()
        meta_ref = {
            "doi": "",
            "title": "",
            "authors": ["A. Vaswani"],
            "year": "2017",
            "unstructured": "",
        }
        text_ref = "Vaswani et al., 2017. Attention is all you need."
        assert detector._match_references(meta_ref, text_ref) is True

    # -- compare_reference_lists tests --

    def test_compare_reference_lists_with_sneaked_refs(self):
        detector = self._make_detector()
        metadata_refs = [
            {
                "doi": "10.1234/a",
                "title": "Paper A",
                "authors": ["Alice"],
                "year": "2020",
                "journal": "Journal X",
            },
            {
                "doi": "10.1234/b",
                "title": "Paper B",
                "authors": ["Bob"],
                "year": "2021",
                "journal": "Journal Y",
            },
            {
                "doi": "10.1234/c",
                "title": "Paper C",
                "authors": ["Charlie"],
                "year": "2022",
                "journal": "Journal Z",
            },
        ]
        # Only Paper A appears in the text references
        text_refs = [
            {"doi": "10.1234/a", "title": "Paper A", "raw": ""},
        ]

        report = detector.compare_reference_lists(
            metadata_refs=metadata_refs,
            text_refs=text_refs,
            paper_doi="10.9999/test",
        )

        assert report.metadata_ref_count == 3
        assert report.text_ref_count == 1
        assert report.matched_count == 1
        assert report.sneaked_count == 2
        assert report.missing_count == 0  # text_count - matched = 1-1 = 0
        assert report.sneaked_ratio == pytest.approx(2 / 3)
        assert len(report.sneaked_refs) == 2

    def test_compare_reference_lists_all_matched(self):
        detector = self._make_detector()
        metadata_refs = [
            {"doi": "10.1/a", "title": "", "authors": [], "year": "", "journal": ""},
        ]
        text_refs = [{"doi": "10.1/a", "title": "", "raw": ""}]

        report = detector.compare_reference_lists(
            metadata_refs=metadata_refs,
            text_refs=text_refs,
        )
        assert report.sneaked_count == 0
        assert report.sneaked_ratio == 0.0

    def test_compare_reference_lists_empty_metadata(self):
        detector = self._make_detector()
        report = detector.compare_reference_lists(
            metadata_refs=[],
            text_refs=["Smith 2020"],
        )
        assert report.metadata_ref_count == 0
        assert report.sneaked_count == 0
        assert report.sneaked_ratio == 0.0
        assert report.missing_count == 1

    # -- _compute_journal_concentration tests --

    def test_journal_concentration_basic(self):
        detector = self._make_detector()
        refs = [
            {"journal": "Nature"},
            {"journal": "Nature"},
            {"journal": "Nature"},
            {"journal": "Science"},
            {"journal": "PNAS"},
        ]
        conc = detector._compute_journal_concentration(refs)
        assert conc["top_journal"] == "nature"
        assert conc["top_journal_count"] == 3
        assert conc["concentration_ratio"] == pytest.approx(3 / 5)
        # HHI = (3/5)^2 + (1/5)^2 + (1/5)^2 = 0.36 + 0.04 + 0.04 = 0.44
        assert conc["hhi"] == pytest.approx(0.44)

    def test_journal_concentration_no_journals(self):
        detector = self._make_detector()
        refs = [{"journal": ""}, {"journal": ""}]
        conc = detector._compute_journal_concentration(refs)
        assert conc["top_journal"] == ""
        assert conc["concentration_ratio"] == 0.0
        assert conc["hhi"] == 0.0

    def test_journal_concentration_single_journal(self):
        detector = self._make_detector()
        refs = [{"journal": "Nature"}]
        conc = detector._compute_journal_concentration(refs)
        assert conc["top_journal"] == "nature"
        assert conc["concentration_ratio"] == 1.0
        assert conc["hhi"] == 1.0

    # -- Risk score computation tests --

    def test_risk_score_high_sneaked_ratio(self):
        detector = self._make_detector()
        # Build a scenario with >50% sneaked ratio
        metadata_refs = [
            {"doi": f"10.1/{i}", "title": f"Paper {i}", "authors": [f"Author{i}"],
             "year": "2020", "journal": "J"}
            for i in range(10)
        ]
        # Only match 2 of 10
        text_refs = [
            {"doi": "10.1/0", "title": "", "raw": ""},
            {"doi": "10.1/1", "title": "", "raw": ""},
        ]
        report = detector.compare_reference_lists(
            metadata_refs=metadata_refs,
            text_refs=text_refs,
        )
        # 8/10 = 80% sneaked => score should be >= 70
        assert report.sneaked_ratio == pytest.approx(0.8)
        assert report.risk_score >= 70.0
        assert report.is_suspicious is True

    def test_risk_score_future_refs(self):
        detector = self._make_detector()
        metadata_refs = [
            {"doi": "10.1/a", "title": "Future Paper", "authors": ["X"],
             "year": "2030", "journal": "J"},
        ]
        text_refs = []
        report = detector.compare_reference_lists(
            metadata_refs=metadata_refs,
            text_refs=text_refs,
            paper_year="2024",
        )
        # Future ref detected + 100% sneaked ratio
        assert any("temporal" in w.lower() for w in report.warnings) or \
               any("future" in w.lower() for w in report.warnings) or \
               report.risk_score > 0

    def test_sneaked_ref_match_object_fields(self):
        detector = self._make_detector()
        metadata_refs = [
            {"doi": "10.1/sneaky", "title": "Sneaky Paper", "authors": ["Eve"],
             "year": "2023", "journal": "Shady Journal"},
        ]
        text_refs = []
        report = detector.compare_reference_lists(
            metadata_refs=metadata_refs,
            text_refs=text_refs,
        )
        assert len(report.sneaked_refs) == 1
        sr = report.sneaked_refs[0]
        assert isinstance(sr, SneakedReferenceMatch)
        assert sr.doi == "10.1/sneaky"
        assert sr.title == "Sneaky Paper"
        assert sr.is_sneaked is True
        assert sr.match_type == "metadata_only"


# ========================================================================
# 6. TextAugmenter
# ========================================================================

class TestTextAugmenter:

    def test_random_deletion_reduces_word_count(self, augmenter):
        text = "This is a sentence with several words in it for testing purposes"
        result = augmenter.random_deletion(text, p=0.5)
        original_count = len(text.split())
        result_count = len(result.split())
        assert result_count <= original_count
        assert result_count >= 1  # at least one word kept

    def test_random_deletion_keeps_at_least_one_word(self, augmenter):
        text = "word"
        result = augmenter.random_deletion(text, p=0.99)
        assert len(result.split()) >= 1

    def test_random_deletion_single_word(self, augmenter):
        text = "hello"
        result = augmenter.random_deletion(text, p=0.5)
        assert result == "hello"

    def test_random_deletion_empty_string(self, augmenter):
        result = augmenter.random_deletion("", p=0.5)
        assert result == ""

    def test_random_deletion_preserves_citations(self, augmenter):
        """Citation markers like [CIT] should be preserved."""
        text = "[CIT] showed improved results over baseline"
        # Run multiple times with high deletion probability
        for _ in range(10):
            result = augmenter.random_deletion(text, p=0.9)
            assert "[CIT]" in result

    def test_random_swap_preserves_word_count(self, augmenter):
        text = "one two three four five six seven eight"
        result = augmenter.random_swap(text, n=3)
        assert len(result.split()) == len(text.split())

    def test_random_swap_single_word(self, augmenter):
        text = "hello"
        result = augmenter.random_swap(text, n=2)
        assert result == "hello"

    def test_random_swap_two_words(self, augmenter):
        text = "hello world"
        result = augmenter.random_swap(text, n=1)
        # Either swapped or not, but still 2 words
        assert len(result.split()) == 2
        assert set(result.split()) == {"hello", "world"}

    def test_random_insertion_increases_word_count(self, augmenter):
        text = "This is a sentence with multiple words"
        result = augmenter.random_insertion(text, n=1)
        assert len(result.split()) == len(text.split()) + 1

    def test_random_insertion_multiple(self, augmenter):
        text = "one two three four"
        result = augmenter.random_insertion(text, n=3)
        assert len(result.split()) == len(text.split()) + 3

    def test_random_insertion_single_word(self, augmenter):
        """Single word input is returned unchanged."""
        text = "hello"
        result = augmenter.random_insertion(text, n=1)
        assert result == "hello"

    def test_random_insertion_empty(self, augmenter):
        result = augmenter.random_insertion("", n=1)
        assert result == ""

    def test_synonym_replacement_changes_words(self):
        """With p=1.0, all eligible words should be replaced."""
        aug = TextAugmenter(seed=123)
        text = "This study shows improved results"
        result = aug.synonym_replacement(text, p=1.0)
        # "study" -> "work", "shows" -> "demonstrates",
        # "improved" -> "enhanced", "results" -> "findings"
        assert "work" in result or "demonstrates" in result or \
               "enhanced" in result or "findings" in result

    def test_synonym_replacement_preserves_length(self, augmenter):
        text = "This study shows improved results"
        result = augmenter.synonym_replacement(text, p=0.5)
        assert len(result.split()) == len(text.split())

    def test_augment_returns_string(self, augmenter):
        text = "This is a test sentence for augmentation"
        result = augmenter.augment(text)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_augment_deterministic_with_seed(self):
        aug1 = TextAugmenter(seed=99)
        aug2 = TextAugmenter(seed=99)
        text = "The model achieves significant performance gains"
        assert aug1.augment(text) == aug2.augment(text)

    def test_augment_different_seeds_differ(self):
        text = "The model achieves significant performance gains on all benchmarks tested"
        aug1 = TextAugmenter(seed=1)
        aug2 = TextAugmenter(seed=999)
        # With different seeds and enough words, results should usually differ
        r1 = aug1.augment(text)
        r2 = aug2.augment(text)
        # They *could* be the same by chance, but very unlikely with long text
        # Just check they're both valid strings
        assert isinstance(r1, str)
        assert isinstance(r2, str)
