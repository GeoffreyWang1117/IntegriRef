# Software Design Document (SDD)

**Project**: IntegriRef — Multi-Layer Reference Integrity Verification Engine
**Version**: 0.1.0
**Date**: 2026-03-16
**Status**: Draft

---

## 1. Introduction

### 1.1 Purpose

This document describes the software architecture, module decomposition, data structures, algorithms, and interface specifications for IntegriRef. It serves as the primary technical reference for developers extending or maintaining the system.

### 1.2 Scope

Covers the design of the five-layer verification pipeline (L0-L4), the 62-adapter registry framework, the REST API layer, and supporting subsystems (caching, circuit breaker, async I/O).

### 1.3 Design Goals

1. **Modularity**: Each layer is independently testable and deployable
2. **Extensibility**: New registries and signals added by implementing interfaces
3. **Resilience**: Graceful degradation when external APIs fail
4. **Interpretability**: Every risk score traceable to specific signals and evidence
5. **Performance**: Parallel I/O, lazy model loading, optional caching

---

## 2. System Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         REST API Layer                          │
│                    (FastAPI + Pydantic models)                   │
│  POST /v1/verify  POST /v1/batch  GET /v1/graph/{doi}  ...     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                   IntegriRefPipeline                            │
│              (Unified L0-L4 Orchestrator)                       │
│                                                                 │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │
│  │   L0    │→│   L1    │→│   L2    │→│   L3    │→│   L4    │ │
│  │Existence│ │ Intent  │ │Semantic │ │ Graph   │ │Bayesian │ │
│  │  Check  │ │Classify │ │  NLI    │ │Anomaly  │ │  Risk   │ │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ │
│       │           │           │           │           │       │
│       └───────────┴───────────┴───────────┴───────────┘       │
│                    SignalExtractor (L0-L3 → signals)            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                  Infrastructure Layer                           │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Registry    │  │   Circuit    │  │   Response Cache     │  │
│  │  Discovery   │  │   Breaker    │  │   (LRU + Redis)      │  │
│  │  (62 adapts) │  │              │  │                      │  │
│  └──────┬───────┘  └──────────────┘  └──────────────────────┘  │
│         │                                                       │
│  ┌──────▼─────────────────────────────────────────────────────┐ │
│  │              Registry Adapters (6 domains)                 │ │
│  │  Academic(31) Patent(8) Legal(8) Govt(6) Finance(4) Std(5) │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Package Structure

