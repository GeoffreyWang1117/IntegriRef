"""Bayesian risk scoring — likelihood-ratio model for paper integrity.

Upgrades the weighted-average IntegrityScorer with a principled Bayesian
approach.  Each integrity signal (phantom DOI, citation ring, tortured
phrases, …) carries a literature-derived likelihood ratio.  Starting from
a domain-specific prior P(problematic), the model multiplies in each
observed signal to produce a calibrated posterior probability.

Maths
-----
    prior_odds   = P / (1 - P)
    posterior_odds = prior_odds × LR_1 × LR_2 × … × LR_n
    posterior_prob = posterior_odds / (1 + posterior_odds)

When a signal's *confidence* < 1.0 the effective LR is interpolated
toward 1.0 (neutral) so that uncertain observations pull less.

References
----------
- Scancar 2025: ~9.87 % problematic-paper base rate in cancer research
- Likelihood ratios estimated from retraction/fabrication prevalence data
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Risk tiers
# ---------------------------------------------------------------------------

class RiskTier(str, Enum):
    LOW = "low"              # < 0.05
    ELEVATED = "elevated"    # 0.05 – 0.20
    HIGH = "high"            # 0.20 – 0.50
    CRITICAL = "critical"    # > 0.50


# ---------------------------------------------------------------------------
# Signal definitions
# ---------------------------------------------------------------------------

@dataclass
class SignalDef:
    """Definition of a single integrity signal."""
    lr_positive: float   # likelihood ratio when signal fires
    lr_negative: float   # likelihood ratio when signal does NOT fire
    category: str        # L0, L1, L2, L3, text, stats, image
    description: str = ""


SIGNAL_DEFINITIONS: dict[str, SignalDef] = {
    # -- L0 signals ----------------------------------------------------------
    "reference_not_found": SignalDef(
        lr_positive=15.0, lr_negative=0.95, category="L0",
        description="Reference could not be found in any registry",
    ),
    "phantom_doi": SignalDef(
        lr_positive=25.0, lr_negative=0.99, category="L0",
        description="DOI resolves but metadata does not match the claim",
    ),
    "metadata_mismatch": SignalDef(
        lr_positive=10.0, lr_negative=0.85, category="L0",
        description="Author/title/year disagrees with registry record",
    ),
    "no_id_match": SignalDef(
        lr_positive=6.0, lr_negative=0.92, category="L0",
        description="Found by search only — provided DOI/arXiv ID did not resolve",
    ),
    "chimera_detected": SignalDef(
        lr_positive=8.0, lr_negative=0.95, category="L0",
        description="ID resolves but author/year fields contradict registry record",
    ),
    "retracted_citation": SignalDef(
        lr_positive=8.0, lr_negative=0.99, category="L0",
        description="Cited paper has been retracted",
    ),

    # -- L1 signals ----------------------------------------------------------
    "citation_misrepresents_source": SignalDef(
        lr_positive=5.0, lr_negative=0.85, category="L1",
        description="Citing sentence misrepresents the cited source",
    ),
    "all_citations_mentioning": SignalDef(
        lr_positive=2.0, lr_negative=0.95, category="L1",
        description="Every citation is 'mentioning' — none substantively engage",
    ),

    # -- L2 signals ----------------------------------------------------------
    "claim_contradicted": SignalDef(
        lr_positive=6.0, lr_negative=0.90, category="L2",
        description="NLI finds cited source contradicts the claim",
    ),
    "claim_unsupported": SignalDef(
        lr_positive=3.0, lr_negative=0.85, category="L2",
        description="NLI finds cited source does not support the claim",
    ),

    # -- L3 signals ----------------------------------------------------------
    "citation_ring_detected": SignalDef(
        lr_positive=10.0, lr_negative=0.95, category="L3",
        description="Mutual-citation ring detected in graph analysis",
    ),
    "excessive_self_citation": SignalDef(
        lr_positive=4.0, lr_negative=0.90, category="L3",
        description="Unusually high self-citation ratio",
    ),
    "temporal_anomaly": SignalDef(
        lr_positive=8.0, lr_negative=0.98, category="L3",
        description="Citation predates the cited work's publication",
    ),
    "orphan_cluster": SignalDef(
        lr_positive=2.5, lr_negative=0.92, category="L3",
        description="Group of papers cite only each other",
    ),

    "benford_violation": SignalDef(
        lr_positive=6.0, lr_negative=0.95, category="L3",
        description="Citation count first-digit distribution violates Benford's law",
    ),
    "reciprocal_citation": SignalDef(
        lr_positive=5.0, lr_negative=0.92, category="L3",
        description="Bidirectional citation pattern between authors",
    ),
    "citation_burst": SignalDef(
        lr_positive=3.0, lr_negative=0.95, category="L3",
        description="Sudden spike of citations from concentrated sources",
    ),

    # -- Text signals --------------------------------------------------------
    "tortured_phrases": SignalDef(
        lr_positive=25.0, lr_negative=0.99, category="text",
        description="Tortured-phrase detector fired (paper-mill indicator)",
    ),
    "suspicious_email": SignalDef(
        lr_positive=4.0, lr_negative=0.95, category="text",
        description="Author email matches known paper-mill domains",
    ),

    # -- Statistical signals (future) ---------------------------------------
    "grim_test_failure": SignalDef(
        lr_positive=50.0, lr_negative=0.99, category="stats",
        description="GRIM test: reported mean impossible given N",
    ),
    "statcheck_error": SignalDef(
        lr_positive=8.0, lr_negative=0.95, category="stats",
        description="statcheck found inconsistent test statistic / p-value",
    ),
}


# ---------------------------------------------------------------------------
# Domain priors
# ---------------------------------------------------------------------------

DOMAIN_PRIORS: dict[str, float] = {
    "cancer_research": 0.10,   # Scancar 2025: 9.87 %
    "biomedical": 0.05,
    "computer_science": 0.02,
    "social_science": 0.03,
    "psychology": 0.04,
    "engineering": 0.02,
    "humanities": 0.01,
    "default": 0.03,
}


# ---------------------------------------------------------------------------
# Observation & report dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SignalObservation:
    """A single observed (or not-observed) signal."""
    signal_name: str
    fired: bool
    confidence: float = 1.0   # 0-1; scales LR toward 1.0 when uncertain
    details: str = ""


@dataclass
class BayesianRiskReport:
    """Full output of a Bayesian risk assessment."""
    prior_probability: float
    posterior_probability: float
    risk_tier: RiskTier
    domain: str
    signals_observed: list[SignalObservation]
    signals_fired: int
    total_signals: int
    log_odds_contributions: dict[str, float]   # per-signal log-odds shift
    equivalent_weighted_score: float            # 0-100 for compatibility
    warnings: list[str]

    def summary(self) -> dict:
        """Return a JSON-friendly summary dict."""
        return {
            "prior": round(self.prior_probability, 4),
            "posterior": round(self.posterior_probability, 4),
            "risk_tier": self.risk_tier.value,
            "domain": self.domain,
            "signals_fired": self.signals_fired,
            "total_signals": self.total_signals,
            "equivalent_score": round(self.equivalent_weighted_score, 1),
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Bayesian risk scorer
# ---------------------------------------------------------------------------

class BayesianRiskScorer:
    """Bayesian likelihood-ratio model for paper integrity risk.

    Usage::

        scorer = BayesianRiskScorer(domain="biomedical")
        scorer.observe("phantom_doi", fired=True)
        scorer.observe("metadata_mismatch", fired=False)
        scorer.observe("tortured_phrases", fired=True, confidence=0.8)
        report = scorer.compute()
        print(report.risk_tier, report.posterior_probability)
    """

    def __init__(
        self,
        domain: str = "default",
        signal_definitions: Optional[dict[str, SignalDef]] = None,
        domain_priors: Optional[dict[str, float]] = None,
    ):
        self._domain = domain
        self._signal_defs = signal_definitions or SIGNAL_DEFINITIONS
        self._domain_priors = domain_priors or DOMAIN_PRIORS
        self._observations: list[SignalObservation] = []

    # -- observation API -----------------------------------------------------

    def observe(
        self,
        signal_name: str,
        fired: bool,
        confidence: float = 1.0,
        details: str = "",
    ) -> None:
        """Record a single signal observation."""
        if signal_name not in self._signal_defs:
            raise ValueError(
                f"Unknown signal: {signal_name!r}. "
                f"Known signals: {sorted(self._signal_defs)}"
            )
        confidence = max(0.0, min(1.0, confidence))
        self._observations.append(
            SignalObservation(
                signal_name=signal_name,
                fired=fired,
                confidence=confidence,
                details=details,
            )
        )

    def observe_batch(self, observations: list[SignalObservation]) -> None:
        """Record multiple signal observations at once."""
        for obs in observations:
            self.observe(
                signal_name=obs.signal_name,
                fired=obs.fired,
                confidence=obs.confidence,
                details=obs.details,
            )

    # -- compute -------------------------------------------------------------

    def compute(self) -> BayesianRiskReport:
        """Run Bayesian update and return a full risk report."""
        prior = self._domain_priors.get(
            self._domain, self._domain_priors["default"]
        )
        warnings: list[str] = []

        if not self._observations:
            warnings.append("No signals observed — returning prior as posterior")

        # Prior odds
        prior_odds = prior / (1.0 - prior)
        log_prior_odds = math.log(prior_odds)

        # Accumulate log-LR contributions
        log_odds = log_prior_odds
        contributions: dict[str, float] = {}
        signals_fired = 0

        for obs in self._observations:
            sig = self._signal_defs[obs.signal_name]
            raw_lr = sig.lr_positive if obs.fired else sig.lr_negative

            # Confidence interpolation: effective_lr = raw_lr^confidence
            # When confidence=1 → full LR; when confidence=0 → LR=1 (neutral)
            if obs.confidence < 1.0:
                effective_lr = raw_lr ** obs.confidence
            else:
                effective_lr = raw_lr

            log_lr = math.log(effective_lr)
            log_odds += log_lr
            contributions[obs.signal_name] = round(log_lr, 4)

            if obs.fired:
                signals_fired += 1

        # Convert back to probability
        # Guard against overflow: if log_odds is very large, posterior → 1.0
        if log_odds > 700:
            posterior = 1.0
        elif log_odds < -700:
            posterior = 0.0
        else:
            posterior_odds = math.exp(log_odds)
            posterior = posterior_odds / (1.0 + posterior_odds)

        risk_tier = self._assign_tier(posterior)
        equiv_score = (1.0 - posterior) * 100.0

        # Additional warnings
        if posterior >= 0.50:
            warnings.append(
                "CRITICAL: posterior probability exceeds 50 % — "
                "manual review strongly recommended"
            )
        elif posterior >= 0.20:
            warnings.append(
                "HIGH risk: posterior probability exceeds 20 %"
            )

        return BayesianRiskReport(
            prior_probability=prior,
            posterior_probability=round(posterior, 6),
            risk_tier=risk_tier,
            domain=self._domain,
            signals_observed=list(self._observations),
            signals_fired=signals_fired,
            total_signals=len(self._observations),
            log_odds_contributions=contributions,
            equivalent_weighted_score=round(equiv_score, 1),
            warnings=warnings,
        )

    # -- reset ---------------------------------------------------------------

    def reset(self) -> None:
        """Clear all observations so the scorer can be reused."""
        self._observations.clear()

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _assign_tier(probability: float) -> RiskTier:
        if probability >= 0.50:
            return RiskTier.CRITICAL
        if probability >= 0.20:
            return RiskTier.HIGH
        if probability >= 0.05:
            return RiskTier.ELEVATED
        return RiskTier.LOW
