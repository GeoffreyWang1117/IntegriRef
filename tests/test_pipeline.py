"""Tests for the unified IntegriRef pipeline (L0-L4 orchestrator)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field

from core.pipeline import (
    IntegriRefPipeline,
    PipelineReport,
    SignalExtractor,
)
from scoring.bayesian import (
    BayesianRiskScorer,
    SignalObservation,
    RiskTier,
    SIGNAL_DEFINITIONS,
)
from semantic.intent_classifier import IntentResult, CitationIntent
from semantic.nli_verifier import NLIResult, AlignmentLabel
from verification.engine import ReferenceResult, SourceMatch, FieldCheck


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_l0_result(found=True, overall="OK", retracted=False,
                    hallucination_score=0, match_score=90):
    """Build a minimal ReferenceResult for testing."""
    result = ReferenceResult(key="test_ref", title="Test Paper")
    result.overall = overall
    result.is_retracted = retracted
    if found:
        result.sources_tried = ["crossref", "openalex"]
        result.sources_hit = ["crossref"]
        mock_match = MagicMock()
        mock_match.match_score = match_score
        mock_match.is_confirmed = True
        mock_match.is_retracted = retracted
        mock_match.registry = "crossref"
        mock_match.entity = MagicMock()
        mock_match.entity.title = "Test Paper"
        mock_match.entity.authors = ["Author A"]
        mock_match.entity.year = "2024"
        mock_match.found = True
        mock_match.found_by_id = True
        result.source_matches = [mock_match]
    else:
        result.sources_tried = ["crossref", "openalex"]
        result.sources_hit = []
    # Hallucination
    if hallucination_score > 0:
        hall = MagicMock()
        hall.hallucination_score = hallucination_score
        result.hallucination = hall
    result.needs_l2_escalation = not found or overall != "OK"
    return result


def _make_nli_result(label=AlignmentLabel.SUPPORTED, confidence=0.9,
                     ent=0.9, con=0.02, neu=0.08):
    return NLIResult(
        label=label, confidence=confidence,
        entailment_score=ent, contradiction_score=con,
        neutral_score=neu, claim="test claim", premise="test abstract",
    )


def _make_intent_result(intent=CitationIntent.SUPPORTING, confidence=0.85):
    return IntentResult(
        citation_key="test_ref", intent=intent, confidence=confidence,
        citing_sentence="Following the approach of Author (2024)...",
    )


# ── SignalExtractor tests ─────────────────────────────────────────────────


class TestSignalExtractorL0:
    """Test signal extraction from L0 results."""

    def test_found_reference(self):
        result = _make_l0_result(found=True)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert "reference_not_found" in names
        assert names["reference_not_found"].fired is False

    def test_not_found_reference(self):
        result = _make_l0_result(found=False)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert names["reference_not_found"].fired is True

    def test_retracted_citation(self):
        result = _make_l0_result(retracted=True)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert names["retracted_citation"].fired is True
        assert names["retracted_citation"].confidence == 1.0

    def test_not_retracted(self):
        result = _make_l0_result(retracted=False)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert names["retracted_citation"].fired is False

    def test_phantom_doi_high_score(self):
        result = _make_l0_result(hallucination_score=75)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert names["phantom_doi"].fired is True
        assert names["phantom_doi"].confidence == 0.75

    def test_phantom_doi_low_score(self):
        result = _make_l0_result(hallucination_score=20)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert names["phantom_doi"].fired is False

    def test_metadata_mismatch_low_score(self):
        result = _make_l0_result(match_score=50)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert names["metadata_mismatch"].fired is True

    def test_metadata_match_high_score(self):
        result = _make_l0_result(match_score=95)
        signals = SignalExtractor.from_l0(result)
        names = {s.signal_name: s for s in signals}
        assert names["metadata_mismatch"].fired is False


class TestSignalExtractorL1:
    """Test signal extraction from L1 intent results."""

    def test_contrasting_fires_misrepresent(self):
        result = _make_intent_result(intent=CitationIntent.CONTRASTING)
        signals = SignalExtractor.from_l1(result)
        names = {s.signal_name: s for s in signals}
        assert names["citation_misrepresents_source"].fired is True

    def test_supporting_no_misrepresent(self):
        result = _make_intent_result(intent=CitationIntent.SUPPORTING)
        signals = SignalExtractor.from_l1(result)
        names = {s.signal_name: s for s in signals}
        assert names["citation_misrepresents_source"].fired is False

    def test_mentioning_no_misrepresent(self):
        result = _make_intent_result(intent=CitationIntent.MENTIONING)
        signals = SignalExtractor.from_l1(result)
        names = {s.signal_name: s for s in signals}
        assert names["citation_misrepresents_source"].fired is False


class TestSignalExtractorL2:
    """Test signal extraction from L2 NLI results."""

    def test_contradicted_fires(self):
        result = _make_nli_result(label=AlignmentLabel.CONTRADICTED, con=0.8)
        signals = SignalExtractor.from_l2(result)
        names = {s.signal_name: s for s in signals}
        assert names["claim_contradicted"].fired is True
        assert names["claim_unsupported"].fired is False

    def test_unsupported_fires(self):
        result = _make_nli_result(label=AlignmentLabel.UNSUPPORTED, ent=0.1)
        signals = SignalExtractor.from_l2(result)
        names = {s.signal_name: s for s in signals}
        assert names["claim_contradicted"].fired is False
        assert names["claim_unsupported"].fired is True

    def test_supported_nothing_fires(self):
        result = _make_nli_result(label=AlignmentLabel.SUPPORTED)
        signals = SignalExtractor.from_l2(result)
        names = {s.signal_name: s for s in signals}
        assert names["claim_contradicted"].fired is False
        assert names["claim_unsupported"].fired is False


class TestSignalExtractorL3:
    """Test signal extraction from L3 anomaly results."""

    def test_no_anomalies(self):
        signals = SignalExtractor.from_l3([])
        # Should still produce signals (all not-fired)
        names = {s.signal_name for s in signals}
        assert "citation_ring_detected" in names
        assert all(not s.fired for s in signals)

    def test_with_anomalies(self):
        anomaly = MagicMock()
        anomaly.anomaly_type = MagicMock()
        anomaly.anomaly_type.value = "SELF_CITATION_RING"
        anomaly.severity = 0.8
        anomaly.description = "test ring"

        signals = SignalExtractor.from_l3([anomaly])
        names = {s.signal_name: s for s in signals}
        assert names["citation_ring_detected"].fired is True
        assert names["citation_ring_detected"].confidence == 0.8


# ── Pipeline tests ────────────────────────────────────────────────────────


class TestPipelineBasic:
    """Test IntegriRefPipeline with mocked components."""

    def _make_pipeline(self, layers=None):
        discovery = MagicMock()
        pipeline = IntegriRefPipeline(
            discovery=discovery,
            layers=layers or ["L0", "L4"],
            domain="default",
            enable_l2_only_on_escalation=False,
        )
        return pipeline

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l0_only(self, mock_l3):
        pipeline = self._make_pipeline(layers=["L0"])
        # Mock the engine
        mock_result = _make_l0_result(found=True)
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        report = pipeline.verify({"title": "Test", "doi": "10.1234/test"})

        assert report.l0 is not None
        assert report.l0.overall == "OK"
        assert "L0" in report.layers_run
        assert "L4" not in report.layers_run

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l0_l4_found(self, mock_l3):
        pipeline = self._make_pipeline(layers=["L0", "L4"])
        mock_result = _make_l0_result(found=True)
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        report = pipeline.verify({"title": "Test", "doi": "10.1234/test"})

        assert "L0" in report.layers_run
        assert "L4" in report.layers_run
        assert report.l4 is not None
        assert report.risk_tier == RiskTier.LOW.value  # found, no issues

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l0_l4_not_found(self, mock_l3):
        pipeline = self._make_pipeline(layers=["L0", "L4"])
        mock_result = _make_l0_result(found=False)
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        report = pipeline.verify({"title": "Fake Paper"})

        assert report.l4 is not None
        # reference_not_found has LR=15, prior=3% → should push risk up
        assert report.risk_probability > 0.05
        assert report.risk_tier in (RiskTier.ELEVATED.value,
                                     RiskTier.HIGH.value,
                                     RiskTier.CRITICAL.value)

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l0_l4_retracted(self, mock_l3):
        pipeline = self._make_pipeline(layers=["L0", "L4"])
        mock_result = _make_l0_result(found=True, retracted=True)
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        report = pipeline.verify({"title": "Retracted", "doi": "10.x/y"})

        # retracted_citation signal should fire
        fired = [s.signal_name for s in report.signals if s.fired]
        assert "retracted_citation" in fired

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l1_intent(self, mock_l3):
        pipeline = self._make_pipeline(layers=["L0", "L1", "L4"])
        mock_result = _make_l0_result(found=True)
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        mock_intent = _make_intent_result(intent=CitationIntent.SUPPORTING)
        pipeline._intent_classifier = MagicMock()
        pipeline._intent_classifier.classify.return_value = mock_intent

        report = pipeline.verify(
            {"title": "Test"},
            citing_sentence="Following Author (2024)...",
        )

        assert "L1" in report.layers_run
        assert report.l1 is not None
        assert report.l1.intent == CitationIntent.SUPPORTING

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l2_with_abstract(self, mock_l3):
        pipeline = self._make_pipeline(layers=["L0", "L2", "L4"])
        mock_result = _make_l0_result(found=True)
        mock_result.needs_l2_escalation = True  # Force escalation
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        mock_nli = _make_nli_result(label=AlignmentLabel.SUPPORTED)
        pipeline._nli_verifier = MagicMock()
        pipeline._nli_verifier.verify.return_value = mock_nli
        pipeline._nli_loaded = True

        report = pipeline.verify(
            {"title": "Test"},
            abstract="Some abstract text.",
            citing_sentence="The claim text.",
        )

        assert "L2" in report.layers_run
        assert report.l2 is not None
        assert report.l2.label == AlignmentLabel.SUPPORTED

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l2_skipped_no_escalation(self, mock_l3):
        """L2 skipped when L0 does not escalate and escalation_only=True."""
        discovery = MagicMock()
        pipeline = IntegriRefPipeline(
            discovery=discovery,
            layers=["L0", "L2", "L4"],
            enable_l2_only_on_escalation=True,
        )
        mock_result = _make_l0_result(found=True)
        mock_result.needs_l2_escalation = False
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        report = pipeline.verify(
            {"title": "Test"},
            abstract="Some abstract.",
        )

        assert "L2" not in report.layers_run
        assert report.l2 is None


class TestPipelineReport:
    """Test PipelineReport output methods."""

    def test_summary(self):
        report = PipelineReport(
            reference={"title": "Test Paper Title"},
            risk_tier="LOW",
            risk_probability=0.02,
            layers_run=["L0", "L4"],
            processing_time_ms=150.5,
        )
        s = report.summary()
        assert s["risk_tier"] == "LOW"
        assert s["risk_probability"] == 0.02
        assert "L0" in s["layers_run"]

    def test_api_response(self):
        report = PipelineReport(
            reference={"title": "Test"},
            risk_tier="ELEVATED",
            risk_probability=0.12,
            layers_run=["L0"],
        )
        resp = report.api_response()
        assert resp["status"] == "success"
        assert resp["risk_tier"] == "ELEVATED"


class TestPipelineAllSignals:
    """Test that all signal names produced are valid Bayesian signal names."""

    def test_l0_signals_valid(self):
        result = _make_l0_result(found=False, retracted=True,
                                 hallucination_score=60, match_score=50)
        signals = SignalExtractor.from_l0(result)
        for s in signals:
            assert s.signal_name in SIGNAL_DEFINITIONS, \
                f"Unknown signal: {s.signal_name}"

    def test_l1_signals_valid(self):
        result = _make_intent_result(intent=CitationIntent.CONTRASTING)
        signals = SignalExtractor.from_l1(result)
        for s in signals:
            assert s.signal_name in SIGNAL_DEFINITIONS

    def test_l2_signals_valid(self):
        result = _make_nli_result(label=AlignmentLabel.CONTRADICTED)
        signals = SignalExtractor.from_l2(result)
        for s in signals:
            assert s.signal_name in SIGNAL_DEFINITIONS

    def test_l3_signals_valid(self):
        signals = SignalExtractor.from_l3([])
        for s in signals:
            assert s.signal_name in SIGNAL_DEFINITIONS


class TestPipelineBatch:
    """Test batch verification."""

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_batch_basic(self, mock_l3):
        discovery = MagicMock()
        pipeline = IntegriRefPipeline(
            discovery=discovery,
            layers=["L0", "L4"],
        )
        mock_result = _make_l0_result(found=True)
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        refs = [
            {"title": "Paper 1", "doi": "10.1/a"},
            {"title": "Paper 2", "doi": "10.1/b"},
            {"title": "Paper 3", "doi": "10.1/c"},
        ]
        reports = pipeline.verify_batch(refs)
        assert len(reports) == 3
        assert all(r.l0 is not None for r in reports)

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_batch_all_mentioning(self, mock_l3):
        """When all citations are mentioning, add bonus signal."""
        discovery = MagicMock()
        pipeline = IntegriRefPipeline(
            discovery=discovery,
            layers=["L0", "L1", "L4"],
            enable_l2_only_on_escalation=False,
        )
        mock_result = _make_l0_result(found=True)
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = mock_result

        mock_intent = _make_intent_result(intent=CitationIntent.MENTIONING)
        pipeline._intent_classifier = MagicMock()
        pipeline._intent_classifier.classify.return_value = mock_intent

        refs = [{"title": f"P{i}"} for i in range(5)]
        sentences = [f"See Author{i} (2024)" for i in range(5)]
        reports = pipeline.verify_batch(refs, citing_sentences=sentences)

        # all_citations_mentioning should be added
        for r in reports:
            sig_names = [s.signal_name for s in r.signals]
            assert "all_citations_mentioning" in sig_names
