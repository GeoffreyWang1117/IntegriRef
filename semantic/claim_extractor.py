"""Claim extraction — extract what a paper claims about its citations.

Based on SCitance (2024 EMNLP SDP):
  - Citation sentences are natural claims
  - Extract citation sentence + ±1 sentence context
  - Clean citation markers to produce standalone claims
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CitationClaim:
    """A claim made about a cited reference."""
    claim_text: str
    raw_sentence: str
    context_before: str = ""
    context_after: str = ""
    citation_key: str = ""
    cited_identifier: str = ""
    position_in_doc: int = 0


class ClaimExtractor:
    """Extract citation claims from academic text."""

    PATTERNS = [
        # Numbered: [1], [1,2], [1-3]
        r'\[(\d+(?:\s*[,\-–]\s*\d+)*)\]',
        # Author-year: (Smith, 2020), (Smith et al., 2020)
        r'\(([A-Z][a-zA-Z]+(?:\s+(?:&|and)\s+[A-Z][a-zA-Z]+)?(?:\s+et\s+al\.?)?,?\s*\d{4}[a-z]?)\)',
        # Multiple: (Smith, 2020; Jones, 2021)
        r'\(([A-Z][a-zA-Z]+(?:\s+et\s+al\.?)?,?\s*\d{4}[a-z]?(?:\s*;\s*[A-Z][a-zA-Z]+(?:\s+et\s+al\.?)?,?\s*\d{4}[a-z]?)+)\)',
    ]

    def __init__(self):
        self._compiled = [re.compile(p) for p in self.PATTERNS]

    def extract_from_text(self, text: str) -> list[CitationClaim]:
        """Extract all citation claims from text."""
        if not text.strip():
            return []

        sentences = self._split_sentences(text)
        claims = []

        for i, sentence in enumerate(sentences):
            for pattern in self._compiled:
                for match in pattern.finditer(sentence):
                    citation_key = match.group(1)
                    context_before = sentences[i - 1] if i > 0 else ""
                    context_after = sentences[i + 1] if i < len(sentences) - 1 else ""
                    claim_text = self._clean_claim(sentence)

                    if len(claim_text.split()) < 5:
                        continue

                    claims.append(CitationClaim(
                        claim_text=claim_text,
                        raw_sentence=sentence,
                        context_before=context_before,
                        context_after=context_after,
                        citation_key=citation_key,
                        position_in_doc=text.find(sentence),
                    ))

        return claims

    def extract_claims_for_reference(self, text: str,
                                      ref_key: str) -> list[CitationClaim]:
        """Extract claims about a specific reference key."""
        all_claims = self.extract_from_text(text)
        return [c for c in all_claims if ref_key in c.citation_key]

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences, handling abbreviations."""
        t = re.sub(r'\b(et al|Fig|Eq|Sec|Ref|Vol|No|Dr|Prof|Inc|Ltd|Jr|Sr)\.',
                   r'\1<PERIOD>', text)
        t = re.sub(r'(\d+)\.(\d+)', r'\1<PERIOD>\2', t)
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z\[])', t)
        sentences = [s.replace('<PERIOD>', '.').strip() for s in sentences]
        return [s for s in sentences if s]

    def _clean_claim(self, sentence: str) -> str:
        """Remove citation markers to create a standalone claim."""
        cleaned = re.sub(r'\s*\[\d+(?:\s*[,\-–]\s*\d+)*\]', '', sentence)
        cleaned = re.sub(
            r'\s*\([A-Z][a-zA-Z]+(?:\s+(?:&|and)\s+[A-Z][a-zA-Z]+)?'
            r'(?:\s+et\s+al\.?)?,?\s*\d{4}[a-z]?'
            r'(?:\s*;\s*[A-Z][a-zA-Z]+(?:\s+et\s+al\.?)?,?\s*\d{4}[a-z]?)*\)',
            '', cleaned
        )
        cleaned = re.sub(r'^(As\s+(?:shown|demonstrated|discussed|reported|noted)\s+(?:in|by)\s*,?\s*)',
                         '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'^(According\s+to\s*,?\s*)', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = re.sub(r'^[,;:\s]+', '', cleaned)
        return cleaned
