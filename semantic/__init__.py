"""Semantic verification — claim extraction and NLI-based alignment (L2)."""

from .claim_extractor import ClaimExtractor, CitationClaim
from .abstract_fetcher import AbstractFetcher, AbstractResult
from .nli_verifier import NLIVerifier, NLIResult, AlignmentLabel
from .semantic_pipeline import SemanticPipeline, SemanticReport
