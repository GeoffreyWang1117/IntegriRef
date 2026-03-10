"""Tests for risk scoring system."""

import pytest


class TestScoringProfiles:
    def test_all_profiles_valid(self):
        from scoring.profiles import SCORING_PROFILES
        for name, profile in SCORING_PROFILES.items():
            assert profile.validate(), f"Profile '{name}' weights don't sum to 1.0"

    def test_profile_names(self):
        from scoring.profiles import SCORING_PROFILES
        expected = {"academic", "patent", "legal", "financial", "standard"}
        assert set(SCORING_PROFILES.keys()) == expected

    def test_academic_weights_semantic_highest(self):
        from scoring.profiles import SCORING_PROFILES
        academic = SCORING_PROFILES["academic"]
        assert academic.semantic_alignment >= academic.metadata_accuracy
        assert academic.semantic_alignment >= academic.existence_verification

    def test_patent_weights_existence_highest(self):
        from scoring.profiles import SCORING_PROFILES
        patent = SCORING_PROFILES["patent"]
        assert patent.existence_verification >= patent.graph_health

    def test_legal_weights_metadata_high(self):
        from scoring.profiles import SCORING_PROFILES
        legal = SCORING_PROFILES["legal"]
        assert legal.metadata_accuracy >= legal.graph_health


class TestIntegrityScorer:
    def test_basic_scoring(self):
        from scoring.scorer import IntegrityScorer
        scorer = IntegrityScorer(profile="academic")
        scorer.set_metadata_score(95)
        scorer.set_existence_score(98)
        scorer.set_graph_health_score(72)
        scorer.set_semantic_score(85)
        scorer.set_integrity_risk_score(92)
        scorer.set_reference_counts(50, 48, 2)

        report = scorer.compute()
        assert 0 <= report.overall_score <= 100
        assert report.grade in ("A", "B", "C", "D", "F")
        assert report.profile_used == "academic"
        assert report.total_references == 50

    def test_perfect_score(self):
        from scoring.scorer import IntegrityScorer
        scorer = IntegrityScorer(profile="academic")
        scorer.set_metadata_score(100)
        scorer.set_existence_score(100)
        scorer.set_graph_health_score(100)
        scorer.set_semantic_score(100)
        scorer.set_integrity_risk_score(100)

        report = scorer.compute()
        assert abs(report.overall_score - 100.0) < 0.1
        assert report.grade == "A"

    def test_failing_score(self):
        from scoring.scorer import IntegrityScorer
        scorer = IntegrityScorer(profile="academic")
        scorer.set_metadata_score(20)
        scorer.set_existence_score(30)
        scorer.set_graph_health_score(10)
        scorer.set_semantic_score(15)
        scorer.set_integrity_risk_score(25)

        report = scorer.compute()
        assert report.overall_score < 50
        assert report.grade == "F"

    def test_unknown_profile(self):
        from scoring.scorer import IntegrityScorer
        with pytest.raises(ValueError):
            IntegrityScorer(profile="unknown")

    def test_different_profiles_different_scores(self):
        from scoring.scorer import IntegrityScorer
        # Same raw scores, different profiles
        scores = {
            "metadata": 90, "existence": 90,
            "graph": 50, "semantic": 90, "risk": 90,
        }

        scorer_academic = IntegrityScorer(profile="academic")
        scorer_legal = IntegrityScorer(profile="legal")

        for scorer in [scorer_academic, scorer_legal]:
            scorer.set_metadata_score(scores["metadata"])
            scorer.set_existence_score(scores["existence"])
            scorer.set_graph_health_score(scores["graph"])
            scorer.set_semantic_score(scores["semantic"])
            scorer.set_integrity_risk_score(scores["risk"])

        report_academic = scorer_academic.compute()
        report_legal = scorer_legal.compute()

        # Scores should differ due to different weight profiles
        # (graph_health is 50, and academic weights it 0.20 vs legal 0.05)
        assert report_academic.overall_score != report_legal.overall_score

    def test_compute_metadata_score(self):
        from scoring.scorer import IntegrityScorer
        results = [
            {"title_match": "OK", "author_match": "OK", "year_match": "OK", "venue_match": "WARN"},
            {"title_match": "OK", "author_match": "FAIL", "year_match": "OK", "venue_match": "OK"},
        ]
        score, details = IntegrityScorer.compute_metadata_score(results)
        assert 0 <= score <= 100
        assert details["total_checks"] == 8
        assert details["fail"] == 1
        assert details["warn"] == 1

    def test_compute_existence_score(self):
        from scoring.scorer import IntegrityScorer
        score, details = IntegrityScorer.compute_existence_score(50, 47, retracted_count=1)
        assert score < 100
        assert details["found"] == 47
        assert details["retracted"] == 1

    def test_report_summary(self):
        from scoring.scorer import IntegrityScorer
        scorer = IntegrityScorer(profile="academic")
        scorer.set_metadata_score(90)
        scorer.set_existence_score(95)
        scorer.set_reference_counts(30, 28, 1)

        report = scorer.compute()
        summary = report.summary()
        assert "overall_score" in summary
        assert "dimensions" in summary
        assert "warnings" in summary
        assert len(summary["warnings"]) >= 1  # high_risk warning

    def test_grade_boundaries(self):
        from scoring.scorer import IntegrityScorer
        assert IntegrityScorer._score_to_grade(95) == "A"
        assert IntegrityScorer._score_to_grade(90) == "A"
        assert IntegrityScorer._score_to_grade(85) == "B"
        assert IntegrityScorer._score_to_grade(75) == "C"
        assert IntegrityScorer._score_to_grade(65) == "D"
        assert IntegrityScorer._score_to_grade(55) == "F"
