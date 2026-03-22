# API Specification Document

**Project**: IntegriRef REST API
**Version**: v1
**Base URL**: `http://{host}:8000/v1`
**Date**: 2026-03-16

---

## 1. Overview

The IntegriRef REST API exposes the L0-L4 verification pipeline as HTTP endpoints. Built with FastAPI, it supports automatic OpenAPI documentation at `/docs` (Swagger UI) and `/redoc`.

### 1.1 Authentication

Optional API key authentication via the `Authorization` header.

```
Authorization: Bearer <your-api-key>
```

When the environment variable `INTEGRIREF_API_KEY` is not set, all endpoints are publicly accessible. When set, all endpoints except `/v1/health` require a valid bearer token.

| Status Code | Condition |
|-------------|-----------|
| 401 | API key required but `Authorization` header missing |
| 403 | API key provided but does not match |

### 1.2 Common Response Format

All responses are JSON. Errors follow this format:

```json
{
    "detail": "Error description"
}
```

### 1.3 CORS

Cross-Origin Resource Sharing is enabled for all origins by default. Production deployments should restrict `allow_origins`.

---

## 2. Endpoints

### 2.1 Health Check

```
GET /v1/health
```

No authentication required. Returns service status and loaded registry count.

**Response** (200):
```json
{
    "status": "ok",
    "version": "0.1.0",
    "registries_loaded": 62
}
```

---

### 2.2 List Registries

```
GET /v1/registries
```

Returns all registered adapters with metadata.

**Response** (200):
```json
{
    "registries": [
        {
            "name": "crossref",
            "domain": "academic",
            "base_url": "https://api.crossref.org",
            "coverage": "130M+ DOIs"
        },
        {
            "name": "pubmed",
            "domain": "academic",
            "base_url": "https://eutils.ncbi.nlm.nih.gov",
            "coverage": "36M+ biomedical articles"
        }
    ],
    "count": 62
}
```

---

### 2.3 Verify Single Reference

```
POST /v1/verify
```

Run the L0-L4 pipeline on a single reference.

**Request Body**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `reference` | ReferenceInput | Yes | — | The reference to verify |
| `citing_sentence` | string | No | `""` | Sentence where reference is cited (for L1) |
| `abstract` | string | No | `""` | Abstract of cited paper (for L2 NLI) |
| `full_text` | string | No | `""` | Full text for detector modules |
| `layers` | string[] | No | `["L0","L1","L2","L3","L4"]` | Which layers to run |
| `domains` | string[] | No | `[]` | Registry domain filter |
| `domain` | string | No | `"default"` | Bayesian prior domain |

**ReferenceInput**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | No | Paper/document title |
| `authors` | string[] | No | Author names |
| `year` | string | No | Publication year |
| `doi` | string | No | Digital Object Identifier |
| `arxiv_id` | string | No | arXiv identifier |
| `venue` | string | No | Journal or conference name |
| `key` | string | No | Citation key |
| `author_emails` | string[] | No | Author email addresses |

**Example Request**:
```json
{
    "reference": {
        "title": "Attention Is All You Need",
        "authors": ["Vaswani", "Shazeer", "Parmar"],
        "year": "2017",
        "doi": "10.48550/arXiv.1706.03762"
    },
    "citing_sentence": "We adopt the transformer architecture from Vaswani et al. (2017).",
    "layers": ["L0", "L1", "L4"]
}
```

**Response** (200):
```json
{
    "status": "success",
    "risk_tier": "low",
    "risk_probability": 0.012,
    "layers_run": ["L0", "L1", "L4"],
    "signals_fired": [],
    "processing_time_ms": 2341.5,
    "l0": {
        "citation_id": "",
        "existence": {
            "status": "found",
            "sources": ["crossref", "semantic_scholar"],
            "candidates": [
                {
                    "title": "Attention is All you Need",
                    "authors": ["Ashish Vaswani", "..."],
                    "year": "2017",
                    "similarity": 0.98
                }
            ]
        },
        "scores": {
            "existence": 1.0,
            "title": 0.98,
            "author": 0.95,
            "venue": 0.90,
            "overall": 0.96,
            "label": "supported"
        },
        "is_retracted": false
    },
    "l1": {
        "intent": "supporting",
        "confidence": 0.82,
        "cue_phrase": "adopt"
    },
    "l4": {
        "prior": 0.03,
        "posterior": 0.012,
        "risk_tier": "LOW",
        "signals_fired": 0,
        "total_signals": 5
    }
}
```

