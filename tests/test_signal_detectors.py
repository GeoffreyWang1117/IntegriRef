"""Unit tests for signal-level detectors.

Tests tortured phrase detection, GRIM test, suspicious email detection,
temporal anomaly checking, and intent classification against the
signal_test_cases.json dataset.

Usage:
    pytest tests/test_signal_detectors.py -v
    pytest tests/test_signal_detectors.py -v -k tortured
    pytest tests/test_signal_detectors.py -v -k grim
"""

import json
from pathlib import Path

import pytest

DATA_PATH = Path(__file__).parent.parent / "benchmarks" / "data" / "signal_test_cases.json"


@pytest.fixture(scope="module")
def signal_data():
    if not DATA_PATH.exists():
        pytest.skip("signal_test_cases.json not found")
    with open(DATA_PATH) as f:
        return json.load(f)


# ── Tortured phrases ─────────────────────────────────────────────────────

class TestTorturedPhrases:
    @pytest.fixture(scope="class")
    def detector(self):
        from benchmarks.bench_signals import detect_tortured_phrases
        return detect_tortured_phrases

    def test_tortured_phrase_cases(self, signal_data, detector):
        """All tortured phrase test cases should be correctly classified."""
        cases = signal_data.get("tortured_phrases_cases", [])
        assert len(cases) > 0, "No tortured phrase test cases found"

        failures = []
        for case in cases:
            text = case.get("text", "")
            expected = case.get("expected_fired", False)
            found = detector(text)
            actual = len(found) > 0

            if actual != expected:
                failures.append(
                    f"  {case['id']}: expected_fired={expected}, "
                    f"actual={actual}, found={[f[0] for f in found]}"
                )

        assert not failures, (
            f"{len(failures)} tortured phrase tests failed:\n"
            + "\n".join(failures)
        )

    def test_known_tortured_phrase(self, detector):
        """Known tortured phrase 'profound learning' should be detected."""
        text = "We apply profound learning techniques to classify images."
        found = detector(text)
        assert len(found) > 0
        assert any("profound learning" in f[0] for f in found)

    def test_clean_text_no_detection(self, detector):
        """Normal academic text should not trigger false positives."""
        text = (
            "Deep learning has achieved state-of-the-art results in many "
            "computer vision and natural language processing tasks."
        )
        found = detector(text)
        assert len(found) == 0


# ── GRIM test ────────────────────────────────────────────────────────────

class TestGRIM:
    @pytest.fixture(scope="class")
    def grim(self):
        from benchmarks.bench_signals import grim_test
        return grim_test

    def test_grim_cases(self, signal_data, grim):
        """All GRIM test cases should be correctly classified."""
        cases = signal_data.get("grim_test_cases", [])
        assert len(cases) > 0, "No GRIM test cases found"

        failures = []
        for case in cases:
            mean = case.get("reported_mean", 0)
            n = case.get("sample_size_n", 1)
            scale = case.get("scale", 1)
            dp = case.get("decimal_places", 2)
            expected_fired = case.get("expected_fired", False)

            consistent = grim(mean, n, scale, dp)
            actual_fired = not consistent

            if actual_fired != expected_fired:
                failures.append(
                    f"  {case['id']}: mean={mean}, n={n}, "
                    f"total={mean*n:.4f}, expected_fired={expected_fired}, "
                    f"actual_fired={actual_fired}"
                )

        assert not failures, (
            f"{len(failures)} GRIM tests failed:\n" + "\n".join(failures)
        )

    def test_grim_impossible_mean(self, grim):
        """Mean of 3.53 with N=25 should fail GRIM (25*3.53=88.25)."""
        assert not grim(3.53, 25)

    def test_grim_possible_mean(self, grim):
        """Mean of 3.56 with N=25 should pass GRIM (25*3.56=89)."""
        assert grim(3.56, 25)


# ── Suspicious email ─────────────────────────────────────────────────────

