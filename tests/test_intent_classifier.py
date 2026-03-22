"""Tests for the L1 citation intent classifier."""

import pytest
from semantic.intent_classifier import (
    IntentClassifier, IntentResult, CitationIntent,
)


class TestCitationIntent:
    def test_all_intents_exist(self):
        expected = {"supporting", "contrasting", "mentioning", "extending", "using"}
        actual = {e.value for e in CitationIntent}
        assert actual == expected

    def test_string_enum(self):
        assert CitationIntent.SUPPORTING == "supporting"
        assert CitationIntent.CONTRASTING == "contrasting"


class TestIntentClassifierHeuristic:
    def setup_method(self):
        self.classifier = IntentClassifier(use_model=False)

    def test_supporting_cue(self):
        sentence = "As demonstrated by [1], the transformer model achieves state-of-the-art performance on translation benchmarks."
        result = self.classifier.classify(sentence, "1")
        assert result.intent == CitationIntent.SUPPORTING
        assert result.confidence > 0.4

    def test_supporting_confirm(self):
        sentence = "This confirms the findings of [23], which showed that deep learning models outperform traditional approaches."
        result = self.classifier.classify(sentence, "23")
        assert result.intent == CitationIntent.SUPPORTING

    def test_supporting_evidence(self):
        sentence = "Evidence from [5] shows that the proposed method significantly improves accuracy across all tested benchmarks."
        result = self.classifier.classify(sentence, "5")
        assert result.intent == CitationIntent.SUPPORTING

    def test_contrasting_however(self):
        sentence = "However, [7] showed that this approach fails to scale to large datasets and has significant computational overhead."
        result = self.classifier.classify(sentence, "7")
        assert result.intent == CitationIntent.CONTRASTING

    def test_contrasting_limitation(self):
        sentence = "A key limitation of [12] is that their method cannot handle multi-modal inputs and requires extensive preprocessing."
        result = self.classifier.classify(sentence, "12")
        assert result.intent == CitationIntent.CONTRASTING

    def test_contrasting_unlike(self):
        sentence = "Unlike [3], our method does not require labeled data for training and can generalize to unseen domains effectively."
        result = self.classifier.classify(sentence, "3")
        assert result.intent == CitationIntent.CONTRASTING

    def test_mentioning_background(self):
        sentence = "The topic of reinforcement learning was studied by [15] in the context of Markov decision processes."
        result = self.classifier.classify(sentence, "15")
        assert result.intent == CitationIntent.MENTIONING

    def test_mentioning_neutral(self):
        sentence = "Related work on graph neural networks includes [1], [2], and [3], among other relevant publications."
        result = self.classifier.classify(sentence, "2")
        assert result.intent == CitationIntent.MENTIONING

    def test_extending_cue(self):
        sentence = "We extend the framework of [8] by incorporating attention mechanisms into the encoder-decoder architecture."
        result = self.classifier.classify(sentence, "8")
        # EXTENDING merges into SUPPORTING
        assert result.intent == CitationIntent.SUPPORTING

    def test_using_cue(self):
        sentence = "We use the dataset from [4] to evaluate our model across multiple downstream classification tasks."
        result = self.classifier.classify(sentence, "4")
        # USING merges into SUPPORTING
        assert result.intent == CitationIntent.SUPPORTING

    def test_confidence_range(self):
        sentence = "Previous work [1] has explored this area of research in various settings."
        result = self.classifier.classify(sentence, "1")
        assert 0 <= result.confidence <= 1.0

    def test_needs_model_upgrade(self):
        # Ambiguous sentence should flag for model upgrade
        sentence = "The results presented in [10] are discussed in the appendix section of this document."
        result = self.classifier.classify(sentence, "10")
        assert isinstance(result.needs_model_upgrade, bool)

    def test_citing_sentence_stored(self):
        sentence = "This is a test sentence with a citation [1] that should be stored."
        result = self.classifier.classify(sentence, "1")
        assert "test sentence" in result.citing_sentence


class TestIntentClassifierBatch:
    def setup_method(self):
        self.classifier = IntentClassifier(use_model=False)

    def test_classify_batch(self):
        sentences = [
            ("As shown by [1], the method works very well and achieves excellent results.", "1"),
            ("However, [2] found that the approach fails on large datasets and is too slow.", "2"),
            ("Several methods have been proposed [3] for this important research problem.", "3"),
        ]
        results = self.classifier.classify_batch(sentences)
        assert len(results) == 3
        assert all(isinstance(r, IntentResult) for r in results)

    def test_classify_document(self):
        doc = (
            "Deep learning has revolutionized NLP [1]. "
            "As demonstrated by [2], transformers outperform RNNs on most tasks. "
            "However, [3] showed that CNNs remain competitive for certain applications."
        )
        results = self.classifier.classify_document(doc)
        assert len(results) >= 2  # Should find at least [1] and [2]

    def test_classify_document_with_keys(self):
        doc = (
            "Method A [1] works well. Method B [2] is faster. "
            "Method C [3] is more accurate on benchmark datasets."
        )
        results = self.classifier.classify_document(doc, citation_keys=["2"])
        found_keys = [r.citation_key for r in results]
        assert "2" in found_keys


class TestIntentDistribution:
    def setup_method(self):
        self.classifier = IntentClassifier(use_model=False)

    def test_distribution(self):
        results = [
            IntentResult("1", CitationIntent.SUPPORTING, 0.8),
            IntentResult("2", CitationIntent.CONTRASTING, 0.7),
            IntentResult("3", CitationIntent.MENTIONING, 0.6),
            IntentResult("4", CitationIntent.SUPPORTING, 0.9),
        ]
        dist = self.classifier.intent_distribution(results)
        assert dist["total"] == 4
        assert dist["supporting"] == 2
        assert dist["contrasting"] == 1
        assert dist["mentioning"] == 1
        assert dist["supporting_pct"] == 50.0

    def test_empty_distribution(self):
        dist = self.classifier.intent_distribution([])
        assert dist["total"] == 0


class TestCleanSentence:
    def test_numbered_citation(self):
        result = IntentClassifier._clean_sentence("As shown in [23], transformers work.")
        assert "[CIT]" in result
        assert "[23]" not in result

    def test_author_year_citation(self):
        result = IntentClassifier._clean_sentence(
            "As shown by (Smith et al., 2020), the approach works.")
        assert "[CIT]" in result
        assert "Smith" not in result


class TestExtractCitingSentences:
    def test_numbered(self):
        text = "First sentence. The key result [1] was important. Third sentence."
        results = IntentClassifier._extract_citing_sentences(text)
        assert len(results) >= 1
        assert any(k == "1" for _, k in results)

    def test_author_year(self):
        text = "Background information. (Smith, 2020) showed important results. End."
        results = IntentClassifier._extract_citing_sentences(text)
        assert len(results) >= 1

    def test_filter_keys(self):
        text = "Result [1] and [2] and [3] are interesting findings."
        results = IntentClassifier._extract_citing_sentences(text, keys=["2"])
        found_keys = [k for _, k in results]
        assert "2" in found_keys
