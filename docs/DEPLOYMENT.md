# Deployment Guide

**Project**: IntegriRef — Multi-Layer Reference Integrity Verification Engine
**Version**: 0.1.0
**Date**: 2026-03-16

---

## 1. System Requirements

### 1.1 Minimum Requirements

| Resource | Specification |
|----------|--------------|
| Python | 3.10 or later |
| RAM | 512 MB (without NLI model) |
| Disk | 200 MB (code + dependencies) |
| Network | Outbound HTTPS (ports 443) |
| OS | Linux, macOS, or Windows |

### 1.2 Recommended (with NLI model)

| Resource | Specification |
|----------|--------------|
| RAM | 4 GB |
| Disk | 3 GB (code + model weights) |
| CPU | 4+ cores (parallel registry queries) |
| GPU | Optional (NVIDIA for ONNX acceleration) |

---

## 2. Installation

### 2.1 From Source

```bash
# Clone repository
git clone https://github.com/<org>/IntegriRef.git
cd IntegriRef

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install core dependencies
pip install -r requirements.txt

# Verify installation
python -m pytest tests/ -x -q
```

### 2.2 Optional Dependencies

```bash
# NLI model for L2 semantic verification (adds ~2GB download)
pip install transformers torch sentencepiece

# ONNX acceleration (2-8x faster NLI inference)
pip install onnxruntime
# pip install onnxruntime-gpu  # for NVIDIA GPU

# Redis caching (for high-throughput deployments)
pip install redis[hiredis]
```

### 2.3 Verify Installation

```bash
# Run full test suite
python -m pytest -x -q --timeout=120
# Expected: 555 passed

# Quick smoke test
python -c "
from core.discovery import RegistryDiscovery
from registries.academic import ALL_ACADEMIC
d = RegistryDiscovery()
d.register_all(ALL_ACADEMIC)
print(f'Loaded {len(d._registries)} academic adapters')
"
```

---

## 3. Configuration

### 3.1 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INTEGRIREF_API_KEY` | _(empty)_ | API key for REST endpoint authentication. If empty, API is open access |
| `INTEGRIREF_DOMAIN` | `default` | Default Bayesian prior domain |
| `NLI_MODEL_PATH` | _(auto)_ | Path to custom NLI model checkpoint |
| `REDIS_URL` | _(none)_ | Redis connection URL for response caching (e.g., `redis://localhost:6379`) |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

### 3.2 Registry API Keys (Optional)

Some registries provide better rate limits or features with API keys:

| Registry | Environment Variable | How to Get |
|----------|---------------------|------------|
| Semantic Scholar | `S2_API_KEY` | https://api.semanticscholar.org/ |
| CORE | `CORE_API_KEY` | https://core.ac.uk/services/api |
| NASA ADS | `ADS_API_KEY` | https://ui.adsabs.harvard.edu/user/settings/token |
| USPTO | `USPTO_API_KEY` | https://developer.uspto.gov/ |
| FRED | `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html |

### 3.3 Example `.env` File

```bash
# API Authentication
INTEGRIREF_API_KEY=your-secret-api-key-here

# Domain prior
INTEGRIREF_DOMAIN=biomedical

# Optional registry keys
S2_API_KEY=your-semantic-scholar-key
CORE_API_KEY=your-core-key

# Caching
REDIS_URL=redis://localhost:6379/0

# Logging
LOG_LEVEL=INFO
```

---

## 4. Running the REST API

### 4.1 Development Server

```bash
# Start with auto-reload
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Access documentation
# Swagger UI: http://localhost:8000/docs
# ReDoc:      http://localhost:8000/redoc
```

### 4.2 Production Server

```bash
# Multi-worker production deployment
uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --loop uvloop \
    --no-access-log

# Or with gunicorn
pip install gunicorn
gunicorn api.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --timeout 120
```

### 4.3 Health Check

```bash
curl http://localhost:8000/v1/health
# {"status":"ok","version":"0.1.0","registries_loaded":62}
```

---

## 5. Docker Deployment

### 5.1 Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Run tests to verify build
RUN python -m pytest tests/test_pipeline.py tests/test_scoring.py -x -q

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/v1/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

### 5.2 docker-compose.yml

```yaml
version: "3.9"

services:
  integriref:
    build: .
    ports:
      - "8000:8000"
    environment:
      - INTEGRIREF_API_KEY=${INTEGRIREF_API_KEY:-}
      - INTEGRIREF_DOMAIN=default
      - REDIS_URL=redis://redis:6379/0
      - LOG_LEVEL=INFO
    depends_on:
      - redis
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1G

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    restart: unless-stopped

volumes:
  redis-data:
```

### 5.3 Build and Run

```bash
# Build image
docker build -t integriref:latest .

# Run standalone
docker run -d -p 8000:8000 \
    -e INTEGRIREF_API_KEY=your-key \
    integriref:latest

# Run with docker-compose
docker-compose up -d

