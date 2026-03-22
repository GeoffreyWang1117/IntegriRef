"""IntegriRef REST API — FastAPI application.

Exposes the unified L0-L4 pipeline as a REST service.

Usage:
    uvicorn api.main:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /v1/verify         — Single reference verification
    POST /v1/batch          — Batch reference verification
    POST /v1/hallucination  — Fast hallucination check
    GET  /v1/graph/{doi}    — Citation graph analysis
    POST /v1/risk           — Bayesian risk scoring
    GET  /v1/health         — Service health check
    GET  /v1/registries     — List available registries
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.discovery import RegistryDiscovery
from core.async_discovery import AsyncRegistryDiscovery
from core.async_pipeline import AsyncIntegriRefPipeline
from core.pipeline import IntegriRefPipeline, PipelineReport
from registries.academic import ALL_ACADEMIC
from registries.patents import ALL_PATENTS
from registries.legal import ALL_LEGAL
from registries.government import ALL_GOVERNMENT
from registries.financial import ALL_FINANCIAL
from registries.standards import ALL_STANDARDS
from scoring.bayesian import (
    BayesianRiskScorer,
    BayesianRiskReport,
    SignalObservation,
    RiskTier,
    SIGNAL_DEFINITIONS,
    DOMAIN_PRIORS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state (initialised in lifespan)
# ---------------------------------------------------------------------------

_discovery: Optional[RegistryDiscovery] = None
_async_discovery: Optional[AsyncRegistryDiscovery] = None
_pipeline: Optional[IntegriRefPipeline] = None
_async_pipeline: Optional[AsyncIntegriRefPipeline] = None


def _init_discovery() -> tuple[RegistryDiscovery, AsyncRegistryDiscovery]:
    """Initialise both sync and async registry discovery with all adapters."""
    all_registries = (
        ALL_ACADEMIC + ALL_PATENTS + ALL_LEGAL
        + ALL_GOVERNMENT + ALL_FINANCIAL + ALL_STANDARDS
    )
    # Sync discovery (fallback)
    sync_disc = RegistryDiscovery()
    sync_disc.register_all(all_registries)
    # Async discovery (wraps sync adapters with executor)
    async_disc = AsyncRegistryDiscovery()
    async_disc.register_all(all_registries)
    return sync_disc, async_disc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global _discovery, _async_discovery, _pipeline, _async_pipeline
    logger.info("IntegriRef API starting up...")
    _discovery, _async_discovery = _init_discovery()
    domain = os.getenv("INTEGRIREF_DOMAIN", "default")
    _pipeline = IntegriRefPipeline(discovery=_discovery, domain=domain)
    _async_pipeline = AsyncIntegriRefPipeline(
        discovery=_async_discovery, domain=domain)
    count = len(_discovery._registries) if hasattr(_discovery, '_registries') else 0
    logger.info("Loaded %d registry adapters (sync + async)", count)

    # Log Rust acceleration status
    try:
        import integriref_core
        logger.info("Rust acceleration: ENABLED (integriref_core)")
    except ImportError:
        logger.info("Rust acceleration: DISABLED (fallback to Python)")
    yield
    # Cleanup async session
    from core.async_registry import close_shared_session
    await close_shared_session()
    logger.info("IntegriRef API shut down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IntegriRef API",
    description="Multi-layer reference integrity verification engine (L0-L4).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth (simple API key)
# ---------------------------------------------------------------------------

_API_KEY = os.getenv("INTEGRIREF_API_KEY", "")


async def verify_api_key(authorization: str = Header(default="")):
    """Optional API key verification."""
    if not _API_KEY:
        return  # No key configured → open access
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != _API_KEY:
        raise HTTPException(403, "Invalid API key")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ReferenceInput(BaseModel):
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: str = ""
    doi: str = ""
    arxiv_id: str = ""
    venue: str = ""
    key: str = ""
    author_emails: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        d = {}
        if self.title:
            d["title"] = self.title
        if self.authors:
            d["authors"] = self.authors
        if self.year:
            d["year"] = self.year
        if self.doi:
            d["doi"] = self.doi
        if self.arxiv_id:
            d["arxiv_id"] = self.arxiv_id
        if self.venue:
            d["venue"] = self.venue
        if self.key:
            d["key"] = self.key
        if self.author_emails:
            d["author_emails"] = self.author_emails
        return d


class VerifyRequest(BaseModel):
    reference: ReferenceInput
    citing_sentence: str = ""
    abstract: str = ""
    full_text: str = ""
    layers: list[str] = Field(
        default=["L0", "L1", "L2", "L3", "L4"],
        description="Which layers to run",
    )
    domains: list[str] = Field(
        default_factory=list,
        description="Domain filter (e.g. ['academic'])",
    )
    domain: str = Field(
        default="default",
        description="Bayesian prior domain (e.g. 'cancer_research')",
    )


class BatchVerifyRequest(BaseModel):
    references: list[ReferenceInput]
    citing_sentences: list[str] = Field(default_factory=list)
    abstracts: list[str] = Field(default_factory=list)
    full_text: str = ""
    layers: list[str] = Field(default=["L0", "L4"])
    domains: list[str] = Field(default_factory=list)
    domain: str = "default"


class HallucinationRequest(BaseModel):
    references: list[ReferenceInput]


class RiskRequest(BaseModel):
    domain: str = "default"
    signals: dict[str, dict] = Field(
        default_factory=dict,
        description="signal_name → {present: bool, confidence: float}",
    )


class GraphRequest(BaseModel):
    hops: int = Field(default=1, ge=1, le=2)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/health")
async def health():
    """Service health check."""
    registry_count = 0
    if _discovery and hasattr(_discovery, '_registries'):
        registry_count = len(_discovery._registries)
    return {
        "status": "ok",
        "version": "0.1.0",
        "registries_loaded": registry_count,
    }


@app.get("/v1/registries")
async def list_registries(_=Depends(verify_api_key)):
    """List all available registry adapters."""
    if not _discovery:
        raise HTTPException(503, "Service not initialised")
    adapters = []
    if hasattr(_discovery, '_registries'):
        for a in _discovery._registries:
            info = a.info()
            adapters.append({
                "name": info.name,
                "domain": info.domain,
                "base_url": info.base_url,
                "coverage": info.coverage,
            })
    return {"registries": adapters, "count": len(adapters)}


@app.post("/v1/verify")
async def verify_reference(req: VerifyRequest, _=Depends(verify_api_key)):
    """Verify a single reference through the async L0-L4 pipeline."""
    if not _async_pipeline:
        raise HTTPException(503, "Service not initialised")

    # Use async pipeline for non-blocking I/O
    pipeline = AsyncIntegriRefPipeline(
        discovery=_async_discovery,
        layers=req.layers,
        domain=req.domain,
    )

    report = await pipeline.verify(
        ref=req.reference.to_dict(),
        citing_sentence=req.citing_sentence,
        full_text=req.full_text,
        abstract=req.abstract,
        domains=req.domains or None,
    )
    return report.api_response()


@app.post("/v1/batch")
async def batch_verify(req: BatchVerifyRequest, _=Depends(verify_api_key)):
    """Batch verify multiple references using async concurrency."""
    if not _async_pipeline:
        raise HTTPException(503, "Service not initialised")

    pipeline = AsyncIntegriRefPipeline(
        discovery=_async_discovery,
        layers=req.layers,
        domain=req.domain,
    )

    start = time.monotonic()
    refs = [r.to_dict() for r in req.references]
    reports = await pipeline.verify_batch(
        references=refs,
        citing_sentences=req.citing_sentences or None,
        full_text=req.full_text,
        abstracts=req.abstracts or None,
        domains=req.domains or None,
    )
    elapsed = (time.monotonic() - start) * 1000

    return {
        "status": "success",
        "total": len(reports),
        "results": [r.summary() for r in reports],
        "processing_time_ms": round(elapsed, 1),
    }


@app.post("/v1/hallucination")
async def hallucination_check(req: HallucinationRequest,
                               _=Depends(verify_api_key)):
    """Fast hallucination check (L0 only, no heavy model inference)."""
    if not _discovery:
        raise HTTPException(503, "Service not initialised")

    from verification.hallucination_detector import HallucinationDetector
    detector = HallucinationDetector()

    results = []
    for ref_input in req.references:
        ref = ref_input.to_dict()
        report = detector.analyze(ref)
        results.append({
            "title": ref.get("title", "")[:80],
            "is_suspicious": report.is_likely_hallucinated,
            "hallucination_score": round(report.hallucination_score, 1),
            "flags": [{"type": f.flag_type, "severity": f.severity,
                        "detail": f.detail} for f in report.flags],
        })

    return {"results": results}


@app.get("/v1/graph/{doi:path}")
async def graph_analysis(doi: str, hops: int = 1,
                          _=Depends(verify_api_key)):
    """Build and analyse a citation graph for a DOI."""
    try:
        from graph.openalex_builder import OpenAlexGraphBuilder
        from graph.anomaly import AnomalyDetector
        from graph.metrics import GraphMetrics

        builder = OpenAlexGraphBuilder()
        graph = builder.build_from_doi(doi, two_hop=(hops >= 2))

        if not graph or graph.node_count < 2:
            return {
                "doi": doi,
                "nodes": 0,
                "edges": 0,
                "anomalies": [],
                "health_score": None,
                "message": "Could not build graph — DOI not found in OpenAlex",
            }

        detector = AnomalyDetector(graph)
        anomalies = []
        anomalies.extend(detector.detect_orphan_clusters())
        anomalies.extend(detector.detect_temporal_anomalies())
        anomalies.extend(detector.detect_excessive_self_citation())
        anomalies.extend(detector.detect_citation_rings())
        anomalies.extend(detector.detect_benford_violation())
        anomalies.extend(detector.detect_reciprocal_citations())
        anomalies.extend(detector.detect_citation_burst())

        metrics = GraphMetrics(graph)
        health = metrics.compute_health()

        return {
            "doi": doi,
            "nodes": graph.node_count,
            "edges": graph.edge_count,
            "health_score": round(health.overall_health_score, 2),
            "dimensions": {
                "interconnection": round(health.interconnection_score, 2),
                "temporal": round(health.temporal_consistency_score, 2),
                "self_citation": round(health.self_citation_score, 2),
                "benford": round(health.benford_score, 2),
                "reciprocal": round(health.reciprocal_score, 2),
            },
            "anomalies": [
                {
                    "type": a.anomaly_type.value if hasattr(a.anomaly_type, 'value') else str(a.anomaly_type),
                    "severity": round(a.severity, 2),
                    "description": a.description,
                }
                for a in anomalies
            ],
        }
    except Exception as e:
        logger.error("Graph analysis error: %s", e)
        raise HTTPException(500, f"Graph analysis failed: {e}")


@app.post("/v1/risk")
async def risk_score(req: RiskRequest, _=Depends(verify_api_key)):
    """Compute Bayesian risk score from raw signals."""
    scorer = BayesianRiskScorer(domain=req.domain)

    for signal_name, signal_data in req.signals.items():
        if signal_name not in SIGNAL_DEFINITIONS:
            raise HTTPException(
                400, f"Unknown signal: {signal_name}. "
                     f"Valid: {sorted(SIGNAL_DEFINITIONS.keys())}")
        scorer.observe(
            signal_name=signal_name,
            fired=signal_data.get("present", False),
            confidence=signal_data.get("confidence", 1.0),
        )

    report = scorer.compute()
    return report.summary()


@app.get("/v1/signals")
async def list_signals(_=Depends(verify_api_key)):
    """List all available Bayesian signal definitions."""
    return {
        "signals": {
            name: {
                "lr_positive": sig.lr_positive,
                "lr_negative": sig.lr_negative,
                "category": sig.category,
                "description": sig.description,
            }
            for name, sig in SIGNAL_DEFINITIONS.items()
        },
        "domains": DOMAIN_PRIORS,
    }
