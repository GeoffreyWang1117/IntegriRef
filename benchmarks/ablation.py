"""Layer ablation experiment for IntegriRef pipeline.

Measures the contribution of each layer (L0-L4) to overall detection
accuracy using synthetic test cases with known ground truth.

Usage:
    python -m benchmarks.ablation [--verbose]

Each test case has a known risk label (SAFE, SUSPICIOUS, FABRICATED).
We run the pipeline with different layer combinations and measure how
well each combination classifies the references.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.discovery import RegistryDiscovery
from core.pipeline import IntegriRefPipeline
from benchmarks.metrics import (
    ClassificationMetrics,
    compute_multiclass_metrics,
)


# ---------------------------------------------------------------------------
# Ground-truth test cases (no network required — use mocked L0 results)
# ---------------------------------------------------------------------------

@dataclass
class AblationCase:
    """A single test case with ground truth."""
    name: str
    ref: dict
    citing_sentence: str = ""
    abstract: str = ""
    full_text: str = ""
    expected_label: str = "SAFE"  # SAFE, SUSPICIOUS, FABRICATED


# These cases exercise different layers:
ABLATION_CASES = [
    # ── SAFE references ──
    AblationCase(
        name="real_paper_with_doi",
        ref={"title": "Attention Is All You Need", "authors": ["Vaswani", "Shazeer"],
             "year": "2017", "doi": "10.48550/arXiv.1706.03762"},
        expected_label="SAFE",
    ),
    AblationCase(
        name="real_paper_supporting",
        ref={"title": "Deep Residual Learning for Image Recognition",
             "authors": ["He", "Zhang"], "year": "2016"},
        citing_sentence="Following the ResNet architecture proposed by He et al. (2016), we apply skip connections.",
        expected_label="SAFE",
    ),
    AblationCase(
        name="real_paper_with_abstract",
        ref={"title": "BERT: Pre-training of Deep Bidirectional Transformers",
             "authors": ["Devlin", "Chang"], "year": "2019"},
        citing_sentence="BERT (Devlin et al., 2019) achieves state-of-the-art performance.",
        abstract="We introduce BERT, a new language representation model.",
        expected_label="SAFE",
    ),

    # ── SUSPICIOUS references (misrepresentation, retraction, anomalies) ──
    AblationCase(
        name="contrasting_citation_as_support",
        ref={"title": "Attention Is All You Need", "authors": ["Vaswani"],
             "year": "2017"},
        citing_sentence="Contrary to the claims in Vaswani et al. (2017), RNNs remain superior for sequence modeling.",
        expected_label="SUSPICIOUS",
    ),
    AblationCase(
        name="retracted_paper",
        ref={"title": "Ileal-lymphoid-nodular hyperplasia",
             "authors": ["Wakefield"], "year": "1998",
             "doi": "10.1016/S0140-6736(97)11096-0"},
        expected_label="SUSPICIOUS",
    ),

    # ── FABRICATED references ──
    AblationCase(
        name="hallucinated_future_paper",
        ref={"title": "Quantum Neural Networks for Consciousness Detection via Dark Matter",
             "authors": ["Fakeman, J.", "NotReal, K."],
             "year": "2030"},
        expected_label="FABRICATED",
    ),
    AblationCase(
        name="hallucinated_plausible",
        ref={"title": "A Novel Framework for Multimodal Reasoning in LLMs",
             "authors": ["Zhang, W.", "Liu, H."],
             "year": "2024"},
        expected_label="FABRICATED",
    ),
    AblationCase(
        name="fabricated_with_contradiction",
        ref={"title": "Proving P=NP via Gradient Descent",
             "authors": ["Imaginary, A."], "year": "2025"},
        citing_sentence="As proven by Imaginary (2025), P equals NP.",
        abstract="We conclusively prove that P does not equal NP.",
        expected_label="FABRICATED",
    ),
]


# ---------------------------------------------------------------------------
# Risk tier → label mapping
# ---------------------------------------------------------------------------

def risk_tier_to_label(tier: str) -> str:
    """Map pipeline risk tier to ablation label."""
    t = tier.upper()
    if t in ("LOW",):
        return "SAFE"
    elif t in ("ELEVATED",):
        return "SUSPICIOUS"
    elif t in ("HIGH", "CRITICAL"):
        return "FABRICATED"
    return "SAFE"


# ---------------------------------------------------------------------------
# Ablation runner
# ---------------------------------------------------------------------------

@dataclass
class AblationResult:
    """Result of a single ablation configuration."""
    config_name: str
    layers: list[str]
    predictions: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    case_details: list[dict] = field(default_factory=list)
    elapsed_ms: float = 0.0


def run_ablation(
    cases: list[AblationCase],
    layer_configs: dict[str, list[str]],
    discovery: RegistryDiscovery | None = None,
    verbose: bool = False,
) -> dict[str, AblationResult]:
    """Run ablation experiment across multiple layer configurations.

    Args:
        cases: test cases with ground truth labels
        layer_configs: {config_name: [layers]} e.g. {"L0_only": ["L0"]}
        discovery: optional pre-built discovery; creates new if None
        verbose: print per-case details

    Returns:
        {config_name: AblationResult}
    """
    if discovery is None:
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

    results = {}

    for config_name, layers in layer_configs.items():
        if verbose:
            print(f"\n{'='*60}")
            print(f"Config: {config_name} (layers: {layers})")
            print(f"{'='*60}")

        pipeline = IntegriRefPipeline(
            discovery=discovery,
            layers=layers,
            domain="default",
        )

        result = AblationResult(
            config_name=config_name,
            layers=layers,
        )

        start = time.monotonic()

        for case in cases:
            report = pipeline.verify(
                ref=case.ref,
                citing_sentence=case.citing_sentence,
                full_text=case.full_text,
                abstract=case.abstract,
            )

            predicted = risk_tier_to_label(report.risk_tier)
            result.predictions.append(predicted)
            result.labels.append(case.expected_label)

            detail = {
                "name": case.name,
                "expected": case.expected_label,
                "predicted": predicted,
                "risk_tier": report.risk_tier,
                "correct": predicted == case.expected_label,
                "signals": len(report.signals),
            }
            result.case_details.append(detail)

            if verbose:
                mark = "OK" if detail["correct"] else "MISS"
                print(f"  [{mark}] {case.name}: expected={case.expected_label} "
                      f"got={predicted} (tier={report.risk_tier})")

        result.elapsed_ms = (time.monotonic() - start) * 1000
        results[config_name] = result

    return results


def format_ablation_report(results: dict[str, AblationResult]) -> str:
    """Format ablation results into a readable report."""
    lines = [
        "=" * 70,
        "IntegriRef Layer Ablation Report",
        "=" * 70,
        "",
    ]

    classes = ["SAFE", "SUSPICIOUS", "FABRICATED"]

    for name, result in results.items():
        overall, per_class, confusion = compute_multiclass_metrics(
            result.predictions, result.labels, classes)

        lines.append(f"Config: {name}")
        lines.append(f"  Layers: {result.layers}")
        lines.append(f"  Time: {result.elapsed_ms:.0f}ms")
        lines.append(f"  Overall: P={overall.precision:.3f} R={overall.recall:.3f} "
                      f"F1={overall.f1:.3f} Acc={overall.accuracy:.3f}")

        for cls in classes:
            if cls in per_class:
                m = per_class[cls]
                lines.append(f"    {cls:12s}: P={m.precision:.3f} R={m.recall:.3f} "
                              f"F1={m.f1:.3f} (TP={m.tp} FP={m.fp} FN={m.fn})")

        # Show misses
        misses = [d for d in result.case_details if not d["correct"]]
        if misses:
            lines.append(f"  Misclassified ({len(misses)}):")
            for m in misses:
                lines.append(f"    - {m['name']}: expected={m['expected']} "
                              f"got={m['predicted']} (tier={m['risk_tier']})")
        lines.append("")

    # Comparison table
    lines.append("-" * 70)
    lines.append(f"{'Config':<25s} {'Accuracy':>8s} {'F1':>6s} {'Time':>8s}")
    lines.append("-" * 70)
    for name, result in results.items():
        overall, _, _ = compute_multiclass_metrics(
            result.predictions, result.labels, classes)
        lines.append(f"{name:<25s} {overall.accuracy:>8.1%} "
                      f"{overall.f1:>6.3f} {result.elapsed_ms:>7.0f}ms")
    lines.append("-" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standard layer configurations for ablation
# ---------------------------------------------------------------------------

STANDARD_CONFIGS = {
    "L0_only":        ["L0"],
    "L0+L4":          ["L0", "L4"],
    "L0+L1+L4":       ["L0", "L1", "L4"],
    "L0+L1+L2+L4":    ["L0", "L1", "L2", "L4"],
    "full_L0-L4":     ["L0", "L1", "L2", "L3", "L4"],
}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="IntegriRef layer ablation experiment")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Save JSON results to file")
    args = parser.parse_args()

    results = run_ablation(
        cases=ABLATION_CASES,
        layer_configs=STANDARD_CONFIGS,
        verbose=args.verbose,
    )

    report = format_ablation_report(results)
    print(report)

    if args.output:
        out = {}
        for name, result in results.items():
            classes = ["SAFE", "SUSPICIOUS", "FABRICATED"]
            overall, per_class, confusion = compute_multiclass_metrics(
                result.predictions, result.labels, classes)
            out[name] = {
                "layers": result.layers,
                "overall": overall.to_dict(),
                "per_class": {k: v.to_dict() for k, v in per_class.items()},
                "confusion": confusion,
                "elapsed_ms": round(result.elapsed_ms, 1),
                "cases": result.case_details,
            }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
