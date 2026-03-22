"""Evaluation metrics for IntegriRef benchmarks.

Computes precision, recall, F1, accuracy, confusion matrices,
and domain-specific metrics for L0/L1/L2 verification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ClassificationMetrics:
    """Per-class and aggregate classification metrics."""
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        total = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.tn) / total if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
        }


@dataclass
class BenchmarkResult:
    """Full benchmark evaluation result."""
    name: str
    dataset: str
    n_samples: int = 0
    overall: ClassificationMetrics = field(default_factory=ClassificationMetrics)
    per_class: dict[str, ClassificationMetrics] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    latency_ms: float = 0.0          # Average latency per sample
    latency_p95_ms: float = 0.0      # 95th percentile latency
    errors: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"=== {self.name} ({self.dataset}) ===",
            f"Samples: {self.n_samples}",
            f"Overall: P={self.overall.precision:.3f} R={self.overall.recall:.3f} "
            f"F1={self.overall.f1:.3f} Acc={self.overall.accuracy:.3f}",
        ]
        if self.latency_ms > 0:
            lines.append(
                f"Latency: avg={self.latency_ms:.1f}ms p95={self.latency_p95_ms:.1f}ms")
        for cls, m in sorted(self.per_class.items()):
            lines.append(
                f"  {cls:15s}: P={m.precision:.3f} R={m.recall:.3f} F1={m.f1:.3f}"
                f" (TP={m.tp} FP={m.fp} FN={m.fn})")
        if self.errors:
            lines.append(f"Errors: {len(self.errors)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dataset": self.dataset,
            "n_samples": self.n_samples,
            "overall": self.overall.to_dict(),
            "per_class": {k: v.to_dict() for k, v in self.per_class.items()},
            "confusion_matrix": self.confusion,
            "latency_ms": round(self.latency_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "n_errors": len(self.errors),
            "extra": self.extra,
        }


# ── Metric computation helpers ───────────────────────────────────────────

def compute_binary_metrics(
    predictions: list[bool],
    labels: list[bool],
) -> ClassificationMetrics:
    """Compute binary classification metrics."""
    m = ClassificationMetrics()
    for pred, label in zip(predictions, labels):
        if label and pred:
            m.tp += 1
        elif not label and pred:
            m.fp += 1
        elif label and not pred:
            m.fn += 1
        else:
            m.tn += 1
    return m


def compute_multiclass_metrics(
    predictions: list[str],
    labels: list[str],
    classes: list[str] | None = None,
) -> tuple[ClassificationMetrics, dict[str, ClassificationMetrics], dict[str, dict[str, int]]]:
    """Compute multiclass classification metrics.

    Returns (overall_metrics, per_class_metrics, confusion_matrix).
    """
    if classes is None:
        classes = sorted(set(labels) | set(predictions))

    # Confusion matrix
    confusion: dict[str, dict[str, int]] = {
        c: {c2: 0 for c2 in classes} for c in classes}
    for pred, label in zip(predictions, labels):
        if label in confusion and pred in confusion[label]:
            confusion[label][pred] += 1

    # Per-class metrics (one-vs-rest)
    per_class = {}
    for cls in classes:
        m = ClassificationMetrics()
        for pred, label in zip(predictions, labels):
            is_pos = label == cls
            pred_pos = pred == cls
            if is_pos and pred_pos:
                m.tp += 1
            elif not is_pos and pred_pos:
                m.fp += 1
            elif is_pos and not pred_pos:
                m.fn += 1
            else:
                m.tn += 1
        per_class[cls] = m

    # Overall: macro-average
    overall = ClassificationMetrics()
    overall.tp = sum(m.tp for m in per_class.values())
    overall.fp = sum(m.fp for m in per_class.values())
    overall.fn = sum(m.fn for m in per_class.values())
    overall.tn = sum(m.tn for m in per_class.values())

    return overall, per_class, confusion


def compute_l0_metrics(
    predictions: list[dict],
    labels: list[bool],
) -> dict:
    """Compute L0-specific metrics.

    predictions: list of {"found": bool, "composite_score": float, ...}
    labels: list of expected_found booleans

    Returns dict with:
      - binary metrics (found vs not found)
      - false_positive_rate (spec target: ≤2%)
      - precision at various thresholds
    """
    found_preds = [p.get("found", False) for p in predictions]
    binary = compute_binary_metrics(found_preds, labels)

    # False positive rate
    n_neg = sum(1 for l in labels if not l)
    fp_rate = binary.fp / n_neg if n_neg > 0 else 0.0

    # Score distribution
    scores = [p.get("composite_score", 0.0) for p in predictions]

    # Precision at thresholds
    thresholds = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]
    precision_at = {}
    for thresh in thresholds:
        tp = sum(1 for s, l in zip(scores, labels) if s >= thresh and l)
        fp = sum(1 for s, l in zip(scores, labels) if s >= thresh and not l)
        precision_at[f"p@{thresh}"] = tp / (tp + fp) if (tp + fp) > 0 else 1.0

    return {
        "binary": binary.to_dict(),
        "false_positive_rate": round(fp_rate, 4),
        "precision_at_thresholds": precision_at,
        "score_stats": {
            "mean": round(sum(scores) / len(scores), 4) if scores else 0,
            "min": round(min(scores), 4) if scores else 0,
            "max": round(max(scores), 4) if scores else 0,
        },
    }
