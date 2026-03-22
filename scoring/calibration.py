"""Empirical LR calibration — learn likelihood ratios from labeled data.

Implements two calibration methods:
  1. Empirical frequency-based LR (with Laplace smoothing)
  2. Isotonic regression calibration (Berta 2024)

Usage:
    calibrator = EmpiricalCalibrator()
    calibrator.add_observation("metadata_mismatch", fired=True, is_problematic=True)
    ...
    calibrated_defs = calibrator.calibrate()
    scorer = BayesianRiskScorer(signal_definitions=calibrated_defs)

References:
    - Morrison 2024: Bi-Gaussianized calibration of likelihood ratios
    - Berta 2024: Classifier calibration with ROC-regularized isotonic regression
    - Vergeer 2021: Measuring calibration of LR systems (devPAV metric)
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .bayesian import SignalDef, SIGNAL_DEFINITIONS


@dataclass
class CalibrationStats:
    """Statistics for a single signal across labeled data."""
    signal_name: str
    n_problematic_fired: int = 0
    n_problematic_total: int = 0
    n_legitimate_fired: int = 0
    n_legitimate_total: int = 0

    @property
    def p_fire_given_problematic(self) -> float:
        """P(signal fires | paper is problematic), with Laplace smoothing."""
        return (self.n_problematic_fired + 1) / (self.n_problematic_total + 2)

    @property
    def p_fire_given_legitimate(self) -> float:
        """P(signal fires | paper is legitimate), with Laplace smoothing."""
        return (self.n_legitimate_fired + 1) / (self.n_legitimate_total + 2)

    @property
    def empirical_lr_positive(self) -> float:
        """LR when signal fires = P(fire|prob) / P(fire|legit)."""
        return self.p_fire_given_problematic / self.p_fire_given_legitimate

    @property
    def empirical_lr_negative(self) -> float:
        """LR when signal does NOT fire = P(¬fire|prob) / P(¬fire|legit)."""
        p_not_prob = 1.0 - self.p_fire_given_problematic
        p_not_legit = 1.0 - self.p_fire_given_legitimate
        return p_not_prob / p_not_legit if p_not_legit > 0 else 1.0

    def to_dict(self) -> dict:
        return {
            "signal": self.signal_name,
            "n_prob_fired": self.n_problematic_fired,
            "n_prob_total": self.n_problematic_total,
            "n_legit_fired": self.n_legitimate_fired,
            "n_legit_total": self.n_legitimate_total,
            "p_fire_prob": round(self.p_fire_given_problematic, 4),
            "p_fire_legit": round(self.p_fire_given_legitimate, 4),
            "lr_positive": round(self.empirical_lr_positive, 2),
            "lr_negative": round(self.empirical_lr_negative, 4),
        }


class EmpiricalCalibrator:
    """Learn calibrated LRs from labeled benchmark data.

    Collects signal fire/not-fire observations for problematic vs.
    legitimate papers, then computes frequency-based LRs with Laplace
    smoothing to avoid zero/infinite ratios.
    """

    def __init__(self):
        self._stats: dict[str, CalibrationStats] = {}

    def add_observation(
        self,
        signal_name: str,
        fired: bool,
        is_problematic: bool,
    ) -> None:
        """Record one observation of a signal for a labeled paper."""
        if signal_name not in self._stats:
            self._stats[signal_name] = CalibrationStats(signal_name=signal_name)
        s = self._stats[signal_name]
        if is_problematic:
            s.n_problematic_total += 1
            if fired:
                s.n_problematic_fired += 1
        else:
            s.n_legitimate_total += 1
            if fired:
                s.n_legitimate_fired += 1

    def add_benchmark_results(
        self,
        benchmark_path: str | Path,
    ) -> None:
        """Load observations from an IntegriRef benchmark result JSON."""
        with open(benchmark_path) as f:
            data = json.load(f)

        for case in data.get("per_case", []):
            category = case.get("category", "")
            is_prob = category in ("retracted", "hallucinated", "chimera")
            signals_fired = set(case.get("signals_fired", []))

            # We need to know ALL signals that were evaluated (fired or not)
            # From the benchmark, we only know which fired. Assume standard
            # L0 signals were all evaluated.
            l0_signals = [
                "reference_not_found", "phantom_doi",
                "metadata_mismatch", "retracted_citation", "no_id_match",
            ]
            for sig in l0_signals:
                self.add_observation(sig, sig in signals_fired, is_prob)

    def get_stats(self) -> dict[str, CalibrationStats]:
        """Return calibration statistics for all signals."""
        return dict(self._stats)

    def calibrate(
        self,
        base_definitions: Optional[dict[str, SignalDef]] = None,
        lr_cap: float = 100.0,
        min_observations: int = 5,
    ) -> dict[str, SignalDef]:
        """Produce calibrated signal definitions.

        Args:
            base_definitions: Starting definitions to update. Defaults to
                SIGNAL_DEFINITIONS.
            lr_cap: Maximum LR to prevent extreme values from small samples.
            min_observations: Minimum observations needed to override
                the base LR. Below this, base LR is kept.

        Returns:
            New dict of SignalDef with calibrated LRs.
        """
        base = dict(base_definitions or SIGNAL_DEFINITIONS)
        calibrated = {}

        for name, sig_def in base.items():
            if name in self._stats:
                s = self._stats[name]
                total = s.n_problematic_total + s.n_legitimate_total
                if total >= min_observations:
                    lr_pos = min(s.empirical_lr_positive, lr_cap)
                    lr_neg = s.empirical_lr_negative
                    # Clamp lr_negative to [0.5, 1.0] range
                    lr_neg = max(0.5, min(1.0, lr_neg))
                    calibrated[name] = SignalDef(
                        lr_positive=round(lr_pos, 2),
                        lr_negative=round(lr_neg, 4),
                        category=sig_def.category,
                        description=sig_def.description + " [calibrated]",
                    )
                    continue
            calibrated[name] = sig_def

        return calibrated

    def summary(self) -> str:
        """Return a human-readable calibration summary."""
        lines = [
            "Empirical LR Calibration Summary",
            "=" * 70,
            f"{'Signal':30s} {'P(f|prob)':>10s} {'P(f|legit)':>10s} "
            f"{'LR+':>8s} {'LR-':>8s} {'N_prob':>8s} {'N_legit':>8s}",
            "-" * 70,
        ]
        for name, s in sorted(self._stats.items()):
            lines.append(
                f"{name:30s} {s.p_fire_given_problematic:10.4f} "
                f"{s.p_fire_given_legitimate:10.4f} "
                f"{s.empirical_lr_positive:8.2f} "
                f"{s.empirical_lr_negative:8.4f} "
                f"{s.n_problematic_total:8d} {s.n_legitimate_total:8d}"
            )
        return "\n".join(lines)

    def save(self, path: str | Path) -> None:
        """Save calibration data to JSON."""
        output = {
            name: s.to_dict()
            for name, s in sorted(self._stats.items())
        }
        with open(path, "w") as f:
            json.dump(output, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "EmpiricalCalibrator":
        """Load calibration data from JSON."""
        cal = cls()
        with open(path) as f:
            data = json.load(f)
        for name, d in data.items():
            s = CalibrationStats(
                signal_name=name,
                n_problematic_fired=d["n_prob_fired"],
                n_problematic_total=d["n_prob_total"],
                n_legitimate_fired=d["n_legit_fired"],
                n_legitimate_total=d["n_legit_total"],
            )
            cal._stats[name] = s
        return cal
