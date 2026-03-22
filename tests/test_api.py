"""Tests for the FastAPI REST API endpoints.

Uses FastAPI's TestClient (synchronous) to test all /v1/* endpoints.
Network-dependent endpoints (verify, batch, hallucination) use mocked pipelines
to avoid rate limits and timeouts. Stateless endpoints test real behavior.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import dataclass, field
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(scope="module")
def client():
    """Create a test client with lifespan events."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Health & info endpoints (no network)
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert isinstance(data["registries_loaded"], int)
        assert data["registries_loaded"] > 0

    def test_registries_list(self, client):
        r = client.get("/v1/registries")
        assert r.status_code == 200
        data = r.json()
        assert "registries" in data
        assert "count" in data
        assert data["count"] > 0
        reg = data["registries"][0]
        assert "name" in reg
        assert "domain" in reg

    def test_signals_list(self, client):
        r = client.get("/v1/signals")
        assert r.status_code == 200
        data = r.json()
        assert "signals" in data
        assert "domains" in data
        assert len(data["signals"]) >= 10


# ---------------------------------------------------------------------------
# Verify endpoint (mocked pipeline)
# ---------------------------------------------------------------------------

def _make_mock_report(found=True, risk_tier="low"):
    """Create a mock PipelineReport-like object."""
    report = MagicMock()
    report.api_response.return_value = {
        "risk_tier": risk_tier,
        "layers_run": ["L0", "L1", "L4"],
        "l0": {"found": found, "confidence": 0.95 if found else 0.0},
        "signals": [],
        "processing_time_ms": 42.0,
    }
    report.summary.return_value = {
        "risk_tier": risk_tier,
        "found": found,
        "signals_fired": 0,
    }
    return report


