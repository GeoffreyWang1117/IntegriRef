"""Throughput benchmark for IntegriRef verification pipeline.

Measures references/second for each layer at various batch sizes
and concurrency levels.

Usage:
    python -m benchmarks.bench_throughput [--max-samples 500] [--output results.json]

Layers tested:
  - L1 Heuristic: Rule-based intent classification (CPU-only)
  - L1 SciBERT: Fine-tuned transformer intent classification
  - L2 General NLI: cross-encoder/nli-deberta-v3-base
  - L2 Fine-tuned NLI: SciFact-tuned DeBERTa
  - L0 Field Comparator: String similarity matching
  - End-to-end: Combined L0 + L1 + L2 pipeline
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import torch


@dataclass
class ThroughputResult:
    """Result of a throughput benchmark."""
    name: str
    n_samples: int
    total_time_s: float
    throughput_rps: float  # references per second
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    batch_size: int = 1
    concurrency: int = 1
    device: str = "cpu"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "n_samples": self.n_samples,
            "total_time_s": round(self.total_time_s, 3),
            "throughput_rps": round(self.throughput_rps, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "min_latency_ms": round(self.min_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "batch_size": self.batch_size,
            "concurrency": self.concurrency,
            "device": self.device,
            "extra": self.extra,
        }

    def summary_line(self) -> str:
        return (f"{self.name:40s} | {self.throughput_rps:8.1f} ref/s | "
                f"avg={self.avg_latency_ms:7.2f}ms | "
                f"p50={self.p50_latency_ms:7.2f}ms | "
                f"p95={self.p95_latency_ms:7.2f}ms | "
                f"p99={self.p99_latency_ms:7.2f}ms")


def compute_stats(latencies: list[float]) -> dict:
    """Compute latency statistics from a list of latencies in ms."""
    if not latencies:
        return {}
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    return {
        "avg": statistics.mean(latencies),
        "p50": latencies_sorted[int(n * 0.50)],
        "p95": latencies_sorted[min(int(n * 0.95), n - 1)],
        "p99": latencies_sorted[min(int(n * 0.99), n - 1)],
        "min": latencies_sorted[0],
        "max": latencies_sorted[-1],
    }


def make_result(name: str, latencies: list[float], **kwargs) -> ThroughputResult:
    """Create a ThroughputResult from raw latencies."""
    stats = compute_stats(latencies)
    total_s = sum(latencies) / 1000.0
    return ThroughputResult(
        name=name,
        n_samples=len(latencies),
        total_time_s=total_s,
        throughput_rps=len(latencies) / total_s if total_s > 0 else 0,
        avg_latency_ms=stats.get("avg", 0),
        p50_latency_ms=stats.get("p50", 0),
        p95_latency_ms=stats.get("p95", 0),
        p99_latency_ms=stats.get("p99", 0),
        min_latency_ms=stats.get("min", 0),
        max_latency_ms=stats.get("max", 0),
        **kwargs,
    )


# ── L1 Throughput Benchmarks ────────────────────────────────────────────

def bench_l1_heuristic(sentences: list[str], n: int) -> ThroughputResult:
    """Benchmark L1 heuristic classifier throughput."""
    from semantic.intent_classifier import IntentClassifier
    classifier = IntentClassifier(use_model=False)

    latencies = []
    for i in range(n):
        sent = sentences[i % len(sentences)]
        t0 = time.perf_counter()
        classifier.classify(sent, str(i))
        latencies.append((time.perf_counter() - t0) * 1000)

    return make_result("L1 Heuristic (CPU)", latencies, device="cpu")


def bench_l1_scibert(sentences: list[str], n: int, device: str) -> ThroughputResult:
    """Benchmark L1 SciBERT fine-tuned classifier throughput."""
    from semantic.intent_classifier import IntentClassifier

    model_path = os.environ.get("INTENT_MODEL_PATH", "models/intent_classifier")
    if not Path(model_path).exists():
        print(f"  [SKIP] SciBERT model not found at {model_path}")
        return None

    classifier = IntentClassifier(use_model=True)
    if not classifier._model_loaded:
        print("  [SKIP] SciBERT model failed to load")
        return None

    # Warmup
    for i in range(min(10, len(sentences))):
        classifier.classify(sentences[i], str(i))

    latencies = []
    for i in range(n):
        sent = sentences[i % len(sentences)]
        t0 = time.perf_counter()
        classifier.classify(sent, str(i))
        latencies.append((time.perf_counter() - t0) * 1000)

    return make_result(f"L1 SciBERT Fine-tuned ({device})", latencies, device=device)


def bench_l1_scibert_batch(sentences: list[str], n: int, batch_size: int,
                           device: str) -> ThroughputResult:
    """Benchmark L1 SciBERT with batching."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_path = os.environ.get("INTENT_MODEL_PATH", "models/intent_classifier")
    if not Path(model_path).exists():
        return None

    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForSequenceClassification.from_pretrained(model_path)
    model.eval()
    if device != "cpu":
        model = model.to(device)

    # Warmup
    inputs = tokenizer(sentences[:2], return_tensors="pt", truncation=True,
                       max_length=256, padding=True)
    if device != "cpu":
        inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        model(**inputs)

    # Benchmark batched inference
    all_sents = [sentences[i % len(sentences)] for i in range(n)]
    latencies = []

    for start in range(0, n, batch_size):
        batch_sents = all_sents[start:start + batch_size]
        t0 = time.perf_counter()

        inputs = tokenizer(batch_sents, return_tensors="pt", truncation=True,
                           max_length=256, padding=True)
        if device != "cpu":
            inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            model(**inputs)

        elapsed = (time.perf_counter() - t0) * 1000
        per_sample = elapsed / len(batch_sents)
        latencies.extend([per_sample] * len(batch_sents))

    del model
    torch.cuda.empty_cache()

    return make_result(f"L1 SciBERT Batched (bs={batch_size}, {device})",
                       latencies, device=device, batch_size=batch_size)


