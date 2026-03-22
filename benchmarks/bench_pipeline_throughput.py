#!/usr/bin/env python3
"""Pipeline throughput benchmark — measures refs/sec for L0-L4 pipeline.

Tests:
  1. Single reference latency (L0-only, L0+L4, full pipeline)
  2. Batch throughput (sequential vs concurrent)
  3. Cache hit speedup (second pass of same references)
  4. Layer-by-layer latency breakdown

Usage:
    python -m benchmarks.bench_pipeline_throughput [--live] [--batch-sizes 2,4,8]
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Test fixtures — synthetic references (no network needed for pure pipeline)
# ---------------------------------------------------------------------------

KNOWN_REFS = [
    {"title": "Attention Is All You Need", "authors": ["Vaswani", "Shazeer"],
     "year": "2017", "doi": "10.48550/arXiv.1706.03762"},
    {"title": "Deep Residual Learning for Image Recognition",
     "authors": ["He", "Zhang"], "year": "2016"},
    {"title": "BERT: Pre-training of Deep Bidirectional Transformers",
     "authors": ["Devlin"], "year": "2019"},
    {"title": "ImageNet Large Scale Visual Recognition Challenge",
     "authors": ["Russakovsky"], "year": "2015"},
    {"title": "Dropout: A Simple Way to Prevent Neural Networks from Overfitting",
     "authors": ["Srivastava"], "year": "2014"},
]

FAKE_REFS = [
    {"title": "Quantum Neural Networks for Consciousness Detection",
     "authors": ["Fakeman, J."], "year": "2030"},
    {"title": "Proving P=NP via Gradient Descent",
     "authors": ["Imaginary, A."], "year": "2025"},
    {"title": "Telepathic Communication through Deep Learning",
     "authors": ["Nobody, X."], "year": "2031"},
    {"title": "Cold Fusion Energy from Transformer Attention",
     "authors": ["Fictional, B."], "year": "2029"},
    {"title": "Reversing Entropy with Convolutional Filters",
     "authors": ["Unreal, C."], "year": "2028"},
]


@dataclass
class BenchResult:
    """Single benchmark result."""
    name: str
    refs_count: int
    total_ms: float
    per_ref_ms: float
    throughput_rps: float  # refs per second
    latencies_ms: list[float] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def p50_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        return statistics.median(self.latencies_ms)

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    def __str__(self):
        return (f"  {self.name:<40s} "
                f"total={self.total_ms:>8.1f}ms "
                f"per_ref={self.per_ref_ms:>7.1f}ms "
                f"throughput={self.throughput_rps:>6.1f} refs/s "
                f"p50={self.p50_ms:>7.1f}ms "
                f"p95={self.p95_ms:>7.1f}ms")


def _setup_pipeline(layers=None):
    """Create pipeline with all adapters registered."""
    from core.discovery import RegistryDiscovery
    from core.pipeline import IntegriRefPipeline
    from registries.academic import ALL_ACADEMIC
    from registries.patents import ALL_PATENTS
    from registries.legal import ALL_LEGAL
    from registries.government import ALL_GOVERNMENT
    from registries.financial import ALL_FINANCIAL
    from registries.standards import ALL_STANDARDS

    discovery = RegistryDiscovery()
    all_adapters = (ALL_ACADEMIC + ALL_PATENTS + ALL_LEGAL
                    + ALL_GOVERNMENT + ALL_FINANCIAL + ALL_STANDARDS)
    discovery.register_all(all_adapters)

    pipeline = IntegriRefPipeline(
        discovery=discovery,
        layers=layers or ["L0", "L4"],
        domain="default",
    )
    return pipeline


# ---------------------------------------------------------------------------
# Benchmark functions
# ---------------------------------------------------------------------------

def bench_single_ref_latency(pipeline, refs, label="single"):
    """Measure per-reference latency for single verify() calls."""
    latencies = []
    for ref in refs:
        start = time.monotonic()
        pipeline.verify(ref)
        elapsed = (time.monotonic() - start) * 1000
        latencies.append(elapsed)

    total = sum(latencies)
    per_ref = total / len(refs) if refs else 0
    rps = len(refs) / (total / 1000) if total > 0 else 0

    return BenchResult(
        name=f"single_{label}",
        refs_count=len(refs),
        total_ms=total,
        per_ref_ms=per_ref,
        throughput_rps=rps,
        latencies_ms=latencies,
    )


def bench_batch_throughput(pipeline, refs, label="batch", max_workers=8):
    """Measure batch verify_batch() throughput."""
    start = time.monotonic()
    reports = pipeline.verify_batch(refs, max_workers=max_workers)
    total = (time.monotonic() - start) * 1000

    per_ref = total / len(refs) if refs else 0
    rps = len(refs) / (total / 1000) if total > 0 else 0

    latencies = [r.processing_time_ms for r in reports if r]

    return BenchResult(
        name=f"batch_{label}_w{max_workers}",
        refs_count=len(refs),
        total_ms=total,
        per_ref_ms=per_ref,
        throughput_rps=rps,
        latencies_ms=latencies,
        extra={"max_workers": max_workers},
    )


def bench_cache_hit(pipeline, refs, label="cache"):
    """Measure cache hit speedup (second pass of same references)."""
    # First pass — populate cache
    for ref in refs:
        pipeline.verify(ref)

    # Second pass — should hit cache
    latencies = []
    for ref in refs:
        start = time.monotonic()
        pipeline.verify(ref)
        elapsed = (time.monotonic() - start) * 1000
        latencies.append(elapsed)

    total = sum(latencies)
    per_ref = total / len(refs) if refs else 0
    rps = len(refs) / (total / 1000) if total > 0 else 0

    return BenchResult(
        name=f"cache_hit_{label}",
        refs_count=len(refs),
        total_ms=total,
        per_ref_ms=per_ref,
        throughput_rps=rps,
        latencies_ms=latencies,
    )


def bench_layer_breakdown(pipeline_factory, ref):
    """Measure latency contribution of each layer."""
    results = {}

    for layer_set, label in [
        (["L0"], "L0_only"),
        (["L0", "L4"], "L0+L4"),
        (["L0", "L1", "L4"], "L0+L1+L4"),
    ]:
        p = pipeline_factory(layers=layer_set)
        latencies = []
        for _ in range(3):
            start = time.monotonic()
            p.verify(ref, citing_sentence="As shown by the authors...")
            elapsed = (time.monotonic() - start) * 1000
            latencies.append(elapsed)

        avg = statistics.mean(latencies)
        results[label] = BenchResult(
            name=f"layer_{label}",
            refs_count=3,
            total_ms=sum(latencies),
            per_ref_ms=avg,
            throughput_rps=1000 / avg if avg > 0 else 0,
            latencies_ms=latencies,
        )

    return results


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

def run_benchmarks(live: bool = False, batch_sizes: list[int] = None):
    """Run all throughput benchmarks.

    Args:
        live: If True, use real network queries. If False, measure with
              local-only processing (faster but lower latency numbers).
        batch_sizes: Batch sizes to test (default: [2, 4, 8]).
    """
    if batch_sizes is None:
        batch_sizes = [2, 4, 8]

    print("=" * 72)
    print("IntegriRef Pipeline Throughput Benchmark")
    print("=" * 72)
    print(f"Mode: {'LIVE (real network)' if live else 'LOCAL (mocked adapters)'}")
    print()

    refs = KNOWN_REFS + FAKE_REFS if live else FAKE_REFS[:3]
    all_results = []

    # 1. Single reference latency
    print("--- Single Reference Latency ---")
    pipeline = _setup_pipeline(["L0", "L4"])

    result = bench_single_ref_latency(pipeline, refs[:3], "L0+L4")
    print(result)
    all_results.append(result)

    # 2. Cache hit
    print("\n--- Cache Hit Speedup ---")
    result_cold = bench_single_ref_latency(pipeline, refs[:3], "cold")
    result_warm = bench_cache_hit(pipeline, refs[:3], "warm")
    print(f"  Cold: {result_cold}")
    print(f"  Warm: {result_warm}")
    if result_cold.per_ref_ms > 0 and result_warm.per_ref_ms > 0:
        speedup = result_cold.per_ref_ms / result_warm.per_ref_ms
        print(f"  Cache speedup: {speedup:.1f}x")
    all_results.extend([result_cold, result_warm])

    # 3. Batch throughput at various sizes
    print("\n--- Batch Throughput ---")
    for size in batch_sizes:
        batch_refs = (refs * ((size // len(refs)) + 1))[:size]

        # Sequential (workers=1)
        r_seq = bench_batch_throughput(
            pipeline, batch_refs, f"seq_n{size}", max_workers=1)
        print(r_seq)
        all_results.append(r_seq)

        # Concurrent
        for workers in [4, 8]:
            if workers > size:
                continue
            r_par = bench_batch_throughput(
                pipeline, batch_refs, f"par_n{size}", max_workers=workers)
            print(r_par)
            all_results.append(r_par)

            if r_seq.total_ms > 0 and r_par.total_ms > 0:
                speedup = r_seq.total_ms / r_par.total_ms
                print(f"    ↳ Speedup vs sequential: {speedup:.1f}x")

    # 4. Layer breakdown
    print("\n--- Layer Breakdown ---")
    test_ref = refs[0]
    layer_results = bench_layer_breakdown(_setup_pipeline, test_ref)
    for label, result in layer_results.items():
        print(result)
        all_results.append(result)

    # Summary table
    print("\n" + "=" * 72)
    print(f"{'Benchmark':<42s} {'Refs':>5s} {'Total':>9s} "
          f"{'Per-ref':>9s} {'Throughput':>12s}")
    print("-" * 72)
    for r in all_results:
        print(f"{r.name:<42s} {r.refs_count:>5d} "
              f"{r.total_ms:>8.1f}ms "
              f"{r.per_ref_ms:>8.1f}ms "
              f"{r.throughput_rps:>9.1f} r/s")
    print("=" * 72)

    return all_results


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="IntegriRef pipeline throughput benchmark")
    parser.add_argument("--live", action="store_true",
                        help="Use real network queries (slower)")
    parser.add_argument("--batch-sizes", type=str, default="2,4,8",
                        help="Comma-separated batch sizes")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Save JSON results to file")
    args = parser.parse_args()

    sizes = [int(s) for s in args.batch_sizes.split(",")]
    results = run_benchmarks(live=args.live, batch_sizes=sizes)

    if args.output:
        out = []
        for r in results:
            out.append({
                "name": r.name,
                "refs_count": r.refs_count,
                "total_ms": round(r.total_ms, 2),
                "per_ref_ms": round(r.per_ref_ms, 2),
                "throughput_rps": round(r.throughput_rps, 2),
                "p50_ms": round(r.p50_ms, 2),
                "p95_ms": round(r.p95_ms, 2),
                "extra": r.extra,
            })
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
