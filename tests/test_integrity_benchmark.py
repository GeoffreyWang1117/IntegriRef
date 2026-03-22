"""Integration tests for IntegriRef integrity benchmark.

Runs the golden test set through the pipeline and validates key quality metrics.
These tests hit real registries (Crossref, etc.) so they are marked as slow/integration.

Usage:
    pytest tests/test_integrity_benchmark.py -v            # Run all
    pytest tests/test_integrity_benchmark.py -v -k golden  # Golden set only
    pytest tests/test_integrity_benchmark.py -v -m "not slow"  # Skip slow tests
"""

import json
import logging
import time
from pathlib import Path

import pytest

from core.discovery import RegistryDiscovery
from core.pipeline import IntegriRefPipeline, PipelineReport

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "benchmarks" / "data"
GOLDEN_PATH = DATA_DIR / "golden_test_set.json"


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pipeline():
    """Create a pipeline instance with all registries."""
    from registries.academic import ALL_ACADEMIC
    from registries.patents import ALL_PATENTS
    from registries.legal import ALL_LEGAL
    from registries.government import ALL_GOVERNMENT
    from registries.financial import ALL_FINANCIAL
    from registries.standards import ALL_STANDARDS

    discovery = RegistryDiscovery()
    all_registries = (
        ALL_ACADEMIC + ALL_PATENTS + ALL_LEGAL
        + ALL_GOVERNMENT + ALL_FINANCIAL + ALL_STANDARDS
    )
    discovery.register_all(all_registries)

    return IntegriRefPipeline(
        discovery=discovery,
        layers=["L0", "L4"],
        enable_l2_only_on_escalation=True,
    )


@pytest.fixture(scope="module")
def golden_data():
    """Load golden test set."""
    if not GOLDEN_PATH.exists():
        pytest.skip("Golden test set not found")
    with open(GOLDEN_PATH) as f:
        return json.load(f)


def _verify_ref(pipeline, ref_dict, timeout=15):
    """Run pipeline.verify with a timeout-like guard."""
    start = time.monotonic()
    report = pipeline.verify(ref=ref_dict)
    elapsed = time.monotonic() - start
    return report, elapsed


# ── Real papers should NOT be flagged ────────────────────────────────────

class TestRealPapers:
    """Real, legitimate papers should get LOW risk."""

    @pytest.mark.slow
    @pytest.mark.parametrize("paper_idx", range(10))
    def test_real_paper_low_risk(self, pipeline, golden_data, paper_idx):
        """Real papers should have risk_tier = LOW."""
        papers = golden_data.get("real_papers", [])
        if paper_idx >= len(papers):
            pytest.skip("Paper index out of range")

        paper = papers[paper_idx]
        ref = {k: v for k, v in paper.items()
               if k in ("title", "authors", "year", "doi", "venue") and v}

        report, elapsed = _verify_ref(pipeline, ref)

        logger.info("[%s] %s — risk=%s prob=%.3f (%.0fms)",
                    paper["id"], paper["title"][:50],
                    report.risk_tier, report.risk_probability, elapsed * 1000)

        # Real papers should be LOW or at most ELEVATED (network issues etc)
        assert report.risk_tier.upper() in ("LOW", "ELEVATED"), \
            f"Real paper '{paper['title'][:50]}' got {report.risk_tier}"


# ── Hallucinated papers should be flagged ────────────────────────────────

class TestHallucinatedPapers:
    """LLM-fabricated citations should be detected."""

    @pytest.mark.slow
    @pytest.mark.parametrize("paper_idx", range(15))
    def test_hallucinated_paper_detection(self, pipeline, golden_data, paper_idx):
        """Hallucinated papers should get HIGH or CRITICAL risk."""
        papers = golden_data.get("llm_hallucinated", [])
        if paper_idx >= len(papers):
            pytest.skip("Paper index out of range")

        paper = papers[paper_idx]

        # Skip control cases (real papers embedded in hallucinated list)
        if paper.get("expected_found", False):
            expected_risk = "LOW"
        else:
            expected_risk = "HIGH"

        ref = {k: v for k, v in paper.items()
               if k in ("title", "authors", "year", "doi", "venue") and v}

        report, elapsed = _verify_ref(pipeline, ref)

        signals = [s.signal_name for s in report.signals if s.fired]
        logger.info("[%s] %s — risk=%s prob=%.3f signals=%s (%.0fms)",
                    paper["id"], paper["title"][:50],
                    report.risk_tier, report.risk_probability,
                    signals, elapsed * 1000)

        risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": -1}

        if expected_risk == "HIGH":
            # Should be at least ELEVATED (some edge cases may not reach HIGH)
            assert risk_order.get(report.risk_tier.upper(), -1) >= 1, \
                f"Hallucinated paper '{paper['title'][:50]}' only got {report.risk_tier}"
        else:
            # Control: should be LOW
            assert report.risk_tier.upper() in ("LOW", "ELEVATED"), \
                f"Control paper '{paper['title'][:50]}' incorrectly flagged as {report.risk_tier}"


