"""Semantic verification pipeline — end-to-end L2 verification.

Pipeline:
  1. Extract citation claims from document
  2. Resolve cited references to get abstracts
  3. Run NLI verification on each (claim, abstract) pair
  4. Aggregate results into a verification report
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.discovery import RegistryDiscovery
from .claim_extractor import ClaimExtractor, CitationClaim
from .abstract_fetcher import AbstractFetcher, AbstractResult
from .nli_verifier import NLIVerifier, NLIResult, AlignmentLabel


@dataclass
class ReferenceVerification:
    """Verification result for a single citation."""
    citation_key: str
    claim: CitationClaim
    abstract_result: Optional[AbstractResult]
    nli_result: NLIResult
    risk_level: str = "unknown"


@dataclass
class SemanticReport:
    """Complete semantic verification report for a document."""
    total_citations: int = 0
    verified: int = 0
    supported: int = 0
    partially_supported: int = 0
    unsupported: int = 0
    contradicted: int = 0
    unverifiable: int = 0
    needs_llm_review: int = 0
    verifications: list[ReferenceVerification] = field(default_factory=list)
    alignment_score: float = 0.0

    @property
    def high_risk_citations(self) -> list[ReferenceVerification]:
        return [v for v in self.verifications
                if v.risk_level in ("high", "critical")]

    def summary(self) -> dict:
        return {
            "total_citations": self.total_citations,
            "verified": self.verified,
            "supported": self.supported,
            "partially_supported": self.partially_supported,
            "unsupported": self.unsupported,
            "contradicted": self.contradicted,
            "unverifiable": self.unverifiable,
            "needs_llm_review": self.needs_llm_review,
            "alignment_score": round(self.alignment_score, 1),
            "high_risk_count": len(self.high_risk_citations),
        }


class SemanticPipeline:
    """End-to-end semantic verification pipeline.

    Usage:
        discovery = RegistryDiscovery()
        discovery.register_all(ALL_ACADEMIC)
        pipeline = SemanticPipeline(discovery)
        report = pipeline.verify_document(text, references)
    """

    def __init__(self, discovery: RegistryDiscovery,
                 nli_model: str = "cross-encoder/nli-deberta-v3-base",
                 device: str = "cpu"):
        self._discovery = discovery
        self._claim_extractor = ClaimExtractor()
        self._abstract_fetcher = AbstractFetcher(discovery)
        self._nli_verifier = NLIVerifier(model_name=nli_model, device=device)

    def verify_document(self, text: str,
                        references: list[dict] = None) -> SemanticReport:
        """Run full semantic verification on a document."""
        report = SemanticReport()
        all_claims = self._claim_extractor.extract_from_text(text)
        report.total_citations = len(all_claims)

        if not all_claims:
            report.alignment_score = 100.0
            return report

        for claim in all_claims:
            ref = self._match_claim_to_reference(claim, references or [])

            abstract_result = None
            if ref:
                abstract_result = self._abstract_fetcher.fetch_abstract(
                    identifier=ref.get("identifier", ""),
                    title=ref.get("title", ""),
                    author=ref.get("author", ""),
                    year=ref.get("year", ""),
                )

            abstract_text = abstract_result.abstract if abstract_result else ""
            nli_result = self._nli_verifier.verify(claim.claim_text, abstract_text)
            risk = self._assess_risk(nli_result)

            verification = ReferenceVerification(
                citation_key=claim.citation_key,
                claim=claim,
                abstract_result=abstract_result,
                nli_result=nli_result,
                risk_level=risk,
            )
            report.verifications.append(verification)
            report.verified += 1

            if nli_result.label == AlignmentLabel.SUPPORTED:
                report.supported += 1
            elif nli_result.label == AlignmentLabel.PARTIALLY_SUPPORTED:
                report.partially_supported += 1
            elif nli_result.label == AlignmentLabel.UNSUPPORTED:
                report.unsupported += 1
            elif nli_result.label == AlignmentLabel.CONTRADICTED:
                report.contradicted += 1
            elif nli_result.label == AlignmentLabel.UNVERIFIABLE:
                report.unverifiable += 1

            if nli_result.needs_llm_upgrade:
                report.needs_llm_review += 1

        report.alignment_score = self._compute_alignment_score(report)
        return report

    def verify_single_claim(self, claim_text: str,
                            abstract: str) -> NLIResult:
        """Verify a single claim against an abstract."""
        return self._nli_verifier.verify(claim_text, abstract)

    def _match_claim_to_reference(self, claim: CitationClaim,
                                   references: list[dict]) -> Optional[dict]:
        """Match a citation claim to a reference entry."""
        if not references:
            return None

        key = claim.citation_key.strip()
        for ref in references:
            ref_key = str(ref.get("key", "")).strip()
            if ref_key == key:
                return ref
            if key.isdigit():
                idx = int(key)
                if ref_key == str(idx) or references.index(ref) == idx - 1:
                    return ref
            if "," in key:
                parts = key.split(",")
                if len(parts) >= 2:
                    author_part = parts[0].strip().lower()
                    year_part = parts[-1].strip()
                    ref_author = ref.get("author", "").lower()
                    ref_year = ref.get("year", "")
                    if author_part in ref_author and year_part == ref_year:
                        return ref
        return None

    @staticmethod
    def _assess_risk(nli_result: NLIResult) -> str:
        if nli_result.label == AlignmentLabel.CONTRADICTED:
            return "critical"
        elif nli_result.label == AlignmentLabel.UNSUPPORTED:
            return "high" if nli_result.confidence > 0.7 else "medium"
        elif nli_result.label == AlignmentLabel.PARTIALLY_SUPPORTED:
            return "medium"
        elif nli_result.label == AlignmentLabel.UNVERIFIABLE:
            return "unknown"
        return "low"

    @staticmethod
    def _compute_alignment_score(report: SemanticReport) -> float:
        """Compute overall alignment score (0-100)."""
        verifiable = report.verified - report.unverifiable
        if verifiable == 0:
            return 100.0
        score = (
            report.supported * 100 +
            report.partially_supported * 60 +
            report.unsupported * 20 +
            report.contradicted * 0
        ) / verifiable
        return min(max(score, 0.0), 100.0)