```
IntegriRef/
├── core/                          # Core engine & data model
│   ├── entity.py                  # ICEntity canonical model
│   ├── registry.py                # RegistryAdapter base class
│   ├── discovery.py               # RegistryDiscovery routing
│   ├── pipeline.py                # IntegriRefPipeline orchestrator
│   ├── async_registry.py          # Async adapter variant
│   ├── async_discovery.py         # Async discovery
│   ├── batch_processor.py         # Parallel batch processing
│   ├── cache.py                   # Dual-layer cache
│   ├── circuit_breaker.py         # Circuit breaker pattern
│   ├── oai_pmh.py                 # OAI-PMH protocol support
│   └── parsing.py                 # Reference parsing utilities
│
├── verification/                  # L0 verification & detectors
│   ├── engine.py                  # VerificationEngine (9-phase)
│   ├── hallucination_detector.py  # AI citation detection
│   ├── metadata_validator.py      # Cross-registry consistency
│   ├── tortured_phrases.py        # Paper mill phrase detection
│   ├── email_risk.py              # Author email risk assessment
│   ├── statistical.py             # GRIM test & statcheck
│   └── sneaked_references.py      # Sneaked reference detection
│
├── semantic/                      # L1 + L2 semantic analysis
│   ├── intent_classifier.py       # Citation intent (L1)
│   └── nli_verifier.py            # NLI claim verification (L2)
│
├── graph/                         # L3 citation graph analysis
│   ├── builder.py                 # CitationGraph data structure
│   ├── openalex_builder.py        # OpenAlex graph construction
│   ├── anomaly.py                 # 7 anomaly detection algorithms
│   └── metrics.py                 # Graph health scoring
│
├── scoring/                       # L4 Bayesian risk scoring
│   └── bayesian.py                # BayesianRiskScorer
│
├── registries/                    # 62 registry adapters
│   ├── academic/                  # 31 academic registries
│   ├── patents/                   # 8 patent registries
│   ├── legal/                     # 8 legal registries
│   ├── government/                # 6 government registries
│   ├── financial/                 # 4 financial registries
│   └── standards/                 # 5 standards registries
│
├── api/                           # REST API
│   └── main.py                    # FastAPI application
│
├── benchmarks/                    # Evaluation framework
│   ├── ablation.py                # Layer ablation experiment
│   ├── metrics.py                 # Classification metrics
│   ├── bench_l0.py                # L0 benchmarks
│   ├── bench_l1.py                # L1 benchmarks
│   ├── bench_l2.py                # L2 benchmarks
│   └── datasets.py                # Benchmark datasets
│
├── models/                        # ML model definitions
│
├── tests/                         # 555 tests
│   ├── test_pipeline.py           # Pipeline unit tests (31)
│   ├── test_integration.py        # End-to-end tests (12)
│   ├── test_api.py                # REST API tests (20)
│   ├── test_ablation.py           # Ablation framework tests (12)
│   ├── test_verification.py       # Verification engine tests
│   ├── test_semantic.py           # L1/L2 tests
│   ├── test_scoring.py            # Bayesian scorer tests
│   ├── test_registry_base.py      # Registry adapter tests
│   ├── test_benchmarks.py         # Benchmark tests
│   ├── test_week1_modules.py      # Detection module tests
│   └── test_week2_modules.py      # Graph/advanced tests
│
├── web/                           # Documentation site
│   ├── index.html                 # Landing page
│   ├── docs.html                  # Full documentation
│   └── assets/style.css           # Design system
│
└── docs/                          # Engineering documents
```

---

## 3. Module Design

### 3.1 Core Data Model — `core/entity.py`

#### 3.1.1 ICEntity (IntegriRef Canonical Entity)

The canonical cross-registry representation that normalizes data from 62 sources into a uniform structure.

```
┌─────────────────────────────────────────────────────────┐
│                       ICEntity                          │
├─────────────────────────────────────────────────────────┤
│ ice_id: str           # Stable hash (DOI > arXiv > hash)│
│ entity_type: EntityType  # PAPER, PATENT, CASE, ...    │
│ external_ids: list[ExternalID]  # Multi-registry IDs   │
│ title: str                                              │
│ title_normalized: str  # NFC + lowercase + strip punct  │
│ authors: list[str]                                      │
│ authors_normalized: list[str]  # Accent-stripped        │
│ year: str                                               │
│ venue: str                                              │
│ metadata: dict         # Type-specific (abstract, etc.) │
│ source_registries: list[str]                            │
│ verification_status: str  # confirmed/unverified/...    │
│ confidence: float      # 0.0-1.0                        │
├─────────────────────────────────────────────────────────┤
│ + add_external_id(registry, id_type, id_value)          │
│ + get_id(id_type) -> Optional[str]                      │
│ + generate_ice_id() -> str                              │
│ + normalize() -> None                                   │
│ + normalize_title(title) -> str  [static]               │
│ + normalize_author(name) -> str  [static]               │
└─────────────────────────────────────────────────────────┘
```

**Design Decisions**:
- `ice_id` uses priority order: DOI > arXiv > PMID > title hash, ensuring stable identity across registries
- Normalization is Unicode NFC-based for correct handling of 15+ languages
- `metadata` is a generic dict to accommodate domain-specific fields (patent claims, legal statutes, etc.)

#### 3.1.2 EntityType Enum

```
PAPER | PATENT | CASE | STATUTE | STANDARD | REPORT | FILING | DATASET | BOOK | THESIS | OTHER
```

