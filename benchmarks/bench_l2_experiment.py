"""L2 experiment: Compare NLI models and threshold tuning.

Tests:
  1. cross-encoder/nli-deberta-v3-base (current)
  2. cross-encoder/nli-deberta-v3-large (upgrade)
  3. Threshold tuning on dev set

Usage:
    python -m benchmarks.bench_l2_experiment
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import torch
from benchmarks.datasets import load_scifact

# ── SciFact label mapping ──────────────────────────────────────────────────
_SCIFACT_LABEL_MAP = {
    "SUPPORT": "SUPPORTS",
    "SUPPORTS": "SUPPORTS",
    "CONTRADICT": "REFUTES",
    "REFUTES": "REFUTES",
    "NOT_ENOUGH_INFO": "NOT_ENOUGH_INFO",
    "NEI": "NOT_ENOUGH_INFO",
}


def run_nli_experiment(model_name: str, samples, device: str = "cuda"):
    """Run NLI on SciFact samples and return raw scores + predictions."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    import re

    print(f"\n{'='*60}")
    print(f"Model: {model_name}")
    print(f"Device: {device}")
    print(f"{'='*60}")

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    model.eval()
    model.to(device)

    results = []
    latencies = []

    for i, sample in enumerate(samples):
        evidence_text = " ".join(sample.evidence)
        claim = sample.claim[:500]

        # Split evidence into sentences
        sentences = re.split(r'(?<=[.!?])\s+', evidence_text.strip())
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
        if not sentences:
            sentences = [evidence_text[:1500]]

        t0 = time.time()

        best_ent, best_con, best_neu = 0.0, 0.0, 0.0

        for sent in sentences[:10]:
            inputs = tokenizer(
                sent, claim,
                return_tensors="pt", truncation=True, max_length=512,
            )
            inputs = {k: v.to(device) for k, v in inputs.items()}

            with torch.no_grad():
                logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)[0].cpu().tolist()
            # cross-encoder label order: 0=contradiction, 1=entailment, 2=neutral
            con_s, ent_s, neu_s = probs[0], probs[1], probs[2]

            best_ent = max(best_ent, ent_s)
            best_con = max(best_con, con_s)
            best_neu = max(best_neu, neu_s)

        elapsed = (time.time() - t0) * 1000
        latencies.append(elapsed)

        expected = _SCIFACT_LABEL_MAP.get(sample.expected_label, "NOT_ENOUGH_INFO")

        results.append({
            "claim_id": sample.claim_id,
            "entailment": best_ent,
            "contradiction": best_con,
            "neutral": best_neu,
            "expected": expected,
        })

        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(samples)}] avg_latency={sum(latencies)/len(latencies):.1f}ms")

    # Clean up GPU memory
    del model
    torch.cuda.empty_cache()

    return results, latencies


def evaluate_with_thresholds(results, ent_thresh=0.5, con_thresh=0.35):
    """Evaluate with given thresholds, return metrics."""
    predictions = []
    labels = []

    for r in results:
        ent = r["entailment"]
        con = r["contradiction"]
        expected = r["expected"]

        # Classification logic
        if con > ent and con > con_thresh:
            pred = "REFUTES"
        elif ent > ent_thresh:
            pred = "SUPPORTS"
        elif ent > ent_thresh * 0.5:  # partial support
            pred = "SUPPORTS"
        else:
            pred = "NOT_ENOUGH_INFO"

        predictions.append(pred)
        labels.append(expected)

    # Compute metrics
    classes = ["SUPPORTS", "REFUTES", "NOT_ENOUGH_INFO"]
    correct = sum(1 for p, l in zip(predictions, labels) if p == l)
    acc = correct / len(labels) if labels else 0

    per_class = {}
    for cls in classes:
        tp = sum(1 for p, l in zip(predictions, labels) if p == cls and l == cls)
        fp = sum(1 for p, l in zip(predictions, labels) if p == cls and l != cls)
        fn = sum(1 for p, l in zip(predictions, labels) if p != cls and l == cls)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        per_class[cls] = {"P": round(prec, 3), "R": round(rec, 3), "F1": round(f1, 3),
                          "TP": tp, "FP": fp, "FN": fn}

    macro_f1 = sum(v["F1"] for v in per_class.values()) / len(per_class)

    return acc, macro_f1, per_class


