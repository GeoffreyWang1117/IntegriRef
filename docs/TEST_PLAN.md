# Test Plan Document

**Project**: IntegriRef — Multi-Layer Reference Integrity Verification Engine
**Version**: 0.1.0
**Date**: 2026-03-16
**Status**: Active
**Test Framework**: pytest 7.0+

---

## 1. Introduction

### 1.1 Purpose

This document defines the testing strategy, test categories, coverage requirements, and test execution procedures for IntegriRef. It covers unit tests, integration tests, API tests, and benchmark evaluations.

### 1.2 Test Scope

| In Scope | Out of Scope |
|----------|-------------|
| All L0-L4 pipeline components | External registry API behavior |
| 62 registry adapter interfaces | NLI model training |
| REST API endpoints | UI/frontend testing |
| Detection modules (6 types) | Load/stress testing |
| Bayesian risk scoring | Security penetration testing |
| Signal extraction and aggregation | |
| Batch processing | |
| Error handling and degradation | |

### 1.3 Current Test Statistics

| Metric | Value |
|--------|-------|
| Total tests | 555 |
| Test files | 11 |
| Pass rate | 100% |
| Execution time | ~9 seconds |

---

## 2. Test Strategy

### 2.1 Test Levels

```
┌───────────────────────────────────────────────────┐
│           Level 4: Benchmark Evaluation            │
│  (ablation experiments, accuracy measurements)     │
├───────────────────────────────────────────────────┤
│           Level 3: API Tests                       │
│  (endpoint testing, auth, validation, mocked I/O)  │
├───────────────────────────────────────────────────┤
│           Level 2: Integration Tests               │
│  (pipeline end-to-end, cross-layer signal flow)    │
├───────────────────────────────────────────────────┤
│           Level 1: Unit Tests                      │
│  (individual classes and functions)                │
└───────────────────────────────────────────────────┘
```

### 2.2 Test Categories

| Category | Description | Network Required | Mocking |
|----------|-------------|-----------------|---------|
| Unit | Single class/function behavior | No | Yes — all external dependencies |
| Integration | Cross-module pipeline flow | No | Partially — registry responses mocked |
| API | REST endpoint request/response | No | Yes — pipeline mocked for network calls |
| Benchmark | Accuracy/performance measurement | Optional | Some tests require live registries |

### 2.3 Principles

1. **No network dependency in CI**: All tests runnable offline with mocked responses
2. **Deterministic results**: No randomness-dependent assertions
3. **Fast feedback**: Full suite completes in < 30 seconds
4. **Fixture reuse**: Common test references defined as module-level fixtures
5. **Isolation**: Each test class independent; no shared mutable state

---

## 3. Test Coverage by Module

### 3.1 Core Pipeline (`test_pipeline.py` — 31 tests)

| Test Class | Count | Coverage |
|------------|-------|----------|
| TestSignalExtractorL0 | 8 | Signal extraction from L0 results: found, not_found, retracted, phantom_doi, metadata_mismatch, tortured phrases, email risk, statistical verification |
| TestSignalExtractorL1 | 3 | Signal extraction from L1: contrasting → misrepresent signal, supporting/mentioning → no signal |
| TestSignalExtractorL2 | 3 | Signal extraction from L2: contradicted, unsupported, supported |
| TestSignalExtractorL3 | 2 | Signal extraction from L3: empty anomalies, anomalies with type mapping |
| TestPipelineBasic | 7 | L0 only, L0+L4 found/not_found/retracted, L1 intent, L2 with abstract, L2 skipped without escalation |
| TestPipelineReport | 2 | summary() and api_response() structure validation |
| TestPipelineAllSignals | 4 | All signal names valid in SIGNAL_DEFINITIONS; no duplicate signals |
| TestPipelineBatch | 2 | Basic batch processing, all_mentioning post-processing |

### 3.2 Integration Tests (`test_integration.py` — 12 tests)

| Test Class | Count | Coverage |
|------------|-------|----------|
| TestFullPipeline | 8 | End-to-end: real paper → LOW, hallucinated → HIGH, retracted → ELEVATED, contrasting intent, L2 contradicted, full L0+L1+L2+L4, api_response structure, summary structure |
| TestBatchIntegration | 1 | Mixed batch: good + fake + retracted references |
| TestSignalExtractorWithDetectors | 3 | Tortured phrases in text, GRIM/statcheck in text, empty text safety |

