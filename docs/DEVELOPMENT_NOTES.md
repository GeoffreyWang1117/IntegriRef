# IntegriRef Development Notes

> Comprehensive technical record of all design decisions, implementation details, benchmarks, and optimization work.
> Last updated: 2026-03-16

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Layer-by-Layer Design](#2-layer-by-layer-design)
3. [Registry Adapter System](#3-registry-adapter-system)
4. [Benchmark Results & Competitive Position](#4-benchmark-results--competitive-position)
5. [Fine-Tuning Records](#5-fine-tuning-records)
6. [Week 1 Sprint: Integrity Signals](#6-week-1-sprint-integrity-signals)
7. [Week 2 Sprint: Deep Capabilities](#7-week-2-sprint-deep-capabilities)
8. [Code Review & Bug Fixes](#8-code-review--bug-fixes)
9. [Performance Optimization](#9-performance-optimization)
10. [Acceleration Infrastructure](#10-acceleration-infrastructure)
11. [Future Rust/C++ Rewrite Plan](#11-future-rustc-rewrite-plan)
12. [Paper Positioning](#12-paper-positioning)

---

## 1. Architecture Overview

### Core Design: L0–L4 Five-Layer Verification Stack

```
┌─────────────────────────────────────────────────────────┐
│  L4: Integrity Scoring (scoring/)                        │
│  └─ BayesianRiskScorer: 18 signals × likelihood ratios  │
│  └─ IntegrityScorer: 5 domain profiles, weighted avg    │
├─────────────────────────────────────────────────────────┤
│  L3: Citation Graph Analysis (graph/)                    │
│  └─ OpenAlexGraphBuilder: 2-hop citation graphs          │
│  └─ AnomalyDetector: 7 anomaly types (CIDRE, Benford)   │
│  └─ GraphMetrics: 5-dimension health score               │
├─────────────────────────────────────────────────────────┤
│  L2: Semantic Claim Verification (semantic/)             │
│  └─ NLIVerifier: DeBERTa-v3 fine-tuned on SciFact       │
│  └─ ClaimExtractor + AbstractFetcher                     │
│  └─ SemanticPipeline: end-to-end claim-abstract verify   │
├─────────────────────────────────────────────────────────┤
│  L1: Citation Intent Classification (semantic/)          │
│  └─ IntentClassifier: SciBERT fine-tuned on SciCite      │
│  └─ Supporting / Contrasting / Mentioning                │
├─────────────────────────────────────────────────────────┤
│  L0: Reference Existence Verification (verification/)    │
│  └─ VerificationEngine: multi-registry cascade           │
│  └─ FieldComparator: RapidFuzz + Jaccard similarity      │
│  └─ HallucinationDetector: multi-signal fake detection   │
│  └─ RetractionChecker: Crossref retraction status        │
│  └─ TorturedPhraseDetector: 281 paper-mill phrases       │
│  └─ EmailRiskDetector: 55 providers, 25 mill domains     │
│  └─ StatisticalVerifier: GRIM + statcheck                │
│  └─ SneakedReferenceDetector: Crossref vs text compare   │
├─────────────────────────────────────────────────────────┤
│  Foundation: 62 Registry Adapters (registries/)          │
│  └─ core/entity.py: ICEntity normalization               │
│  └─ core/registry.py: RegistryAdapter base class         │
│  └─ core/discovery.py: RegistryDiscovery routing         │
│  └─ core/oai_pmh.py: OAI-PMH harvesting                 │
├─────────────────────────────────────────────────────────┤
│  Orchestration: core/pipeline.py                         │
│  └─ IntegriRefPipeline: L0→L1→L2→L3→L4 unified call    │
│  └─ SignalExtractor: auto-collects signals from layers   │
│  └─ PipelineReport: structured output + API response     │
└─────────────────────────────────────────────────────────┘
```

### Key Invariants

- **Every adapter**: `info()`, `query_by_id()`, `search()` → always `entity.normalize()` before return
- **Never raise in adapters**: Return `None` / `[]` on errors
- **Rate limiting**: Class-level `_last_call` per adapter via `RegistryAdapter._rate_limit()`
- **Config**: `config.py` manages all API keys via environment variables
- **Models**: Env vars `INTENT_MODEL_PATH` and `NLI_MODEL_PATH` override defaults

---

## 2. Layer-by-Layer Design

### L0: Reference Existence Verification

**Module**: `verification/`

**Core flow** (`engine.py`):
1. Parse reference → extract IDs (DOI, PMID, ISBN, etc.)
2. ID lookup: `RegistryDiscovery.parallel_query_by_id()` across matching adapters
3. Title search fallback: `parallel_search()` if no ID match
4. Field comparison: `FieldComparator.compare()` for title/author/year/venue
5. Composite scoring: weighted formula → `CompositeScore`
6. Provenance audit: full match trail for transparency
7. L2 escalation: if L0 score < threshold, trigger semantic verification

**FieldComparator** (`field_comparator.py`):
- Title: `token_similarity()` = max(RapidFuzz token_set_ratio, Jaccard)
- Authors: `match_authors()` = surname matching with Jaro-Winkler
- Year: exact or ±1 year tolerance
- Venue: fuzzy match with abbreviation expansion
- LaTeX stripping: 6 regex passes for `\textit{}`, accents, etc.

**HallucinationDetector** (`hallucination_detector.py`):
- Multi-signal: DOI format validation, year plausibility, author name patterns
- Throughput: 336K ref/s (pure CPU)

### L1: Citation Intent Classification

**Module**: `semantic/intent_classifier.py`

**Architecture**: SciBERT (110M params) fine-tuned on SciCite
- Input: citing sentence text
- Output: 3-class (background=0/mentioning, method=1/supporting, result=2/supporting)
- Heuristic fallback: lexical cue matching (no GPU)

**Cue system** (heuristic):
- Strong contrasting cues: "however", "contradicts", "disagrees", "challenges"
- Weak discourse markers: "although", "while", "instead" (only boost existing signal)
- Supporting cues: "demonstrates", "confirms", "shows that"
- Method cues: "following", "based on", "using the method of"

### L2: Semantic Claim Verification

**Module**: `semantic/nli_verifier.py`

**Architecture**: DeBERTa-v3-base fine-tuned on SciFact
- Input: (premise=abstract_sentence, hypothesis=claim)
- Output: 3-class (contradiction=0, entailment=1, neutral=2)
- Per-sentence scoring: split abstract → score each sentence → take max

**Label mapping** (cross-encoder/nli-deberta-v3-base):
- Index 0 = contradiction, 1 = entailment, 2 = neutral

**Classification thresholds** (`_classify()`):
- Argmax-based with minimum margins
- Contradiction: con > 0.35 → CONTRADICTED
- Entailment: ent > 0.5 → SUPPORTED, ent > 0.25 → PARTIALLY_SUPPORTED
- Neutral dominant: ent > 0.25 → PARTIALLY, else UNSUPPORTED
- Low confidence: max_prob < 0.5 → needs_llm_upgrade flag

### L3: Citation Graph Analysis

**Module**: `graph/`

**Graph construction** (`builder.py`):
- `GraphBuilder.build_from_references()`: 1-hop from ICEntity
- `GraphBuilder.build_two_hop()`: 2-hop with pre-fetched references
- `OpenAlexGraphBuilder`: automated 2-hop via OpenAlex API (batch by 50)

**Anomaly detection** (`anomaly.py`) — 7 types:
1. `ORPHAN_CLUSTER`: references with low interconnection density
2. `TEMPORAL_ANOMALY`: citations to future papers
3. `EXCESSIVE_SELF_CITATION`: >30% self-citation rate
4. `SELF_CITATION_RING`: mutual citation cycles (CIDRE variant)
5. `BENFORD_VIOLATION`: chi-squared test on first-digit citation counts
6. `RECIPROCAL_CITATION`: bidirectional author-pair citation patterns
7. `CITATION_BURST`: year-over-year spikes + source concentration

**Health scoring** (`metrics.py`):
- 5 dimensions: interconnection (25%), temporal (25%), self-citation (20%), Benford (15%), reciprocal (15%)
- Each dimension scored 0–100 based on anomaly severity

### L4: Integrity Scoring

**Module**: `scoring/`

**BayesianRiskScorer** (`bayesian.py`):
```
prior_odds   = P(problematic) / (1 - P(problematic))
posterior_odds = prior_odds × LR_1 × LR_2 × … × LR_n
posterior_prob = posterior_odds / (1 + posterior_odds)
```

- 18 signal definitions with literature-derived likelihood ratios
- 8 domain priors (cancer_research=10%, CS=2%, default=3%)
- 4 risk tiers: LOW (<5%), ELEVATED (5–20%), HIGH (20–50%), CRITICAL (>50%)
- Confidence interpolation: `effective_lr = raw_lr^confidence`

**Key signal LR+ values**:
| Signal | LR+ | Category |
|--------|-----|----------|
| grim_test_failure | 50.0 | stats |
| tortured_phrases | 25.0 | text |
| phantom_doi | 25.0 | L0 |
| reference_not_found | 15.0 | L0 |
| citation_ring_detected | 10.0 | L3 |
| retracted_citation | 8.0 | L0 |
| statcheck_error | 8.0 | stats |
| temporal_anomaly | 8.0 | L3 |

---

## 3. Registry Adapter System

### Coverage: 62 adapters across 6 domains

| Domain | Count | Key Registries |
|--------|-------|---------------|
| Academic | 35 | OpenAlex, Crossref, PubMed, Semantic Scholar, arXiv, DBLP, ... |
| Patents | 5 | USPTO, EPO, WIPO, Google Patents, KIPRIS |
| Legal | 8 | CourtListener, EUR-Lex, Legifrance, Indian Kanoon, Korean Law, ... |
| Government | 6 | data.gov, e-Gov Japan, e-Stat, ... |
| Financial | 4 | EDGAR, FRED, INPI, ... |
| Standards | 4 | ISO, IEC, NIST, ... |

### Multilingual Coverage (15+ languages)
- **French**: HAL, Legifrance, Persée, theses.fr, INPI
- **Japanese**: CiNii, J-STAGE, e-Gov Japan Law, e-Stat
- **Korean**: KCI, KIPRIS, Korean Law Center
- **Latin America**: SciELO, Redalyc, Dialnet
- **Turkish**: DergiPark
- **EU**: Europeana
- **OAI-PMH**: DiVA (Nordic), CyberLeninka (Russian), GARUDA (Indonesian), Shodhganga (Indian)

### Adapter Pattern
```python
class ExampleRegistry(RegistryAdapter):
    def info(self) -> RegistryInfo:
        return RegistryInfo(name="example", domain="academic", ...)

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        resp = self._get(url, params=...)
        if not resp: return None
        entity = self._parse(resp.json())
        entity.normalize()
        return entity

    def search(self, title, author="", year="", **kw) -> list[ICEntity]:
        ...
```

---

## 4. Benchmark Results & Competitive Position

### Accuracy

| Layer | Before Fine-Tuning | After Fine-Tuning | Published SOTA |
|-------|--------------------|--------------------|----------------|
| L1 (SciCite) | 69.8% acc / 46.9 F1 | **87.6% acc / 86.0 F1** | 88.9 F1 (ImpactCite) |
| L2 (SciFact) | 52.4% acc | **93.5% acc** | 88% F1 (DeBERTa ft) |

**Key findings**:
- Domain fine-tuning >> model scaling: +41% from SciFact fine-tuning vs +5% from larger model
- L2 exceeds human agreement (93.5% vs 89.1%)
- L1 within 3 points of SOTA using only 110M params (vs ImpactCite XLNet-Large)
- scite.ai independent evaluation (Bakker 2023) showed F1~29%, far below self-reported

### Throughput

| Component | Throughput | Avg Latency | P95 Latency |
|-----------|-----------|-------------|-------------|
| L0 FieldComparator | 18,555 ref/s | 0.05ms | 0.06ms |
| L0 HallucinationDetector | 336,136 ref/s | <0.01ms | <0.01ms |
| L1 Heuristic (CPU) | 3,471 ref/s | 0.29ms | 0.49ms |
| L1 SciBERT batch=8 (GPU) | 263.5 ref/s | 3.80ms | 6.44ms |
| L2 NLI (GPU) | 25.1 ref/s | 39.8ms | 96.5ms |
| L2 Heuristic (CPU) | 96,540 ref/s | 0.01ms | 0.02ms |

**Batching insight**: batch=8 gives 10.8× improvement (24.4→263.5 ref/s). Batch=16 shows diminishing returns on 24GB GPU.

### Feature Comparison

| Capability | IntegriRef | scite.ai | CiteTrue | Papermill Alarm |
|-----------|-----------|----------|----------|-----------------|
| L0 Existence | 62 registries | 30M+ | 150M+ | — |
| L1 Intent | 86.0% F1 | ~74% F1† | — | — |
| L2 Claim Verify | 93.5% | — | — | — |
| L3 Graph | 7 anomaly types | Basic | — | — |
| L4 Risk Score | Bayesian 18 signals | — | — | — |
| Tortured Phrases | 281 patterns | — | — | ✓ |
| GRIM/statcheck | ✓ | — | — | — |
| Sneaked Refs | ✓ | — | — | — |
| Retraction Check | ✓ | ✓ | ✓ | — |
| Multilingual | 15+ langs | Partial | — | — |
| Open Source | ✓ | — | — | — |

†scite.ai self-reported; independent evaluation (Bakker 2023) showed F1~29%.

---

## 5. Fine-Tuning Records

### L1: SciBERT on SciCite

**Script**: `benchmarks/train_l1.py`
**Model**: `allenai/scibert_scivocab_uncased` (110M params)
**Dataset**: SciCite — train: 8,243, dev: 916, test: 1,861
**Label mapping**: background→0 (mentioning), method→1 (supporting), result→2 (supporting)

**Training config**:
- Learning rate: 2e-5, batch size: 16, max_length: 256
- 5 epochs, best at epoch 3
- GPU: NVIDIA 24GB (CUDA_VISIBLE_DEVICES=0)

**Results** (test set):
| Metric | Value |
|--------|-------|
| 3-class accuracy | 87.6% |
| Macro F1 | 0.860 |
| Supporting F1 | 0.870 |
| Mentioning F1 | 0.883 |
| Contrasting F1 | 0.826 |

**Saved to**: `models/intent_classifier/`
**Env var**: `INTENT_MODEL_PATH=models/intent_classifier`

### L2: DeBERTa-v3-base on SciFact

**Script**: `benchmarks/train_l2.py`
**Model**: `cross-encoder/nli-deberta-v3-base` (184M params)
**Dataset**: SciFact — 3,828 NLI pairs (gold annotations + synthetic negatives)
**Pair construction**: SUPPORT→entailment(1), CONTRADICT→contradiction(0), non-evidence→neutral(2)

**Training config**:
- Learning rate: 2e-5, batch size: 8, max_length: 384
- 10 epochs, best at epoch 6
- GPU: NVIDIA 24GB (CUDA_VISIBLE_DEVICES=0)
- First attempt OOM at batch=16/max_len=512, fixed by reducing

**Results** (SciFact task eval):
| Metric | Value |
|--------|-------|
| Task accuracy | 93.5% |
| SUPPORTS F1 | 0.963 |
| REFUTES F1 | 0.941 |

**Saved to**: `models/scifact_nli/`
**Env var**: `NLI_MODEL_PATH=models/scifact_nli`

### L2 Model Comparison Experiment

**Script**: `benchmarks/bench_l2_experiment.py`

| Model | Task Accuracy | Delta |
|-------|--------------|-------|
| nli-deberta-v3-base (general) | 52.4% | baseline |
| nli-deberta-v3-large (general) | 57.1% | +4.7% |
| + threshold optimization | 55.1% | +2.7% |
| **+ SciFact fine-tuning** | **93.5%** | **+41.1%** |

**Conclusion**: Domain fine-tuning is the single most impactful intervention, dwarfing model scaling and threshold tuning.

### L1 Augmented Training (prepared, not yet run)

**Script**: `benchmarks/train_l1_augmented.py`
**Target**: 89%+ accuracy

**Strategies**:
1. Class-balanced oversampling (background:4840 → oversample method/result to match)
2. Text augmentation: random deletion (10%), swap, insertion, synonym replacement (60+ synonym map)
3. ACL-ARC joint training (~1,700 samples, 6 categories → 3-class mapping)
4. Label smoothing: `CrossEntropyLoss(label_smoothing=0.1)`

**Output**: `models/intent_classifier_v2/`

---

## 6. Week 1 Sprint: Integrity Signals

### Retraction Checker (`verification/retraction_checker.py`)
- **API**: Crossref REST API (`/works/{doi}`)
- **Detection**: `update-to` field for retraction notices + `relation.is-retracted-by`
- **Features**: Single + batch check, in-memory cache, 20ms rate limit
- **Data class**: `RetractionInfo(doi, is_retracted, retraction_date, retraction_notice_doi, retraction_reason, source)`

### Tortured Phrases Detector (`verification/tortured_phrases.py`)
- **Dictionary**: 281 tortured→standard mappings across 6 domains (ML/AI, Stats, Medical, CS, Physics, General)
- **Matching**: Aho-Corasick automaton (single-pass) with word boundary checks; regex fallback
- **Scoring**: 0→0, 1→30, 2→55, 3→70, 4→80, 5→90, 6+→92-100; diversity bonus for unique phrases
- **Abstract mode**: 1.2× score boost (shorter text → each match more alarming)
- **Examples**: "profound learning"→"deep learning", "arbitrary forest"→"random forest", "bosom malignancy"→"breast cancer"

### Email Risk Detector (`verification/email_risk.py`)
- **Free providers**: 55 domains (Gmail, QQ, 163, Yandex, Naver, etc.)
- **Mill domains**: 25 known paper-mill fronts (from Retraction Watch / PPS)
- **Patterns**: numbered addresses (`author123@`), random-looking strings, fake institutional domains
- **Scoring**: mill domain=+80, free provider=+20, numbered=+15, fake institutional=+25
- **Aggregate**: `assess_author_list()` adds bonuses for multiple free emails, same-provider clustering

### Bayesian Risk Scorer (`scoring/bayesian.py`)
- **Model**: Prior odds × ∏(LR_i) → posterior probability
- **Signals**: 18 defined (L0: 4, L1: 2, L2: 2, L3: 4, text: 2, stats: 2)
- **Priors**: 8 domain-specific (cancer=10%, CS=2%, default=3%)
- **Confidence**: `effective_lr = raw_lr^confidence` (interpolates toward neutral)
- **Risk tiers**: LOW (<5%), ELEVATED (5–20%), HIGH (20–50%), CRITICAL (>50%)
- **Overflow protection**: log-odds capped at ±700

### Enhanced Graph Anomaly (`graph/anomaly.py`)
- **Benford's law**: chi-squared test on first-digit distribution of citation counts (n≥20 required)
- **Reciprocal citations**: bidirectional author-pair counts; flags if both dirs ≥3 and ratio >0.5
- **Citation burst**: year-over-year spikes (>3× factor) + source concentration (>50% from <3 sources)
- **Graph metrics**: rebalanced weights 25/25/20/15/15

---

## 7. Week 2 Sprint: Deep Capabilities

### OpenAlex 2-Hop Graph (`graph/openalex_builder.py`)
- **API**: `GET /works/{id}?select=...` + batch `GET /works?filter=openalex:W1|W2|...`
- **Batch size**: 50 per request; rate limit 100ms (10 req/s polite pool)
- **Flow**: root→hop1 batch→hop2 batch; deduplicates existing nodes
- **Metadata**: normalizes `counts_by_year` from OpenAlex list format to dict `{year: count}`
- **Stats**: tracks API calls, total time, average latency

### Statistical Verifier (`verification/statistical.py`)

**GRIM Test** (Brown & Heathers 2016):
- `mean × N × items` must be integer (±tolerance)
- Regex: `M(?:ean)?\s*=\s*(\d+\.\d+)` ... `N\s*=\s*(\d+)` with sentence boundary lookahead
- Improved regex: allows dots inside numbers (e.g., "M = 2.31; SD = 0.5; N = 25")

**statcheck** (Epskamp & Nuijten 2016):
- 5 APA patterns: t(df)=, F(df1,df2)=, χ²(df)=, r(df)=, Z=
- p-value recomputation via `scipy.stats` (fallback marks as unchecked)
- Consistency: |reported_p - computed_p| ≤ 0.05
- Decision error: significance flipped (p<.05 vs p≥.05)
- `_parse_p()`: handles leading-dot `p = .05` → captures `"05"` → divides by `10^len`

**Risk scoring**: GRIM failure=+25, statcheck error=+10, decision error=+20 (cap 100)

### Sneaked References (`verification/sneaked_references.py`)
- **Based on**: Besancon et al. 2024 (JASIST Best Paper 2025)
- **Detection**: compare Crossref `reference` list vs in-text citations
- **Matching**: DOI exact → title fuzzy (RapidFuzz ≥0.85) → author surname + year in raw string
- **Red flags**: sneaked ratio >20%=+40, >50%=+70; journal concentration >30%=+20; future refs=+15
- **Journal analysis**: Herfindahl-Hirschman Index for concentration measurement

### L1 Augmented Training Script (`benchmarks/train_l1_augmented.py`)
- **TextAugmenter**: 4 strategies — deletion (10%), swap (2 pairs), insertion (1 word), synonym replacement (60+ map)
- **AugmentedSciCiteDataset**: oversamples minority classes to majority count, creates augmented copies
- **ACL-ARC**: loads from HuggingFace `citation_intent_classification`, maps 6→3 classes
- **Training**: AdamW + linear schedule with warmup, gradient clipping, per-epoch dev evaluation
- **`--dry-run`**: prints data stats and augmentation examples without training

---

## 8. Code Review & Bug Fixes

### Bug: email_risk.py TLD matching (2026-03-11)
**Problem**: `_is_institutional_tld()` used `if tld in domain` — substring match caused ".edu" to match "reduced.com" (contains "edu").
**Fix**: Segment-aware matching: split domain into dot-separated parts, check if TLD segments appear consecutively.

### Bug: openalex_builder.py counts_by_year format (2026-03-11)
**Problem**: OpenAlex returns `counts_by_year` as list `[{"year": 2023, "cited_by_count": 15}]`, but `anomaly.py` `detect_citation_burst()` expected dict `{2023: 15}`.
**Fix**: Normalize list→dict in `_parse_to_node()`.

### Bug: statistical.py GRIM regex (2026-03-11)
**Problem**: GRIM pattern `[^.]*?` stopped at dots inside numbers, failing on "M = 2.31; SD = 0.5; N = 25".
**Fix**: Replace with `(?:(?!\.\s+[A-Z]).){0,80}?` — negative lookahead stops only at sentence boundaries (period + space + uppercase).

### Bug: bench_l2.py load_model missing (earlier)
**Problem**: Called `verifier.load_model()` but `NLIVerifier` had no such method.
**Fix**: Use `verifier._ensure_loaded()` with device detection.

### Bug: bench_l2.py _NLI_TO_REPORT undefined (earlier)
**Problem**: Referenced undefined `_NLI_TO_REPORT` variable.
**Fix**: Replaced with `_SCIFACT_LABEL_MAP`.

### Bug: L2 training OOM (earlier)
**Problem**: batch_size=16 + max_length=512 on 24GB GPU caused OOM.
**Fix**: Reduced to batch_size=8, max_length=384.

### Bug: bench_throughput.py HallucinationDetector API (earlier)
**Problem**: Called `detector.analyze(title=..., doi=...)` but method signature is `analyze(ref: dict)`.
**Fix**: Pass `ref=ref` dict.

---

## 9. Performance Optimization

### Quick Wins Implemented

#### 1. NLI Sentence Batching (5-10× L2 throughput)
**Before**: Per-sentence GPU calls → 10 sentences × 40ms = 400ms
**After**: Batch all sentences → 1 GPU call → ~50ms
**Method**: Tokenize all (sentence, claim) pairs at once, single `model(**inputs)` call

#### 2. Registry Parallel Queries (10-20× L0 throughput)
**Before**: Sequential loop across 62 adapters → 3-8 seconds per reference
**After**: `ThreadPoolExecutor(max_workers=8)` → all adapters queried in parallel → ~300ms
**Method**: `RegistryDiscovery.parallel_query_by_id()` with timeout per future

#### 3. Aho-Corasick for Tortured Phrases (5-20× text scanning)
**Before**: 281 individual regex `finditer()` passes → O(281 × text_length)
**After**: Single-pass Aho-Corasick automaton → O(text_length)
**Method**: `pyahocorasick` library with word-boundary post-check; regex fallback

### Performance Bottleneck Analysis

| Component | Bottleneck | Type | Current | Potential |
|-----------|-----------|------|---------|-----------|
| Registry Discovery | 62 sequential HTTP | I/O | 3-8s/ref | 300ms/ref |
| NLI Verification | Per-sentence GPU | Compute | 25 ref/s | 250 ref/s |
| Tortured Phrases | 281 regex scans | CPU | 200 papers/s | 1000+ papers/s |
| Graph Anomaly | O(n²) author pairs | CPU | ~1000 nodes/s | 10K nodes/s |
| Field Comparator | Repeated strip/tokenize | CPU | 18K ref/s | 50K ref/s |
| OpenAlex Graph | Sequential hop fetches | I/O | ~3 calls | ~1 call |

### Recommended Next Optimizations

**Phase 2 (Medium effort)**:
- Global `requests.Session` with connection pooling across adapters
- Model singleton cache (eliminate 500ms load per pipeline instance)
- Registry response cache per session (avoid duplicate HTTP calls)
- Combined regex patterns in `strip_latex()` (single pass instead of 6)

**Phase 3 (High effort — Rust/C++ via PyO3)**:
- Graph anomaly detection algorithms
- String similarity computations (Jaro-Winkler, token set ratio)
- Multi-pattern text matching
- LaTeX stripping and Unicode normalization

---

## 10. Acceleration Infrastructure

> Phase 1 加速方案：在 Python 生态内实现生产级性能优化，无需语言重写。

### 10.1 架构决策：Python 还是 Java/Go 重写？

**结论**：Python 生态内加速优先，仅在 >1M citations/day 场景下考虑 Go/Rust 微服务提取。

**理由**：
- IntegriRef 瓶颈是 **I/O（62 注册表 HTTP 调用）** 和 **模型推理（GPU/CPU bound）**，非 Python 本身
- `asyncio + aiohttp` 已达到接近 Go 的 I/O 并发性能
- ONNX Runtime 是 C++ 底层，Python 仅做调度
- 真正需要重写的场景：高并发 API 网关（Go）、CPU 密集图算法（Rust via PyO3）

### 10.2 新增模块概览

| 模块 | 文件 | 行数 | 功能 |
|------|------|------|------|
| 响应缓存 | `core/cache.py` | 223 | LRU + Redis 双层缓存，TTL，负缓存 |
| 熔断器 | `core/circuit_breaker.py` | 203 | 滑动窗口故障检测，CLOSED→OPEN→HALF_OPEN |
| 异步注册表 | `core/async_registry.py` | 310 | aiohttp 异步基类，连接池，重试 |
| 异步发现 | `core/async_discovery.py` | 328 | asyncio.gather 并发查询 |
| 批处理器 | `core/batch_processor.py` | 154 | Semaphore 控制并发验证 |
| 解析工具 | `core/parsing.py` | 261 | 统一的 title/author/year/doi 提取 |
| ONNX 加速 | `semantic/onnx_exporter.py` | 371 | DeBERTa ONNX 导出 + INT8 量化 |

### 10.3 响应缓存 (`core/cache.py`)

**双层架构**：
```
请求 → LRU 内存缓存 (2.2M ops/s) → Redis (可选) → 注册表 API
```

- `LRUCache`：线程安全，OrderedDict 实现，max_size 淘汰，hit/miss 统计
- `ResponseCache`：统一接口，`make_key()` MD5 哈希，`get_or_none()` 区分 uncached vs cached-None
- **负缓存**：`set_not_found()` 缓存"不存在"结果，避免重复查询失败 ID
- **TTL 策略**：ID 查询 24h，搜索 1h，未找到 10min，撤稿状态 12h

### 10.4 熔断器 (`core/circuit_breaker.py`)

**状态机**：
```
CLOSED ──(5 failures/60s)──→ OPEN ──(30s recovery)──→ HALF_OPEN ──(success)──→ CLOSED
                                                        └──(failure)──→ OPEN
```

- 滑动窗口故障追踪（deque + timestamp），不是简单计数器
- `CircuitBreakerRegistry`：管理所有注册表的熔断器，提供 `open_circuits()`、`all_stats()`
- 已集成到 `RegistryAdapter._get()` 和 `_post()`，62 个适配器**自动受益**

### 10.5 异步 I/O (`core/async_registry.py` + `core/async_discovery.py`)

**AsyncRegistryAdapter**：
- 共享 `TCPConnector(limit=100, limit_per_host=10)` 连接池
- aiohttp session 生命周期管理：`get_shared_session()` / `close_shared_session()`
- 内置熔断器 + 3 次指数退避重试 + HTTP 429 特殊处理

**AsyncRegistryDiscovery**：
- 同时支持 sync（`run_in_executor` 包装）和 async 适配器
- `parallel_query_by_id()`：`asyncio.gather()` 真并发，cache 集成
- `verify_reference()`：高级 API，ID 查询 → 搜索回退

### 10.6 ONNX Runtime 加速 (`semantic/onnx_exporter.py`)

**导出流程**：
```
HuggingFace DeBERTa-v3 → torch.onnx.export (opset 18)
                        → ONNX 图优化 (onnxruntime.transformers.optimizer)
                        → INT8 动态量化 (onnxruntime.quantization)
```

**模型大小**：
| 格式 | 大小 | 占比 |
|------|------|------|
| PyTorch (FP32) | ~704 MB | 100% |
| ONNX INT8 量化 | ~233 MB | 33% |

**ONNXNLIVerifier**：NLIVerifier 的 drop-in 替换
- `predict_batch()`：numpy 推理，兼容 NLIVerifier 接口
- `verify()` / `verify_batch()`：相同接口，ONNX 不可用时自动回退 PyTorch
- 关键修复：DeBERTa v3 不接受 `token_type_ids`，需检查 `model_input_names`

### 10.7 Benchmark 结果

#### 基础设施开销

| 组件 | 吞吐量 | 开销 |
|------|--------|------|
| LRU Cache 读 | 2,200,000 ops/s | <1μs |
| LRU Cache 写 | 1,800,000 ops/s | <1μs |
| 熔断器检查 | 1,800,000 ops/s | <1μs |
| 解析工具 | 500,000+ ops/s | <2μs |

**结论**：缓存 + 熔断器 + 重试的每次调用开销 <1μs，零感知。

#### I/O 并发加速

| 策略 | 吞吐量 | 延迟 | 加速比 |
|------|--------|------|--------|
| 同步顺序 (baseline) | — | 62×2ms = 124ms | 1× |
| asyncio.gather | — | ~8.5ms | **14.5×** |
| asyncio + 缓存命中 | — | ~0.85ms | **145.7×** |

#### 批处理并发

| 并发度 | 加速比 |
|--------|--------|
| 1 (顺序) | 1× |
| 8 workers | 7.2× |
| 16 workers | 12.9× |
| 32 workers | 19.8× |

#### ONNX vs PyTorch 推理

| 引擎 | 吞吐量 | vs PyTorch CPU |
|------|--------|----------------|
| PyTorch CPU (FP32) | baseline | 1× |
| ONNX CPU (FP32) | — | ~1.5× |
| ONNX CPU (INT8) | — | **2.2×** |
| PyTorch GPU | — | ~2.8× |
| ONNX CPU INT8 vs GPU | — | 达 GPU 的 79% |

**关键发现**：ONNX INT8 在无 GPU 环境下达到 PyTorch GPU 性能的 79%，对纯 CPU 部署意义重大。

### 10.8 加速测试覆盖

| 测试文件 | 测试数 | 覆盖 |
|----------|--------|------|
| `test_acceleration.py` | 70 | Cache, CircuitBreaker, Parsing, Async, ONNX |

### 10.9 生产部署建议

```python
# 启动配置
import uvloop
uvloop.install()  # 替换默认事件循环，接近 Go 性能

from core.registry import configure_cache
configure_cache(redis_url="redis://localhost:6379", max_memory=50_000)

from core.async_discovery import AsyncRegistryDiscovery
discovery = AsyncRegistryDiscovery()
discovery.register_all(all_adapters)

# 批量验证
from core.batch_processor import BatchProcessor
processor = BatchProcessor(discovery, max_concurrent=32)
results = await processor.verify_batch(references)
```

**依赖**（`requirements.txt` 新增）：
- `aiohttp>=3.9.0` — 异步 HTTP
- `uvloop>=0.19.0` — 高性能事件循环（非 Windows）
- `pytest-asyncio>=0.23.0` — 异步测试
- `redis[hiredis]>=5.0.0` — Redis 缓存（可选）
- `onnxruntime>=1.17.0` — ONNX 推理（可选）

---

## 11. Future Rust/C++ Rewrite Plan

### Priority Ranking for Compiled Rewrite

| Priority | Module | Lines | Estimated Speedup | Reason |
|----------|--------|-------|-------------------|--------|
| P0 | `graph/anomaly.py` | ~480 | 20-100× | Nested loops, set operations, math |
| P0 | `verification/tortured_phrases.py` | ~600 | 5-20× | CPU-bound text scanning |
| P1 | `verification/field_comparator.py` | ~400 | 3-10× | Called 1000s of times, string ops |
| P1 | `verification/sneaked_references.py` | ~350 | 3-5× | O(n²) fuzzy matching |
| P2 | `semantic/intent_classifier.py` (heuristic) | ~200 | 2-3× | 100+ regex patterns |
| P2 | `verification/email_risk.py` | ~380 | 2-3× | Regex patterns |

### Rewrite Strategy

**Recommended approach**: PyO3 (Rust → Python bindings)
```
src/
├── lib.rs              # PyO3 module definition
├── graph_anomaly.rs    # Graph algorithms
├── text_matching.rs    # Aho-Corasick + field comparison
└── string_utils.rs     # LaTeX stripping, Unicode normalization
```

**Keep in Python** (I/O bound, not worth rewriting):
- All registry adapters (HTTP I/O)
- NLI/intent model inference (PyTorch handles GPU)
- Bayesian scorer (simple math, few iterations)
- Configuration and orchestration

### Integration Pattern
```python
# Python wrapper
try:
    from integriref_core import graph_anomaly_detect, text_match_aho
    _HAS_NATIVE = True
except ImportError:
    _HAS_NATIVE = False

class AnomalyDetector:
    def detect_benford_violation(self):
        if _HAS_NATIVE:
            return graph_anomaly_detect.benford(self._graph_data)
        return self._detect_benford_python()  # fallback
```

---

## 12. Paper Positioning

### Title
"IntegriRef: A Multi-Layer Open-Source Framework for Automated Reference Integrity Verification"

### Core Contributions (6 points)
1. First L0–L4+ verification stack (vs competitors' 1–2 layers)
2. L2 semantic verification exceeds human agreement (93.5% vs 89.1% on SciFact)
3. L1 intent classification competitive with commercial SOTA (86% F1 vs scite.ai independent 29%)
4. Bayesian risk model with 18 literature-derived signal likelihood ratios
5. Open source, multilingual (15+ languages), self-hostable (62 registry adapters)
6. Novel paper-mill detection signals: tortured phrases + GRIM + statcheck + sneaked references

### Target Venues
- **Tier 1**: JASIST, Scientometrics, QSS
- **Tier 1 CS**: ACL/EMNLP (NLP direction), JCDL
- **High impact**: Nature Scientific Reports, PLOS ONE

### Key References
- Cohan et al. 2019 "Structural Scaffolds" (NAACL) — SciCite dataset
- Wadden et al. 2022 "MultiVerS" (NAACL Findings) — SciFact SOTA
- Kojaku & Masuda 2021 "CIDRE" (Scientific Reports) — citation cartel detection
- Besancon et al. 2024 "Sneaked References" (JASIST Best Paper) — reference manipulation
- Scancar 2025 — ML-based paper mill detection (91% acc)
- Bakker et al. 2023 — independent scite.ai evaluation (F1~29%)
- Cabanac et al. 2021 — tortured phrases
- Brown & Heathers 2016 — GRIM test
- Epskamp & Nuijten 2016 — statcheck

---

## Test Coverage

| Test File | Tests | Coverage |
|-----------|-------|---------|
| `test_entity.py` | 17 | ICEntity, normalization |
| `test_graph.py` | 15 | GraphBuilder, CitationGraph |
| `test_intent_classifier.py` | 25 | IntentClassifier heuristic |
| `test_oai_pmh.py` | 1 | OAI-PMH harvester |
| `test_registry_base.py` | 16 | All 62 adapter smoke tests |
| `test_scoring.py` | 14 | IntegrityScorer, profiles |
| `test_semantic.py` | 23 | NLIVerifier, ClaimExtractor, pipeline |
| `test_verification.py` | 86 | FieldComparator, engine, hallucination |
| `test_benchmarks.py` | 49 | Dataset loaders, metrics |
| `test_week1_modules.py` | 69 | Retraction, tortured, Bayesian, graph |
| `test_week2_modules.py` | 84 | OpenAlex, GRIM, statcheck, sneaked, augmenter |
| `test_acceleration.py` | 70 | Cache, CircuitBreaker, Parsing, Async, ONNX |
| `test_pipeline.py` | 31 | SignalExtractor (L0-L3), Pipeline (L0-L4), batch |
| **Total** | **511** | |
