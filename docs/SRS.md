# Software Requirements Specification (SRS)

**Project**: IntegriRef — Multi-Layer Reference Integrity Verification Engine
**Version**: 0.1.0
**Date**: 2026-03-16
**Status**: Draft

---

## 1. Introduction

### 1.1 Purpose

This document specifies the functional and non-functional requirements for IntegriRef, a software system that verifies the integrity of bibliographic references across academic, legal, patent, government, financial, and standards domains. It is intended for developers, testers, researchers, and stakeholders evaluating the system's capabilities.

### 1.2 Scope

IntegriRef provides a five-layer verification pipeline (L0-L4) that detects fabricated, hallucinated, retracted, and misrepresented citations. The system:

- Queries 62 registry adapters across 6 domains to verify reference existence
- Classifies citation intent (supporting, contrasting, mentioning)
- Verifies semantic claim-evidence alignment via Natural Language Inference
- Detects citation graph anomalies (rings, orphans, temporal anomalies)
- Produces calibrated risk scores using Bayesian likelihood-ratio models
- Exposes functionality via Python API and REST API

### 1.3 Definitions and Acronyms

| Term | Definition |
|------|-----------|
| L0 | Layer 0: Reference existence verification |
| L1 | Layer 1: Citation intent classification |
| L2 | Layer 2: Semantic claim-evidence verification (NLI) |
| L3 | Layer 3: Citation graph anomaly detection |
| L4 | Layer 4: Bayesian risk scoring and aggregation |
| ICEntity | IntegriRef Canonical Entity — unified data model |
| NLI | Natural Language Inference |
| LR | Likelihood Ratio (Bayesian signal strength) |
| DOI | Digital Object Identifier |
| GRIM | Granularity-Related Inconsistency of Means test |

### 1.4 References

- IEEE 830-1998 (SRS standard)
- Scancar 2025 — cancer research fabrication base rate (9.87%)
- SciCite (Cohan et al., 2019) — citation intent taxonomy
- Wadden et al. (2020) — SciFact claim verification
- Benford's Law — first-digit distribution for citation count anomaly detection

---

## 2. Overall Description

### 2.1 Product Perspective

IntegriRef fills a gap in the scholarly communication ecosystem where no single tool provides end-to-end reference integrity verification across all layers. Existing tools address fragments of the problem:

| Tool | Coverage |
|------|----------|
| scite.ai | Citation intent (L1 only) |
| Papermill Alarm | Tortured phrases (partial L0) |
| Retraction Watch | Retraction status (partial L0) |
| RefChecker | Existence only (partial L0) |
| **IntegriRef** | **Full L0-L4 stack** |

### 2.2 Product Functions

1. **Reference Existence Verification (L0)**: Query registries to confirm a cited work exists with matching metadata
2. **Citation Intent Classification (L1)**: Determine how a citing paper uses each reference
3. **Semantic Claim Verification (L2)**: Verify if cited evidence supports or contradicts claims
4. **Citation Graph Analysis (L3)**: Detect structural anomalies in citation networks
5. **Bayesian Risk Scoring (L4)**: Aggregate signals into calibrated posterior probabilities
6. **Hallucination Detection**: Identify AI-fabricated citations
7. **Paper Mill Detection**: Flag tortured phrases, suspicious emails, statistical anomalies
8. **REST API**: Expose all capabilities as HTTP endpoints

### 2.3 User Classes

| User Class | Description | Usage Pattern |
|------------|-------------|---------------|
| Journal Editor | Evaluates manuscript citations during peer review | Batch verification via API |
| Researcher | Checks own references before submission | Single/batch via Python |
| Institutional Reviewer | Monitors research integrity across departments | Batch + graph analysis |
| LLM Application Developer | Validates AI-generated citations | Hallucination endpoint |
| Funding Agency | Audits grant applications | Batch + risk scoring |

### 2.4 Operating Environment

- **Runtime**: Python 3.10+
- **OS**: Linux, macOS, Windows (Linux recommended for production)
- **Dependencies**: requests, rapidfuzz, pyahocorasick, aiohttp (core); transformers, torch (optional NLI); FastAPI, uvicorn (API)
- **Network**: Outbound HTTPS to 62 external registry APIs
- **Optional**: Redis for response caching; GPU for NLI acceleration

