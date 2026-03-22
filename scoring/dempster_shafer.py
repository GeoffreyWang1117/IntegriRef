"""Dempster-Shafer evidence fusion — conflict-aware signal combination.

Replaces the naive product-of-LRs assumption (signals are independent) with
Dempster-Shafer evidence theory, which:
  1. Models belief, plausibility, and uncertainty explicitly
  2. Handles conflicts between signals (e.g., L0 says suspicious but L2 says entailed)
  3. Groups correlated signals to avoid double-counting

Signal groups (correlated signals combined before fusion):
  - existence_group:  reference_not_found, phantom_doi
  - metadata_group:   metadata_mismatch, no_id_match
  - semantic_group:   claim_contradicted, claim_unsupported
  - graph_group:      citation_ring_detected, excessive_self_citation,
                      temporal_anomaly, orphan_cluster
  - text_group:       tortured_phrases, suspicious_email
  - stats_group:      grim_test_failure, statcheck_error
  - independent:      retracted_citation, citation_misrepresents_source,
                      all_citations_mentioning

Within each group, signals are combined using the max-belief heuristic
(avoids double-counting correlated evidence). Between groups, Dempster's
rule of combination is applied.

References:
  - Shafer (1976): A Mathematical Theory of Evidence
  - Yager (1987): Dempster-Shafer with conflict redistribution
  - Murphy (2000): Average-then-combine for high-conflict scenarios
  - Lu & Zhu (2025): D-S for heterogeneous evidence fusion
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .bayesian import (
    SignalDef, SignalObservation, BayesianRiskReport,
    RiskTier, SIGNAL_DEFINITIONS, DOMAIN_PRIORS,
)


# ---------------------------------------------------------------------------
# Mass function (Basic Probability Assignment)
# ---------------------------------------------------------------------------

@dataclass
class MassFunction:
    """Dempster-Shafer mass function over {problematic, legitimate, Θ}."""
    m_prob: float = 0.0     # mass assigned to {problematic}
    m_legit: float = 0.0    # mass assigned to {legitimate}
    m_theta: float = 1.0    # mass assigned to Θ (frame of discernment = uncertainty)

    def __post_init__(self):
        total = self.m_prob + self.m_legit + self.m_theta
        if abs(total - 1.0) > 1e-9:
            # Normalize
            if total > 0:
                self.m_prob /= total
                self.m_legit /= total
                self.m_theta /= total
            else:
                self.m_theta = 1.0

    @property
    def belief_prob(self) -> float:
        """Belief that paper is problematic: Bel({prob}) = m({prob})."""
        return self.m_prob

    @property
    def plausibility_prob(self) -> float:
        """Plausibility that paper is problematic: Pl({prob}) = 1 - m({legit})."""
        return 1.0 - self.m_legit

    @property
    def belief_legit(self) -> float:
        """Belief that paper is legitimate."""
        return self.m_legit

    @property
    def uncertainty(self) -> float:
        """Epistemic uncertainty: Pl - Bel = m(Θ)."""
        return self.m_theta

    @property
    def pignistic_prob(self) -> float:
        """Pignistic probability of 'problematic' (decision-making).

        BetP({prob}) = m({prob}) + m(Θ) / 2
        (Theta mass split equally between singletons)
        """
        return self.m_prob + self.m_theta / 2.0

    def to_dict(self) -> dict:
        return {
            "m_problematic": round(self.m_prob, 6),
            "m_legitimate": round(self.m_legit, 6),
            "m_uncertainty": round(self.m_theta, 6),
            "belief_prob": round(self.belief_prob, 6),
            "plausibility_prob": round(self.plausibility_prob, 6),
            "pignistic_prob": round(self.pignistic_prob, 6),
        }


# ---------------------------------------------------------------------------
# Signal-to-mass conversion
# ---------------------------------------------------------------------------

def signal_to_mass(
    sig_def: SignalDef,
    fired: bool,
    confidence: float = 1.0,
) -> MassFunction:
    """Convert a signal observation to a D-S mass function.

    Uses the likelihood ratio to derive mass assignments:
      - When signal fires with LR_pos > 1:
          m({prob}) = 1 - 1/LR_pos  (discounted by confidence)
          m(Θ)      = 1/LR_pos      (remaining uncertainty)
      - When signal does not fire with LR_neg < 1:
          m({legit}) = 1 - LR_neg   (discounted by confidence)
          m(Θ)       = LR_neg       (remaining uncertainty)

    This is the standard LR→mass conversion from forensic statistics.
    """
    if fired:
        lr = sig_def.lr_positive
        if lr <= 1.0:
            return MassFunction(m_theta=1.0)  # No evidence
        # Discount by confidence
        raw_mass = 1.0 - 1.0 / lr
        discounted = raw_mass * confidence
        return MassFunction(
            m_prob=discounted,
            m_legit=0.0,
            m_theta=1.0 - discounted,
        )
    else:
        lr = sig_def.lr_negative
        if lr >= 1.0:
            return MassFunction(m_theta=1.0)  # No evidence
        raw_mass = 1.0 - lr
        discounted = raw_mass * confidence
        return MassFunction(
            m_prob=0.0,
            m_legit=discounted,
            m_theta=1.0 - discounted,
        )


# ---------------------------------------------------------------------------
# Combination rules
# ---------------------------------------------------------------------------

def dempster_combine(m1: MassFunction, m2: MassFunction) -> MassFunction:
    """Classical Dempster's rule of combination with normalization.

    Handles conflict by normalizing: K = Σ m1(A)·m2(B) where A∩B=∅
    """
    # Compute all intersections
    # {prob}∩{prob} = {prob}
    pp = m1.m_prob * m2.m_prob
    # {prob}∩Θ = {prob}
    pt = m1.m_prob * m2.m_theta
    tp = m1.m_theta * m2.m_prob
    # {legit}∩{legit} = {legit}
    ll = m1.m_legit * m2.m_legit
    # {legit}∩Θ = {legit}
    lt = m1.m_legit * m2.m_theta
    tl = m1.m_theta * m2.m_legit
    # Θ∩Θ = Θ
    tt = m1.m_theta * m2.m_theta

    # Conflict: {prob}∩{legit} = ∅
    conflict = m1.m_prob * m2.m_legit + m1.m_legit * m2.m_prob

    if conflict >= 1.0 - 1e-12:
        # Total conflict — return vacuous
        return MassFunction(m_theta=1.0)

    # Normalize
    norm = 1.0 / (1.0 - conflict)

    return MassFunction(
        m_prob=(pp + pt + tp) * norm,
        m_legit=(ll + lt + tl) * norm,
        m_theta=tt * norm,
    )


def murphy_combine(masses: list[MassFunction]) -> MassFunction:
    """Murphy's average-then-combine rule for high-conflict scenarios.

    Average all mass functions first, then combine the average with itself
    (n-1) times. Reduces the impact of outlier evidence sources.
    """
    if not masses:
        return MassFunction(m_theta=1.0)
    if len(masses) == 1:
        return masses[0]

    n = len(masses)
    # Average
    avg = MassFunction(
        m_prob=sum(m.m_prob for m in masses) / n,
        m_legit=sum(m.m_legit for m in masses) / n,
        m_theta=sum(m.m_theta for m in masses) / n,
    )

    # Combine average with itself (n-1) times
    result = avg
    for _ in range(n - 1):
        result = dempster_combine(result, avg)

    return result


# ---------------------------------------------------------------------------
# Signal groups (correlated signals)
# ---------------------------------------------------------------------------

SIGNAL_GROUPS: dict[str, list[str]] = {
    "existence": ["reference_not_found", "phantom_doi"],
    "metadata": ["metadata_mismatch", "no_id_match"],
    "semantic": ["claim_contradicted", "claim_unsupported"],
    "graph": ["citation_ring_detected", "excessive_self_citation",
              "temporal_anomaly", "orphan_cluster"],
    "text": ["tortured_phrases", "suspicious_email"],
    "stats": ["grim_test_failure", "statcheck_error"],
}

# Signals treated as independent (no grouping)
INDEPENDENT_SIGNALS = {
    "retracted_citation",
    "citation_misrepresents_source",
    "all_citations_mentioning",
}


def _group_masses(
    observations: list[SignalObservation],
    signal_defs: dict[str, SignalDef],
) -> list[MassFunction]:
    """Convert observations to grouped mass functions.

    Within each group, take the max-evidence mass (avoids double-counting).
    Independent signals contribute their own mass directly.
    """
    # Build observation lookup
    obs_by_name = {obs.signal_name: obs for obs in observations}

    masses = []

    # Process groups
    for group_name, group_signals in SIGNAL_GROUPS.items():
        group_masses = []
        for sig_name in group_signals:
            if sig_name in obs_by_name:
                obs = obs_by_name[sig_name]
                sig_def = signal_defs.get(sig_name)
                if sig_def:
                    m = signal_to_mass(sig_def, obs.fired, obs.confidence)
                    group_masses.append(m)

        if group_masses:
            # Max-belief within group (strongest evidence wins)
            best = max(group_masses, key=lambda m: m.m_prob if m.m_prob > 0 else -m.m_legit)
            masses.append(best)

    # Process independent signals
    for sig_name in INDEPENDENT_SIGNALS:
        if sig_name in obs_by_name:
            obs = obs_by_name[sig_name]
            sig_def = signal_defs.get(sig_name)
            if sig_def:
                m = signal_to_mass(sig_def, obs.fired, obs.confidence)
                masses.append(m)

    return masses


# ---------------------------------------------------------------------------
# D-S Risk Report
# ---------------------------------------------------------------------------

@dataclass
class DSRiskReport:
    """Full output of a Dempster-Shafer risk assessment."""
    prior_probability: float
    posterior_probability: float     # pignistic probability
    belief_problematic: float       # lower bound on prob
    plausibility_problematic: float # upper bound on prob
    uncertainty: float              # epistemic uncertainty
    conflict_level: float           # total conflict encountered
    risk_tier: RiskTier
    domain: str
    signals_observed: list[SignalObservation]
    signals_fired: int
    total_signals: int
    group_masses: dict[str, dict]   # per-group mass assignments
    combined_mass: dict             # final combined mass
    equivalent_weighted_score: float
    warnings: list[str]

    def summary(self) -> dict:
        return {
            "prior": round(self.prior_probability, 4),
            "posterior": round(self.posterior_probability, 4),
            "belief": round(self.belief_problematic, 4),
            "plausibility": round(self.plausibility_problematic, 4),
            "uncertainty": round(self.uncertainty, 4),
            "conflict": round(self.conflict_level, 4),
            "risk_tier": self.risk_tier.value,
            "domain": self.domain,
            "signals_fired": self.signals_fired,
            "total_signals": self.total_signals,
            "equivalent_score": round(self.equivalent_weighted_score, 1),
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# D-S Risk Scorer
# ---------------------------------------------------------------------------

class DempsterShaferScorer:
    """Pure Dempster-Shafer evidence fusion for paper integrity risk.

    Uses D-S mass functions and Dempster's combination rule with signal
    grouping. Suitable for scenarios with significant inter-source conflict.

    Usage::

        scorer = DempsterShaferScorer(domain="biomedical")
        scorer.observe("phantom_doi", fired=True)
        scorer.observe("metadata_mismatch", fired=True)
        report = scorer.compute()
    """

    def __init__(
        self,
        domain: str = "default",
        signal_definitions: Optional[dict[str, SignalDef]] = None,
        domain_priors: Optional[dict[str, float]] = None,
        combination_rule: str = "dempster",
        prior_strength: float = 0.8,
    ):
        self._domain = domain
        self._signal_defs = signal_definitions or SIGNAL_DEFINITIONS
        self._domain_priors = domain_priors or DOMAIN_PRIORS
        self._combination_rule = combination_rule
        self._prior_strength = max(0.0, min(1.0, prior_strength))
        self._observations: list[SignalObservation] = []

    def observe(self, signal_name: str, fired: bool,
                confidence: float = 1.0, details: str = "") -> None:
        if signal_name not in self._signal_defs:
            raise ValueError(f"Unknown signal: {signal_name!r}")
        confidence = max(0.0, min(1.0, confidence))
        self._observations.append(SignalObservation(
            signal_name=signal_name, fired=fired,
            confidence=confidence, details=details,
        ))

    def observe_batch(self, observations: list[SignalObservation]) -> None:
        for obs in observations:
            self.observe(obs.signal_name, obs.fired, obs.confidence, obs.details)

    def compute(self) -> DSRiskReport:
        prior = self._domain_priors.get(self._domain, self._domain_priors["default"])
        warnings: list[str] = []

        if not self._observations:
            warnings.append("No signals observed — returning prior as posterior")

        alpha = self._prior_strength
        prior_mass = MassFunction(
            m_prob=prior * alpha,
            m_legit=(1.0 - prior) * alpha,
            m_theta=1.0 - alpha,
        )

        grouped = _group_masses(self._observations, self._signal_defs)
        obs_by_name = {obs.signal_name: obs for obs in self._observations}
        group_mass_report = _build_group_report(obs_by_name, self._signal_defs)

        all_masses = [prior_mass] + grouped

        if not grouped:
            combined = prior_mass
            conflict = 0.0
        elif self._combination_rule == "murphy":
            combined = murphy_combine(all_masses)
            conflict = 0.0
        else:
            combined = all_masses[0]
            total_conflict = 0.0
            for m in all_masses[1:]:
                c = combined.m_prob * m.m_legit + combined.m_legit * m.m_prob
                total_conflict = 1.0 - (1.0 - total_conflict) * (1.0 - c)
                combined = dempster_combine(combined, m)
            conflict = total_conflict

        posterior = combined.pignistic_prob
        risk_tier = _assign_tier(posterior)
        signals_fired = sum(1 for obs in self._observations if obs.fired)

        if conflict > 0.5:
            warnings.append(
                f"HIGH CONFLICT ({conflict:.1%}): evidence sources disagree")
        if posterior >= 0.50:
            warnings.append("CRITICAL: posterior > 50% — manual review recommended")
        elif posterior >= 0.20:
            warnings.append("HIGH risk: posterior > 20%")

        return DSRiskReport(
            prior_probability=prior,
            posterior_probability=round(posterior, 6),
            belief_problematic=round(combined.belief_prob, 6),
            plausibility_problematic=round(combined.plausibility_prob, 6),
            uncertainty=round(combined.uncertainty, 6),
            conflict_level=round(conflict, 6),
            risk_tier=risk_tier,
            domain=self._domain,
            signals_observed=list(self._observations),
            signals_fired=signals_fired,
            total_signals=len(self._observations),
            group_masses=group_mass_report,
            combined_mass=combined.to_dict(),
            equivalent_weighted_score=round((1.0 - posterior) * 100.0, 1),
            warnings=warnings,
        )

    def reset(self) -> None:
        self._observations.clear()


# ---------------------------------------------------------------------------
# Grouped Bayesian Scorer — hybrid approach
# ---------------------------------------------------------------------------

class GroupedBayesianScorer:
    """Grouped Bayesian scorer — handles signal correlations via grouping.

    Within each correlated signal group, takes max |log-LR| (strongest
    evidence) to avoid double-counting. Between groups, uses standard
    Bayesian product-of-LRs. This combines D-S's correlation insight with
    Bayesian calibration.

    Compared to naive BayesianRiskScorer:
      - Prevents double-counting of correlated signals (e.g., phantom_doi
        and reference_not_found firing together no longer multiplies risk)
      - Preserves proper Bayesian posterior calibration
      - Reports inter-group conflict for diagnostics

    Usage::

        scorer = GroupedBayesianScorer(domain="biomedical")
        scorer.observe("phantom_doi", fired=True)
        scorer.observe("reference_not_found", fired=True)  # same group!
        scorer.observe("metadata_mismatch", fired=True)     # different group
        report = scorer.compute()
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

    def observe(self, signal_name: str, fired: bool,
                confidence: float = 1.0, details: str = "") -> None:
        if signal_name not in self._signal_defs:
            raise ValueError(f"Unknown signal: {signal_name!r}")
        confidence = max(0.0, min(1.0, confidence))
        self._observations.append(SignalObservation(
            signal_name=signal_name, fired=fired,
            confidence=confidence, details=details,
        ))

    def observe_batch(self, observations: list[SignalObservation]) -> None:
        for obs in observations:
            self.observe(obs.signal_name, obs.fired, obs.confidence, obs.details)

    def compute(self) -> DSRiskReport:
        """Run grouped Bayesian update and return a risk report."""
        prior = self._domain_priors.get(
            self._domain, self._domain_priors["default"])
        warnings: list[str] = []

        if not self._observations:
            warnings.append("No signals observed — returning prior as posterior")

        # Prior odds
        prior_odds = prior / (1.0 - prior)
        log_odds = math.log(prior_odds)

        obs_by_name = {obs.signal_name: obs for obs in self._observations}
        group_contributions: dict[str, float] = {}
        signals_fired = 0

        # Process groups: within each group, take max |log-LR|
        for group_name, group_signals in SIGNAL_GROUPS.items():
            group_log_lrs = []
            for sig_name in group_signals:
                if sig_name in obs_by_name:
                    obs = obs_by_name[sig_name]
                    sig = self._signal_defs[sig_name]
                    raw_lr = sig.lr_positive if obs.fired else sig.lr_negative
                    effective_lr = raw_lr ** obs.confidence if obs.confidence < 1.0 else raw_lr
                    group_log_lrs.append((sig_name, math.log(effective_lr), obs.fired))

            if group_log_lrs:
                # Take the contribution with max absolute value
                best_name, best_log_lr, best_fired = max(
                    group_log_lrs, key=lambda x: abs(x[1]))
                log_odds += best_log_lr
                group_contributions[f"{group_name}:{best_name}"] = round(best_log_lr, 4)
                if best_fired:
                    signals_fired += 1

        # Process independent signals normally
        for sig_name in INDEPENDENT_SIGNALS:
            if sig_name in obs_by_name:
                obs = obs_by_name[sig_name]
                sig = self._signal_defs[sig_name]
                raw_lr = sig.lr_positive if obs.fired else sig.lr_negative
                effective_lr = raw_lr ** obs.confidence if obs.confidence < 1.0 else raw_lr
                log_lr = math.log(effective_lr)
                log_odds += log_lr
                group_contributions[sig_name] = round(log_lr, 4)
                if obs.fired:
                    signals_fired += 1

        # Convert to probability
        if log_odds > 700:
            posterior = 1.0
        elif log_odds < -700:
            posterior = 0.0
        else:
            posterior_odds = math.exp(log_odds)
            posterior = posterior_odds / (1.0 + posterior_odds)

        risk_tier = _assign_tier(posterior)

        # Compute inter-group conflict as diagnostic
        # Conflict = some groups say problematic, others say legitimate
        pos_groups = sum(1 for v in group_contributions.values() if v > 0.1)
        neg_groups = sum(1 for v in group_contributions.values() if v < -0.1)
        conflict = min(pos_groups, neg_groups) / max(pos_groups + neg_groups, 1)

        if posterior >= 0.50:
            warnings.append("CRITICAL: posterior > 50% — manual review recommended")
        elif posterior >= 0.20:
            warnings.append("HIGH risk: posterior > 20%")
        if conflict > 0.3:
            warnings.append(
                f"Signal conflict detected: {pos_groups} groups incriminating, "
                f"{neg_groups} groups exculpating")

        # Build D-S-style report for compatibility
        group_mass_report = _build_group_report(obs_by_name, self._signal_defs)

        return DSRiskReport(
            prior_probability=prior,
            posterior_probability=round(posterior, 6),
            belief_problematic=round(posterior, 6),  # In Bayes, belief = posterior
            plausibility_problematic=round(posterior, 6),
            uncertainty=0.0,  # No epistemic uncertainty in Bayesian
            conflict_level=round(conflict, 4),
            risk_tier=risk_tier,
            domain=self._domain,
            signals_observed=list(self._observations),
            signals_fired=signals_fired,
            total_signals=len(self._observations),
            group_masses=group_mass_report,
            combined_mass={"log_odds_contributions": group_contributions},
            equivalent_weighted_score=round((1.0 - posterior) * 100.0, 1),
            warnings=warnings,
        )

    def reset(self) -> None:
        self._observations.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assign_tier(probability: float) -> RiskTier:
    if probability >= 0.50:
        return RiskTier.CRITICAL
    if probability >= 0.20:
        return RiskTier.HIGH
    if probability >= 0.05:
        return RiskTier.ELEVATED
    return RiskTier.LOW


def _build_group_report(
    obs_by_name: dict[str, SignalObservation],
    signal_defs: dict[str, SignalDef],
) -> dict[str, dict]:
    """Build per-group mass report for diagnostics."""
    report = {}
    for group_name, group_signals in SIGNAL_GROUPS.items():
        group_obs = [obs_by_name[s] for s in group_signals if s in obs_by_name]
        if group_obs:
            group_m_list = []
            for obs in group_obs:
                sig_def = signal_defs.get(obs.signal_name)
                if sig_def:
                    m = signal_to_mass(sig_def, obs.fired, obs.confidence)
                    group_m_list.append((obs.signal_name, m))
            if group_m_list:
                best_name, best_m = max(
                    group_m_list,
                    key=lambda x: x[1].m_prob if x[1].m_prob > 0 else -x[1].m_legit)
                report[group_name] = {
                    "dominant_signal": best_name, **best_m.to_dict()}

    for sig_name in INDEPENDENT_SIGNALS:
        if sig_name in obs_by_name:
            obs = obs_by_name[sig_name]
            sig_def = signal_defs.get(sig_name)
            if sig_def:
                m = signal_to_mass(sig_def, obs.fired, obs.confidence)
                report[sig_name] = m.to_dict()

    return report