**Test Fixtures** (real paper metadata):
- "Attention Is All You Need" (Vaswani 2017)
- "Deep Residual Learning" (He 2016)
- Wakefield 1998 (retracted)
- Hallucinated future paper (2030)
- Contrasting citation example

### 3.3 REST API Tests (`test_api.py` — 20 tests)

| Test Class | Count | Coverage |
|------------|-------|----------|
| TestHealth | 3 | /v1/health, /v1/registries, /v1/signals |
| TestVerify | 5 | Verify known/hallucinated papers, intent layer, minimal request, custom domain |
| TestBatch | 2 | Batch verify, empty batch |
| TestHallucination | 2 | Suspicious reference, real paper low score |
| TestRisk | 3 | No signals → LOW, bad signals → HIGH, invalid signal → 400 |
| TestAuth | 2 | Open access default, API key enforcement (401/403/200) |
| TestRequestValidation | 3 | Missing reference → 422, ReferenceInput.to_dict() sparse/full |

### 3.4 Verification Engine (`test_verification.py` — ~85 tests)

Covers:
- FieldCheck and field comparison logic
- SourceMatch construction and scoring
- CompositeScore weighted formula
- ReferenceResult status determination (OK/WARN/FAIL)
- L2 escalation decision logic
- Provenance audit trail generation
- Retraction flag propagation
- Multi-registry deduplication

### 3.5 Semantic Layer (`test_semantic.py` — ~23 tests)

Covers:
- IntentClassifier heuristic patterns (supporting, contrasting, mentioning)
- Position-weighted cue scoring
- Weak discourse marker handling
- NLIVerifier heuristic fallback
- AlignmentLabel threshold classification
- Batch processing

### 3.6 Scoring Layer (`test_scoring.py` — 14 tests)

Covers:
- BayesianRiskScorer initialization with domain priors
- Single signal observation and compute
- Multiple signal accumulation
- Confidence-weighted LR interpolation
- Risk tier threshold boundaries
- Log-odds contribution tracking
- Reset functionality
- Edge cases (unknown signal, confidence = 0)

### 3.7 Registry Adapters (`test_registry_base.py` — 16 tests)

Covers:
- All 62 adapters have valid `info()` return
- All adapters implement required interface methods
- Domain categorization correctness
- RegistryInfo field validation
- Adapter count per domain

### 3.8 Detection Modules (`test_week1_modules.py` — ~67 tests)

Covers:
- TorturedPhraseDetector: scan accuracy, scoring formula, empty text
- EmailRiskDetector: mill domains, free providers, institutional patterns
- GRIMTester: consistent/inconsistent means, edge cases
- StatChecker: t-test, F-test, chi-squared recomputation
- HallucinationDetector: phantom DOI, temporal, no-match signals
- MetadataValidator: cross-registry consistency

### 3.9 Graph Analysis (`test_week2_modules.py` — ~80 tests)

Covers:
- CitationGraph construction and traversal
- AnomalyDetector: all 7 detection algorithms
- GraphMetrics: health score computation
- Edge cases (empty graphs, single nodes)
- Benford's law chi-squared computation
- Citation ring CIDRE variant
- Temporal anomaly detection accuracy

### 3.10 Benchmark Framework (`test_benchmarks.py`, `test_ablation.py` — combined ~30 tests)

Covers:
- ClassificationMetrics (precision, recall, F1, accuracy)
- BenchmarkResult formatting
- L0 metrics computation
- Multiclass metrics
- Ablation risk_tier_to_label mapping
- AblationCase coverage (all labels)
- Ablation runner with mocked pipeline
- Report formatting with misclassification details

---

## 4. Test Data

### 4.1 Reference Fixtures

| Name | Type | Purpose |
|------|------|---------|
| attention_2017 | Real paper | Vaswani et al., known DOI → should verify as FOUND |
| resnet_2016 | Real paper | He et al. → should verify as FOUND |
| wakefield_1998 | Retracted | Known retracted paper → should flag retraction |
| hallucinated_2030 | Fabricated | Future year, fake authors → should flag as HIGH risk |
| contrasting_cite | Misrepresentation | CONTRASTING intent presented as support |