### 3.2 Registry Framework — `core/registry.py`

#### 3.2.1 RegistryAdapter (Abstract Base Class)

```
┌─────────────────────────────────────────────────────────┐
│              RegistryAdapter (ABC)                       │
├─────────────────────────────────────────────────────────┤
│ _last_call: float           # Rate limit tracking       │
│ _session: requests.Session  # HTTP session with UA      │
├─────────────────────────────────────────────────────────┤
│ [abstract] info() -> RegistryInfo                       │
│ [abstract] query_by_id(id_type, id_value) -> ICEntity?  │
│ [abstract] search(title, author, year) -> list[ICEntity]│
│ verify_exists(id_type, id_value) -> bool                │
│ _get(url, params, headers) -> Response  [with retry+CB] │
│ _post(url, data, headers) -> Response   [with retry+CB] │
│ _rate_limit() -> None                                   │
└─────────────────────────────────────────────────────────┘
```

**Built-in Resilience**:
- **Rate limiting**: Per-adapter delay from `RegistryInfo.rate_limit`
- **Circuit breaker**: 5 failures in 60s -> open for 30s (shared registry)
- **Exponential backoff**: 3 retries with jitter (1s, 2s, 4s base delays)
- **Session management**: Persistent HTTP sessions with user agent header

#### 3.2.2 RegistryInfo

```
name: str, domain: str, base_url: str,
auth_type: str (none|api_key_free|oauth),
rate_limit: float (seconds), coverage: str,
entity_types: list[EntityType]
```

### 3.3 Registry Discovery — `core/discovery.py`

```
┌─────────────────────────────────────────────────────────┐
│                  RegistryDiscovery                       │
├─────────────────────────────────────────────────────────┤
│ _registries: list[RegistryAdapter]                      │
│ _id_type_map: dict[str, list[RegistryAdapter]]          │
│ _domain_map: dict[str, list[RegistryAdapter]]           │
├─────────────────────────────────────────────────────────┤
│ register(adapter) / register_all(classes)               │
│ detect_id_type(identifier) -> str?      [static]        │
│ query_by_id(identifier, id_type?) -> list[ICEntity]     │
│ search(title, author, year, domains?) -> list[ICEntity] │
│ parallel_query_by_id(..., max_workers=8) -> list[tuple] │
│ parallel_search(..., max_workers=8) -> list[tuple]      │
│ verify_reference(title, id, author, year) -> dict       │
│ list_registries() -> list[dict]                         │
└─────────────────────────────────────────────────────────┘
```

**Identifier Auto-Detection** (regex patterns for 12 types):
```
DOI      → 10.xxxx/...
arXiv    → 2301.12345 or hep-th/0401234
PMID     → pure digits (1-9 digits)
Patent   → US|EP|WO|CN|JP|KR|DE|FR|GB + digits
RFC      → RFC 1234
CVE      → CVE-2023-12345
CELEX    → 3|6 + 4digits + letter
ISO      → ISO + digits
ISBN     → 10 or 13 digits
Case     → v. pattern or § symbol
ORCID    → 0000-0000-0000-000X
CIK      → 10 digits
```

### 3.4 Verification Engine — `verification/engine.py`

#### 3.4.1 Nine-Phase Verification Workflow

