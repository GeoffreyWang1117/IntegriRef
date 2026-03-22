# Changelog

All notable changes to IntegriRef are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.1.0] — 2026-03-16

### Added

**Core Pipeline**
- Unified L0-L4 pipeline orchestrator (`IntegriRefPipeline`) — single `verify()` call chains all layers
- `SignalExtractor` — automatic Bayesian signal extraction from L0/L1/L2/L3 outputs
- `PipelineReport` — unified output with `summary()` and `api_response()` methods
- Conditional L2 escalation — NLI runs only when L0 flags ambiguity (`needs_l2_escalation`)
- Batch verification with post-processing (all_citations_mentioning detection)

**Registry Adapters (62 total)**
- Academic (31): Crossref, Semantic Scholar, OpenAlex, PubMed, arXiv, DBLP, CORE, Europe PMC, bioRxiv, Zenodo, SciELO, HAL, CiNii, J-STAGE, KCI, INSPIRE-HEP, zbMATH, NASA ADS, ClinicalTrials, Shodhganga, DiVA, Redalyc, Dialnet, DergiPark, Garuda, CyberLeninka, Persee, Europeana, Theses.fr, CNKI, Scopus
- Patents (8): USPTO, EPO, WIPO, DPMA, INPI, KIPRIS, J-PlatPat, CPC
- Legal (8): CourtListener, EUR-Lex, GovInfo, CanLII, Indian Kanoon, Korean Law, Legifrance, Open Legal DE
- Government (6): data.gov, e-Gov Japan, e-Stat Japan, Data.gov.uk, EDGAR, FRED
- Financial (4): EDGAR, FRED, INPI Financial, Bloomberg
- Standards (5): ISO, IEEE, NIST, IETF RFC, W3C

**Verification Engine (L0)**
- 9-phase verification workflow (ID lookup → search → consistency → retraction → hallucination → candidates → provenance → composite score → escalation)
- Parallel registry queries (ThreadPoolExecutor, 8 workers)
- Composite confidence scoring (35% existence + 25% title + 15% author + 10% venue + 15% context)
- Cross-registry metadata validation

**Citation Intent Classification (L1)**
- Heuristic classifier with 70+ lexical cue patterns
- Position-weighted scoring (proximity to citation marker)
- 5-class taxonomy (SUPPORTING, CONTRASTING, MENTIONING, EXTENDING, USING)
- Optional transformer model support (SciBERT/DeBERTa)

**Semantic NLI Verification (L2)**
- Cross-encoder NLI using DeBERTa-v3-base
- Per-sentence scoring with max-pooling
- 5 alignment labels (SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED, CONTRADICTED, UNVERIFIABLE)
- Token-overlap heuristic fallback
- Batched inference support

**Citation Graph Analysis (L3)**
- OpenAlex-based citation graph construction (1-hop and 2-hop)
- 7 anomaly detection algorithms: orphan clusters, temporal anomalies, excessive self-citation, citation rings (CIDRE), Benford's law violation, reciprocal citations, citation bursts
- Graph health scoring (weighted 5-dimension composite)

**Bayesian Risk Scoring (L4)**
- Likelihood-ratio model with 18 signals
- Domain-specific priors (8 domains: cancer_research, biomedical, psychology, social_science, CS, engineering, humanities, default)
- Confidence-weighted LR interpolation
- 4 risk tiers: LOW, ELEVATED, HIGH, CRITICAL
- Per-signal log-odds contribution tracking

**Detection Modules**
- Tortured phrase detector (350+ patterns, Aho-Corasick)
- Email risk assessor (25 mill domains, 50+ free providers)
- Statistical verifier (GRIM test + statcheck with scipy)
- Sneaked reference detector (Crossref metadata vs text comparison)
- Hallucination detector (8 signal types, composite scoring)
- Metadata consistency validator (cross-registry field comparison)

**REST API**
- FastAPI application with 8 endpoints
- Optional API key authentication
- CORS support
- Pydantic request/response models
- Automatic OpenAPI documentation

**Infrastructure**
- Circuit breaker pattern for external APIs
- Dual-layer caching (LRU + Redis)
- Async registry adapters and discovery
- Batch processor for parallel verification
- OAI-PMH protocol support

**Testing**
- 555 tests across 11 test files
- Unit, integration, API, and benchmark tests
- 100% pass rate, ~9 second execution time

**Documentation**
- Software Requirements Specification (SRS)
- Software Design Document (SDD)
- API Specification
- Test Plan
- Deployment Guide
- Landing page and documentation site

**Benchmarks**
- Layer ablation experiment framework
- L0/L1/L2 benchmark runners
- Classification metrics (precision, recall, F1, accuracy)
- L0-specific metrics (false positive rate, precision@threshold)
