"""Integrity scorer — compute multi-dimensional reference integrity scores.

Produces IntegriRef Integrity Score (0-100) across five dimensions:
  1. Metadata Accuracy
  2. Existence Verification
  3. Graph Health
  4. Semantic Alignment
  5. Integrity Risk
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profiles import ScoringProfile, SCORING_PROFILES


@dataclass
class DimensionScore:
    """Score for a single dimension."""
    name: str
    score: float
    weight: float
    weighted_score: float
    details: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class IntegrityReport:
    """Complete integrity assessment report."""
    overall_score: float = 0.0
    grade: str = "?"
    dimensions: list[DimensionScore] = field(default_factory=list)
    profile_used: str = ""
    total_references: int = 0
    references_verified: int = 0
    high_risk_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "overall_score": round(self.overall_score, 1),
            "grade": self.grade,
            "profile": self.profile_used,
            "total_references": self.total_references,
            "references_verified": self.references_verified,
            "high_risk_count": self.high_risk_count,
            "dimensions": {
                d.name: {
                    "score": round(d.score, 1),
                    "weight": d.weight,
                    "weighted": round(d.weighted_score, 1),
                }
                for d in self.dimensions
            },
            "warnings": self.warnings,
        }


class IntegrityScorer:
    """Compute reference integrity scores.

    Usage:
        scorer = IntegrityScorer(profile="academic")
        scorer.set_metadata_score(95)
        scorer.set_existence_score(98)
        scorer.set_graph_health_score(72)
        scorer.set_semantic_score(85)
        scorer.set_integrity_risk_score(92)
        report = scorer.compute()
    """

    def __init__(self, profile: str = "academic"):
        if profile not in SCORING_PROFILES:
            raise ValueError(f"Unknown profile: {profile}. "
                             f"Available: {list(SCORING_PROFILES.keys())}")
        self._profile = SCORING_PROFILES[profile]
        self._dimensions: dict[str, tuple[float, dict, list[str]]] = {}
        self._total_references = 0
        self._references_verified = 0
        self._high_risk_count = 0

    def set_metadata_score(self, score: float, details: dict = None,
                           warnings: list[str] = None):
        self._dimensions["metadata_accuracy"] = (
            self._clamp(score), details or {}, warnings or [])

    def set_existence_score(self, score: float, details: dict = None,
                            warnings: list[str] = None):
        self._dimensions["existence_verification"] = (
            self._clamp(score), details or {}, warnings or [])

    def set_graph_health_score(self, score: float, details: dict = None,
                                warnings: list[str] = None):
        self._dimensions["graph_health"] = (
            self._clamp(score), details or {}, warnings or [])

    def set_semantic_score(self, score: float, details: dict = None,
                           warnings: list[str] = None):
        self._dimensions["semantic_alignment"] = (
            self._clamp(score), details or {}, warnings or [])

    def set_integrity_risk_score(self, score: float, details: dict = None,
                                  warnings: list[str] = None):
        self._dimensions["integrity_risk"] = (
            self._clamp(score), details or {}, warnings or [])

    def set_reference_counts(self, total: int, verified: int,
                             high_risk: int = 0):
        self._total_references = total
        self._references_verified = verified
        self._high_risk_count = high_risk

    def compute(self) -> IntegrityReport:
        report = IntegrityReport(
            profile_used=self._profile.name,
            total_references=self._total_references,
            references_verified=self._references_verified,
            high_risk_count=self._high_risk_count,
        )

        weight_map = {
            "metadata_accuracy": self._profile.metadata_accuracy,
            "existence_verification": self._profile.existence_verification,
            "graph_health": self._profile.graph_health,
            "semantic_alignment": self._profile.semantic_alignment,
            "integrity_risk": self._profile.integrity_risk,
        }

        total_weighted = 0.0
        total_weight = 0.0

        for dim_name, weight in weight_map.items():
            if dim_name in self._dimensions:
                score, details, warnings = self._dimensions[dim_name]
            else:
                score, details, warnings = (
                    100.0, {}, ["No data available for this dimension"])

            weighted = score * weight
            total_weighted += weighted
            total_weight += weight

            dim_score = DimensionScore(
                name=dim_name, score=score, weight=weight,
                weighted_score=weighted, details=details, warnings=warnings,
            )
            report.dimensions.append(dim_score)
            report.warnings.extend(warnings)

        report.overall_score = (total_weighted / total_weight
                                if total_weight > 0 else 0)
        report.grade = self._score_to_grade(report.overall_score)

        if self._high_risk_count > 0:
            report.warnings.insert(
                0, f"{self._high_risk_count} high-risk citation(s) detected")

        return report

    @staticmethod
    def compute_metadata_score(verification_results: list[dict]) -> tuple[float, dict]:
        """Compute metadata accuracy from verification results."""
        if not verification_results:
            return 100.0, {}

        total_checks = ok_count = warn_count = fail_count = 0
        for result in verification_results:
            for field_name in ("title_match", "author_match",
                               "year_match", "venue_match"):
                status = result.get(field_name, "OK")
                total_checks += 1
                if status == "OK":
                    ok_count += 1
                elif status == "WARN":
                    warn_count += 1
                else:
                    fail_count += 1

        if total_checks == 0:
            return 100.0, {}

        score = (ok_count * 100 + warn_count * 60) / total_checks
        return score, {"total_checks": total_checks, "ok": ok_count,
                       "warn": warn_count, "fail": fail_count}

    @staticmethod
    def compute_existence_score(total_refs: int, found_refs: int,
                                 retracted_count: int = 0) -> tuple[float, dict]:
        """Compute existence verification score."""
        if total_refs == 0:
            return 100.0, {}
        base_score = (found_refs / total_refs) * 100
        retraction_penalty = retracted_count * 5
        score = max(0.0, base_score - retraction_penalty)
        return score, {"total": total_refs, "found": found_refs,
                       "not_found": total_refs - found_refs,
                       "retracted": retracted_count}

    @staticmethod
    def _clamp(score: float) -> float:
        return max(0.0, min(100.0, score))

    @staticmethod
    def _score_to_grade(score: float) -> str:
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        return "F"