```
Input: ref dict {title, authors, year, doi, ...}
                           │
            ┌──────────────▼──────────────┐
Phase 1     │  ID-based Lookup (parallel) │  DOI/arXiv/PMID → direct registry query
            │  ThreadPoolExecutor(8)      │  Return early if 2+ sources confirm
            └──────────────┬──────────────┘
                           │ (if not confirmed)
            ┌──────────────▼──────────────┐
Phase 2     │  Search-based Lookup        │  title+author+year → cascading search
            │  Priority: crossref → s2    │  Through ACADEMIC_PRIORITY list
            │  → openalex → dblp → ...    │  Stop at 2 confirmed matches
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
Phase 3     │  Metadata Consistency       │  Cross-registry field comparison
            │  MetadataValidator          │  Title/author/year/venue/DOI agreement
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
Phase 4     │  Retraction Check           │  Flag is_retracted from registry data
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
Phase 5     │  Hallucination Analysis     │  8-signal composite scoring
            │  HallucinationDetector      │  Phantom DOI, chimerism, temporal, ...
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
Phase 6     │  Build Candidates           │  Top-3 matches by similarity
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
Phase 7     │  Structured Provenance      │  Audit trail per-registry
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
Phase 8     │  Composite Score            │  Weighted confidence formula
            │  35% exist + 25% title +    │
            │  15% author + 10% venue +   │
            │  15% context                │
            └──────────────┬──────────────┘
                           │
            ┌──────────────▼──────────────┐
Phase 9     │  L2 Escalation Decision     │  composite 0.55-0.85 or
            │                             │  hallucination_score > 30
            └──────────────┬──────────────┘
                           │
Output: ReferenceResult
```

#### 3.4.2 CompositeScore Formula

```
overall = 0.35 × existence_score
        + 0.25 × title_similarity
        + 0.15 × author_similarity
        + 0.10 × venue_similarity
        + 0.15 × context_intent_score

Labels:
  overall >= 0.85  →  "supported"
  overall >= 0.60  →  "possible"
  overall <  0.60  →  "suspicious"
```

### 3.5 Pipeline Orchestrator — `core/pipeline.py`

#### 3.5.1 IntegriRefPipeline

```
┌─────────────────────────────────────────────────────────┐
│                 IntegriRefPipeline                       │
├─────────────────────────────────────────────────────────┤
│ _discovery: RegistryDiscovery                           │
│ _engine: VerificationEngine                             │
│ _intent_classifier: IntentClassifier                    │
│ _nli_verifier: NLIVerifier (lazy)                       │
│ _layers: list[str]                                      │
│ _domain: str                                            │
│ _enable_l2_only_on_escalation: bool                     │
├─────────────────────────────────────────────────────────┤
│ verify(ref, citing_sentence, full_text, abstract,       │
│        domains) -> PipelineReport                       │
│ verify_batch(references, citing_sentences, full_text,   │
│        abstracts, domains) -> list[PipelineReport]      │
│ _run_l3(doi) -> tuple[list[anomaly], float]             │
└─────────────────────────────────────────────────────────┘
```

#### 3.5.2 SignalExtractor

Static class that converts each layer's output into `SignalObservation` objects:

```
from_l0(result, ref, full_text) → [reference_not_found, phantom_doi,
                                    metadata_mismatch, retracted_citation,
                                    tortured_phrases, suspicious_email,
                                    grim_test_failure, statcheck_error]

from_l1(result)                 → [citation_misrepresents_source]

from_l2(result)                 → [claim_contradicted, claim_unsupported]

from_l3(anomalies)              → [citation_ring_detected, excessive_self_citation,
                                    temporal_anomaly, orphan_cluster]
```

#### 3.5.3 PipelineReport

```
┌─────────────────────────────────────────────────────────┐
│                   PipelineReport                        │
├─────────────────────────────────────────────────────────┤
│ reference: dict                                         │
│ l0: ReferenceResult         # L0 output                 │
│ l1: IntentResult            # L1 output                 │
│ l2: NLIResult               # L2 output                 │
│ l3_anomalies: list          # L3 anomalies              │
│ l3_health_score: float      # L3 graph health            │
│ l4: BayesianRiskReport      # L4 output                 │
│ signals: list[SignalObservation]  # All collected signals│
│ risk_tier: str              # LOW|ELEVATED|HIGH|CRITICAL│
│ risk_probability: float     # Posterior P(fabricated)    │
│ layers_run: list[str]       # Which layers ran           │
│ processing_time_ms: float   # Wall-clock time            │
├─────────────────────────────────────────────────────────┤
│ summary() -> dict           # Compact representation     │
│ api_response() -> dict      # Full structured response   │
└─────────────────────────────────────────────────────────┘
```

