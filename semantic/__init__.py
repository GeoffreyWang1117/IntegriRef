"""Semantic verification — claim extraction, NLI alignment (L2), intent classification (L1)."""

from .claim_extractor import ClaimExtractor, CitationClaim
from .abstract_fetcher import AbstractFetcher, AbstractResult
from .nli_verifier import NLIVerifier, NLIResult, AlignmentLabel
from .semantic_pipeline import SemanticPipeline, SemanticReport
from .intent_classifier import IntentClassifier, IntentResult, CitationIntent
