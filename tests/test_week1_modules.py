"""Tests for Week 1 modules: RetractionChecker, TorturedPhraseDetector,
BayesianRiskScorer, and enhanced graph anomaly detectors."""

import math
from unittest.mock import patch, MagicMock

import pytest

from verification.retraction_checker import (
    RetractionChecker, RetractionInfo, _format_date_parts,
)
from verification.tortured_phrases import (
    TorturedPhraseDetector, TorturedPhraseReport, TORTURED_PHRASES,
)
from scoring.bayesian import (
    BayesianRiskScorer, RiskTier, SIGNAL_DEFINITIONS, DOMAIN_PRIORS,
    BayesianRiskReport, SignalObservation,
)
from graph.anomaly import AnomalyDetector, AnomalyType, GraphAnomaly
from graph.builder import CitationGraph, CitationNode, CitationEdge


# ═══════════════════════════════════════════════════════════════════════════
# RetractionChecker tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatDateParts:
    def test_full_date(self):
        assert _format_date_parts([[2023, 5, 12]]) == "2023-05-12"

    def test_year_month(self):
        assert _format_date_parts([[2023, 5]]) == "2023-05"

    def test_year_only(self):
        assert _format_date_parts([[2023]]) == "2023"

    def test_none(self):
        assert _format_date_parts(None) == ""

    def test_empty_list(self):
        assert _format_date_parts([]) == ""

    def test_empty_inner_list(self):
        assert _format_date_parts([[]]) == ""

    def test_zero_padded(self):
        assert _format_date_parts([[2023, 1, 3]]) == "2023-01-03"


class TestParseRetraction:
    def test_update_to_retraction(self):
        """Crossref work with update-to retraction notice."""
        work = {
            "update-to": [
                {
                    "label": "Retraction",
                    "type": "retraction",
                    "DOI": "10.1234/retraction-notice",
                    "updated": {"date-parts": [[2024, 1, 15]]},
                }
            ]
        }
        info = RetractionChecker._parse_retraction("10.1234/original", work)
        assert info.is_retracted is True
        assert info.retraction_notice_doi == "10.1234/retraction-notice"
        assert info.retraction_date == "2024-01-15"
        assert info.retraction_reason == "Retraction"
        assert info.source == "crossref"

    def test_update_to_retraction_watch_source(self):
        """Retraction Watch label in update-to."""
        work = {
            "update-to": [
                {
                    "label": "Retraction Watch: data fabrication",
                    "type": "retraction",
                    "DOI": "10.1234/rw-notice",
                    "updated": {"date-parts": [[2024, 6, 1]]},
                }
            ]
        }
        info = RetractionChecker._parse_retraction("10.1234/test", work)
        assert info.is_retracted is True
        assert info.source == "retraction_watch"

    def test_relation_is_retracted_by(self):
        """Crossref work with relation.is-retracted-by."""
        work = {
            "relation": {
                "is-retracted-by": [
                    {"id-type": "doi", "id": "10.5678/retraction"}
                ]
            }
        }
        info = RetractionChecker._parse_retraction("10.1234/paper", work)
        assert info.is_retracted is True
        assert info.retraction_notice_doi == "10.5678/retraction"
        assert info.source == "crossref"

    def test_relation_is_replaced_by(self):
        """is-replaced-by also triggers retraction flag."""
        work = {
            "relation": {
                "is-replaced-by": [
                    {"id-type": "doi", "id": "10.5678/replacement"}
                ]
            }
        }
        info = RetractionChecker._parse_retraction("10.1234/paper", work)
        assert info.is_retracted is True

    def test_clean_paper_no_retraction(self):
        """Paper with no retraction signals."""
        work = {
            "title": ["A Normal Paper"],
            "author": [{"family": "Smith", "given": "John"}],
        }
        info = RetractionChecker._parse_retraction("10.1234/clean", work)
        assert info.is_retracted is False
        assert info.retraction_date == ""
        assert info.retraction_notice_doi == ""

    def test_empty_work(self):
        info = RetractionChecker._parse_retraction("10.1234/empty", {})
        assert info.is_retracted is False

    def test_non_retraction_update(self):
        """update-to with a non-retraction type (e.g. correction)."""
        work = {
            "update-to": [
                {
                    "label": "Correction",
                    "type": "correction",
                    "DOI": "10.1234/corrigendum",
                }
            ]
        }
        info = RetractionChecker._parse_retraction("10.1234/paper", work)
        assert info.is_retracted is False


