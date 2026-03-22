"""Tests for benchmark framework — datasets, metrics, and runners."""

import pytest
from benchmarks.datasets import (
    L0Sample, L1Sample, L2Sample,
    generate_synthetic_negatives,
    available_datasets,
    _perturb_title, _shift_year, _swap_author,
    _SCICITE_LABEL_MAP,
)
from benchmarks.metrics import (
    ClassificationMetrics,
    BenchmarkResult,
    compute_binary_metrics,
    compute_multiclass_metrics,
    compute_l0_metrics,
)


# ── Metrics tests ────────────────────────────────────────────────────────

class TestClassificationMetrics:
    def test_perfect_precision(self):
        m = ClassificationMetrics(tp=10, fp=0, fn=0, tn=10)
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.accuracy == 1.0

    def test_zero_division(self):
        m = ClassificationMetrics(tp=0, fp=0, fn=0, tn=0)
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.f1 == 0.0
        assert m.accuracy == 0.0

    def test_partial_metrics(self):
        m = ClassificationMetrics(tp=8, fp=2, fn=3, tn=7)
        assert abs(m.precision - 0.8) < 0.001
        assert abs(m.recall - 8/11) < 0.001

    def test_to_dict(self):
        m = ClassificationMetrics(tp=5, fp=1, fn=2, tn=3)
        d = m.to_dict()
        assert "precision" in d
        assert "recall" in d
        assert "f1" in d
        assert d["tp"] == 5


class TestBinaryMetrics:
    def test_all_correct(self):
        preds = [True, True, False, False]
        labels = [True, True, False, False]
        m = compute_binary_metrics(preds, labels)
        assert m.tp == 2
        assert m.tn == 2
        assert m.fp == 0
        assert m.fn == 0

    def test_all_wrong(self):
        preds = [True, True, False, False]
        labels = [False, False, True, True]
        m = compute_binary_metrics(preds, labels)
        assert m.tp == 0
        assert m.fp == 2
        assert m.fn == 2
        assert m.tn == 0

    def test_mixed(self):
        preds = [True, False, True, False, True]
        labels = [True, True, False, False, True]
        m = compute_binary_metrics(preds, labels)
        assert m.tp == 2
        assert m.fn == 1
        assert m.fp == 1
        assert m.tn == 1


class TestMulticlassMetrics:
    def test_three_class(self):
        preds  = ["A", "A", "B", "B", "C", "C"]
        labels = ["A", "B", "B", "C", "C", "A"]
        overall, per_class, confusion = compute_multiclass_metrics(
            preds, labels, ["A", "B", "C"])

        assert "A" in per_class
        assert "B" in per_class
        assert "C" in per_class
        # A: tp=1 fp=1 fn=1
        assert per_class["A"].tp == 1
        assert per_class["A"].fp == 1
        assert per_class["A"].fn == 1
        # Confusion matrix
        assert confusion["A"]["A"] == 1  # correct A
        assert confusion["B"]["A"] == 1  # B predicted as A

    def test_perfect(self):
        preds = labels = ["X", "Y", "Z", "X", "Y"]
        overall, per_class, confusion = compute_multiclass_metrics(preds, labels)
        for cls, m in per_class.items():
            assert m.precision == 1.0
            assert m.recall == 1.0


class TestL0Metrics:
    def test_basic(self):
        preds = [
            {"found": True, "composite_score": 0.95},
            {"found": True, "composite_score": 0.85},
            {"found": False, "composite_score": 0.2},
            {"found": False, "composite_score": 0.1},
        ]
        labels = [True, True, False, False]
        result = compute_l0_metrics(preds, labels)
        assert result["binary"]["precision"] == 1.0
        assert result["binary"]["recall"] == 1.0
        assert result["false_positive_rate"] == 0.0

    def test_false_positive(self):
        preds = [
            {"found": True, "composite_score": 0.9},
            {"found": True, "composite_score": 0.7},  # FP
        ]
        labels = [True, False]
        result = compute_l0_metrics(preds, labels)
        assert result["false_positive_rate"] == 1.0  # 1 FP out of 1 negative


# ── Dataset tests ────────────────────────────────────────────────────────