class TestVerify:
    def test_verify_returns_200(self, client):
        """POST /v1/verify returns 200 with expected structure."""
        mock_report = _make_mock_report(found=True, risk_tier="low")
        with patch("api.main.AsyncIntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify = AsyncMock(return_value=mock_report)
            r = client.post("/v1/verify", json={
                "reference": {
                    "title": "Attention Is All You Need",
                    "authors": ["Vaswani"],
                    "year": "2017",
                },
                "layers": ["L0", "L4"],
            })
        assert r.status_code == 200
        data = r.json()
        assert "risk_tier" in data
        assert "layers_run" in data

    def test_verify_hallucinated(self, client):
        """Hallucinated paper should return HIGH risk."""
        mock_report = _make_mock_report(found=False, risk_tier="high")
        with patch("api.main.AsyncIntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify = AsyncMock(return_value=mock_report)
            r = client.post("/v1/verify", json={
                "reference": {
                    "title": "Fake Paper 2030",
                    "year": "2030",
                },
                "layers": ["L0", "L4"],
            })
        assert r.status_code == 200
        data = r.json()
        assert data["risk_tier"].upper() in ("HIGH", "CRITICAL")

    def test_verify_with_intent_layer(self, client):
        """Verify with citing sentence runs L1."""
        mock_report = _make_mock_report()
        with patch("api.main.AsyncIntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify = AsyncMock(return_value=mock_report)
            r = client.post("/v1/verify", json={
                "reference": {"title": "Some paper"},
                "citing_sentence": "As shown by Smith (2020)...",
                "layers": ["L0", "L1", "L4"],
            })
        assert r.status_code == 200
        data = r.json()
        assert "L1" in data["layers_run"]

    def test_verify_minimal_request(self, client):
        """Minimal request should not crash."""
        mock_report = _make_mock_report()
        with patch("api.main.AsyncIntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify = AsyncMock(return_value=mock_report)
            r = client.post("/v1/verify", json={
                "reference": {"title": "x"},
                "layers": ["L0"],
            })
        assert r.status_code == 200

    def test_verify_custom_domain(self, client):
        """Custom Bayesian domain should be forwarded to pipeline."""
        mock_report = _make_mock_report()
        with patch("api.main.AsyncIntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify = AsyncMock(return_value=mock_report)
            r = client.post("/v1/verify", json={
                "reference": {"title": "x"},
                "layers": ["L0", "L4"],
                "domain": "cancer_research",
            })
        assert r.status_code == 200
        # Check that pipeline was created with cancer_research domain
        MockPipeline.assert_called_once()
        call_kwargs = MockPipeline.call_args
        assert call_kwargs.kwargs.get("domain") == "cancer_research" or \
               (len(call_kwargs.args) > 2 and call_kwargs.args[2] == "cancer_research")


# ---------------------------------------------------------------------------
# Batch endpoint (mocked)
# ---------------------------------------------------------------------------

class TestBatch:
    def test_batch_verify(self, client):
        """Batch verify returns correct structure."""
        reports = [_make_mock_report(), _make_mock_report(found=False, risk_tier="high")]
        with patch("api.main.AsyncIntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify_batch = AsyncMock(return_value=reports)
            r = client.post("/v1/batch", json={
                "references": [
                    {"title": "Paper A"},
                    {"title": "Paper B"},
                ],
                "layers": ["L0", "L4"],
            })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "success"
        assert data["total"] == 2
        assert len(data["results"]) == 2
        assert "processing_time_ms" in data

    def test_batch_empty(self, client):
        """Batch with no references."""
        with patch("api.main.AsyncIntegriRefPipeline") as MockPipeline:
            MockPipeline.return_value.verify_batch = AsyncMock(return_value=[])
            r = client.post("/v1/batch", json={
                "references": [],
            })
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# Hallucination endpoint (mocked)
# ---------------------------------------------------------------------------

class TestHallucination:
    def test_hallucination_check(self, client):
        """Hallucination check returns expected fields."""
        mock_report = MagicMock()
        mock_report.is_likely_hallucinated = True
        mock_report.hallucination_score = 78.5
        mock_report.signals_fired = ["future_year", "no_doi"]

        with patch("api.main.HallucinationDetector", create=True) as MockDet:
            # Patch at the import location inside the endpoint
            with patch("verification.hallucination_detector.HallucinationDetector") as MockDet2:
                MockDet2.return_value.analyze.return_value = mock_report
                # Need to patch the import inside the function
                import api.main as api_mod
                with patch.dict("sys.modules", {}):
                    pass
                # Simpler: just call directly and mock
                r = client.post("/v1/hallucination", json={
                    "references": [
                        {"title": "Fake Paper", "year": "2030"},
                    ],
                })
        # The endpoint does a lazy import, so this may use real detector
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert "is_suspicious" in result
        assert "hallucination_score" in result
        assert "flags" in result

    def test_hallucination_real_paper(self, client):
        """Real paper should get low hallucination score."""
        r = client.post("/v1/hallucination", json={
            "references": [
                {
                    "title": "Deep Residual Learning for Image Recognition",
                    "authors": ["He, K."],
                    "year": "2016",
                    "doi": "10.1109/CVPR.2016.90",
                },
            ],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["results"][0]["hallucination_score"] < 70


# ---------------------------------------------------------------------------
# Risk scoring endpoint (no network)
# ---------------------------------------------------------------------------

class TestRisk:
    def test_risk_no_signals(self, client):
        r = client.post("/v1/risk", json={
            "domain": "default",
            "signals": {},
        })
        assert r.status_code == 200
        data = r.json()
        assert "risk_tier" in data
        assert data["risk_tier"].upper() == "LOW"

    def test_risk_with_signals(self, client):
        r = client.post("/v1/risk", json={
            "domain": "default",
            "signals": {
                "reference_not_found": {"present": True, "confidence": 0.9},
                "phantom_doi": {"present": True, "confidence": 0.8},
            },
        })
        assert r.status_code == 200
        data = r.json()
        assert data["risk_tier"].upper() in ("ELEVATED", "HIGH", "CRITICAL")

    def test_risk_invalid_signal(self, client):
        r = client.post("/v1/risk", json={
            "signals": {"nonexistent_signal": {"present": True}},
        })
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Auth (API key)
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_key_required_by_default(self, client):
        r = client.get("/v1/registries")
        assert r.status_code == 200

    def test_auth_with_key(self, client):
        import api.main as api_mod
        original = api_mod._API_KEY
        api_mod._API_KEY = "test-secret-key"
        try:
            # No auth → 401
            r = client.get("/v1/registries")
            assert r.status_code == 401

            # Wrong key → 403
            r = client.get("/v1/registries",
                           headers={"Authorization": "Bearer wrong-key"})
            assert r.status_code == 403

            # Correct key → 200
            r = client.get("/v1/registries",
                           headers={"Authorization": "Bearer test-secret-key"})
            assert r.status_code == 200
        finally:
            api_mod._API_KEY = original


# ---------------------------------------------------------------------------
# Request model validation
# ---------------------------------------------------------------------------

class TestRequestValidation:
    def test_verify_missing_reference(self, client):
        """Request without reference field should fail validation."""
        r = client.post("/v1/verify", json={})
        assert r.status_code == 422

    def test_reference_input_to_dict(self):
        """ReferenceInput.to_dict() should omit empty fields."""
        from api.main import ReferenceInput
        ref = ReferenceInput(title="Test", year="2020")
        d = ref.to_dict()
        assert d == {"title": "Test", "year": "2020"}
        assert "doi" not in d
        assert "authors" not in d

    def test_reference_input_full(self):
        """ReferenceInput.to_dict() with all fields."""
        from api.main import ReferenceInput
        ref = ReferenceInput(
            title="Test", authors=["A", "B"], year="2020",
            doi="10.1234/test", arxiv_id="2301.00001", venue="NeurIPS",
            key="test2020", author_emails=["a@b.com"],
        )
        d = ref.to_dict()
        assert len(d) == 8