class TestCheckReferenceNoDoi:
    def test_missing_doi_returns_none(self):
        checker = RetractionChecker(email="test@example.com", timeout=5)
        result = checker.check_reference({"title": "Some Paper"})
        assert result is None

    def test_empty_doi_returns_none(self):
        checker = RetractionChecker(email="test@example.com", timeout=5)
        result = checker.check_reference({"title": "Paper", "doi": ""})
        assert result is None

    def test_whitespace_doi_returns_none(self):
        checker = RetractionChecker(email="test@example.com", timeout=5)
        result = checker.check_reference({"doi": "   "})
        assert result is None


class TestCheckDoiEmpty:
    def test_empty_string(self):
        checker = RetractionChecker(email="test@example.com", timeout=5)
        assert checker.check_doi("") is None

    def test_whitespace(self):
        checker = RetractionChecker(email="test@example.com", timeout=5)
        assert checker.check_doi("   ") is None


class TestRetractionCheckerCaching:
    @patch("verification.retraction_checker.requests.Session")
    def test_caching_avoids_duplicate_requests(self, mock_session_cls):
        """Second call for same DOI should use cache, not HTTP."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {
                "DOI": "10.1234/cached",
                "title": ["Test"],
            }
        }
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        checker = RetractionChecker(email="test@example.com", timeout=5)
        checker._session = mock_session

        result1 = checker.check_doi("10.1234/cached")
        result2 = checker.check_doi("10.1234/cached")

        assert result1 is not None
        assert result2 is not None
        assert result1.doi == result2.doi
        # Only one HTTP call should have been made
        assert mock_session.get.call_count == 1

    @patch("verification.retraction_checker.requests.Session")
    def test_api_error_returns_none(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Network error")
        mock_session_cls.return_value = mock_session

        checker = RetractionChecker(email="test@example.com", timeout=5)
        checker._session = mock_session

        result = checker.check_doi("10.1234/failing")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# TorturedPhraseDetector tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTorturedPhraseScanClean:
    def test_clean_text_no_matches(self):
        detector = TorturedPhraseDetector()
        text = (
            "This paper presents a deep learning approach using "
            "random forest classifiers and neural network architectures "
            "for breast cancer detection via systematic review."
        )
        report = detector.scan(text)
        assert report.match_count == 0
        assert report.tortured_score == 0.0
        assert report.is_suspicious is False

    def test_empty_text(self):
        detector = TorturedPhraseDetector()
        report = detector.scan("")
        assert report.match_count == 0
        assert report.text_length == 0


class TestTorturedPhraseScanMatches:
    def test_single_tortured_phrase(self):
        detector = TorturedPhraseDetector()
        text = "We applied profound learning to classify images."
        report = detector.scan(text)
        assert report.match_count == 1
        assert report.unique_phrases == 1
        assert report.matches[0].standard_term == "deep learning"

    def test_multiple_tortured_phrases(self):
        detector = TorturedPhraseDetector()
        text = (
            "We used arbitrary forest and profound learning for "
            "bosom malignancy detection."
        )
        report = detector.scan(text)
        assert report.match_count == 3
        assert report.unique_phrases == 3

    def test_case_insensitive(self):
        detector = TorturedPhraseDetector()
        text = "PROFOUND LEARNING is used for classification."
        report = detector.scan(text)
        assert report.match_count == 1


class TestTorturedPhraseOverlap:
    def test_longer_phrase_wins(self):
        """When a shorter tortured phrase is a substring of a longer one,
        the longer match should take precedence and the shorter should
        not create a second match at the same position."""
        detector = TorturedPhraseDetector()
        # "stochastic slope descent" should match as one phrase,
        # not also match "slope descent" separately.
        text = "We optimize using stochastic slope descent."
        report = detector.scan(text)
        # Should have exactly 1 match (the longer phrase)
        assert report.match_count == 1
        assert "stochastic" in report.matches[0].tortured_phrase.lower()


class TestTorturedPhraseScoring:
    def test_zero_matches_score_zero(self):
        score = TorturedPhraseDetector._compute_score(0, 0, 1000)
        assert score == 0.0

    def test_one_match_score_30(self):
        score = TorturedPhraseDetector._compute_score(1, 1, 1000)
        assert score == 30.0

    def test_two_matches_score_55(self):
        # 2 matches, 1 unique -> base 55, no diversity bonus (unique < 2 check)
        score = TorturedPhraseDetector._compute_score(2, 1, 1000)
        assert score == 55.0

    def test_two_matches_two_unique(self):
        # 2 matches, 2 unique -> base 55 + diversity bonus
        score = TorturedPhraseDetector._compute_score(2, 2, 1000)
        assert score > 55.0

    def test_three_matches_score_70(self):
        score = TorturedPhraseDetector._compute_score(3, 1, 1000)
        assert score == 70.0

    def test_six_plus_matches(self):
        score = TorturedPhraseDetector._compute_score(8, 4, 5000)
        assert score > 90.0
        assert score <= 100.0


class TestTorturedPhraseScanAbstract:
    def test_abstract_boost(self):
        detector = TorturedPhraseDetector()
        text = "We applied profound learning to classify images."
        report_body = detector.scan(text)
        report_abstract = detector.scan_abstract(text)
        # Abstract should have 1.2x boosted score
        assert report_abstract.tortured_score == pytest.approx(
            report_body.tortured_score * 1.2, abs=0.1
        )

    def test_abstract_no_match_no_boost(self):
        detector = TorturedPhraseDetector()
        report = detector.scan_abstract("Normal deep learning text.")
        assert report.tortured_score == 0.0


class TestTorturedPhraseIsSuspicious:
    def test_below_threshold(self):
        detector = TorturedPhraseDetector()
        # 1 match -> score 30, below 40 threshold
        text = "We used profound learning for classification."
        report = detector.scan(text)
        assert report.tortured_score == 30.0
        assert report.is_suspicious is False

    def test_above_threshold(self):
        detector = TorturedPhraseDetector()
        # 2 matches -> score >= 55, above 40 threshold
        text = "We used profound learning and arbitrary forest."
        report = detector.scan(text)
        assert report.tortured_score >= 40.0
        assert report.is_suspicious is True


class TestTorturedPhraseCustomPhrases:
    def test_extra_phrases(self):
        detector = TorturedPhraseDetector(
            extra_phrases={"wobble analysis": "regression analysis"}
        )
        text = "We performed wobble analysis on the data."
        report = detector.scan(text)
        assert report.match_count == 1
        assert report.matches[0].standard_term == "regression analysis"

    def test_extra_phrases_adds_to_count(self):
        base = TorturedPhraseDetector()
        extended = TorturedPhraseDetector(
            extra_phrases={"xyzzy plugh": "test term"}
        )
        assert extended.phrase_count >= base.phrase_count + 1


class TestTorturedPhrasePhraseCount:
    def test_phrase_count_positive(self):
        detector = TorturedPhraseDetector()
        assert detector.phrase_count > 100  # We have many phrases defined
        assert detector.phrase_count == len(detector._phrases)


# ═══════════════════════════════════════════════════════════════════════════
# BayesianRiskScorer tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBayesianNoObservations:
    def test_returns_prior(self):
        scorer = BayesianRiskScorer(domain="default")
        report = scorer.compute()
        assert report.posterior_probability == pytest.approx(
            DOMAIN_PRIORS["default"], abs=0.001
        )
        assert report.signals_fired == 0
        assert report.total_signals == 0
        assert len(report.warnings) >= 1  # "No signals observed" warning


class TestBayesianPositiveSignal:
    def test_single_positive_raises_posterior(self):
        scorer = BayesianRiskScorer(domain="default")
        prior = DOMAIN_PRIORS["default"]
        scorer.observe("phantom_doi", fired=True)
        report = scorer.compute()
        assert report.posterior_probability > prior

    def test_strong_positive_signal(self):
        scorer = BayesianRiskScorer(domain="computer_science")
        scorer.observe("grim_test_failure", fired=True)
        report = scorer.compute()
        # GRIM has LR=50, should push posterior way up from 0.02
        assert report.posterior_probability > 0.3


class TestBayesianNegativeSignal:
    def test_single_negative_lowers_posterior(self):
        scorer = BayesianRiskScorer(domain="default")
        prior = DOMAIN_PRIORS["default"]
        scorer.observe("phantom_doi", fired=False)
        report = scorer.compute()
        assert report.posterior_probability < prior


class TestBayesianConfidenceInterpolation:
    def test_zero_confidence_neutral(self):
        """confidence=0 should make the signal have no effect (LR=1)."""
        scorer = BayesianRiskScorer(domain="default")
        prior = DOMAIN_PRIORS["default"]
        scorer.observe("phantom_doi", fired=True, confidence=0.0)
        report = scorer.compute()
        assert report.posterior_probability == pytest.approx(prior, abs=0.001)

    def test_partial_confidence_less_effect(self):
        scorer_full = BayesianRiskScorer(domain="default")
        scorer_full.observe("phantom_doi", fired=True, confidence=1.0)
        report_full = scorer_full.compute()

        scorer_half = BayesianRiskScorer(domain="default")
        scorer_half.observe("phantom_doi", fired=True, confidence=0.5)
        report_half = scorer_half.compute()

        prior = DOMAIN_PRIORS["default"]
        # Half confidence should produce a posterior between prior and full
        assert prior < report_half.posterior_probability < report_full.posterior_probability


class TestBayesianRiskTiers:
    def test_low_tier(self):
        assert BayesianRiskScorer._assign_tier(0.01) == RiskTier.LOW
        assert BayesianRiskScorer._assign_tier(0.04) == RiskTier.LOW

    def test_elevated_tier(self):
        assert BayesianRiskScorer._assign_tier(0.05) == RiskTier.ELEVATED
        assert BayesianRiskScorer._assign_tier(0.15) == RiskTier.ELEVATED

    def test_high_tier(self):
        assert BayesianRiskScorer._assign_tier(0.20) == RiskTier.HIGH
        assert BayesianRiskScorer._assign_tier(0.40) == RiskTier.HIGH

    def test_critical_tier(self):
        assert BayesianRiskScorer._assign_tier(0.50) == RiskTier.CRITICAL
        assert BayesianRiskScorer._assign_tier(0.99) == RiskTier.CRITICAL

    def test_boundary_exactly_005(self):
        assert BayesianRiskScorer._assign_tier(0.05) == RiskTier.ELEVATED

    def test_boundary_exactly_020(self):
        assert BayesianRiskScorer._assign_tier(0.20) == RiskTier.HIGH

    def test_boundary_exactly_050(self):
        assert BayesianRiskScorer._assign_tier(0.50) == RiskTier.CRITICAL


class TestBayesianDomainPriors:
    def test_cancer_research_higher_than_cs(self):
        assert DOMAIN_PRIORS["cancer_research"] > DOMAIN_PRIORS["computer_science"]

    def test_domain_affects_posterior(self):
        scorer_cancer = BayesianRiskScorer(domain="cancer_research")
        scorer_cs = BayesianRiskScorer(domain="computer_science")

        # Same signals, different domains
        for scorer in [scorer_cancer, scorer_cs]:
            scorer.observe("metadata_mismatch", fired=True)

        report_cancer = scorer_cancer.compute()
        report_cs = scorer_cs.compute()

        # Cancer research has higher prior, so posterior should be higher
        assert report_cancer.posterior_probability > report_cs.posterior_probability


class TestBayesianUnknownSignal:
    def test_unknown_signal_raises(self):
        scorer = BayesianRiskScorer(domain="default")
        with pytest.raises(ValueError, match="Unknown signal"):
            scorer.observe("totally_fake_signal", fired=True)


class TestBayesianReset:
    def test_reset_clears_observations(self):
        scorer = BayesianRiskScorer(domain="default")
        scorer.observe("phantom_doi", fired=True)
        scorer.observe("tortured_phrases", fired=True)
        assert len(scorer._observations) == 2

        scorer.reset()
        assert len(scorer._observations) == 0

        report = scorer.compute()
        assert report.posterior_probability == pytest.approx(
            DOMAIN_PRIORS["default"], abs=0.001
        )


class TestBayesianSummary:
    def test_summary_keys(self):
        scorer = BayesianRiskScorer(domain="biomedical")
        scorer.observe("phantom_doi", fired=True)
        report = scorer.compute()
        summary = report.summary()

        expected_keys = {
            "prior", "posterior", "risk_tier", "domain",
            "signals_fired", "total_signals", "equivalent_score", "warnings",
        }
        assert set(summary.keys()) == expected_keys
        assert summary["domain"] == "biomedical"
        assert summary["signals_fired"] == 1
        assert summary["total_signals"] == 1


class TestBayesianOverflowProtection:
    def test_many_positive_signals_capped(self):
        """Observing many strong positive signals should not cause overflow."""
        scorer = BayesianRiskScorer(domain="cancer_research")
        # Observe many strong positive signals
        strong_signals = [
            "phantom_doi", "grim_test_failure", "tortured_phrases",
            "reference_not_found", "citation_ring_detected",
            "temporal_anomaly", "retracted_citation",
        ]
        for sig in strong_signals:
            scorer.observe(sig, fired=True)

        report = scorer.compute()
        # Should be very high but not crash
        assert report.posterior_probability <= 1.0
        assert report.risk_tier == RiskTier.CRITICAL

    def test_many_negative_signals_bounded(self):
        """Many negative signals should not cause underflow."""
        scorer = BayesianRiskScorer(domain="cancer_research")
        for sig_name in list(SIGNAL_DEFINITIONS.keys()):
            scorer.observe(sig_name, fired=False)

        report = scorer.compute()
        assert report.posterior_probability >= 0.0
        assert report.posterior_probability < DOMAIN_PRIORS["cancer_research"]


# ═══════════════════════════════════════════════════════════════════════════
# Graph anomaly detector tests
# ═══════════════════════════════════════════════════════════════════════════

def _make_graph_with_benford_compliant_data() -> CitationGraph:
    """Build a graph where citation counts follow Benford's law."""
    graph = CitationGraph()
    # Benford-compliant: P(d) = log10(1 + 1/d)
    # For 100 nodes: ~30 start with 1, ~18 with 2, ~12 with 3, etc.
    benford_counts = (
        [1] * 5 + [10] * 5 + [15] * 5 + [100] * 5 + [12] * 5 + [13] * 5 +
        [2] * 3 + [20] * 3 + [25] * 3 + [200] * 3 + [22] * 3 + [23] * 3 +
        [3] * 2 + [30] * 2 + [35] * 2 + [300] * 2 + [32] * 2 + [33] * 2 +
        [4] * 2 + [40] * 2 +
        [5] * 1 + [50] * 1 +
        [6] * 1 + [60] * 1 +
        [7] * 1 + [70] * 1 +
        [8] * 1 + [80] * 1 +
        [9] * 1 + [90] * 1
    )
    for i, count in enumerate(benford_counts):
        graph.add_node(CitationNode(
            ice_id=f"benford_{i}",
            title=f"Paper {i}",
            citation_count=count,
        ))
    return graph