class TestSyntheticNegatives:
    def test_generate_count(self):
        positives = [
            L0Sample(ref_id="p1", title="Deep Learning for NLP",
                     authors=["Smith, J."], year="2020"),
            L0Sample(ref_id="p2", title="Attention Mechanisms in Neural Networks",
                     authors=["Jones, A."], year="2019"),
            L0Sample(ref_id="p3", title="Graph Neural Networks: A Survey",
                     authors=["Lee, K."], year="2021"),
        ]
        negs = generate_synthetic_negatives(positives, n_negatives=5, seed=42)
        assert len(negs) == 5
        assert all(not n.expected_found for n in negs)
        assert all(n.source.startswith("synthetic_") for n in negs)

    def test_default_count_matches_positives(self):
        positives = [
            L0Sample(ref_id=f"p{i}", title=f"Paper {i}",
                     authors=[f"Author {i}"], year=str(2020+i))
            for i in range(10)
        ]
        negs = generate_synthetic_negatives(positives)
        assert len(negs) == 10

    def test_perturbation_changes_data(self):
        positives = [
            L0Sample(ref_id="p1", title="Deep Learning for Natural Language Processing",
                     authors=["Smith, John"], year="2020"),
        ]
        negs = generate_synthetic_negatives(positives, n_negatives=20, seed=42)
        # At least some negatives should have different titles
        original_title = positives[0].title
        changed = [n for n in negs if n.title != original_title]
        assert len(changed) > 0

    def test_no_valid_dois(self):
        positives = [
            L0Sample(ref_id="p1", title="Test Paper",
                     authors=["A."], year="2020", doi="10.1234/test"),
        ]
        negs = generate_synthetic_negatives(positives, n_negatives=5)
        assert all(n.doi == "" for n in negs)


class TestPerturbationHelpers:
    def test_perturb_title(self):
        title = "Deep Learning for Image Classification"
        perturbed = _perturb_title(title)
        # Should be different (with high probability for long titles)
        assert isinstance(perturbed, str)
        assert len(perturbed) > 0

    def test_shift_year(self):
        shifted = _shift_year("2020")
        assert shifted != "2020"
        y = int(shifted)
        assert abs(y - 2020) >= 2

    def test_swap_author(self):
        swapped = _swap_author(["Smith, John"])
        assert len(swapped) == 1
        assert swapped[0] != "Smith, John"


class TestSciCiteLabelMap:
    def test_background_is_mentioning(self):
        assert _SCICITE_LABEL_MAP["background"] == "mentioning"

    def test_method_is_supporting(self):
        assert _SCICITE_LABEL_MAP["method"] == "supporting"

    def test_result_is_supporting(self):
        assert _SCICITE_LABEL_MAP["result"] == "supporting"


class TestAvailableDatasets:
    def test_returns_dict(self):
        info = available_datasets()
        assert isinstance(info, dict)
        assert "scifact" in info
        assert "scicite" in info
        assert "fever" in info
        assert "openalex" in info

    def test_structure(self):
        info = available_datasets()
        for name, details in info.items():
            assert "path" in details
            assert "available" in details
            assert "files_found" in details
            assert "files_expected" in details


# ── BenchmarkResult tests ────────────────────────────────────────────────

class TestBenchmarkResult:
    def test_summary(self):
        r = BenchmarkResult(
            name="Test Bench",
            dataset="test",
            n_samples=100,
            overall=ClassificationMetrics(tp=90, fp=5, fn=3, tn=2),
        )
        s = r.summary()
        assert "Test Bench" in s
        assert "100" in s

    def test_to_dict(self):
        r = BenchmarkResult(
            name="Test", dataset="test", n_samples=10,
            overall=ClassificationMetrics(tp=8, fp=1, fn=1, tn=0),
        )
        d = r.to_dict()
        assert d["name"] == "Test"
        assert d["n_samples"] == 10
        assert "overall" in d


# ── L1 built-in test ─────────────────────────────────────────────────────

class TestL1BuiltinClassification:
    """Smoke test: run IntentClassifier on built-in L1 test set."""

    def test_builtin_samples(self):
        from benchmarks.bench_l1 import _builtin_test_set
        from semantic.intent_classifier import IntentClassifier, CitationIntent

        classifier = IntentClassifier()
        samples = _builtin_test_set()

        correct = 0
        for sample in samples:
            result = classifier.classify(
                citing_sentence=sample.citing_sentence,
                citation_key=sample.sample_id,
            )
            intent = result.intent
            if intent in (CitationIntent.EXTENDING, CitationIntent.USING):
                pred = "supporting"
            else:
                pred = intent.value

            if pred == sample.expected_intent:
                correct += 1

        accuracy = correct / len(samples)
        # Heuristic should get at least 70% on these clear examples
        assert accuracy >= 0.7, f"Accuracy {accuracy:.2f} too low on built-in set"


class TestL2BuiltinVerification:
    """Smoke test: run NLIVerifier on built-in L2 test claims."""

    def test_builtin_claims(self):
        from benchmarks.bench_l2 import _builtin_test_claims, _ALIGNMENT_TO_REPORT, _SCIFACT_LABEL_MAP
        from semantic.nli_verifier import NLIVerifier

        verifier = NLIVerifier()
        claims = _builtin_test_claims()

        for claim in claims:
            if not claim.evidence:
                continue
            evidence_text = " ".join(claim.evidence)
            result = verifier.verify(claim=claim.claim, abstract=evidence_text)
            # Just verify it runs without error
            assert result is not None
            assert result.label is not None
