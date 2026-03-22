"""Risk scoring — multi-dimensional integrity assessment (L4)."""

from .scorer import IntegrityScorer, IntegrityReport, DimensionScore
from .profiles import ScoringProfile, SCORING_PROFILES
from .bayesian import (
    BayesianRiskScorer,
    BayesianRiskReport,
    SignalDef,
    SignalObservation,
    RiskTier,
    SIGNAL_DEFINITIONS,
    DOMAIN_PRIORS,
)
from .calibration import EmpiricalCalibrator, CalibrationStats
from .dempster_shafer import (
    DempsterShaferScorer,
    GroupedBayesianScorer,
    DSRiskReport,
    MassFunction,
    dempster_combine,
    murphy_combine,
    SIGNAL_GROUPS,
)
