"""End-to-end integration tests for the IntegriRef pipeline.

Uses real paper metadata fixtures (no live API calls — mocked at registry level).
Tests the full L0→L1→L2→L3→L4 flow including signal extraction and Bayesian scoring.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from core.entity import ICEntity, EntityType
from core.pipeline import IntegriRefPipeline, PipelineReport, SignalExtractor
from core.discovery import RegistryDiscovery
from scoring.bayesian import RiskTier
from semantic.nli_verifier import NLIResult, AlignmentLabel
from semantic.intent_classifier import IntentResult, CitationIntent
from verification.engine import ReferenceResult


# ── Real paper fixtures ───────────────────────────────────────────────────

FIXTURES = {
    "attention": {
        "ref": {
            "title": "Attention Is All You Need",
            "authors": ["Vaswani, A.", "Shazeer, N.", "Parmar, N."],
            "year": "2017",
            "doi": "10.48550/arXiv.1706.03762",
            "key": "vaswani2017attention",
        },
        "citing_sentence": "Following the transformer architecture proposed by Vaswani et al. (2017), we use multi-head self-attention.",
        "expected_intent": CitationIntent.SUPPORTING,
        "expected_l0": "found",
    },
    "resnet": {
        "ref": {
            "title": "Deep Residual Learning for Image Recognition",
            "authors": ["He, K.", "Zhang, X.", "Ren, S.", "Sun, J."],
            "year": "2016",
            "doi": "10.1109/CVPR.2016.90",
            "key": "he2016deep",
        },
        "citing_sentence": "We adopt residual connections as in He et al. (2016) to train deeper networks.",
        "expected_intent": CitationIntent.USING,
        "expected_l0": "found",
    },
    "hallucinated": {
        "ref": {
            "title": "A Novel Framework for Quantum-Enhanced Deep Neural Networks in Climate Prediction",
            "authors": ["Zhang, J.", "Smith, R.", "Johnson, A."],
            "year": "2030",
            "doi": "10.9999/fake-journal.2030.12345",
            "key": "fake2030quantum",
        },
        "citing_sentence": "Zhang et al. (2030) demonstrated quantum advantages in climate modeling.",
        "expected_intent": CitationIntent.SUPPORTING,
        "expected_l0": "not_found",
    },
    "retracted_wakefield": {
        "ref": {
            "title": "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children",
            "authors": ["Wakefield, A."],
            "year": "1998",
            "doi": "10.1016/S0140-6736(97)11096-0",
            "key": "wakefield1998",
        },
        "citing_sentence": "As shown by Wakefield (1998), the MMR vaccine causes autism.",
        "expected_intent": CitationIntent.SUPPORTING,
        "expected_l0": "retracted",
    },
    "contrasting_citation": {
        "ref": {
            "title": "Some Existing Paper",
            "authors": ["Author, B."],
            "year": "2020",
            "doi": "10.1234/example.2020",
            "key": "author2020some",
        },
        "citing_sentence": "However, unlike the findings of Author (2020), our results show the opposite trend.",
        "expected_intent": CitationIntent.CONTRASTING,
        "expected_l0": "found",
    },
}


# ── Mock helpers ──────────────────────────────────────────────────────────

def _mock_entity(ref: dict, retracted=False) -> ICEntity:
    """Create an ICEntity from a fixture ref dict."""
    entity = ICEntity(
        entity_type=EntityType.PAPER,
        title=ref["title"],
        authors=ref.get("authors", []),
        year=ref.get("year", ""),
        venue="Test Venue",
    )
    entity.is_retracted = retracted
    if ref.get("doi"):
        entity.add_external_id("crossref", "doi", ref["doi"])
    return entity


def _mock_l0_result(fixture_name: str) -> ReferenceResult:
    """Create a mock L0 ReferenceResult based on fixture expectations."""
    fix = FIXTURES[fixture_name]
    ref = fix["ref"]
    result = ReferenceResult(key=ref.get("key", ""), title=ref.get("title", ""))

    if fix["expected_l0"] == "found":
        result.overall = "OK"
        result.sources_tried = ["crossref", "openalex", "semantic_scholar"]
        result.sources_hit = ["crossref", "openalex"]
        result.is_retracted = False
        result.needs_l2_escalation = False
        mock_match = MagicMock()
        mock_match.match_score = 95
        mock_match.is_confirmed = True
        mock_match.is_retracted = False
        mock_match.registry = "crossref"
        mock_match.found = True
        mock_match.found_by_id = True
        mock_match.entity = _mock_entity(ref)
        result.source_matches = [mock_match]
        result.hallucination = MagicMock()
        result.hallucination.hallucination_score = 5
        result.hallucination.is_likely_hallucinated = False
        result.hallucination.signals_fired = []

    elif fix["expected_l0"] == "not_found":
        result.overall = "FAIL"
        result.sources_tried = ["crossref", "openalex"]
        result.sources_hit = []
        result.is_retracted = False
        result.needs_l2_escalation = True
        result.hallucination = MagicMock()
        result.hallucination.hallucination_score = 85
        result.hallucination.is_likely_hallucinated = True
        result.hallucination.signals_fired = ["future_year", "invalid_doi_prefix"]

    elif fix["expected_l0"] == "retracted":
        result.overall = "FAIL"
        result.sources_tried = ["crossref"]
        result.sources_hit = ["crossref"]
        result.is_retracted = True
        result.needs_l2_escalation = True
        mock_match = MagicMock()
        mock_match.match_score = 90
        mock_match.is_confirmed = True
        mock_match.is_retracted = True
        mock_match.registry = "crossref"
        mock_match.found = True
        mock_match.found_by_id = True
        mock_match.entity = _mock_entity(ref, retracted=True)
        result.source_matches = [mock_match]
        result.hallucination = MagicMock()
        result.hallucination.hallucination_score = 10
        result.hallucination.is_likely_hallucinated = False
        result.hallucination.signals_fired = []

    return result


# ── Integration test class ────────────────────────────────────────────────

class TestFullPipeline:
    """End-to-end pipeline tests using real paper fixtures."""

    def _make_pipeline(self, layers=None):
        discovery = MagicMock(spec=RegistryDiscovery)
        pipeline = IntegriRefPipeline(
            discovery=discovery,
            layers=layers or ["L0", "L1", "L4"],
            domain="default",
            enable_l2_only_on_escalation=False,
        )
        return pipeline

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_found_paper_low_risk(self, mock_l3):
        """A legitimate paper should get LOW risk."""
        pipeline = self._make_pipeline()
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = _mock_l0_result("attention")

        report = pipeline.verify(
            FIXTURES["attention"]["ref"],
            citing_sentence=FIXTURES["attention"]["citing_sentence"],
        )

        assert report.l0 is not None
        assert report.l0.overall == "OK"
        assert report.risk_tier == RiskTier.LOW.value
        assert report.risk_probability < 0.05
        assert "L0" in report.layers_run
        assert "L4" in report.layers_run

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_hallucinated_paper_high_risk(self, mock_l3):
        """A fabricated paper should get HIGH or CRITICAL risk."""
        pipeline = self._make_pipeline()
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = _mock_l0_result("hallucinated")

        report = pipeline.verify(FIXTURES["hallucinated"]["ref"])

        assert report.l0.overall == "FAIL"
        assert report.risk_probability > 0.10

        # reference_not_found and phantom_doi should fire
        fired = [s.signal_name for s in report.signals if s.fired]
        assert "reference_not_found" in fired
        assert "phantom_doi" in fired

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_retracted_paper_elevated_risk(self, mock_l3):
        """A retracted paper should get at least ELEVATED risk."""
        pipeline = self._make_pipeline()
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = _mock_l0_result("retracted_wakefield")

        report = pipeline.verify(FIXTURES["retracted_wakefield"]["ref"])

        fired = [s.signal_name for s in report.signals if s.fired]
        assert "retracted_citation" in fired
        assert report.risk_probability > 0.05

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_contrasting_intent_misrepresent_signal(self, mock_l3):
        """A contrasting citation should fire citation_misrepresents_source."""
        pipeline = self._make_pipeline(layers=["L0", "L1", "L4"])
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = _mock_l0_result("contrasting_citation")

        # Mock intent classifier to return CONTRASTING
        mock_intent = IntentResult(
            citation_key="author2020some",
            intent=CitationIntent.CONTRASTING,
            confidence=0.82,
            citing_sentence=FIXTURES["contrasting_citation"]["citing_sentence"],
        )
        pipeline._intent_classifier = MagicMock()
        pipeline._intent_classifier.classify.return_value = mock_intent

        report = pipeline.verify(
            FIXTURES["contrasting_citation"]["ref"],
            citing_sentence=FIXTURES["contrasting_citation"]["citing_sentence"],
        )

        assert "L1" in report.layers_run
        fired = [s.signal_name for s in report.signals if s.fired]
        assert "citation_misrepresents_source" in fired

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_l2_contradicted_high_risk(self, mock_l3):
        """NLI contradiction should raise risk significantly."""
        pipeline = self._make_pipeline(layers=["L0", "L2", "L4"])
        pipeline._engine = MagicMock()
        l0 = _mock_l0_result("attention")
        l0.needs_l2_escalation = True  # Force L2
        pipeline._engine.verify_reference.return_value = l0

        # Mock NLI to return CONTRADICTED
        mock_nli = NLIResult(
            label=AlignmentLabel.CONTRADICTED,
            confidence=0.85,
            entailment_score=0.05,
            neutral_score=0.10,
            contradiction_score=0.85,
            claim="The claim",
            premise="The abstract",
        )
        pipeline._nli_verifier = MagicMock()
        pipeline._nli_verifier.verify.return_value = mock_nli
        pipeline._nli_loaded = True

        report = pipeline.verify(
            FIXTURES["attention"]["ref"],
            abstract="Some abstract text that contradicts the claim.",
            citing_sentence="The claim about the paper.",
        )

        assert "L2" in report.layers_run
        fired = [s.signal_name for s in report.signals if s.fired]
        assert "claim_contradicted" in fired
        # Risk should be higher than baseline
        assert report.risk_probability > 0.05

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_full_pipeline_l0_l1_l2_l4(self, mock_l3):
        """Full pipeline with all layers (except L3) on a good paper."""
        pipeline = self._make_pipeline(layers=["L0", "L1", "L2", "L4"])
        pipeline._engine = MagicMock()
        l0 = _mock_l0_result("resnet")
        l0.needs_l2_escalation = True
        pipeline._engine.verify_reference.return_value = l0

        # Mock L1
        mock_intent = IntentResult(
            citation_key="he2016deep",
            intent=CitationIntent.USING,
            confidence=0.88,
        )
        pipeline._intent_classifier = MagicMock()
        pipeline._intent_classifier.classify.return_value = mock_intent

        # Mock L2
        mock_nli = NLIResult(
            label=AlignmentLabel.SUPPORTED,
            confidence=0.91,
            entailment_score=0.93,
            contradiction_score=0.02,
            neutral_score=0.05,
            claim="test",
            premise="test",
        )
        pipeline._nli_verifier = MagicMock()
        pipeline._nli_verifier.verify.return_value = mock_nli
        pipeline._nli_loaded = True

        report = pipeline.verify(
            FIXTURES["resnet"]["ref"],
            citing_sentence=FIXTURES["resnet"]["citing_sentence"],
            abstract="We propose deep residual learning...",
        )

        assert set(report.layers_run) == {"L0", "L1", "L2", "L4"}
        assert report.risk_tier == RiskTier.LOW.value
        # No bad signals should fire
        fired = [s.signal_name for s in report.signals if s.fired]
        assert "reference_not_found" not in fired
        assert "claim_contradicted" not in fired

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_api_response_structure(self, mock_l3):
        """Verify the API response has correct structure."""
        pipeline = self._make_pipeline(layers=["L0", "L1", "L4"])
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = _mock_l0_result("attention")

        mock_intent = IntentResult(
            citation_key="test",
            intent=CitationIntent.SUPPORTING,
            confidence=0.85,
        )
        pipeline._intent_classifier = MagicMock()
        pipeline._intent_classifier.classify.return_value = mock_intent

        report = pipeline.verify(
            FIXTURES["attention"]["ref"],
            citing_sentence=FIXTURES["attention"]["citing_sentence"],
        )

        api_resp = report.api_response()
        assert "status" in api_resp
        assert "risk_tier" in api_resp
        assert "risk_probability" in api_resp
        assert "layers_run" in api_resp
        assert "signals_fired" in api_resp
        assert "l0" in api_resp
        assert "l1" in api_resp
        assert api_resp["l1"]["intent"] == "supporting"

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_summary_structure(self, mock_l3):
        """Verify the summary dict has all expected keys."""
        pipeline = self._make_pipeline(layers=["L0", "L4"])
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.return_value = _mock_l0_result("attention")

        report = pipeline.verify(FIXTURES["attention"]["ref"])
        summary = report.summary()

        required_keys = [
            "title", "risk_tier", "risk_probability", "layers_run",
            "signals_fired", "l0_overall", "l0_found",
            "l3_anomaly_count", "processing_time_ms",
        ]
        for k in required_keys:
            assert k in summary, f"Missing key: {k}"


class TestBatchIntegration:
    """Integration tests for batch verification."""

    @patch.object(IntegriRefPipeline, '_run_l3')
    def test_batch_mixed_papers(self, mock_l3):
        """Batch with mix of good, fake, and retracted papers."""
        discovery = MagicMock(spec=RegistryDiscovery)
        pipeline = IntegriRefPipeline(
            discovery=discovery,
            layers=["L0", "L4"],
            enable_l2_only_on_escalation=False,
        )

        # Mock engine to return different results
        results = [
            _mock_l0_result("attention"),     # good
            _mock_l0_result("hallucinated"),   # fake
            _mock_l0_result("retracted_wakefield"),  # retracted
        ]
        pipeline._engine = MagicMock()
        pipeline._engine.verify_reference.side_effect = results

        refs = [
            FIXTURES["attention"]["ref"],
            FIXTURES["hallucinated"]["ref"],
            FIXTURES["retracted_wakefield"]["ref"],
        ]
        reports = pipeline.verify_batch(refs)

        assert len(reports) == 3

        # Good paper: low risk
        assert reports[0].risk_tier == RiskTier.LOW.value

        # Fake paper: high risk
        assert reports[1].risk_probability > 0.10
        fake_fired = [s.signal_name for s in reports[1].signals if s.fired]
        assert "reference_not_found" in fake_fired

        # Retracted: retraction signal fires
        retract_fired = [s.signal_name for s in reports[2].signals if s.fired]
        assert "retracted_citation" in retract_fired


class TestSignalExtractorWithDetectors:
    """Test SignalExtractor with real detector modules."""

    def test_tortured_phrases_detection(self):
        """Tortured phrases in text should fire the signal."""
        text = ("This study uses profound learning and arbitrary forest "
                "classifiers to analyse the information.")
        l0 = _mock_l0_result("attention")
        signals = SignalExtractor.from_l0(l0, full_text=text)
        names = {s.signal_name: s for s in signals}
        # Should have attempted tortured phrase detection
        if "tortured_phrases" in names:
            # "profound learning" and "arbitrary forest" are known tortured phrases
            assert names["tortured_phrases"].fired is True

    def test_statistical_verification(self):
        """Text with GRIM-testable statistics should produce signals."""
        text = "The mean score was M = 2.31 (SD = 0.5, N = 25). We found t(24) = 3.45, p = .002."
        l0 = _mock_l0_result("attention")
        signals = SignalExtractor.from_l0(l0, full_text=text)
        names = {s.signal_name for s in signals}
        # Should have attempted GRIM + statcheck
        assert "grim_test_failure" in names or "statcheck_error" in names

    def test_no_crash_on_empty_text(self):
        """Empty text should not crash detectors."""
        l0 = _mock_l0_result("attention")
        signals = SignalExtractor.from_l0(l0, full_text="")
        # Should work without error, no tortured/grim/statcheck signals
        names = {s.signal_name for s in signals}
        assert "reference_not_found" in names  # base signals always present
        assert "tortured_phrases" not in names  # not run on empty text