### 3.6 Semantic Layer — `semantic/`

#### 3.6.1 IntentClassifier (L1)

**Two-tier architecture**:
1. **Model tier**: Fine-tuned SciBERT/DeBERTa on SciCite (if available)
2. **Heuristic tier**: 70+ lexical cue patterns with position-weighted scoring

**Position Weighting Algorithm**:
```
distance = |cue_position - citation_position|
weight = 2.0   if distance < 30
       = 1.5   if distance < 80
       = 1.0   if distance < 150
       = 0.5   otherwise
```

**Intent Taxonomy**:
```
SUPPORTING   →  evidence, confirmation, building upon
CONTRASTING  →  critique, limitation, disagreement
MENTIONING   →  background, neutral acknowledgment
EXTENDING    →  extending previous work (→ merged to SUPPORTING × 0.9)
USING        →  using method/tool/dataset (→ merged to SUPPORTING × 0.85)
```

#### 3.6.2 NLIVerifier (L2)

**Model**: DeBERTa-v3 cross-encoder (`cross-encoder/nli-deberta-v3-base`)

**Algorithm**:
1. Split abstract into sentences
2. For each (claim, sentence) pair, run NLI cross-encoder
3. Max-pool entailment and contradiction scores across sentences
4. Apply thresholds: contradiction > 0.35 → CONTRADICTED; entailment > 0.5 → SUPPORTED
5. Confidence = margin between argmax and runner-up

**Fallback**: Token-overlap Jaccard similarity (heuristic)

### 3.7 Graph Layer — `graph/`

#### 3.7.1 CitationGraph Data Structure

```
CitationGraph
├── nodes: dict[ice_id → CitationNode]
│   └── CitationNode: title, year, authors, venue, citation_count,
│                     reference_count, field, metadata
├── edges: list[CitationEdge]
│   └── CitationEdge: source → target, titles, years, weight
├── _outgoing: dict[node_id → list[node_id]]
└── _incoming: dict[node_id → list[node_id]]
```

#### 3.7.2 Seven Anomaly Detection Algorithms

| # | Algorithm | Threshold | Severity Criteria |
|---|-----------|-----------|-------------------|
| 1 | Orphan clusters | density < 0.05 | < 0.01 → high |
| 2 | Temporal anomalies | citing_year < cited_year | diff > 2yr → critical |
| 3 | Excessive self-citation | ratio > 0.30 | > 0.50 → high |
| 4 | Citation rings (CIDRE) | cluster density | > 0.5 → critical |
| 5 | Benford's law violation | Chi² > 15.507 | > 20.09 → high |
| 6 | Reciprocal citations | ratio > 0.5, min 3 | ratio × total/20 |
| 7 | Citation burst | spike_factor > 3.0 | > 50% from < 3 sources |

#### 3.7.3 Graph Health Score

```
overall = 0.25 × interconnection_score    # orphan penalty
        + 0.25 × temporal_consistency     # temporal anomaly penalty
        + 0.20 × self_citation_score      # self-cite + ring penalty
        + 0.15 × benford_score            # first-digit violation penalty
        + 0.15 × reciprocal_score         # reciprocal + burst penalty
```

### 3.8 Scoring Layer — `scoring/bayesian.py`

#### 3.8.1 Bayesian Update Formula

```
prior_odds        = P_prior / (1 - P_prior)
log_prior_odds    = log(prior_odds)

For each signal i:
    if confidence < 1.0:
        effective_LR = raw_LR ^ confidence    # interpolate toward neutral
    else:
        effective_LR = raw_LR
    log_odds += log(effective_LR)

posterior_odds    = exp(log_odds)
posterior         = posterior_odds / (1 + posterior_odds)
```

#### 3.8.2 Signal Definition Table (18 signals)

