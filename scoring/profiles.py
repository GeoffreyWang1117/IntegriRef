"""Scoring profiles — domain-specific weight configurations.

Different domains weight verification dimensions differently:
  - Academic: semantic alignment matters most
  - Patents: existence verification is critical (prior art)
  - Legal: metadata accuracy is paramount (case citations must be exact)
  - Financial: integrity risk and compliance weight heavily
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoringProfile:
    """Weight configuration for integrity scoring. Weights must sum to 1.0."""
    name: str
    description: str
    metadata_accuracy: float = 0.15
    existence_verification: float = 0.20
    graph_health: float = 0.20
    semantic_alignment: float = 0.30
    integrity_risk: float = 0.15

    def validate(self) -> bool:
        total = (self.metadata_accuracy + self.existence_verification +
                 self.graph_health + self.semantic_alignment + self.integrity_risk)
        return abs(total - 1.0) < 0.01


SCORING_PROFILES: dict[str, ScoringProfile] = {
    "academic": ScoringProfile(
        name="academic",
        description="Academic paper — semantic alignment weighted highest",
        metadata_accuracy=0.15,
        existence_verification=0.20,
        graph_health=0.20,
        semantic_alignment=0.30,
        integrity_risk=0.15,
    ),
    "patent": ScoringProfile(
        name="patent",
        description="Patent examination — existence verification critical",
        metadata_accuracy=0.20,
        existence_verification=0.30,
        graph_health=0.10,
        semantic_alignment=0.25,
        integrity_risk=0.15,
    ),
    "legal": ScoringProfile(
        name="legal",
        description="Legal document — metadata accuracy paramount",
        metadata_accuracy=0.25,
        existence_verification=0.30,
        graph_health=0.05,
        semantic_alignment=0.25,
        integrity_risk=0.15,
    ),
    "financial": ScoringProfile(
        name="financial",
        description="SEC filing / financial report — compliance focus",
        metadata_accuracy=0.20,
        existence_verification=0.25,
        graph_health=0.05,
        semantic_alignment=0.30,
        integrity_risk=0.20,
    ),
    "standard": ScoringProfile(
        name="standard",
        description="Standards document (ISO/IEEE/NIST) — balanced",
        metadata_accuracy=0.20,
        existence_verification=0.25,
        graph_health=0.15,
        semantic_alignment=0.25,
        integrity_risk=0.15,
    ),
}
