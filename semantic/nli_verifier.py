"""NLI verifier — Natural Language Inference for claim-abstract alignment.

Uses DeBERTa-v3 NLI model to classify:
  - ENTAILMENT: abstract supports the claim
  - NEUTRAL: no clear support or contradiction
  - CONTRADICTION: abstract contradicts the claim

Backends: HuggingFace transformers (local) or heuristic fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class AlignmentLabel(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


@dataclass
class NLIResult:
    """Result of NLI-based verification."""
    label: AlignmentLabel
    confidence: float
    entailment_score: float
    neutral_score: float
    contradiction_score: float
    claim: str
    premise: str
    model_name: str = ""
    needs_llm_upgrade: bool = False


class NLIVerifier:
    """NLI-based claim-abstract alignment verifier.

    Thresholds (CiteGuard NeurIPS 2025):
      - contradiction > 0.5 → CONTRADICTED
      - entailment > 0.7    → SUPPORTED
      - entailment > 0.4    → PARTIALLY_SUPPORTED
      - max_prob < 0.6       → needs LLM upgrade
    """

    def __init__(self, model_name: str = "cross-encoder/nli-deberta-v3-base",
                 device: str = "cpu"):
        self._model_name = model_name
        self._device = device
        self._pipeline = None
        self._loaded = False

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return self._pipeline is not None
        self._loaded = True
        try:
            from transformers import pipeline as hf_pipeline
            self._pipeline = hf_pipeline(
                "zero-shot-classification",
                model=self._model_name,
                device=self._device if self._device != "cpu" else -1,
            )
            return True
        except (ImportError, Exception):
            return False

    def verify(self, claim: str, abstract: str) -> NLIResult:
        """Verify a claim against an abstract."""
        if not abstract or not claim:
            return NLIResult(
                label=AlignmentLabel.UNVERIFIABLE,
                confidence=0.0, entailment_score=0.0,
                neutral_score=0.0, contradiction_score=0.0,
                claim=claim, premise=abstract,
                model_name=self._model_name,
            )

        if self._ensure_loaded() and self._pipeline:
            return self._verify_with_model(claim, abstract)
        return self._verify_heuristic(claim, abstract)

    def _verify_with_model(self, claim: str, abstract: str) -> NLIResult:
        abstract_t = abstract[:1500]
        claim_t = claim[:500]
        try:
            result = self._pipeline(
                abstract_t,
                candidate_labels=["entailment", "neutral", "contradiction"],
                hypothesis_template="{}",
            )
            scores = dict(zip(result["labels"], result["scores"]))
            ent = scores.get("entailment", 0.0)
            neu = scores.get("neutral", 0.0)
            con = scores.get("contradiction", 0.0)
        except Exception:
            return self._verify_heuristic(claim, abstract)

        label, confidence, needs_upgrade = self._classify(ent, neu, con)
        return NLIResult(
            label=label, confidence=confidence,
            entailment_score=ent, neutral_score=neu,
            contradiction_score=con,
            claim=claim_t, premise=abstract_t,
            model_name=self._model_name,
            needs_llm_upgrade=needs_upgrade,
        )

    def _verify_heuristic(self, claim: str, abstract: str) -> NLIResult:
        """Token-overlap heuristic fallback."""
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "shall", "should", "may", "might", "can", "could",
            "must", "in", "on", "at", "to", "for", "of", "with", "by",
            "from", "as", "into", "that", "which", "this", "these",
            "those", "and", "or", "but", "not", "no", "nor", "if",
            "then", "than", "it", "its", "we", "our", "they", "their",
        }

        claim_tokens = set(claim.lower().split()) - stopwords
        abstract_tokens = set(abstract.lower().split()) - stopwords

        if not claim_tokens:
            return NLIResult(
                label=AlignmentLabel.UNVERIFIABLE, confidence=0.0,
                entailment_score=0.0, neutral_score=1.0,
                contradiction_score=0.0,
                claim=claim, premise=abstract, model_name="heuristic",
            )

        overlap = len(claim_tokens & abstract_tokens)
        coverage = overlap / len(claim_tokens)

        ent = min(coverage * 0.9, 0.95)
        con = 0.05
        neu = 1.0 - ent - con

        label, confidence, needs_upgrade = self._classify(ent, neu, con)
        if label in (AlignmentLabel.PARTIALLY_SUPPORTED, AlignmentLabel.UNSUPPORTED):
            needs_upgrade = True

        return NLIResult(
            label=label, confidence=confidence * 0.7,
            entailment_score=ent, neutral_score=neu,
            contradiction_score=con,
            claim=claim, premise=abstract,
            model_name="heuristic",
            needs_llm_upgrade=needs_upgrade,
        )

    @staticmethod
    def _classify(ent: float, neu: float,
                  con: float) -> tuple[AlignmentLabel, float, bool]:
        """Apply thresholds to produce alignment label."""
        max_prob = max(ent, neu, con)
        needs_upgrade = max_prob < 0.6

        if con > 0.5:
            return AlignmentLabel.CONTRADICTED, con, False
        elif ent > 0.7:
            return AlignmentLabel.SUPPORTED, ent, False
        elif ent > 0.4:
            return AlignmentLabel.PARTIALLY_SUPPORTED, ent, needs_upgrade
        else:
            return AlignmentLabel.UNSUPPORTED, 1.0 - ent, needs_upgrade

    def verify_batch(self, pairs: list[tuple[str, str]]) -> list[NLIResult]:
        """Verify multiple (claim, abstract) pairs."""
        return [self.verify(claim, abstract) for claim, abstract in pairs]