| Signal | LR+ | LR- | Category |
|--------|------|------|----------|
| reference_not_found | 15.0 | 0.95 | L0 |
| phantom_doi | 25.0 | 0.99 | L0 |
| metadata_mismatch | 3.0 | 0.90 | L0 |
| retracted_citation | 8.0 | 0.99 | L0 |
| citation_misrepresents_source | 5.0 | 0.85 | L1 |
| all_citations_mentioning | 2.0 | 0.95 | L1 |
| claim_contradicted | 6.0 | 0.90 | L2 |
| claim_unsupported | 3.0 | 0.85 | L2 |
| citation_ring_detected | 10.0 | 0.95 | L3 |
| excessive_self_citation | 4.0 | 0.90 | L3 |
| temporal_anomaly | 8.0 | 0.98 | L3 |
| orphan_cluster | 2.5 | 0.92 | L3 |
| tortured_phrases | 25.0 | 0.99 | text |
| suspicious_email | 4.0 | 0.95 | text |
| grim_test_failure | 50.0 | 0.99 | stats |
| statcheck_error | 8.0 | 0.95 | stats |

#### 3.8.3 Domain-Specific Priors

| Domain | Prior P(fabricated) | Source |
|--------|-------------------|--------|
| cancer_research | 0.10 | Scancar 2025 |
| biomedical | 0.05 | Estimated |
| psychology | 0.04 | Replication crisis |
| social_science | 0.03 | Estimated |
| computer_science | 0.02 | Estimated |
| engineering | 0.02 | Estimated |
| humanities | 0.01 | Estimated |
| default | 0.03 | Baseline |

#### 3.8.4 Risk Tier Thresholds

```
LOW      : posterior < 0.05
ELEVATED : 0.05 <= posterior < 0.20
HIGH     : 0.20 <= posterior < 0.50
CRITICAL : posterior >= 0.50
```

### 3.9 Detection Modules — `verification/`

#### 3.9.1 TorturedPhraseDetector

- **Dictionary**: 350+ tortured phrase → correct phrase mappings across 5 domains
- **Algorithm**: Aho-Corasick automaton (O(n+m)) with longest-match overlap resolution
- **Fallback**: Compiled regex (longest-first) when pyahocorasick unavailable
- **Scoring**: Base score by match count (1→30, 2→55, 3→70, 4→80, 5→90, 6+→92-100) + diversity bonus (+3/unique phrase)

#### 3.9.2 EmailRiskDetector

- **Known mill domains**: 25+ (manuscriptfactory.com, editorialsupport.org, etc.)
- **Free providers**: 50+ (Gmail, Yahoo, QQ, 163.com, Outlook, ProtonMail, etc.)
- **Scoring rules**: Mill domain (+80), free provider (+20), numbered email (+15), random pattern (+15), institutional keyword on .com (+25)

#### 3.9.3 StatisticalVerifier (GRIM + statcheck)

**GRIM Test**: Checks if `mean × n × items ≈ integer` (granularity = 1/(n×items))

**StatCheck**: Extracts APA statistics via regex, recomputes p-values:
- Supported: t(df), F(df1,df2), chi²(df), r(df), Z
- Recomputation: scipy.stats (t.sf, f.sf, chi2.sf, norm.sf)
- Decision error: reported significance != computed significance (alpha = 0.05)

#### 3.9.4 SneakedReferenceDetector

Compares Crossref metadata reference list against text-extracted references. Matching uses:
1. DOI exact match
2. Title fuzzy match (RapidFuzz token_set_ratio >= 0.85)
3. Author surname + year + string similarity (>= 0.80)

#### 3.9.5 HallucinationDetector

8 signal types with weighted scoring (max 100):
```
phantom_doi: 40, phantom_arxiv: 35, no_registry_match: 30,
author_title_chimera: 25, temporal_impossibility: 20,
metadata_chimera: 20, venue_nonexistent: 15, format_anomaly: 10
```
Threshold: score >= 50 → is_likely_hallucinated = True