### 2.5 Constraints

- External registry API rate limits and availability
- NLI model requires ~2GB RAM (DeBERTa-v3-base); heuristic fallback available
- OpenAlex API required for L3 graph analysis
- No persistent database required (stateless verification)

### 2.6 Assumptions

- External registry APIs remain accessible and maintain backward-compatible responses
- Base rates for fabrication priors are periodically updated from literature
- Users provide reference metadata in structured format (title, authors, year, identifiers)

---

## 3. Functional Requirements

### 3.1 L0: Reference Existence Verification

| ID | Requirement | Priority |
|----|------------|----------|
| FR-L0-01 | The system SHALL query registries by DOI, arXiv ID, PMID, patent number, case citation, or other supported identifier types | P0 |
| FR-L0-02 | The system SHALL fall back to title+author+year search when no identifier is provided | P0 |
| FR-L0-03 | The system SHALL compare title, author, year, and venue fields with fuzzy matching (RapidFuzz token_set_ratio) | P0 |
| FR-L0-04 | The system SHALL detect phantom DOIs (valid format but unresolvable) | P0 |
| FR-L0-05 | The system SHALL check retraction status and flag retracted papers | P0 |
| FR-L0-06 | The system SHALL produce a composite confidence score (weighted: 35% existence, 25% title, 15% author, 10% venue, 15% context) | P1 |
| FR-L0-07 | The system SHALL generate an L2 escalation flag when composite score is 0.55-0.85 or hallucination score > 30 | P1 |
| FR-L0-08 | The system SHALL query registries in parallel (ThreadPoolExecutor, max 8 workers) with per-adapter rate limiting | P1 |
| FR-L0-09 | The system SHALL detect identifier type automatically (DOI, arXiv, PMID, Patent, RFC, CVE, CELEX, ISO, ISBN, Case Citation, ORCID, CIK) | P1 |
| FR-L0-10 | The system SHALL return top-3 candidate matches with similarity scores | P2 |

### 3.2 L1: Citation Intent Classification

| ID | Requirement | Priority |
|----|------------|----------|
| FR-L1-01 | The system SHALL classify citation intent into SUPPORTING, CONTRASTING, or MENTIONING | P0 |
| FR-L1-02 | The system SHALL use lexical cue phrase matching as default classifier | P0 |
| FR-L1-03 | The system SHALL support fine-tuned transformer models (SciBERT/DeBERTa) when available | P1 |
| FR-L1-04 | The system SHALL apply position-weighted scoring (cues closer to citation carry more weight) | P1 |
| FR-L1-05 | The system SHALL return confidence score (0.0-1.0) and cue phrase for each classification | P1 |
| FR-L1-06 | The system SHALL flag `needs_model_upgrade` when heuristic confidence < 0.6 | P2 |

### 3.3 L2: Semantic Claim Verification

| ID | Requirement | Priority |
|----|------------|----------|
| FR-L2-01 | The system SHALL classify claim-evidence alignment as SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED, CONTRADICTED, or UNVERIFIABLE | P0 |
| FR-L2-02 | The system SHALL use cross-encoder NLI models (DeBERTa-v3-base) when available | P0 |
| FR-L2-03 | The system SHALL fall back to token-overlap heuristic when no NLI model is loaded | P0 |
| FR-L2-04 | The system SHALL split abstracts into sentences and perform per-sentence NLI scoring with max-pooling | P1 |
| FR-L2-05 | The system SHALL only run when L0 sets `needs_l2_escalation=True` (configurable) | P1 |
| FR-L2-06 | The system SHALL support batched inference for efficiency | P1 |

### 3.4 L3: Citation Graph Analysis

