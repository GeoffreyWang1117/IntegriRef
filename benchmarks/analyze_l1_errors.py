"""Analyze L1 heuristic classification errors to understand failure modes."""

from __future__ import annotations
import json
from collections import Counter
from benchmarks.datasets import load_scicite
from semantic.intent_classifier import IntentClassifier, CitationIntent


def main():
    samples = load_scicite()
    test_samples = [s for s in samples if "_test_" in s.sample_id]
    if not test_samples:
        test_samples = samples

    classifier = IntentClassifier(use_model=False)

    errors = {"supporting_as_mentioning": [], "mentioning_as_supporting": [],
              "supporting_as_contrasting": [], "mentioning_as_contrasting": []}

    confusion = Counter()
    total = 0
    correct = 0

    for s in test_samples:
        result = classifier.classify(s.citing_sentence, s.sample_id)
        intent = result.intent
        if intent in (CitationIntent.EXTENDING, CitationIntent.USING):
            pred = "supporting"
        else:
            pred = intent.value
        expected = s.expected_intent

        confusion[(expected, pred)] += 1
        total += 1
        if pred == expected:
            correct += 1
        else:
            key = f"{expected}_as_{pred}"
            if key in errors:
                errors[key].append({
                    "sentence": s.citing_sentence[:150],
                    "cue": result.cue_phrase,
                    "confidence": result.confidence,
                })

    print(f"Total: {total}, Correct: {correct}, Accuracy: {correct/total:.4f}")
    print(f"\nConfusion matrix:")
    for (exp, pred), count in sorted(confusion.items()):
        print(f"  {exp:12s} → {pred:12s}: {count:4d}")

    print(f"\n--- Error Analysis ---")
    for error_type, examples in errors.items():
        print(f"\n{error_type}: {len(examples)} errors")
        for ex in examples[:3]:
            print(f"  [{ex['cue'] or 'no cue'}] (conf={ex['confidence']:.2f})")
            print(f"    {ex['sentence']}")


if __name__ == "__main__":
    main()