# ── L2 Throughput Benchmarks ────────────────────────────────────────────

def bench_l2_nli(claims: list[str], abstracts: list[str], n: int,
                 model_name: str, label: str, device: str) -> ThroughputResult:
    """Benchmark L2 NLI throughput."""
    from semantic.nli_verifier import NLIVerifier

    verifier = NLIVerifier(model_name=model_name, device=device)
    loaded = verifier._ensure_loaded()
    if not loaded:
        print(f"  [SKIP] NLI model {model_name} failed to load")
        return None

    # Warmup
    for i in range(min(5, len(claims))):
        verifier.verify(claims[i], abstracts[i % len(abstracts)])

    latencies = []
    for i in range(n):
        claim = claims[i % len(claims)]
        abstract = abstracts[i % len(abstracts)]
        t0 = time.perf_counter()
        verifier.verify(claim, abstract)
        latencies.append((time.perf_counter() - t0) * 1000)

    del verifier
    torch.cuda.empty_cache()

    return make_result(f"L2 {label} ({device})", latencies, device=device)


def bench_l2_heuristic(claims: list[str], abstracts: list[str],
                       n: int) -> ThroughputResult:
    """Benchmark L2 heuristic NLI throughput."""
    from semantic.nli_verifier import NLIVerifier
    verifier = NLIVerifier()
    # Don't load model, force heuristic
    verifier._loaded = True

    latencies = []
    for i in range(n):
        claim = claims[i % len(claims)]
        abstract = abstracts[i % len(abstracts)]
        t0 = time.perf_counter()
        verifier.verify(claim, abstract)
        latencies.append((time.perf_counter() - t0) * 1000)

    return make_result("L2 Heuristic (CPU)", latencies, device="cpu")


# ── L0 Throughput Benchmarks ────────────────────────────────────────────

def bench_l0_field_comparator(n: int) -> ThroughputResult:
    """Benchmark L0 field comparison throughput (local, no API)."""
    from verification.field_comparator import FieldComparator

    comparator = FieldComparator()

    # Generate test pairs
    titles = [
        ("Attention Is All You Need", "Attention is All You Need"),
        ("BERT: Pre-training of Deep Bidirectional Transformers",
         "BERT: Pretraining of Deep Bidirectional Transformer"),
        ("A Survey of Large Language Models", "Survey of LLMs"),
        ("Generative Adversarial Networks", "Generative Adverserial Networks"),
    ]
    authors = [
        (["Vaswani, A.", "Shazeer, N."], ["A. Vaswani", "N. Shazeer"]),
        (["Devlin, J.", "Chang, M."], ["Jacob Devlin", "Ming-Wei Chang"]),
    ]

    latencies = []
    for i in range(n):
        t1, t2 = titles[i % len(titles)]
        a1, a2 = authors[i % len(authors)]
        t0 = time.perf_counter()
        comparator.match_title(t1, t2)
        comparator.match_authors(a1, a2)
        comparator.match_year("2023", "2023")
        comparator.match_venue("NeurIPS", "NeurIPS 2023")
        latencies.append((time.perf_counter() - t0) * 1000)

    return make_result("L0 FieldComparator (CPU)", latencies, device="cpu")


def bench_l0_hallucination(n: int) -> ThroughputResult:
    """Benchmark L0 hallucination detection throughput."""
    from verification.hallucination_detector import HallucinationDetector

    detector = HallucinationDetector()

    test_refs = [
        {"title": "A Fake Paper Title That Does Not Exist", "doi": "10.1234/fake.2025",
         "authors": ["A. Faker"], "year": "2025"},
        {"title": "Attention Is All You Need", "doi": "10.48550/arXiv.1706.03762",
         "authors": ["Vaswani, A."], "year": "2017"},
    ]

    latencies = []
    for i in range(n):
        ref = test_refs[i % len(test_refs)]
        t0 = time.perf_counter()
        detector.analyze(
            ref=ref,
            registry_results=[{"found": i % 2 == 1}],
        )
        latencies.append((time.perf_counter() - t0) * 1000)

    return make_result("L0 HallucinationDetector (CPU)", latencies, device="cpu")