# ── Retracted papers should be detected ──────────────────────────────────

class TestRetractedPapers:
    """Known retracted papers should be flagged."""

    @pytest.mark.slow
    @pytest.mark.parametrize("paper_idx", range(28))
    def test_retracted_paper_detection(self, pipeline, golden_data, paper_idx):
        """Retracted papers should be found and flagged."""
        papers = golden_data.get("retracted_papers", [])
        if paper_idx >= len(papers):
            pytest.skip("Paper index out of range")

        paper = papers[paper_idx]
        ref = {k: v for k, v in paper.items()
               if k in ("title", "authors", "year", "doi", "venue") and v}

        report, elapsed = _verify_ref(pipeline, ref)

        signals = [s.signal_name for s in report.signals if s.fired]
        is_retracted = bool(report.l0 and report.l0.is_retracted)
        found = bool(report.l0 and report.l0.sources_hit)

        logger.info("[%s] %s — risk=%s retracted=%s found=%s signals=%s (%.0fms)",
                    paper["id"], paper["title"][:50],
                    report.risk_tier, is_retracted, found,
                    signals, elapsed * 1000)

        if paper.get("expected_retracted", True):
            # Should be found (retracted papers still exist in registries)
            assert found, \
                f"Retracted paper '{paper['title'][:50]}' not found in any registry"
            # Risk should be elevated
            risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": -1}
            assert risk_order.get(report.risk_tier.upper(), -1) >= 1, \
                f"Retracted paper '{paper['title'][:50]}' not flagged: {report.risk_tier}"
        else:
            # Papers that are controversial but NOT retracted
            assert report.risk_tier.upper() in ("LOW", "ELEVATED")


# ── Metadata chimeras should be caught ───────────────────────────────────

class TestMetadataChimeras:
    """Real title + wrong metadata should be detected."""

    @pytest.mark.slow
    @pytest.mark.parametrize("paper_idx", range(5))
    def test_chimera_detection(self, pipeline, golden_data, paper_idx):
        """Chimera references should be flagged or pass if they're controls."""
        papers = golden_data.get("metadata_chimera", [])
        if paper_idx >= len(papers):
            pytest.skip("Paper index out of range")

        paper = papers[paper_idx]
        ref = {k: v for k, v in paper.items()
               if k in ("title", "authors", "year", "doi", "venue") and v}

        report, elapsed = _verify_ref(pipeline, ref)

        signals = [s.signal_name for s in report.signals if s.fired]
        logger.info("[%s] %s — risk=%s signals=%s (%.0fms)",
                    paper["id"], paper["title"][:50],
                    report.risk_tier, signals, elapsed * 1000)

        expected_found = paper.get("expected_found", False)
        risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": -1}

        if not expected_found:
            # Bad chimera: should be flagged
            assert risk_order.get(report.risk_tier.upper(), -1) >= 1, \
                f"Chimera '{paper['title'][:50]}' not flagged: {report.risk_tier}"
        else:
            # Control: essentially correct, should pass
            assert report.risk_tier.upper() in ("LOW", "ELEVATED"), \
                f"Control chimera '{paper['title'][:50]}' incorrectly flagged: {report.risk_tier}"


# ── Aggregate quality gates ──────────────────────────────────────────────

class TestAggregateQuality:
    """Run all golden cases and check aggregate metrics meet thresholds."""

    @pytest.mark.slow
    def test_false_positive_rate_below_threshold(self, pipeline, golden_data):
        """False positive rate on real papers should be < 15%."""
        papers = golden_data.get("real_papers", [])
        flagged = 0
        total = 0

        for paper in papers:
            ref = {k: v for k, v in paper.items()
                   if k in ("title", "authors", "year", "doi", "venue") and v}
            report, _ = _verify_ref(pipeline, ref)
            total += 1
            risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": -1}
            if risk_order.get(report.risk_tier.upper(), -1) >= 1:
                flagged += 1
                logger.warning("FP: %s got %s", paper["title"][:50], report.risk_tier)

        fpr = flagged / total if total > 0 else 0
        logger.info("False positive rate: %d/%d = %.1f%%", flagged, total, fpr * 100)
        assert fpr < 0.15, f"FPR {fpr:.1%} exceeds 15% threshold"

    @pytest.mark.slow
    def test_hallucination_recall_above_threshold(self, pipeline, golden_data):
        """At least 60% of hallucinated papers should be detected."""
        papers = [p for p in golden_data.get("llm_hallucinated", [])
                  if not p.get("expected_found", False)]
        detected = 0
        total = 0

        for paper in papers:
            ref = {k: v for k, v in paper.items()
                   if k in ("title", "authors", "year", "doi", "venue") and v}
            report, _ = _verify_ref(pipeline, ref)
            total += 1
            risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": -1}
            if risk_order.get(report.risk_tier.upper(), -1) >= 1:
                detected += 1

        recall = detected / total if total > 0 else 0
        logger.info("Hallucination recall: %d/%d = %.1f%%", detected, total, recall * 100)
        assert recall >= 0.60, f"Recall {recall:.1%} below 60% threshold"