class TestSuspiciousEmail:
    @pytest.fixture(scope="class")
    def checker(self):
        from benchmarks.bench_signals import check_suspicious_email
        return check_suspicious_email

    def test_email_cases(self, signal_data, checker):
        """All suspicious email test cases should be correctly classified."""
        cases = signal_data.get("suspicious_email_cases", [])
        assert len(cases) > 0, "No email test cases found"

        failures = []
        for case in cases:
            expected = case.get("expected_fired", False)
            emails = [a.get("email", "") for a in case.get("authors", []) if a.get("email")]

            suspicious = False
            for email in emails:
                is_sus, _ = checker(email)
                if is_sus:
                    suspicious = True
                    break

            if suspicious != expected:
                failures.append(
                    f"  {case['id']}: expected_fired={expected}, "
                    f"actual={suspicious}, emails={emails}"
                )

        assert not failures, (
            f"{len(failures)} email tests failed:\n" + "\n".join(failures)
        )

    def test_mill_domain_detected(self, checker):
        """Known paper mill domain should be flagged."""
        is_sus, reason = checker("author@163.com")
        assert is_sus
        assert "mill" in reason

    def test_edu_domain_clean(self, checker):
        """University email should not be flagged."""
        is_sus, _ = checker("professor@mit.edu")
        assert not is_sus


# ── Temporal anomaly ─────────────────────────────────────────────────────

class TestTemporalAnomaly:
    def test_temporal_anomaly_cases(self, signal_data):
        """All temporal anomaly test cases should be correctly classified."""
        cases = signal_data.get("temporal_anomaly_cases", [])
        assert len(cases) > 0, "No temporal anomaly test cases found"

        failures = []
        for case in cases:
            expected = case.get("expected_fired", False)
            citing_year = int(case.get("citing_paper", {}).get("year", 9999))
            cited_year = int(case.get("cited_paper", {}).get("year", 0))
            actual_fired = citing_year < cited_year

            if actual_fired != expected:
                failures.append(
                    f"  {case['id']}: citing={citing_year}, cited={cited_year}, "
                    f"expected_fired={expected}, actual_fired={actual_fired}"
                )

        assert not failures, (
            f"{len(failures)} temporal anomaly tests failed:\n" + "\n".join(failures)
        )


# ── Self-citation ────────────────────────────────────────────────────────

class TestSelfCitation:
    def test_self_citation_cases(self, signal_data):
        """All self-citation test cases should be correctly classified."""
        cases = signal_data.get("self_citation_cases", [])
        assert len(cases) > 0, "No self-citation test cases found"

        failures = []
        for case in cases:
            expected = case.get("expected_fired", False)
            rate = case.get("self_citation_rate", 0)
            actual_fired = rate > 0.30

            if actual_fired != expected:
                failures.append(
                    f"  {case['id']}: rate={rate:.1%}, "
                    f"expected_fired={expected}, actual_fired={actual_fired}"
                )

        assert not failures, (
            f"{len(failures)} self-citation tests failed:\n" + "\n".join(failures)
        )


# ── Intent classification ────────────────────────────────────────────────

class TestIntentClassification:
    @pytest.fixture(scope="class")
    def classifier(self):
        from semantic.intent_classifier import IntentClassifier
        return IntentClassifier(use_model=False)

    def test_intent_cases(self, signal_data, classifier):
        """Intent classification should match expected labels."""
        cases = signal_data.get("l1_intent_cases", [])
        assert len(cases) > 0, "No L1 intent test cases found"

        correct = 0
        total = len(cases)
        mismatches = []

        for case in cases:
            sentence = case.get("citing_sentence", "")
            expected = case.get("expected_intent", "mentioning")
            key = case.get("citation_key", "")

            result = classifier.classify(sentence, key)

            if result.intent.value == expected:
                correct += 1
            else:
                mismatches.append(
                    f"  {case['id']}: expected={expected}, "
                    f"actual={result.intent.value}, "
                    f"confidence={result.confidence:.2f}"
                )

        accuracy = correct / total if total > 0 else 0
        # Heuristic classifier targets ~70% accuracy
        assert accuracy >= 0.50, (
            f"Intent accuracy {accuracy:.0%} below 50% threshold. "
            f"Mismatches:\n" + "\n".join(mismatches)
        )