### 4.2 Tortured Phrase Samples

```
"The profound learning model uses slope descent to optimize..."
→ Should detect: "profound learning" (deep learning), "slope descent" (gradient descent)
```

### 4.3 Statistical Test Samples

```
"The mean score was M = 3.47, N = 12 participants..."
→ GRIM test: 3.47 × 12 = 41.64 (not integer → inconsistent)
```

---

## 5. Test Execution

### 5.1 Running All Tests

```bash
# Full suite
python -m pytest -x -q --timeout=120

# With verbose output
python -m pytest -v

# With coverage
python -m pytest --cov=core --cov=verification --cov=semantic --cov=graph --cov=scoring --cov=api
```

### 5.2 Running Specific Test Modules

```bash
# Pipeline tests
python -m pytest tests/test_pipeline.py -v

# Integration tests
python -m pytest tests/test_integration.py -v

# API tests
python -m pytest tests/test_api.py -v

# Ablation framework tests
python -m pytest tests/test_ablation.py -v
```

### 5.3 Running by Test Category

```bash
# Only unit tests (fast, no network)
python -m pytest tests/test_pipeline.py tests/test_scoring.py tests/test_semantic.py tests/test_verification.py -v

# Only integration tests
python -m pytest tests/test_integration.py tests/test_api.py -v

# Only benchmark/ablation tests
python -m pytest tests/test_benchmarks.py tests/test_ablation.py -v
```

### 5.4 Configuration

pytest configuration is in `pytest.ini`:
```ini
[pytest]
testpaths = tests
asyncio_mode = strict
timeout = 120
```

---

## 6. Quality Gates

### 6.1 CI/CD Pass Criteria

| Gate | Criteria |
|------|----------|
| All tests pass | 555/555 (zero failures) |
| No test timeouts | Each test completes within 120s |
| No deprecation errors | Warnings acceptable, errors not |
| Coverage threshold | >= 80% for core/ and verification/ |

### 6.2 Pre-Release Checklist

- [ ] All 555 tests pass
- [ ] No new test failures introduced
- [ ] API tests cover all endpoints
- [ ] Integration tests cover all layer combinations
- [ ] Ablation experiment produces valid metrics
- [ ] Benchmark accuracy targets met (L0 FP <= 2%, L2 accuracy >= 90%)

---

## 7. Known Limitations

1. **Network-dependent tests**: Some integration tests in `test_integration.py` query real registries and may fail due to rate limits or API outages. These are isolated from CI-critical tests.

2. **NLI model tests**: L2 tests fall back to heuristic when DeBERTa model is not installed. Full L2 accuracy testing requires `pip install transformers torch`.

3. **Graph tests**: L3 tests use synthetic graphs. Live OpenAlex queries are tested only in manual benchmark runs.

4. **Temporal sensitivity**: Tests with year-based assertions (e.g., "2030 is future") will need updates after 2029.

---

## 8. Test File Inventory

| File | Tests | Description |
|------|-------|-------------|
| `tests/test_pipeline.py` | 31 | Pipeline orchestrator unit tests |
| `tests/test_integration.py` | 12 | End-to-end pipeline integration |
| `tests/test_api.py` | 20 | FastAPI endpoint tests |
| `tests/test_ablation.py` | 12 | Ablation framework tests |
| `tests/test_verification.py` | ~85 | Verification engine tests |
| `tests/test_semantic.py` | ~23 | Intent classifier + NLI tests |
| `tests/test_scoring.py` | 14 | Bayesian risk scorer tests |
| `tests/test_registry_base.py` | 16 | Registry adapter interface tests |
| `tests/test_benchmarks.py` | ~18 | Benchmark metrics + L2 tests |
| `tests/test_week1_modules.py` | ~67 | Detection module tests |
| `tests/test_week2_modules.py` | ~80 | Graph analysis tests |
| `tests/test_intent_classifier.py` | — | Intent classifier deep tests |
| **Total** | **555** | |
