"""L2 Semantic Claim Verification Benchmark.

Evaluates the SemanticPipeline (ClaimExtractor + NLIVerifier) against
SciFact ground truth.

SciFact task: Given a scientific claim, retrieve evidence sentences
from abstracts and classify as SUPPORTS / REFUTES / NOT_ENOUGH_INFO.

Usage:
    python -m benchmarks.bench_l2 [--data-dir DATA_DIR] [--max-samples N]
                                   [--output results.json]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from benchmarks.datasets import L2Sample, load_scifact
from benchmarks.metrics import (
    BenchmarkResult, compute_multiclass_metrics,
)


# SciFact label → unified label
_SCIFACT_LABEL_MAP = {
    "SUPPORT": "SUPPORTS",
    "SUPPORTS": "SUPPORTS",
    "CONTRADICT": "REFUTES",
    "REFUTES": "REFUTES",
    "NOT_ENOUGH_INFO": "NOT_ENOUGH_INFO",
    "NEI": "NOT_ENOUGH_INFO",
}

# Our AlignmentLabel.value → SciFact report label
_ALIGNMENT_TO_REPORT = {
    "supported": "SUPPORTS",
    "partially_supported": "SUPPORTS",
    "contradicted": "REFUTES",
    "unsupported": "NOT_ENOUGH_INFO",
    "unverifiable": "NOT_ENOUGH_INFO",
}


def run_l2_benchmark(
    samples: list[L2Sample],
    use_model: bool = True,
) -> BenchmarkResult:
    """Run L2 semantic verification benchmark.

    For each sample, runs NLI between claim and evidence sentences.

    Args:
        samples: L2Sample instances with claims and evidence.
        use_model: If True, try to use the DeBERTa NLI model.

    Returns BenchmarkResult.
    """
    from semantic.nli_verifier import NLIVerifier

    device = "cuda" if use_model else "cpu"
    try:
        import torch
        if not torch.cuda.is_available():
            device = "cpu"
    except ImportError:
        device = "cpu"

    verifier = NLIVerifier(device=device)

    # Trigger model load
    if use_model:
        loaded = verifier._ensure_loaded()
        if loaded:
            print(f"  Using transformer NLI model on {device}.")
        else:
            print("  Warning: Could not load NLI model.")
            print("  Falling back to heuristic NLI.")

    predictions = []
    labels = []
    latencies = []
    errors = []

    for i, sample in enumerate(samples):
        if not sample.evidence:
            # No evidence → NOT_ENOUGH_INFO
            predictions.append("NOT_ENOUGH_INFO")
            labels.append(_SCIFACT_LABEL_MAP.get(
                sample.expected_label, "NOT_ENOUGH_INFO"))
            latencies.append(0)
            continue

        t0 = time.time()
        try:
            # Run NLI on each evidence sentence
            evidence_text = " ".join(sample.evidence)
            result = verifier.verify(
                claim=sample.claim,
                abstract=evidence_text,
            )
            elapsed = (time.time() - t0) * 1000

            label_val = result.label.value if hasattr(result.label, 'value') else str(result.label)
            pred_report = _ALIGNMENT_TO_REPORT.get(label_val, "NOT_ENOUGH_INFO")

        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            pred_report = "NOT_ENOUGH_INFO"
            errors.append(f"[{sample.claim_id}] {type(e).__name__}: {e}")

        expected_report = _SCIFACT_LABEL_MAP.get(
            sample.expected_label, "NOT_ENOUGH_INFO")

        predictions.append(pred_report)
        labels.append(expected_report)
        latencies.append(elapsed)

        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(samples)}] "
                  f"avg_latency={sum(latencies)/len(latencies):.1f}ms")

    # Compute metrics
    classes = ["SUPPORTS", "REFUTES", "NOT_ENOUGH_INFO"]
    overall, per_class, confusion = compute_multiclass_metrics(
        predictions, labels, classes)

    latencies_sorted = sorted(latencies)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_idx = int(len(latencies_sorted) * 0.95)
    p95_latency = latencies_sorted[p95_idx] if latencies_sorted else 0

    result = BenchmarkResult(
        name="L2 Semantic Claim Verification",
        dataset="scifact",
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

    # Accuracy
    correct = sum(1 for p, l in zip(predictions, labels) if p == l)
    result.extra["accuracy"] = round(correct / len(samples), 4) if samples else 0

    return result


def main():
    parser = argparse.ArgumentParser(description="L2 Semantic Benchmark")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Path to SciFact data directory")
    parser.add_argument("--max-samples", type=int, default=0,
                        help="Max samples to evaluate (0 = all)")
    parser.add_argument("--no-model", action="store_true",
                        help="Skip loading NLI model, use heuristic only")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file for results JSON")
    args = parser.parse_args()

    print("Loading SciFact dataset...")
    data = load_scifact(args.data_dir)
    samples = data.get("samples_l2", [])

    if not samples:
        print("No SciFact data found. Using built-in test claims.")
        samples = _builtin_test_claims()

    # Filter to only samples with evidence (skip NEI for NLI eval)
    with_evidence = [s for s in samples if s.evidence]
    print(f"  Total claims: {len(samples)}, with evidence: {len(with_evidence)}")

    if args.max_samples and len(with_evidence) > args.max_samples:
        import random
        random.seed(42)
        with_evidence = random.sample(with_evidence, args.max_samples)

    print(f"Running L2 benchmark on {len(with_evidence)} samples...")
    result = run_l2_benchmark(with_evidence, use_model=not args.no_model)

    print()
    print(result.summary())

    print("\n--- Key Metrics ---")
    print(f"  Accuracy:  {result.extra.get('accuracy', 0):.3f}")
    print(f"  Macro F1:  {result.extra.get('macro_f1', 0):.3f}")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\nResults saved to {args.output}")


def _builtin_test_claims() -> list[L2Sample]:
    """Small built-in test set when SciFact is not available."""
    return [
        L2Sample(
            claim_id="builtin_1",
            claim="Transformer models outperform RNNs on machine translation.",
            evidence=["The Transformer achieves 28.4 BLEU on the WMT 2014 English-to-German translation task, "
                       "surpassing the best previously reported models including ensembles by over 2 BLEU."],
            expected_label="SUPPORT",
            source="builtin",
        ),
        L2Sample(
            claim_id="builtin_2",
            claim="BERT pre-training hurts performance on NER tasks.",
            evidence=["We show that pre-training BERT on a large corpus and fine-tuning on CoNLL-2003 "
                       "achieves state-of-the-art results, with F1 of 92.8."],
            expected_label="CONTRADICT",
            source="builtin",
        ),
        L2Sample(
            claim_id="builtin_3",
            claim="Graph neural networks can solve NP-hard problems optimally.",
            evidence=["We present a review of recent advances in graph neural networks and their applications "
                       "to combinatorial optimization problems."],
            expected_label="NOT_ENOUGH_INFO",
            source="builtin",
        ),
    ]


if __name__ == "__main__":
    main()