def _make_graph_with_uniform_data() -> CitationGraph:
    """Build a graph where citation counts have uniform first digits (violates Benford)."""
    graph = CitationGraph()
    # Uniform: equal count of each first digit -> violates Benford
    counts = []
    for digit in range(1, 10):
        counts.extend([digit * 10 + j for j in range(5)])
    for i, count in enumerate(counts):
        graph.add_node(CitationNode(
            ice_id=f"uniform_{i}",
            title=f"Paper {i}",
            citation_count=count,
        ))
    return graph


class TestBenfordViolation:
    def test_benford_compliant_no_anomaly(self):
        graph = _make_graph_with_benford_compliant_data()
        detector = AnomalyDetector(graph)
        anomalies = detector.detect_benford_violation()
        # Should not flag Benford-compliant data
        assert len(anomalies) == 0

    def test_uniform_distribution_violation(self):
        graph = _make_graph_with_uniform_data()
        detector = AnomalyDetector(graph)
        anomalies = detector.detect_benford_violation()
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == AnomalyType.BENFORD_VIOLATION
        assert anomalies[0].details["chi_squared"] > 15.507

    def test_too_few_nodes_skipped(self):
        graph = CitationGraph()
        for i in range(5):
            graph.add_node(CitationNode(
                ice_id=f"small_{i}", title=f"P{i}", citation_count=10,
            ))
        detector = AnomalyDetector(graph)
        assert detector.detect_benford_violation() == []