---

## 4. Data Flow

### 4.1 Single Reference Verification Flow

```
User Input                                Pipeline Output
    │                                         ▲
    ▼                                         │
┌──────────────┐                    ┌─────────┴────────┐
│ ref = {      │                    │ PipelineReport    │
│   title,     │                    │   risk_tier: HIGH │
│   authors,   │                    │   risk_prob: 0.45 │
│   year,      │                    │   signals: [...]  │
│   doi, ...   │                    │   l0: {...}       │
│ }            │                    │   l1: {...}       │
└──────┬───────┘                    │   l2: {...}       │
       │                            │   l3: {...}       │
       ▼                            │   l4: {...}       │
┌──────────────┐                    └──────────────────┘
│ L0: Engine   │                         ▲
│ 62 registries│──── ReferenceResult ────┤
│ 9 phases     │                         │
└──────┬───────┘                         │
       │ needs_l2_escalation?            │
       ▼                                 │
┌──────────────┐                         │
│ L1: Intent   │──── IntentResult ───────┤
│ classify()   │                         │
└──────┬───────┘                         │
       │ (if escalated)                  │
       ▼                                 │
┌──────────────┐                         │
│ L2: NLI      │──── NLIResult ──────────┤
│ verify()     │                         │
└──────┬───────┘                         │
       │ (if DOI available)              │
       ▼                                 │
┌──────────────┐                         │
│ L3: Graph    │──── [anomalies] ────────┤
│ 7 detectors  │                         │
└──────┬───────┘                         │
       │                                 │
       ▼                                 │
┌──────────────┐                         │
│ L4: Bayesian │     SignalExtractor     │
│ scorer       │◄──── from_l0/l1/l2/l3 ──┘
│ 18 signals   │
│ domain prior │
└──────┬───────┘
       │
       ▼
  BayesianRiskReport
  posterior → risk_tier
```

### 4.2 Batch Verification Flow

```
Input: [ref1, ref2, ..., refN]
                │
    ┌───────────┤───────────┐
    ▼           ▼           ▼
 verify(r1)  verify(r2) .. verify(rN)   # Sequential (each runs L0-L4)
    │           │           │
    ▼           ▼           ▼
 report1     report2     reportN
    │           │           │
    └───────────┤───────────┘
                ▼
    Post-processing:
    - Check all_citations_mentioning
    - Recompute L4 if batch signal applies
                │
                ▼
    [report1', report2', ..., reportN']
```

---

## 5. Interface Specifications

### 5.1 Python Public API

#### IntegriRefPipeline

```python
class IntegriRefPipeline:
    def __init__(
        self,
        discovery: RegistryDiscovery,
        layers: list[str] | None = None,        # default: all
        domain: str = "default",                 # Bayesian prior domain
        nli_verifier: NLIVerifier | None = None, # inject or auto-create
        intent_classifier: IntentClassifier | None = None,
        enable_l2_only_on_escalation: bool = True,
    ): ...

    def verify(
        self,
        ref: dict,                    # {title, authors, year, doi, ...}
        citing_sentence: str = "",    # for L1
        full_text: str = "",          # for detector modules
        abstract: str = "",           # for L2 NLI
        domains: list[str] | None = None,  # registry filter
    ) -> PipelineReport: ...

    def verify_batch(
        self,
        references: list[dict],
        citing_sentences: list[str] | None = None,
        full_text: str = "",
        abstracts: list[str] | None = None,
        domains: list[str] | None = None,
    ) -> list[PipelineReport]: ...
```

#### RegistryDiscovery

```python
class RegistryDiscovery:
    def register(self, adapter: RegistryAdapter) -> None: ...
    def register_all(self, adapter_classes: list[type]) -> None: ...
    def verify_reference(
        self, title: str, identifier: str = "",
        author: str = "", year: str = "",
        domains: list[str] | None = None,
    ) -> dict: ...
    def search(self, title: str, author: str = "",
               year: str = "", domains: list[str] | None = None,
    ) -> list[ICEntity]: ...
```