| ID | Requirement | Priority |
|----|------------|----------|
| FR-L3-01 | The system SHALL build citation graphs from DOIs using the OpenAlex API | P0 |
| FR-L3-02 | The system SHALL detect orphan clusters (density < threshold) | P0 |
| FR-L3-03 | The system SHALL detect temporal anomalies (citations to future papers) | P0 |
| FR-L3-04 | The system SHALL detect excessive self-citation (ratio > 0.3) | P0 |
| FR-L3-05 | The system SHALL detect citation rings (mutual-citation clusters, CIDRE variant) | P1 |
| FR-L3-06 | The system SHALL detect Benford's law violations in citation count distributions | P1 |
| FR-L3-07 | The system SHALL detect reciprocal citation patterns | P1 |
| FR-L3-08 | The system SHALL detect citation bursts (temporal concentration) | P2 |
| FR-L3-09 | The system SHALL compute overall graph health score (weighted: 25% interconnection, 25% temporal, 20% self-citation, 15% Benford, 15% reciprocal) | P1 |
| FR-L3-10 | The system SHALL support 1-hop and 2-hop graph construction | P1 |

### 3.5 L4: Bayesian Risk Scoring

| ID | Requirement | Priority |
|----|------------|----------|
| FR-L4-01 | The system SHALL aggregate signals from L0-L3 into Bayesian posterior probability | P0 |
| FR-L4-02 | The system SHALL use literature-derived likelihood ratios for each signal | P0 |
| FR-L4-03 | The system SHALL classify risk into tiers: LOW (<0.05), ELEVATED (0.05-0.20), HIGH (0.20-0.50), CRITICAL (>0.50) | P0 |
| FR-L4-04 | The system SHALL support domain-specific priors (cancer_research: 0.10, biomedical: 0.05, CS: 0.02, etc.) | P1 |
| FR-L4-05 | The system SHALL apply confidence-weighted likelihood ratios: `effective_LR = raw_LR^confidence` | P1 |
| FR-L4-06 | The system SHALL provide per-signal log-odds contributions for interpretability | P1 |
| FR-L4-07 | The system SHALL deduplicate signals by name before scoring | P1 |

### 3.6 Pipeline Orchestration

| ID | Requirement | Priority |
|----|------------|----------|
| FR-PL-01 | The system SHALL chain L0 -> L1 -> L2 -> L3 -> L4 in a single `verify()` call | P0 |
| FR-PL-02 | The system SHALL support configurable layer selection (e.g., ["L0", "L4"] only) | P0 |
| FR-PL-03 | The system SHALL support batch verification with shared signal post-processing | P0 |
| FR-PL-04 | The system SHALL automatically extract Bayesian signals from each layer's output (SignalExtractor) | P0 |
| FR-PL-05 | The system SHALL produce a unified PipelineReport with per-layer results and aggregate risk | P0 |
| FR-PL-06 | The system SHALL detect batch-level anomalies (e.g., all citations are "mentioning") | P2 |

### 3.7 Detection Modules

| ID | Requirement | Priority |
|----|------------|----------|
| FR-DT-01 | The system SHALL detect tortured phrases (350+ patterns) using Aho-Corasick or regex | P0 |
| FR-DT-02 | The system SHALL assess author email risk (known mill domains, free providers, institutional patterns) | P1 |
| FR-DT-03 | The system SHALL perform GRIM test (mean × n consistency) on reported statistics | P1 |
| FR-DT-04 | The system SHALL perform statcheck (APA statistic p-value recomputation) | P1 |
| FR-DT-05 | The system SHALL detect sneaked references (metadata vs. text reference list comparison) | P2 |
| FR-DT-06 | The system SHALL detect hallucinated citations (8 signal types, composite score 0-100) | P0 |

### 3.8 REST API

| ID | Requirement | Priority |
|----|------------|----------|
| FR-API-01 | The system SHALL expose `POST /v1/verify` for single reference verification | P0 |
| FR-API-02 | The system SHALL expose `POST /v1/batch` for batch verification | P0 |
| FR-API-03 | The system SHALL expose `POST /v1/hallucination` for fast hallucination checks | P1 |
| FR-API-04 | The system SHALL expose `GET /v1/graph/{doi}` for citation graph analysis | P1 |
| FR-API-05 | The system SHALL expose `POST /v1/risk` for standalone Bayesian risk scoring | P1 |
| FR-API-06 | The system SHALL expose `GET /v1/health` for service health check | P0 |
| FR-API-07 | The system SHALL expose `GET /v1/registries` to list available adapters | P1 |
| FR-API-08 | The system SHALL expose `GET /v1/signals` to list Bayesian signal definitions | P2 |
| FR-API-09 | The system SHALL support optional API key authentication via Authorization header | P1 |
| FR-API-10 | The system SHALL enable CORS for cross-origin requests | P1 |

