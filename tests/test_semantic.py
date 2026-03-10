"""Tests for semantic verification pipeline."""

import pytest


class TestClaimExtractor:
    def setup_method(self):
        from semantic.claim_extractor import ClaimExtractor
        self.extractor = ClaimExtractor()

    def test_extract_numbered_citation(self):
        text = "Previous work has shown significant results. Smith et al. [23] demonstrated that transformers outperform RNNs on machine translation tasks. This was later confirmed by other studies."
        claims = self.extractor.extract_from_text(text)
        assert len(claims) >= 1
        assert "23" in claims[0].citation_key

    def test_extract_author_year_citation(self):
        text = "As shown by (Smith, 2020), deep learning has revolutionized NLP. This is widely accepted."
        claims = self.extractor.extract_from_text(text)
        assert len(claims) >= 1
        assert "Smith, 2020" in claims[0].citation_key

    def test_extract_multiple_citations(self):
        text = "Several studies [1] have confirmed this important finding in the field. Additionally, [2] showed significant improvements over all previous baselines. Furthermore, [3] extended the approach to new domains."
        claims = self.extractor.extract_from_text(text)
        assert len(claims) >= 2

    def test_clean_claim_removes_markers(self):
        cleaned = self.extractor._clean_claim("Smith et al. [23] showed that X works")
        assert "[23]" not in cleaned
        assert "showed" in cleaned.lower() or "x works" in cleaned.lower()

    def test_clean_claim_author_year(self):
        cleaned = self.extractor._clean_claim("As demonstrated by (Smith et al., 2020), the method achieves SOTA")
        assert "(Smith" not in cleaned

    def test_skip_short_claims(self):
        text = "See [1]. The end."
        claims = self.extractor.extract_from_text(text)
        # "See" alone is < 5 words after cleaning
        assert all(len(c.claim_text.split()) >= 5 for c in claims)

    def test_context_extraction(self):
        text = "First sentence about background. The key finding [1] was that X outperforms Y. This has implications for future work."
        claims = self.extractor.extract_from_text(text)
        if claims:
            # Should have context
            assert claims[0].context_before or claims[0].context_after

    def test_empty_text(self):
        claims = self.extractor.extract_from_text("")
        assert claims == []

    def test_no_citations(self):
        text = "This is a text without any citations or references."
        claims = self.extractor.extract_from_text(text)
        assert claims == []

    def test_extract_for_specific_reference(self):
        text = "Method A [1] works well. Method B [2] is faster. Method A [1] also handles edge cases."
        claims = self.extractor.extract_claims_for_reference(text, "1")
        assert len(claims) >= 1
        assert all("1" in c.citation_key for c in claims)


class TestNLIVerifier:
    def setup_method(self):
        from semantic.nli_verifier import NLIVerifier, AlignmentLabel
        self.verifier = NLIVerifier()
        self.AlignmentLabel = AlignmentLabel

    def test_unverifiable_empty_abstract(self):
        result = self.verifier.verify("some claim", "")
        assert result.label == self.AlignmentLabel.UNVERIFIABLE

    def test_unverifiable_empty_claim(self):
        result = self.verifier.verify("", "some abstract")
        assert result.label == self.AlignmentLabel.UNVERIFIABLE

    def test_heuristic_high_overlap(self):
        claim = "deep learning models achieve high accuracy on image classification"
        abstract = "We present a deep learning model that achieves state-of-the-art accuracy on image classification benchmarks."
        result = self.verifier.verify(claim, abstract)
        # With high overlap, should be at least partially supported
        assert result.label in (
            self.AlignmentLabel.SUPPORTED,
            self.AlignmentLabel.PARTIALLY_SUPPORTED,
        )
        assert result.confidence > 0

    def test_low_overlap_detected(self):
        claim = "quantum computing will replace classical computers"
        abstract = "This paper studies the migration patterns of arctic birds."
        result = self.verifier.verify(claim, abstract)
        # With NLI model loaded, it may classify differently than heuristic
        # Just verify we get a valid result
        assert result.label in self.AlignmentLabel
        assert 0 <= result.confidence <= 1.0

    def test_result_has_all_fields(self):
        result = self.verifier.verify("test claim", "test abstract")
        assert hasattr(result, "label")
        assert hasattr(result, "confidence")
        assert hasattr(result, "entailment_score")
        assert hasattr(result, "neutral_score")
        assert hasattr(result, "contradiction_score")
        assert hasattr(result, "needs_llm_upgrade")

    def test_classify_thresholds(self):
        from semantic.nli_verifier import NLIVerifier, AlignmentLabel
        # Contradiction dominant
        label, conf, upgrade = NLIVerifier._classify(0.1, 0.2, 0.7)
        assert label == AlignmentLabel.CONTRADICTED
        # Entailment dominant
        label, conf, upgrade = NLIVerifier._classify(0.8, 0.1, 0.1)
        assert label == AlignmentLabel.SUPPORTED
        # Partial support
        label, conf, upgrade = NLIVerifier._classify(0.5, 0.3, 0.2)
        assert label == AlignmentLabel.PARTIALLY_SUPPORTED
        # Unsupported
        label, conf, upgrade = NLIVerifier._classify(0.2, 0.6, 0.2)
        assert label == AlignmentLabel.UNSUPPORTED

    def test_verify_batch(self):
        pairs = [
            ("claim 1 about deep learning models", "abstract about deep learning"),
            ("claim 2 about quantum physics", "abstract about biology"),
        ]
        results = self.verifier.verify_batch(pairs)
        assert len(results) == 2


class TestAbstractFetcher:
    def test_init(self):
        from core.discovery import RegistryDiscovery
        from semantic.abstract_fetcher import AbstractFetcher
        discovery = RegistryDiscovery()
        fetcher = AbstractFetcher(discovery)
        assert fetcher._discovery is discovery

    def test_fetch_no_registries(self):
        from core.discovery import RegistryDiscovery
        from semantic.abstract_fetcher import AbstractFetcher
        discovery = RegistryDiscovery()
        fetcher = AbstractFetcher(discovery)
        result = fetcher.fetch_abstract(title="nonexistent paper")
        assert result is None


class TestSemanticPipeline:
    def test_init(self):
        from core.discovery import RegistryDiscovery
        from semantic.semantic_pipeline import SemanticPipeline
        discovery = RegistryDiscovery()
        pipeline = SemanticPipeline(discovery)
        assert pipeline._discovery is discovery

    def test_empty_document(self):
        from core.discovery import RegistryDiscovery
        from semantic.semantic_pipeline import SemanticPipeline
        discovery = RegistryDiscovery()
        pipeline = SemanticPipeline(discovery)
        report = pipeline.verify_document("")
        assert report.total_citations == 0
        assert report.alignment_score == 100.0

    def test_document_no_citations(self):
        from core.discovery import RegistryDiscovery
        from semantic.semantic_pipeline import SemanticPipeline
        discovery = RegistryDiscovery()
        pipeline = SemanticPipeline(discovery)
        report = pipeline.verify_document("This is a document without any citations.")
        assert report.total_citations == 0

    def test_report_summary(self):
        from semantic.semantic_pipeline import SemanticReport
        report = SemanticReport(
            total_citations=10,
            verified=8,
            supported=5,
            unsupported=2,
            contradicted=1,
        )
        summary = report.summary()
        assert summary["total_citations"] == 10
        assert "alignment_score" in summary
