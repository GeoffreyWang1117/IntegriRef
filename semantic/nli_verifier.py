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
        import os
        # Allow override via environment variable or fine-tuned checkpoint
        checkpoint = os.environ.get("NLI_MODEL_PATH", "")
        if checkpoint and os.path.exists(checkpoint):
            self._model_name = checkpoint
        else:
            self._model_name = model_name
        self._device = device
        self._model = None
        self._tokenizer = None
        self._loaded = False

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return self._model is not None
        self._loaded = True
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self._model_name)
            self._model.eval()
            if self._device != "cpu":
                self._model = self._model.to(self._device)
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

        if self._ensure_loaded() and self._model:
            return self._verify_with_model(claim, abstract)
        return self._verify_heuristic(claim, abstract)

    def _verify_with_model(self, claim: str, abstract: str) -> NLIResult:
        """Use cross-encoder NLI model for sentence-pair classification.

        Tokenizes ALL sentence-claim pairs at once and runs a single batched
        forward pass, then aggregates using max-entailment / max-contradiction.

        cross-encoder/nli-deberta-v3-base label order:
          index 0 = contradiction, 1 = entailment, 2 = neutral
        """
        claim_t = claim[:500]

        # Split abstract into sentences for per-sentence scoring
        sentences = self._split_sentences(abstract)
        if not sentences:
            return self._verify_heuristic(claim, abstract)

        try:
            all_probs = self._batched_nli(sentences, [claim_t] * len(sentences))
        except Exception:
            return self._verify_heuristic(claim, abstract)

        best_ent, best_con = 0.0, 0.0
        for con_s, ent_s, neu_s in all_probs:
            best_ent = max(best_ent, ent_s)
            best_con = max(best_con, con_s)

        ent = best_ent
        con = best_con
        neu = max(0.0, 1.0 - ent - con)

        label, confidence, needs_upgrade = self._classify(ent, neu, con)
        return NLIResult(
            label=label, confidence=confidence,
            entailment_score=ent, neutral_score=neu,
            contradiction_score=con,
            claim=claim_t, premise=abstract[:1500],
            model_name=self._model_name,
            needs_llm_upgrade=needs_upgrade,
        )

    def _batched_nli(self, premises: list[str], hypotheses: list[str],
                     batch_size: int = 32) -> list[tuple[float, float, float]]:
        """Run batched NLI inference over premise-hypothesis pairs.

        Returns list of (contradiction, entailment, neutral) score tuples.
        Processes in chunks of batch_size for memory efficiency.
        """
        import torch

        all_probs: list[tuple[float, float, float]] = []
        for i in range(0, len(premises), batch_size):
            batch_premises = premises[i:i + batch_size]
            batch_hypotheses = hypotheses[i:i + batch_size]

            inputs = self._tokenizer(
                batch_premises, batch_hypotheses,
                return_tensors="pt", truncation=True, max_length=512,
                padding=True,
            )
            if self._device != "cpu":
                inputs = {k: v.to(self._device) for k, v in inputs.items()}

            with torch.no_grad():
                logits = self._model(**inputs).logits
            probs = torch.softmax(logits, dim=-1).cpu().tolist()

            for p in probs:
                all_probs.append((p[0], p[1], p[2]))

        return all_probs

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
        """Apply thresholds to produce alignment label.

        Uses argmax with confidence margin. Cross-encoder NLI models
        often produce well-calibrated probabilities, so argmax with
        a minimum confidence gap works better than fixed thresholds.
        """
        max_prob = max(ent, neu, con)
        needs_upgrade = max_prob < 0.5

        # Argmax-based decision with minimum margin
        if con > ent and con > neu:
            if con > 0.35:
                return AlignmentLabel.CONTRADICTED, con, con < 0.5
            return AlignmentLabel.UNSUPPORTED, 1.0 - ent, True
        elif ent > con and ent > neu:
            if ent > 0.5:
                return AlignmentLabel.SUPPORTED, ent, False
            elif ent > 0.25:
                return AlignmentLabel.PARTIALLY_SUPPORTED, ent, True
            return AlignmentLabel.UNSUPPORTED, 1.0 - ent, True
        else:
            # Neutral is dominant
            if ent > 0.25:
                return AlignmentLabel.PARTIALLY_SUPPORTED, ent, True
            return AlignmentLabel.UNSUPPORTED, 1.0 - ent, needs_upgrade

    @staticmethod
    def _split_sentences(text: str, max_sentences: int = 10) -> list[str]:
        """Split text into sentences for per-sentence NLI scoring.

        Uses a simple regex-based splitter. Limits to max_sentences
        to control latency.
        """
        import re
        # Split on sentence-ending punctuation followed by space or end
        parts = re.split(r'(?<=[.!?])\s+', text.strip())
        sentences = [s.strip() for s in parts if len(s.strip()) > 20]
        if not sentences:
            # If splitting produced nothing useful, use whole text
            return [text[:1500]] if text.strip() else []
        return sentences[:max_sentences]

    def verify_batch(self, pairs: list[tuple[str, str]],
                     batch_size: int = 32) -> list[NLIResult]:
        """Verify multiple (claim, abstract) pairs with batched inference.

        Collects all sentence-claim pairs across all inputs and runs them
        through the model in configurable batch sizes, then reassembles
        results per input pair.
        """
        if not self._ensure_loaded() or not self._model:
            # Fall back to sequential heuristic
            return [self.verify(claim, abstract) for claim, abstract in pairs]

        results: list[NLIResult] = []
        # Collect all sentence-claim pairs with bookkeeping
        all_premises: list[str] = []
        all_hypotheses: list[str] = []
        # (pair_index, num_sentences) — to reassemble later
        pair_info: list[tuple[int, int, str, str]] = []

        for idx, (claim, abstract) in enumerate(pairs):
            if not claim or not abstract:
                pair_info.append((idx, 0, claim, abstract))
                continue
            claim_t = claim[:500]
            sentences = self._split_sentences(abstract)
            if not sentences:
                pair_info.append((idx, 0, claim, abstract))
                continue
            pair_info.append((idx, len(sentences), claim_t, abstract))
            for sent in sentences:
                all_premises.append(sent)
                all_hypotheses.append(claim_t)

        # Run batched NLI for all sentence-claim pairs at once
        if all_premises:
            try:
                all_probs = self._batched_nli(all_premises, all_hypotheses,
                                              batch_size=batch_size)
            except Exception:
                # Fall back to sequential heuristic
                return [self.verify(claim, abstract)
                        for claim, abstract in pairs]
        else:
            all_probs = []

        # Reassemble results per input pair
        prob_offset = 0
        for idx, num_sents, claim_t, abstract in pair_info:
            if num_sents == 0:
                results.append(NLIResult(
                    label=AlignmentLabel.UNVERIFIABLE if not claim_t or not abstract
                    else self._verify_heuristic(claim_t, abstract).label,
                    confidence=0.0, entailment_score=0.0,
                    neutral_score=0.0, contradiction_score=0.0,
                    claim=claim_t or "", premise=abstract or "",
                    model_name=self._model_name,
                ))
                continue

            best_ent, best_con = 0.0, 0.0
            for i in range(num_sents):
                con_s, ent_s, neu_s = all_probs[prob_offset + i]
                best_ent = max(best_ent, ent_s)
                best_con = max(best_con, con_s)
            prob_offset += num_sents

            ent = best_ent
            con = best_con
            neu = max(0.0, 1.0 - ent - con)
            label, confidence, needs_upgrade = self._classify(ent, neu, con)
            results.append(NLIResult(
                label=label, confidence=confidence,
                entailment_score=ent, neutral_score=neu,
                contradiction_score=con,
                claim=claim_t, premise=abstract[:1500],
                model_name=self._model_name,
                needs_llm_upgrade=needs_upgrade,
            ))

        return results