def grid_search_thresholds(results):
    """Search for optimal thresholds."""
    print("\n--- Threshold Grid Search ---")
    best_acc = 0
    best_params = (0.5, 0.35)

    for ent_t in [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]:
        for con_t in [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]:
            acc, macro_f1, _ = evaluate_with_thresholds(results, ent_t, con_t)
            if acc > best_acc:
                best_acc = acc
                best_params = (ent_t, con_t)

    print(f"  Best: ent_thresh={best_params[0]}, con_thresh={best_params[1]}, acc={best_acc:.4f}")
    return best_params


def main():
    print("Loading SciFact dataset...")
    data = load_scifact()
    all_samples = data.get("samples_l2", [])
    samples = [s for s in all_samples if s.evidence]
    print(f"  Samples with evidence: {len(samples)}")

    # Split into dev/test by claim file origin
    dev_samples = [s for s in samples
                   if any(s.claim_id == str(c.get("id", ""))
                          for c in data["claims"][:300])]  # first 300 = train claims
    # Use all samples for now (SciFact is small)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # ── Experiment 1: Base model ──────────────────────────────────────────
    base_results, base_latencies = run_nli_experiment(
        "cross-encoder/nli-deberta-v3-base", samples, device)

    print("\n--- Base Model (default thresholds) ---")
    acc, macro_f1, per_class = evaluate_with_thresholds(base_results, 0.5, 0.35)
    print(f"  Accuracy: {acc:.4f}, Macro F1: {macro_f1:.4f}")
    for cls, m in per_class.items():
        print(f"    {cls:17s}: P={m['P']:.3f} R={m['R']:.3f} F1={m['F1']:.3f}")

    # Grid search on base model
    best_base_params = grid_search_thresholds(base_results)
    acc, macro_f1, per_class = evaluate_with_thresholds(
        base_results, *best_base_params)
    print(f"\n--- Base Model (optimized thresholds) ---")
    print(f"  Accuracy: {acc:.4f}, Macro F1: {macro_f1:.4f}")
    for cls, m in per_class.items():
        print(f"    {cls:17s}: P={m['P']:.3f} R={m['R']:.3f} F1={m['F1']:.3f}")

    # ── Experiment 2: Large model ─────────────────────────────────────────
    large_results, large_latencies = run_nli_experiment(
        "cross-encoder/nli-deberta-v3-large", samples, device)

    print("\n--- Large Model (default thresholds) ---")
    acc, macro_f1, per_class = evaluate_with_thresholds(large_results, 0.5, 0.35)
    print(f"  Accuracy: {acc:.4f}, Macro F1: {macro_f1:.4f}")
    for cls, m in per_class.items():
        print(f"    {cls:17s}: P={m['P']:.3f} R={m['R']:.3f} F1={m['F1']:.3f}")

    # Grid search on large model
    best_large_params = grid_search_thresholds(large_results)
    acc, macro_f1, per_class = evaluate_with_thresholds(
        large_results, *best_large_params)
    print(f"\n--- Large Model (optimized thresholds) ---")
    print(f"  Accuracy: {acc:.4f}, Macro F1: {macro_f1:.4f}")
    for cls, m in per_class.items():
        print(f"    {cls:17s}: P={m['P']:.3f} R={m['R']:.3f} F1={m['F1']:.3f}")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    avg_base_lat = sum(base_latencies) / len(base_latencies)
    avg_large_lat = sum(large_latencies) / len(large_latencies)

    print(f"  Base  model avg latency: {avg_base_lat:.1f}ms")
    print(f"  Large model avg latency: {avg_large_lat:.1f}ms")

    # Save full results
    out = {
        "base_results": base_results,
        "large_results": large_results,
        "base_best_thresholds": best_base_params,
        "large_best_thresholds": best_large_params,
        "base_avg_latency_ms": avg_base_lat,
        "large_avg_latency_ms": avg_large_lat,
    }
    out_path = Path("benchmarks/results/l2_experiment.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