#### BayesianRiskScorer

```python
class BayesianRiskScorer:
    def __init__(self, domain: str = "default"): ...
    def observe(self, signal_name: str, fired: bool,
                confidence: float = 1.0, details: str = ""): ...
    def observe_batch(self, observations: list[SignalObservation]): ...
    def compute(self) -> BayesianRiskReport: ...
    def reset(self) -> None: ...
```

### 5.2 REST API Specification

See `docs/API_SPEC.md` for detailed endpoint specifications.

---

## 6. Error Handling Strategy

### 6.1 External API Failures

| Failure Mode | Handling |
|-------------|----------|
| Registry timeout | Circuit breaker opens after 5 failures; skip adapter |
| Rate limit (429) | Exponential backoff with jitter; 3 retries |
| Connection error | Backoff retry; circuit breaker tracking |
| Malformed response | Catch exception; log warning; skip adapter |
| All registries down | Return result with `found=False`, `sources_tried=[...]` |

### 6.2 Model Loading Failures

| Failure Mode | Handling |
|-------------|----------|
| NLI model not found | Fall back to token-overlap heuristic |
| Intent model not found | Fall back to lexical cue heuristic |
| ONNX runtime unavailable | Fall back to PyTorch or heuristic |
| Out of memory | Log error; fall back to heuristic |

### 6.3 Input Validation

| Input Issue | Handling |
|------------|----------|
| Empty reference | Return UNVERIFIABLE with zero confidence |
| Missing title | Attempt ID-based lookup only |
| Future year | Flag temporal_impossibility signal |
| Invalid DOI format | Skip DOI lookup; proceed with search |

---

## 7. Design Patterns

| Pattern | Application | Location |
|---------|-------------|----------|
| **Strategy** | Registry adapter polymorphism | `core/registry.py` |
| **Template Method** | RegistryAdapter with `_get()`/`_post()` hooks | `core/registry.py` |
| **Circuit Breaker** | External API failure isolation | `core/circuit_breaker.py` |
| **Observer** | SignalExtractor collects signals from layer outputs | `core/pipeline.py` |
| **Factory** | `register_all()` instantiates adapter classes | `core/discovery.py` |
| **Facade** | IntegriRefPipeline wraps all layers | `core/pipeline.py` |
| **Chain of Responsibility** | L0 → L1 → L2 → L3 → L4 pipeline | `core/pipeline.py` |
| **Fallback/Degradation** | Model → heuristic for NLI and intent | `semantic/` |
| **Dual-layer Cache** | LRU (in-memory) + Redis (distributed) | `core/cache.py` |

---

## 8. Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | 3.10+ |
| HTTP client | requests | >= 2.28 |
| Fuzzy matching | RapidFuzz | >= 3.0 |
| String matching | pyahocorasick | >= 2.0 |
| Async I/O | aiohttp | >= 3.9 |
| NLI model | transformers + DeBERTa-v3 | Optional |
| API framework | FastAPI | >= 0.110 |
| ASGI server | uvicorn | >= 0.27 |
| PDF parsing | PyMuPDF | >= 1.23 |
| BibTeX parsing | bibtexparser | >= 1.4 |
| Caching | Redis (optional) | >= 5.0 |
| Testing | pytest | >= 7.0 |

---

## 9. Security Considerations

1. **API Key Management**: Keys stored in environment variables (`INTEGRIREF_API_KEY`), transmitted via `Authorization: Bearer` header only
2. **Input Sanitization**: All user input passed through Pydantic models with type validation
3. **No Persistent Storage**: System is stateless — no database, no user data retention
4. **CORS Configuration**: Default allows all origins; production deployments should restrict
5. **Rate Limiting**: Per-adapter limits prevent abuse of external APIs
6. **No Code Execution**: System does not execute arbitrary code from references
7. **Logging**: API keys and full texts are never logged