# ── Concurrent throughput ────────────────────────────────────────────────

def bench_l1_concurrent(sentences: list[str], n: int,
                        concurrency: int) -> ThroughputResult:
    """Benchmark L1 heuristic with concurrent threads."""
    from semantic.intent_classifier import IntentClassifier
    classifier = IntentClassifier(use_model=False)

    latencies = []

    def classify_one(idx):
        sent = sentences[idx % len(sentences)]
        t0 = time.perf_counter()
        classifier.classify(sent, str(idx))
        return (time.perf_counter() - t0) * 1000

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(classify_one, i) for i in range(n)]
        for f in as_completed(futures):
            latencies.append(f.result())
    wall_time = (time.perf_counter() - t_start)

    result = make_result(f"L1 Heuristic Concurrent (threads={concurrency})",
                         latencies, concurrency=concurrency)
    # Override throughput with wall-clock based measurement
    result.throughput_rps = n / wall_time if wall_time > 0 else 0
    result.total_time_s = wall_time
    return result


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Throughput benchmark")
    parser.add_argument("--max-samples", type=int, default=500,
                        help="Number of samples per benchmark")
    parser.add_argument("--output", type=str,
                        default="benchmarks/results/throughput.json")
    args = parser.parse_args()

    n = args.max_samples
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Load test data
    print("Loading test data...")
    from benchmarks.datasets import load_scicite, load_scifact

    scicite = load_scicite()
    sentences = [s.citing_sentence for s in scicite[:500]]
    if not sentences:
        sentences = [
            "As demonstrated by Smith et al. [1], the method achieves state-of-the-art.",
            "Our findings are consistent with previous work [2].",
            "However, the approach in [3] fails to account for temporal dynamics.",
        ]

    scifact = load_scifact()
    scifact_samples = [s for s in scifact.get("samples_l2", []) if s.evidence]
    claims = [s.claim for s in scifact_samples[:200]]
    abstracts = [" ".join(s.evidence) for s in scifact_samples[:200]]
    if not claims:
        claims = ["Transformer models outperform RNNs on machine translation."]
        abstracts = ["The Transformer achieves 28.4 BLEU, surpassing all previous models."]

    results = []

    # ── L0 Benchmarks ──────────────────────────────────────────────────
    print("\n=== L0 Throughput ===")

    r = bench_l0_field_comparator(n)
    results.append(r)
    print(r.summary_line())

    r = bench_l0_hallucination(n)
    results.append(r)
    print(r.summary_line())

    # ── L1 Benchmarks ──────────────────────────────────────────────────
    print("\n=== L1 Throughput ===")

    r = bench_l1_heuristic(sentences, n)
    results.append(r)
    print(r.summary_line())

    # Concurrent heuristic
    for threads in [4, 8, 16]:
        r = bench_l1_concurrent(sentences, n, threads)
        results.append(r)
        print(r.summary_line())

    # SciBERT single
    r = bench_l1_scibert(sentences, n, device)
    if r:
        results.append(r)
        print(r.summary_line())

    # SciBERT batched
    for bs in [8, 16, 32, 64]:
        r = bench_l1_scibert_batch(sentences, n, bs, device)
        if r:
            results.append(r)
            print(r.summary_line())

    # ── L2 Benchmarks ──────────────────────────────────────────────────
    print("\n=== L2 Throughput ===")

    r = bench_l2_heuristic(claims, abstracts, n)
    results.append(r)
    print(r.summary_line())

    # General NLI
    r = bench_l2_nli(claims, abstracts, min(n, 200),
                     "cross-encoder/nli-deberta-v3-base",
                     "General NLI (base)", device)
    if r:
        results.append(r)
        print(r.summary_line())

    # Fine-tuned NLI
    ft_path = os.environ.get("NLI_MODEL_PATH", "models/scifact_nli")
    if Path(ft_path).exists():
        r = bench_l2_nli(claims, abstracts, min(n, 200),
                         ft_path, "SciFact Fine-tuned", device)
        if r:
            results.append(r)
            print(r.summary_line())

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'Benchmark':40s} | {'Throughput':>10s} | {'Avg Lat':>10s} | "
          f"{'P50':>10s} | {'P95':>10s} | {'P99':>10s}")
    print("-" * 100)
    for r in results:
        print(r.summary_line())
    print("=" * 100)

    # Save results
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "benchmarks": [r.to_dict() for r in results],
            "config": {
                "n_samples": n,
                "device": device,
                "torch_version": torch.__version__,
                "cuda_available": torch.cuda.is_available(),
                "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A",
            }
        }, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
