"""L1 Citation Intent Classification Benchmark.

Evaluates IntentClassifier against SciCite ground truth.

SciCite → our 3-class taxonomy mapping:
  background → mentioning
  method     → supporting (using a method functionally supports)
  result     → supporting (result comparison)

Usage:
    python -m benchmarks.bench_l1 [--data-dir DATA_DIR] [--split dev]
                                   [--max-samples N] [--output results.json]

Spec targets:
  - L1 intent precision ≥ 90%
  - 3-class: supporting / contrasting / mentioning
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from benchmarks.datasets import L1Sample, load_scicite
from benchmarks.metrics import (
    BenchmarkResult, compute_multiclass_metrics,
)


def run_l1_benchmark(
    samples: list[L1Sample],
    use_transformer: bool = False,
) -> BenchmarkResult:
    """Run L1 citation intent benchmark.

    Args:
        samples: L1Sample instances with expected_intent labels.
        use_transformer: If True, try to use transformer-based classifier.

    Returns BenchmarkResult with intent classification metrics.
    """
    from semantic.intent_classifier import IntentClassifier, CitationIntent

    classifier = IntentClassifier()

    # If transformer requested, try to load
    if use_transformer:
        try:
            classifier.load_model()
        except Exception as e:
            print(f"  Warning: Could not load transformer model: {e}")
            print("  Falling back to heuristic classifier.")

    predictions = []
    labels = []
    latencies = []
    errors = []

    for i, sample in enumerate(samples):
        t0 = time.time()
        try:
            result = classifier.classify(
                citing_sentence=sample.citing_sentence,
                citation_key=sample.sample_id,
            )
            elapsed = (time.time() - t0) * 1000

            # Map 5-class to 3-class (EXTENDING/USING → SUPPORTING)
            intent = result.intent
            if intent in (CitationIntent.EXTENDING, CitationIntent.USING):
                pred_label = "supporting"
            else:
                pred_label = intent.value

        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            pred_label = "mentioning"  # default fallback
            errors.append(f"[{sample.sample_id}] {type(e).__name__}: {e}")

        predictions.append(pred_label)
        labels.append(sample.expected_intent)
        latencies.append(elapsed)

        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{len(samples)}] "
                  f"avg_latency={sum(latencies)/len(latencies):.1f}ms")

    # Compute metrics
    classes = ["supporting", "contrasting", "mentioning"]
    overall, per_class, confusion = compute_multiclass_metrics(
        predictions, labels, classes)

    latencies_sorted = sorted(latencies)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_idx = int(len(latencies_sorted) * 0.95)
    p95_latency = latencies_sorted[p95_idx] if latencies_sorted else 0

    result = BenchmarkResult(
        name="L1 Citation Intent",
        dataset="scicite",
        n_samples=len(samples),
        overall=overall,
        per_class=per_class,
        confusion=confusion,
        latency_ms=avg_latency,
        latency_p95_ms=p95_latency,
        errors=errors,
    )

    # Macro F1
    macro_f1 = sum(m.f1 for m in per_class.values()) / len(per_class) if per_class else 0
    result.extra["macro_f1"] = round(macro_f1, 4)

    # Accuracy (simple)
    correct = sum(1 for p, l in zip(predictions, labels) if p == l)
    result.extra["accuracy"] = round(correct / len(samples), 4) if samples else 0

    return result


def main():
    parser = argparse.ArgumentParser(description="L1 Intent Benchmark")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Path to SciCite data directory")
    parser.add_argument("--split", type=str, default="dev",
                        choices=["train", "dev", "test"],
                        help="Which split to evaluate on")
    parser.add_argument("--max-samples", type=int, default=0,
                        help="Max samples to evaluate (0 = all)")
    parser.add_argument("--use-transformer", action="store_true",
                        help="Use transformer classifier if available")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file for results JSON")
    args = parser.parse_args()

    print("Loading SciCite dataset...")
    all_samples = load_scicite(args.data_dir)

    if not all_samples:
        print("No SciCite data found. Using built-in test sentences.")
        all_samples = _builtin_test_set()

    # Filter by split if we have split info in sample_id
    if args.split != "train":
        split_samples = [s for s in all_samples
                         if f"_{args.split}_" in s.sample_id]
        if split_samples:
            all_samples = split_samples

    if args.max_samples and len(all_samples) > args.max_samples:
        import random
        random.seed(42)
        all_samples = random.sample(all_samples, args.max_samples)

    print(f"Running L1 benchmark on {len(all_samples)} samples...")
    result = run_l1_benchmark(all_samples, args.use_transformer)

    print()
    print(result.summary())

    # Spec compliance
    print("\n--- Spec Compliance ---")
    acc = result.extra.get("accuracy", 0)
    macro_f1 = result.extra.get("macro_f1", 0)
    print(f"  Accuracy:  {acc:.3f}  (target ≥0.90)  "
          f"{'PASS' if acc >= 0.90 else 'FAIL'}")
    print(f"  Macro F1:  {macro_f1:.3f}")
    print(f"  Avg Latency: {result.latency_ms:.1f}ms")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\nResults saved to {args.output}")


def _builtin_test_set() -> list[L1Sample]:
    """Small built-in test set when SciCite is not available."""
    return [
        L1Sample("builtin_0",
                 "As demonstrated by Smith et al. [1], the method achieves state-of-the-art results.",
                 expected_intent="supporting"),
        L1Sample("builtin_1",
                 "Our findings are consistent with previous work [2].",
                 expected_intent="supporting"),
        L1Sample("builtin_2",
                 "However, the approach in [3] fails to account for temporal dynamics.",
                 expected_intent="contrasting"),
        L1Sample("builtin_3",
                 "Unlike [4], our method does not require labeled data.",
                 expected_intent="contrasting"),
        L1Sample("builtin_4",
                 "Natural language processing [5] has become a core area of AI research.",
                 expected_intent="mentioning"),
        L1Sample("builtin_5",
                 "Related work on graph neural networks includes [6] and [7].",
                 expected_intent="mentioning"),
        L1Sample("builtin_6",
                 "We use the pretrained model from [8] as our backbone.",
                 expected_intent="supporting"),
        L1Sample("builtin_7",
                 "The dataset was collected following the protocol described in [9].",
                 expected_intent="supporting"),
        L1Sample("builtin_8",
                 "In contrast to [10], we observe that larger models do not always improve performance.",
                 expected_intent="contrasting"),
        L1Sample("builtin_9",
                 "Several approaches have been proposed for this task [11, 12, 13].",
                 expected_intent="mentioning"),
    ]


if __name__ == "__main__":
    main()