# Check logs
docker-compose logs -f integriref
```

---

## 6. Python Library Usage

### 6.1 Basic Usage

```python
from core.discovery import RegistryDiscovery
from core.pipeline import IntegriRefPipeline
from registries.academic import ALL_ACADEMIC
from registries.patents import ALL_PATENTS
from registries.legal import ALL_LEGAL
from registries.government import ALL_GOVERNMENT
from registries.financial import ALL_FINANCIAL
from registries.standards import ALL_STANDARDS

# Initialize
discovery = RegistryDiscovery()
all_adapters = (ALL_ACADEMIC + ALL_PATENTS + ALL_LEGAL
                + ALL_GOVERNMENT + ALL_FINANCIAL + ALL_STANDARDS)
discovery.register_all(all_adapters)

# Create pipeline
pipeline = IntegriRefPipeline(
    discovery=discovery,
    layers=["L0", "L1", "L4"],  # Skip L2/L3 for speed
    domain="default",
)

# Verify a reference
report = pipeline.verify(
    ref={
        "title": "Attention Is All You Need",
        "authors": ["Vaswani", "Shazeer"],
        "year": "2017",
        "doi": "10.48550/arXiv.1706.03762",
    },
    citing_sentence="We use the transformer architecture (Vaswani et al., 2017).",
)

print(f"Risk: {report.risk_tier}")
print(f"Probability: {report.risk_probability:.3f}")
print(f"Signals: {len(report.signals)} observed")
```

### 6.2 Batch Processing

```python
references = [
    {"title": "Paper A", "doi": "10.1234/a"},
    {"title": "Paper B", "doi": "10.1234/b"},
    {"title": "Suspicious Paper", "year": "2099"},
]

reports = pipeline.verify_batch(
    references=references,
    citing_sentences=[
        "As shown by Paper A...",
        "Paper B demonstrated...",
        "According to Suspicious Paper...",
    ],
)

for ref, report in zip(references, reports):
    print(f"{ref['title'][:40]:40s} → {report.risk_tier}")
```

### 6.3 Standalone Bayesian Scoring

```python
from scoring.bayesian import BayesianRiskScorer

scorer = BayesianRiskScorer(domain="cancer_research")
scorer.observe("reference_not_found", fired=True, confidence=0.9)
scorer.observe("phantom_doi", fired=True, confidence=0.8)
scorer.observe("tortured_phrases", fired=False, confidence=1.0)

report = scorer.compute()
print(f"Prior: {report.prior_probability:.3f}")
print(f"Posterior: {report.posterior_probability:.3f}")
print(f"Risk: {report.risk_tier}")
```

---

## 7. Monitoring

### 7.1 Logging

Configure via `LOG_LEVEL` environment variable:

```bash
# Production
LOG_LEVEL=WARNING uvicorn api.main:app --host 0.0.0.0 --port 8000

# Debugging
LOG_LEVEL=DEBUG uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Key log sources:
- `api.main` — API request handling
- `core.registry` — Registry adapter failures, retries
- `core.discovery` — Routing decisions
- `verification.engine` — Verification phases
- `core.circuit_breaker` — Circuit breaker state changes

### 7.2 Health Monitoring

```bash
# Periodic health check
watch -n 30 'curl -s http://localhost:8000/v1/health | python -m json.tool'
```

---

## 8. Troubleshooting

### 8.1 Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError: core` | Wrong working directory | Run from project root |
| `TimeoutError: futures unfinished` | Registry API rate limiting | Reduce `max_workers`, add API keys |
| `ConnectionResetError` from DBLP | DBLP rate limit (aggressive) | Retry is built-in; increase timeout |
| `429 Too Many Requests` | API rate limit exceeded | Space requests; add API keys |
| `torch not found` for L2 | Optional NLI dependency | `pip install transformers torch` or use heuristic |
| High memory usage | NLI model loaded | Use ONNX runtime or heuristic fallback |
| Redis connection refused | Redis not running | Start Redis or remove `REDIS_URL` |

### 8.2 Performance Tuning

| Parameter | Default | Tuning Advice |
|-----------|---------|---------------|
| `max_workers` (parallel queries) | 8 | Reduce to 4 if hitting rate limits |
| `layers` | All | Use `["L0", "L4"]` for speed |
| `enable_l2_only_on_escalation` | True | Set False for comprehensive NLI |
| Workers (uvicorn) | 1 | Use 2-4 for production |
| Redis cache | Disabled | Enable for repeated queries |

---

## 9. Upgrading

### 9.1 Version Upgrade Steps

```bash
# Pull latest code
git pull origin main

# Update dependencies
pip install -r requirements.txt --upgrade

# Run tests
python -m pytest -x -q --timeout=120

# Restart API
# (kill existing uvicorn, restart)
```

### 9.2 Breaking Change Checklist

When upgrading, check for:
- [ ] New required environment variables
- [ ] Changed API response formats
- [ ] New registry adapters requiring API keys
- [ ] Updated signal definitions or priors
- [ ] Python version requirement changes