---

### 2.4 Batch Verify

```
POST /v1/batch
```

Verify multiple references in a single call.

**Request Body**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `references` | ReferenceInput[] | Yes | — | List of references |
| `citing_sentences` | string[] | No | `[]` | Parallel array of citing sentences |
| `abstracts` | string[] | No | `[]` | Parallel array of abstracts |
| `full_text` | string | No | `""` | Full text (shared across all refs) |
| `layers` | string[] | No | `["L0","L4"]` | Which layers to run |
| `domains` | string[] | No | `[]` | Registry domain filter |
| `domain` | string | No | `"default"` | Bayesian prior domain |

**Example Request**:
```json
{
    "references": [
        {
            "title": "Attention Is All You Need",
            "year": "2017",
            "doi": "10.48550/arXiv.1706.03762"
        },
        {
            "title": "Fake Paper That Does Not Exist",
            "year": "2099"
        }
    ],
    "layers": ["L0", "L4"]
}
```

**Response** (200):
```json
{
    "status": "success",
    "total": 2,
    "results": [
        {
            "risk_tier": "low",
            "found": true,
            "signals_fired": 0
        },
        {
            "risk_tier": "high",
            "found": false,
            "signals_fired": 2
        }
    ],
    "processing_time_ms": 4567.8
}
```

---

### 2.5 Hallucination Check

```
POST /v1/hallucination
```

Fast hallucination detection without full pipeline (L0 structural analysis only).

**Request Body**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `references` | ReferenceInput[] | Yes | References to check |

**Example Request**:
```json
{
    "references": [
        {
            "title": "Quantum Neural Networks for Consciousness Detection",
            "authors": ["Fakeman, J."],
            "year": "2030"
        }
    ]
}
```

**Response** (200):
```json
{
    "results": [
        {
            "title": "Quantum Neural Networks for Consciousness Detection",
            "is_suspicious": true,
            "hallucination_score": 78.5,
            "flags": [
                {
                    "type": "temporal_impossibility",
                    "severity": "HIGH",
                    "detail": "Publication year 2030 is in the future"
                },
                {
                    "type": "no_registry_match",
                    "severity": "MEDIUM",
                    "detail": "Not found in any registry"
                }
            ]
        }
    ]
}
```

---

### 2.6 Citation Graph Analysis

```
GET /v1/graph/{doi}
```

