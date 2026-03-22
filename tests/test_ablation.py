"""Tests for the ablation experiment framework.

Tests the ablation logic with mocked pipeline results to avoid network calls.
"""

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

from benchmarks.ablation import (
    AblationCase,
    AblationResult,
    risk_tier_to_label,
    run_ablation,
    format_ablation_report,
    ABLATION_CASES,
    STANDARD_CONFIGS,
)


class TestRiskTierMapping:
    def test_low_is_safe(self):
        assert risk_tier_to_label("low") == "SAFE"
        assert risk_tier_to_label("LOW") == "SAFE"

    def test_elevated_is_suspicious(self):
        assert risk_tier_to_label("elevated") == "SUSPICIOUS"
        assert risk_tier_to_label("ELEVATED") == "SUSPICIOUS"

    def test_high_is_fabricated(self):
        assert risk_tier_to_label("high") == "FABRICATED"
        assert risk_tier_to_label("HIGH") == "FABRICATED"

    def test_critical_is_fabricated(self):
        assert risk_tier_to_label("critical") == "FABRICATED"
        assert risk_tier_to_label("CRITICAL") == "FABRICATED"


class TestAblationCases:
    def test_all_cases_have_labels(self):
        for case in ABLATION_CASES:
            assert case.expected_label in ("SAFE", "SUSPICIOUS", "FABRICATED")

    def test_cases_cover_all_labels(self):
        labels = {c.expected_label for c in ABLATION_CASES}
        assert "SAFE" in labels
        assert "SUSPICIOUS" in labels
        assert "FABRICATED" in labels

    def test_minimum_case_count(self):
        assert len(ABLATION_CASES) >= 6


class TestStandardConfigs:
    def test_all_configs_have_l0(self):
        for name, layers in STANDARD_CONFIGS.items():
            assert "L0" in layers, f"{name} missing L0"

    def test_full_config_has_all_layers(self):
        assert "full_L0-L4" in STANDARD_CONFIGS
        full = STANDARD_CONFIGS["full_L0-L4"]
        for layer in ["L0", "L1", "L2", "L3", "L4"]:
            assert layer in full


class TestAblationRunner:
    def test_run_with_mocked_pipeline(self):
        """Run ablation with mocked pipeline to test framework logic."""
        cases = [
            AblationCase(name="safe", ref={"title": "x"}, expected_label="SAFE"),
            AblationCase(name="fab", ref={"title": "y"}, expected_label="FABRICATED"),
        ]

        mock_reports = [MagicMock(), MagicMock()]
        mock_reports[0].risk_tier = "low"
        mock_reports[0].signals = []
        mock_reports[1].risk_tier = "high"
        mock_reports[1].signals = [MagicMock()]

        with patch("benchmarks.ablation.IntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify.side_effect = mock_reports
            mock_disc = MagicMock()

            results = run_ablation(
                cases=cases,
                layer_configs={"test": ["L0"]},
                discovery=mock_disc,
            )

        assert "test" in results
        r = results["test"]
        assert r.predictions == ["SAFE", "FABRICATED"]
        assert r.labels == ["SAFE", "FABRICATED"]
        assert r.elapsed_ms > 0
        assert len(r.case_details) == 2
        assert r.case_details[0]["correct"] is True
        assert r.case_details[1]["correct"] is True


class TestAblationReport:
    def test_format_report(self):
        result = AblationResult(
            config_name="test",
            layers=["L0"],
            predictions=["SAFE", "FABRICATED"],
            labels=["SAFE", "FABRICATED"],
            case_details=[
                {"name": "a", "expected": "SAFE", "predicted": "SAFE",
                 "risk_tier": "low", "correct": True, "signals": 0},
                {"name": "b", "expected": "FABRICATED", "predicted": "FABRICATED",
                 "risk_tier": "high", "correct": True, "signals": 2},
            ],
            elapsed_ms=100.0,
        )

        report = format_ablation_report({"test": result})
        assert "Ablation Report" in report
        assert "test" in report
        assert "SAFE" in report
        assert "FABRICATED" in report

    def test_format_report_shows_misses(self):
        result = AblationResult(
            config_name="test",
            layers=["L0"],
            predictions=["SAFE", "SAFE"],
            labels=["SAFE", "FABRICATED"],
            case_details=[
                {"name": "a", "expected": "SAFE", "predicted": "SAFE",
                 "risk_tier": "low", "correct": True, "signals": 0},
                {"name": "b", "expected": "FABRICATED", "predicted": "SAFE",
                 "risk_tier": "low", "correct": False, "signals": 0},
            ],
            elapsed_ms=50.0,
        )

        report = format_ablation_report({"test": result})
        assert "Misclassified" in report
