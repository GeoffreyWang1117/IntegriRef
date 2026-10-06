"""Evaluation harnesses for deepcite.

Kept separate from deepcite/tests/: tests assert fixed behaviour, these measure
it against external corpora and report numbers that move when the selection
patterns or the ranker change. Every result records the selection-pattern hash
so a number is always attributable to the pattern set that produced it.
"""