Build and analyze a citation graph for a given DOI using OpenAlex data.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `doi` | string | DOI of the paper (URL-encoded if contains `/`) |

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hops` | int | 1 | Graph depth (1 or 2) |

**Example Request**:
```
GET /v1/graph/10.48550/arXiv.1706.03762?hops=2
```

**Response** (200):
```json
{
    "doi": "10.48550/arXiv.1706.03762",
    "nodes": 45,
    "edges": 128,
    "health_score": 82.5,
    "dimensions": {
        "interconnection": 85.0,
        "temporal": 95.0,
        "self_citation": 78.0,
        "benford": 70.0,
        "reciprocal": 90.0
    },
    "anomalies": [
        {
            "type": "excessive_self_citation",
            "severity": 0.35,
            "description": "Self-citation rate of 0.32 exceeds threshold"
        }
    ]
}
```

**Error Response** (graph not found):
```json
{
    "doi": "10.xxxx/nonexistent",
    "nodes": 0,
    "edges": 0,
    "anomalies": [],
    "health_score": null,
    "message": "Could not build graph — DOI not found in OpenAlex"
}
```

---

### 2.7 Bayesian Risk Scoring

```
POST /v1/risk
```

Compute Bayesian risk score from raw signal observations.

**Request Body**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `domain` | string | No | `"default"` | Prior domain |
| `signals` | object | No | `{}` | signal_name → {present, confidence} |

**Example Request**:
```json
{
    "domain": "cancer_research",
    "signals": {
        "reference_not_found": {
            "present": true,
            "confidence": 0.9
        },
        "phantom_doi": {
            "present": true,
            "confidence": 0.8
        },
        "tortured_phrases": {
            "present": false,
            "confidence": 1.0
        }
    }
}
```

**Response** (200):
```json
{
    "prior": 0.10,
    "posterior": 0.72,
    "risk_tier": "CRITICAL",
    "domain": "cancer_research",
    "signals_fired": 2,
    "total_signals": 3,
    "log_odds_contributions": {
        "reference_not_found": 2.44,
        "phantom_doi": 2.57,
        "tortured_phrases": -0.01
    },
    "equivalent_score": 82.3,
    "warnings": []
}
```

**Error Response** (invalid signal):
```json
{
    "detail": "Unknown signal: nonexistent_signal. Valid: ['all_citations_mentioning', 'citation_misrepresents_source', ...]"
}
```

---

### 2.8 List Signal Definitions

```
GET /v1/signals
```

Returns all available Bayesian signal definitions and domain priors.

**Response** (200):
```json
{
    "signals": {
        "reference_not_found": {
            "lr_positive": 15.0,
            "lr_negative": 0.95,
            "category": "L0",
            "description": "Reference not found in any registry"
        },
        "phantom_doi": {
            "lr_positive": 25.0,
            "lr_negative": 0.99,
            "category": "L0",
            "description": "DOI has valid format but cannot be resolved"
        }
    },
    "domains": {
        "cancer_research": 0.10,
        "biomedical": 0.05,
        "default": 0.03
    }
}
```

---

## 3. Data Models

### 3.1 ReferenceInput

```json
{
    "title": "string",
    "authors": ["string"],
    "year": "string",
    "doi": "string",
    "arxiv_id": "string",
    "venue": "string",
    "key": "string",
    "author_emails": ["string"]
}
```

All fields are optional. At minimum, provide `title` or an identifier (`doi`, `arxiv_id`).

### 3.2 Risk Tiers

| Tier | Posterior Range | Interpretation |
|------|----------------|----------------|
| LOW | < 0.05 | Reference appears legitimate |
| ELEVATED | 0.05 - 0.20 | Minor concerns; manual review suggested |
| HIGH | 0.20 - 0.50 | Significant integrity concerns |
| CRITICAL | > 0.50 | Likely fabricated or seriously compromised |

### 3.3 Layer Codes

| Code | Layer | Description |
|------|-------|-------------|
| L0 | Existence | Registry lookup + metadata verification |
| L1 | Intent | Citation intent classification |
| L2 | Semantic | NLI claim-evidence verification |
| L3 | Graph | Citation graph anomaly detection |
| L4 | Risk | Bayesian signal aggregation |

---

## 4. Error Codes

| Status | Meaning | Common Causes |
|--------|---------|---------------|
| 200 | Success | Normal response |
| 400 | Bad Request | Invalid signal name, malformed JSON |
| 401 | Unauthorized | Missing Authorization header when API key is required |
| 403 | Forbidden | Invalid API key |
| 422 | Validation Error | Missing required fields (Pydantic validation) |
| 500 | Internal Error | Graph analysis failure, unexpected exception |
| 503 | Service Unavailable | Service not initialized (startup failure) |

---

## 5. Rate Limits

The API itself does not enforce rate limits. However, individual registry adapters enforce their own rate limits (typically 0.1-1.0s between requests). Batch endpoints may take longer as they process references sequentially.

**Recommended client-side limits**:
- Single verify: max 10 req/sec
- Batch verify: max 2 req/sec (each may query 62 registries)
- Graph analysis: max 1 req/sec (OpenAlex API dependency)

---

## 6. SDK Usage Examples

### 6.1 Python (requests)

```python
import requests

BASE = "http://localhost:8000/v1"

# Single verify
resp = requests.post(f"{BASE}/verify", json={
    "reference": {
        "title": "Attention Is All You Need",
        "doi": "10.48550/arXiv.1706.03762",
    },
    "layers": ["L0", "L4"],
})
result = resp.json()
print(f"Risk: {result['risk_tier']}")
```

### 6.2 cURL

```bash
# Health check
curl http://localhost:8000/v1/health

# Verify with API key
curl -X POST http://localhost:8000/v1/verify \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "reference": {"title": "Attention Is All You Need", "year": "2017"},
    "layers": ["L0", "L4"]
  }'

# Batch verify
curl -X POST http://localhost:8000/v1/batch \
  -H "Content-Type: application/json" \
  -d '{
    "references": [
      {"title": "Paper One", "doi": "10.1234/one"},
      {"title": "Paper Two", "doi": "10.1234/two"}
    ]
  }'
```

### 6.3 JavaScript (fetch)

```javascript
const resp = await fetch("http://localhost:8000/v1/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
        reference: { title: "Attention Is All You Need", year: "2017" },
        layers: ["L0", "L4"],
    }),
});
const result = await resp.json();
console.log(`Risk: ${result.risk_tier}`);
```
