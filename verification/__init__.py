"""Verification module — L0 existence verification and metadata validation.

Provides:
  - VerificationEngine: Multi-registry cascade verification
  - MetadataValidator: Cross-registry metadata consistency
  - HallucinationDetector: AI-generated citation detection
  - FieldComparator: Title/author/year/venue comparison utilities
  - TorturedPhraseDetector: Paper mill / paraphrasing tool detection
  - EmailRiskDetector: Paper mill email domain risk assessment
"""

from .field_comparator import FieldComparator
from .metadata_validator import MetadataValidator
from .hallucination_detector import HallucinationDetector
from .tortured_phrases import (
    TorturedPhraseDetector, TorturedPhraseMatch, TorturedPhraseReport,
)
from .engine import (
    VerificationEngine, VerificationReport, ReferenceResult,
    FieldCheck, CompositeScore, Provenance, SourceMatch,
)
from .retraction_checker import RetractionChecker, RetractionInfo
from .email_risk import EmailRiskDetector, EmailRiskResult
from .statistical import (
    StatisticalVerifier, StatisticalReport,
    GRIMTester, StatChecker,
    GRIMResult, StatCheckResult,
)
from .sneaked_references import (
    SneakedReferenceDetector, SneakedReferenceReport, SneakedReferenceMatch,
)

__all__ = [
    "VerificationEngine",
    "VerificationReport",
    "ReferenceResult",
    "FieldCheck",
    "CompositeScore",
    "Provenance",
    "SourceMatch",
    "FieldComparator",
    "MetadataValidator",
    "HallucinationDetector",
    "TorturedPhraseDetector",
    "TorturedPhraseMatch",
    "TorturedPhraseReport",
    "RetractionChecker",
    "RetractionInfo",
    "EmailRiskDetector",
    "EmailRiskResult",
    "StatisticalVerifier",
    "StatisticalReport",
    "GRIMTester",
    "StatChecker",
    "GRIMResult",
    "StatCheckResult",
    "SneakedReferenceDetector",
    "SneakedReferenceReport",
    "SneakedReferenceMatch",
]