class TestReciprocalCitations:
    def test_bidirectional_pattern(self):
        """Authors A and B cite each other repeatedly."""
        graph = CitationGraph()
        # Create papers by two authors who cite each other
        for i in range(5):
            graph.add_node(CitationNode(
                ice_id=f"alice_{i}", title=f"Alice Paper {i}",
                authors=["Alice Smith"],
            ))
            graph.add_node(CitationNode(
                ice_id=f"bob_{i}", title=f"Bob Paper {i}",
                authors=["Bob Jones"],
            ))

        # Alice cites Bob 4 times
        for i in range(4):
            graph.add_edge(CitationEdge(
                source_id=f"alice_{i}", target_id=f"bob_{i}",
            ))
        # Bob cites Alice 3 times
        for i in range(3):
            graph.add_edge(CitationEdge(
                source_id=f"bob_{i}", target_id=f"alice_{i}",
            ))

        detector = AnomalyDetector(graph)
        anomalies = detector.detect_reciprocal_citations()
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == AnomalyType.RECIPROCAL_CITATION
        assert anomalies[0].details["a_to_b"] + anomalies[0].details["b_to_a"] == 7

    def test_no_reciprocal_one_direction(self):
        """Only one direction should not trigger."""
        graph = CitationGraph()
        for i in range(5):
            graph.add_node(CitationNode(
                ice_id=f"alice_{i}", title=f"Alice Paper {i}",
                authors=["Alice Smith"],
            ))
            graph.add_node(CitationNode(
                ice_id=f"bob_{i}", title=f"Bob Paper {i}",
                authors=["Bob Jones"],
            ))
            graph.add_edge(CitationEdge(
                source_id=f"alice_{i}", target_id=f"bob_{i}",
            ))

        detector = AnomalyDetector(graph)
        anomalies = detector.detect_reciprocal_citations()
        assert len(anomalies) == 0