### 3.9 Registry Adapters

| ID | Requirement | Priority |
|----|------------|----------|
| FR-RA-01 | The system SHALL support a minimum of 60 registry adapters across 6 domains | P0 |
| FR-RA-02 | Each adapter SHALL implement `info()`, `query_by_id()`, and `search()` interfaces | P0 |
| FR-RA-03 | Each adapter SHALL enforce per-adapter rate limiting | P0 |
| FR-RA-04 | Each adapter SHALL implement exponential backoff with jitter (3 retries) | P1 |
| FR-RA-05 | The system SHALL implement circuit breaker pattern (5 failures / 60s -> open for 30s) | P1 |

---

## 4. Non-Functional Requirements

### 4.1 Performance

| ID | Requirement | Target |
|----|------------|--------|
| NFR-P-01 | Single reference L0 verification latency (cached) | < 500ms |
| NFR-P-02 | Single reference L0 verification latency (uncached) | < 5s |
| NFR-P-03 | Single reference full L0-L4 verification latency | < 15s |
| NFR-P-04 | Batch verification throughput (L0 only) | >= 10 refs/sec |
| NFR-P-05 | NLI inference latency (single claim, CPU) | < 200ms |
| NFR-P-06 | API cold start time (adapter initialization) | < 3s |
| NFR-P-07 | Memory usage (without NLI model) | < 512MB |
| NFR-P-08 | Memory usage (with NLI model loaded) | < 3GB |

### 4.2 Accuracy

| ID | Requirement | Target |
|----|------------|--------|
| NFR-A-01 | L0 false positive rate (marking real paper as not found) | <= 2% |
| NFR-A-02 | L1 citation intent F1 (3-class) | >= 70% (heuristic), >= 85% (model) |
| NFR-A-03 | L2 NLI verification accuracy (SciFact-aligned) | >= 90% |
| NFR-A-04 | L4 risk tier sensitivity (detecting fabricated citations) | >= 85% |
| NFR-A-05 | Tortured phrase detection precision | >= 95% |

### 4.3 Reliability

| ID | Requirement | Target |
|----|------------|--------|
| NFR-R-01 | The system SHALL degrade gracefully when external APIs are unavailable | Required |
| NFR-R-02 | Circuit breaker SHALL prevent cascading failures across registries | Required |
| NFR-R-03 | All ML components SHALL have heuristic fallbacks | Required |
| NFR-R-04 | The system SHALL not crash on malformed input references | Required |

### 4.4 Security

| ID | Requirement | Target |
|----|------------|--------|
| NFR-S-01 | API keys SHALL be transmitted via Authorization header only | Required |
| NFR-S-02 | API keys SHALL be stored in environment variables, not in code | Required |
| NFR-S-03 | The system SHALL not log sensitive data (API keys, full texts) | Required |
| NFR-S-04 | CORS SHALL be configurable for production deployments | Required |

### 4.5 Scalability

| ID | Requirement | Target |
|----|------------|--------|
| NFR-SC-01 | The system SHALL support horizontal scaling via stateless API deployment | Required |
| NFR-SC-02 | The system SHALL support optional Redis caching for response deduplication | Desired |
| NFR-SC-03 | The system SHALL support concurrent requests without shared mutable state | Required |

### 4.6 Maintainability

| ID | Requirement | Target |
|----|------------|--------|
| NFR-M-01 | Test coverage SHALL be >= 80% for core modules | Required |
| NFR-M-02 | Adding a new registry adapter SHALL require only implementing the RegistryAdapter interface | Required |
| NFR-M-03 | Adding a new Bayesian signal SHALL require only adding to SIGNAL_DEFINITIONS dict | Required |
| NFR-M-04 | Each verification layer SHALL be independently testable | Required |

---

## 5. Data Requirements

### 5.1 Input Data

