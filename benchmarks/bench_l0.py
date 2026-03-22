"""L0 Existence Verification Benchmark.

Evaluates the VerificationEngine's ability to:
  1. Find real references (true positive rate)
  2. Reject fake/perturbed references (false positive rate ≤2%)
  3. Correctly match metadata (title, author, year, venue)
  4. Composite score calibration

Usage:
    python -m benchmarks.bench_l0 [--data-dir DATA_DIR] [--n-samples N]
                                   [--with-negatives] [--output results.json]

Spec targets:
  - L0 existence precision ≥ 99%
  - False positive rate ≤ 2%
  - Latency ≤ 300ms per reference
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from benchmarks.datasets import (
    L0Sample, load_openalex_sample, generate_synthetic_negatives,
)
from benchmarks.metrics import (
    BenchmarkResult, ClassificationMetrics,
    compute_binary_metrics, compute_l0_metrics,
)


def run_l0_benchmark(
    samples: list[L0Sample],
    max_registries: int = 3,
    timeout: float = 10.0,
) -> BenchmarkResult:
    """Run L0 benchmark on a list of samples.

    Args:
        samples: L0Sample instances (mix of real and fake references).
        max_registries: Max registries to try per reference.
        timeout: Per-reference timeout in seconds.

    Returns BenchmarkResult with existence verification metrics.
    """
    from core.discovery import RegistryDiscovery
    from verification.engine import VerificationEngine

    discovery = RegistryDiscovery()
    engine = VerificationEngine(discovery)

    predictions = []
    labels = []
    latencies = []
    errors = []

    for i, sample in enumerate(samples):
        ref = {
            "title": sample.title,
            "key": sample.ref_id,
        }
        if sample.authors:
            ref["authors"] = sample.authors
        if sample.year:
            ref["year"] = sample.year
        if sample.doi:
            ref["doi"] = sample.doi
        if sample.venue:
            ref["venue"] = sample.venue

        t0 = time.time()
        try:
            result = engine.verify_reference(ref)
            elapsed = (time.time() - t0) * 1000  # ms

            pred = {
                "found": result.is_confirmed,
                "composite_score": (result.composite.overall
                                    if result.composite else 0.0),
                "sources_hit": result.sources_hit,
                "overall": result.overall,
            }
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            pred = {"found": False, "composite_score": 0.0,
                    "sources_hit": [], "overall": "ERROR"}
            errors.append(f"[{sample.ref_id}] {type(e).__name__}: {e}")

        predictions.append(pred)
        labels.append(sample.expected_found)
        latencies.append(elapsed)

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(samples)}] "
                  f"avg_latency={sum(latencies)/len(latencies):.0f}ms")

    # Compute metrics
    binary = compute_binary_metrics(
        [p["found"] for p in predictions], labels)
    l0_metrics = compute_l0_metrics(predictions, labels)

    # Latency stats
    latencies_sorted = sorted(latencies)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_idx = int(len(latencies_sorted) * 0.95)
    p95_latency = latencies_sorted[p95_idx] if latencies_sorted else 0

    result = BenchmarkResult(
        name="L0 Existence Verification",
        dataset="mixed" if any(not s.expected_found for s in samples) else "positives_only",
        n_samples=len(samples),
        overall=binary,
        latency_ms=avg_latency,
        latency_p95_ms=p95_latency,
        errors=errors,
        extra=l0_metrics,
    )

    # Per-source breakdown
    source_groups: dict[str, list[int]] = {}
    for i, s in enumerate(samples):
        source_groups.setdefault(s.source, []).append(i)

    for source, indices in source_groups.items():
        src_preds = [predictions[i]["found"] for i in indices]
        src_labels = [labels[i] for i in indices]
        src_metrics = compute_binary_metrics(src_preds, src_labels)
        result.per_class[source] = src_metrics

    return result


def main():
    parser = argparse.ArgumentParser(description="L0 Existence Benchmark")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Path to OpenAlex sample data")
    parser.add_argument("--n-samples", type=int, default=100,
                        help="Number of positive samples to use")
    parser.add_argument("--with-negatives", action="store_true",
                        help="Generate synthetic negative samples")
    parser.add_argument("--neg-ratio", type=float, default=1.0,
                        help="Ratio of negatives to positives")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file for results JSON")
    args = parser.parse_args()

    print("Loading OpenAlex samples...")
    positives = load_openalex_sample(args.data_dir, n_samples=args.n_samples)

    if not positives:
        print("No OpenAlex data found. Creating minimal synthetic test set.")
        # Minimal known-good references for smoke testing
        positives = [
            L0Sample(ref_id="test_1",
                     title="Attention Is All You Need",
                     authors=["Vaswani, A."],
                     year="2017",
                     doi="10.48550/arXiv.1706.03762",
                     expected_found=True, source="manual"),
            L0Sample(ref_id="test_2",
                     title="BERT: Pre-training of Deep Bidirectional Transformers",
                     authors=["Devlin, J."],
                     year="2019",
                     doi="",
                     expected_found=True, source="manual"),
            L0Sample(ref_id="test_3",
                     title="Deep Residual Learning for Image Recognition",
                     authors=["He, K."],
                     year="2016",
                     doi="10.1109/CVPR.2016.90",
                     expected_found=True, source="manual"),
        ]

    samples = list(positives)

    if args.with_negatives:
        n_neg = int(len(positives) * args.neg_ratio)
        print(f"Generating {n_neg} synthetic negatives...")
        negatives = generate_synthetic_negatives(positives, n_neg)
        samples.extend(negatives)

    print(f"Running L0 benchmark on {len(samples)} samples...")
    result = run_l0_benchmark(samples)

    print()
    print(result.summary())

    # Check against spec targets
    print("\n--- Spec Compliance ---")
    p = result.overall.precision
    fpr = result.extra.get("false_positive_rate", 0)
    print(f"  Precision:          {p:.3f}  (target ≥0.99)  "
          f"{'PASS' if p >= 0.99 else 'FAIL'}")
    print(f"  False Positive Rate: {fpr:.4f}  (target ≤0.02)  "
          f"{'PASS' if fpr <= 0.02 else 'FAIL'}")
    print(f"  Avg Latency:        {result.latency_ms:.0f}ms  (target ≤300ms)  "
          f"{'PASS' if result.latency_ms <= 300 else 'FAIL'}")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