class TestCitationBurst:
    def test_concentrated_sources(self):
        """Many citations from a few repeated sources should trigger burst."""
        graph = CitationGraph()

        target = CitationNode(
            ice_id="target_paper", title="Target Paper",
            authors=["Target Author"],
        )
        graph.add_node(target)

        # Create 8 citing papers, but reuse only 3 source IDs so that
        # get_citations returns repeated entries (concentration > 50%).
        # We add 3 source nodes and have each cite the target multiple
        # times via duplicate edges.
        for src_idx in range(3):
            src_id = f"src_{src_idx}"
            graph.add_node(CitationNode(
                ice_id=src_id, title=f"Source Paper {src_idx}",
                authors=[f"Author{src_idx}"],
            ))
            # Each source cites the target 3 times
            for _ in range(3):
                graph.add_edge(CitationEdge(
                    source_id=src_id, target_id="target_paper",
                ))

        detector = AnomalyDetector(graph)
        anomalies = detector.detect_citation_burst()
        # Should detect concentration: 12 citations from 3 sources (>50% from top 3)
        burst_anomalies = [
            a for a in anomalies if a.anomaly_type == AnomalyType.CITATION_BURST
        ]
        assert len(burst_anomalies) >= 1

    def test_no_burst_diverse_sources(self):
        """Many diverse sources should not trigger."""
        graph = CitationGraph()
        target = CitationNode(
            ice_id="target", title="Popular Paper",
            authors=["Author"],
        )
        graph.add_node(target)

        # 20 unique sources, each citing once
        for i in range(20):
            src_id = f"src_{i}"
            graph.add_node(CitationNode(
                ice_id=src_id, title=f"Paper {i}",
                authors=[f"Author{i}"],
            ))
            graph.add_edge(CitationEdge(
                source_id=src_id, target_id="target",
            ))

        detector = AnomalyDetector(graph)
        anomalies = detector.detect_citation_burst()
        burst = [a for a in anomalies if a.anomaly_type == AnomalyType.CITATION_BURST]
        assert len(burst) == 0


class TestRunAllIncludes:
    def test_run_all_includes_new_detector_types(self):
        """run_all should invoke Benford, reciprocal, and burst detectors."""
        graph = CitationGraph()
        # Minimal graph - just verify run_all doesn't crash and returns a list
        graph.add_node(CitationNode(ice_id="p1", title="Paper 1"))
        graph.add_node(CitationNode(ice_id="p2", title="Paper 2"))
        graph.add_edge(CitationEdge(source_id="p1", target_id="p2"))

        detector = AnomalyDetector(graph)
        anomalies = detector.run_all()
        assert isinstance(anomalies, list)

    def test_run_all_with_violations(self):
        """run_all should find anomalies across all detector types."""
        graph = _make_graph_with_uniform_data()
        detector = AnomalyDetector(graph)
        anomalies = detector.run_all()
        types_found = {a.anomaly_type for a in anomalies}
        # At minimum, Benford violation should be detected
        assert AnomalyType.BENFORD_VIOLATION in types_found