| Data | Format | Source |
|------|--------|--------|
| Reference metadata | JSON dict: `{title, authors, year, doi, ...}` | User input |
| Citing sentence | Plain text string | User input |
| Abstract | Plain text string | User input or registry |
| Full text | Plain text string (optional) | User input |

### 5.2 Output Data

| Data | Format | Description |
|------|--------|-------------|
| PipelineReport | Structured dict (JSON) | Per-layer results, signals, risk tier |
| ReferenceResult | Structured dict | L0 existence, field checks, provenance |
| BayesianRiskReport | Structured dict | Posterior probability, per-signal contributions |
| GraphHealthReport | Structured dict | Node/edge counts, anomalies, health score |

### 5.3 External Data Sources

| Registry Domain | Count | Examples |
|-----------------|-------|----------|
| Academic | 31 | Crossref, Semantic Scholar, PubMed, arXiv, OpenAlex, DBLP, CORE, Europe PMC, bioRxiv, Zenodo, SciELO, HAL, CiNii, J-STAGE, KCI, INSPIRE-HEP, zbMATH, NASA ADS, etc. |
| Patents | 8 | USPTO, EPO, WIPO, DPMA, INPI, KIPRIS, J-PlatPat, CPC |
| Legal | 8 | CourtListener, EUR-Lex, GovInfo, CanLII, Indian Kanoon, Korean Law, Legifrance, Open Legal DE |
| Government | 6 | data.gov, e-Gov Japan, e-Stat Japan, Data.gov.uk, EDGAR, FRED |
| Financial | 4 | EDGAR, FRED, INPI Financial, Bloomberg |
| Standards | 5 | ISO, IEEE, NIST, IETF RFC, W3C |

---

## 6. Interface Requirements

### 6.1 Python API

```python
from core.pipeline import IntegriRefPipeline
from core.discovery import RegistryDiscovery

discovery = RegistryDiscovery()
discovery.register_all(all_adapters)

pipeline = IntegriRefPipeline(discovery=discovery, layers=["L0", "L1", "L4"])
report = pipeline.verify(ref={"title": "...", "doi": "..."})
print(report.risk_tier)       # "LOW" | "ELEVATED" | "HIGH" | "CRITICAL"
print(report.summary())       # compact dict
print(report.api_response())  # full structured response
```

### 6.2 REST API

Base URL: `http://host:8000/v1/`

| Method | Endpoint | Request Body | Response |
|--------|----------|--------------|----------|
| POST | /v1/verify | VerifyRequest JSON | PipelineReport JSON |
| POST | /v1/batch | BatchVerifyRequest JSON | Batch results JSON |
| POST | /v1/hallucination | HallucinationRequest JSON | Flag results JSON |
| GET | /v1/graph/{doi} | Query params: hops | Graph analysis JSON |
| POST | /v1/risk | RiskRequest JSON | BayesianRiskReport JSON |
| GET | /v1/health | — | Health status JSON |
| GET | /v1/registries | — | Adapter list JSON |
| GET | /v1/signals | — | Signal definitions JSON |

---

## 7. Traceability Matrix

| Requirement | Test File | Test Class/Method |
|-------------|-----------|-------------------|
| FR-L0-01 to FR-L0-10 | test_verification.py, test_integration.py | TestFullPipeline |
| FR-L1-01 to FR-L1-06 | test_semantic.py, test_intent_classifier.py | TestIntentClassifier |
| FR-L2-01 to FR-L2-06 | test_semantic.py, test_benchmarks.py | TestL2BuiltinVerification |
| FR-L3-01 to FR-L3-10 | test_week2_modules.py | TestGraphAnalysis |
| FR-L4-01 to FR-L4-07 | test_scoring.py, test_pipeline.py | TestPipelineAllSignals |
| FR-PL-01 to FR-PL-06 | test_pipeline.py, test_integration.py | TestPipelineBasic, TestBatchIntegration |
| FR-DT-01 to FR-DT-06 | test_verification.py, test_week1_modules.py | TestTorturedPhrases, TestGRIM |
| FR-API-01 to FR-API-10 | test_api.py | TestVerify, TestBatch, TestHealth, TestAuth |
| FR-RA-01 to FR-RA-05 | test_registry_base.py | TestRegistryBase |
