"""Statistical integrity verification — GRIM test and statcheck.

GRIM (Brown & Heathers 2016): Checks if reported means are
mathematically possible given sample size.

statcheck (Epskamp & Nuijten 2016): Extracts APA-formatted test
statistics, recomputes p-values, flags inconsistencies.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Try to import scipy for accurate p-value computation
# ---------------------------------------------------------------------------

try:
    from scipy import stats as _scipy_stats
    _HAS_SCIPY = True
except ImportError:
    _scipy_stats = None  # type: ignore[assignment]
    _HAS_SCIPY = False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GRIMResult:
    """Result of a single GRIM test."""

    mean: float
    n: int
    items: int  # number of items (default 1 for single Likert)
    is_consistent: bool
    granularity: float  # 1 / (N * items)
    reported_str: str = ""


@dataclass
class StatCheckResult:
    """Result of checking a single APA-formatted test statistic."""

    test_type: str  # "t", "F", "chi2", "r", "Z"
    test_statistic: float
    df1: int
    df2: int  # 0 for t, chi2, Z
    reported_p: float
    computed_p: float
    is_consistent: bool
    decision_error: bool  # significance flipped (p<.05 vs p>=.05)
    reported_str: str  # original text match


@dataclass
class StatisticalReport:
    """Combined results from GRIM and statcheck analysis."""

    text_length: int
    grim_results: list[GRIMResult]
    statcheck_results: list[StatCheckResult]
    grim_failures: int
    statcheck_errors: int
    statcheck_decision_errors: int
    risk_score: float  # 0-100


# ---------------------------------------------------------------------------
# GRIM tester
# ---------------------------------------------------------------------------

# Pattern to extract M = X.XX ... N = Y from text.
# Handles: M = 2.31, N = 25 / M=2.31 (N=25) / mean = 2.31, n = 25, etc.
# The gap allows dots inside numbers (e.g. "SD = 0.5") but stops at
# sentence-ending periods (period followed by space + uppercase).
_GRIM_PATTERN = re.compile(
    r"[Mm](?:ean)?\s*=\s*(\d+\.\d+)"
    r"(?:(?!\.\s+[A-Z]).){0,80}?"  # up to 80 chars, no sentence boundary
    r"[Nn]\s*=\s*(\d+)",
)


class GRIMTester:
    """GRIM (Granularity-Related Inconsistency of Means) tester.

    Tests whether a reported mean is mathematically possible given
    sample size *N* and number of scale items.
    """

    def test(
        self,
        mean: float,
        n: int,
        items: int = 1,
        tolerance: float = 0.01,
    ) -> GRIMResult:
        """Test if *mean* is GRIM-consistent.

        Parameters
        ----------
        mean : float
            Reported mean value.
        n : int
            Sample size.
        items : int
            Number of summed items (default 1 for a single Likert item).
        tolerance : float
            Rounding tolerance applied to ``mean * N * items``.

        Returns
        -------
        GRIMResult
        """
        if n <= 0:
            # Degenerate — treat as consistent (cannot test).
            return GRIMResult(
                mean=mean, n=n, items=items,
                is_consistent=True,
                granularity=0.0,
            )

        granularity = 1.0 / (n * items)

        # The product mean * n * items must be (close to) an integer.
        product = mean * n * items
        remainder = abs(product - round(product))
        is_consistent = remainder <= tolerance

        return GRIMResult(
            mean=mean,
            n=n,
            items=items,
            is_consistent=is_consistent,
            granularity=granularity,
        )

    def extract_and_test(self, text: str) -> list[GRIMResult]:
        """Extract ``M = X.XX, N = Y`` patterns from *text* and test each."""
        results: list[GRIMResult] = []
        for m in _GRIM_PATTERN.finditer(text):
            try:
                mean = float(m.group(1))
                n = int(m.group(2))
            except (ValueError, IndexError):
                continue

            result = self.test(mean, n)
            result.reported_str = m.group(0)
            results.append(result)

        return results


# ---------------------------------------------------------------------------
# p-value computation helpers
# ---------------------------------------------------------------------------

def _compute_p_value(
    test_type: str,
    statistic: float,
    df1: int,
    df2: int = 0,
) -> float | None:
    """Compute a two-tailed (or one-tailed for F/chi2) p-value.

    Returns *None* when scipy is unavailable.
    """
    if not _HAS_SCIPY:
        return None

    try:
        if test_type == "t":
            return float(_scipy_stats.t.sf(abs(statistic), df1) * 2)
        elif test_type == "F":
            return float(_scipy_stats.f.sf(statistic, df1, df2))
        elif test_type == "chi2":
            return float(_scipy_stats.chi2.sf(statistic, df1))
        elif test_type == "r":
            # Convert r to t, then use t-distribution.
            r = statistic
            if abs(r) >= 1.0:
                return 0.0
            n = df1 + 2  # df for r is N-2
            t_val = r * math.sqrt((n - 2) / (1 - r * r))
            return float(_scipy_stats.t.sf(abs(t_val), n - 2) * 2)
        elif test_type == "Z":
            return float(_scipy_stats.norm.sf(abs(statistic)) * 2)
    except Exception:
        return None

    return None


# ---------------------------------------------------------------------------
# StatChecker
# ---------------------------------------------------------------------------

# Regex helpers — match both regular minus and Unicode minus (−).
_SIGN = r"[−\-]?"
_NUM = r"\d+\.?\d*"  # e.g.  2.34  or  12
_P_OP = r"[<>=≤≥]"
# p-value part: "p < .05" or "p = 0.023" etc.
_P_PART = (
    r",?\s*p\s*(" + _P_OP + r")\s*\.?(" + _NUM + r")"
)

# Individual patterns --------------------------------------------------------

_RE_T = re.compile(
    r"t\s*\(\s*(\d+)\s*\)\s*=\s*(" + _SIGN + _NUM + r")\s*" + _P_PART,
    re.IGNORECASE,
)

_RE_F = re.compile(
    r"F\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*=\s*(" + _NUM + r")\s*" + _P_PART,
)

_RE_CHI2 = re.compile(
    r"(?:[χXx]²|[Cc]hi[\s-]*(?:square|2))\s*\(\s*(\d+)\s*\)\s*=\s*("
    + _NUM + r")\s*" + _P_PART,
)

_RE_R = re.compile(
    r"r\s*\(\s*(\d+)\s*\)\s*=\s*(" + _SIGN + r"\.?\d+\.?\d*)\s*" + _P_PART,
    re.IGNORECASE,
)

_RE_Z = re.compile(
    r"[Zz]\s*=\s*(" + _SIGN + _NUM + r")\s*" + _P_PART,
)

# Consistency threshold: how far reported and computed p may differ before
# we call it an error.
_P_TOLERANCE = 0.05
_ALPHA = 0.05  # significance threshold for decision-error checks


class StatChecker:
    """statcheck — re-compute p-values from APA-formatted test statistics."""

    def check(
        self,
        test_type: str,
        statistic: float,
        df1: int,
        df2: int = 0,
        reported_p: float = 0.0,
    ) -> StatCheckResult:
        """Check a single test statistic against its reported p-value.

        Parameters
        ----------
        test_type : str
            One of ``"t"``, ``"F"``, ``"chi2"``, ``"r"``, ``"Z"``.
        statistic : float
            The test statistic value.
        df1 : int
            Degrees of freedom (first, or only).
        df2 : int
            Second df (for F-tests); 0 otherwise.
        reported_p : float
            The p-value reported in the paper.

        Returns
        -------
        StatCheckResult
        """
        computed_p = _compute_p_value(test_type, statistic, df1, df2)

        if computed_p is None:
            # scipy unavailable — mark as unchecked (consistent by default).
            return StatCheckResult(
                test_type=test_type,
                test_statistic=statistic,
                df1=df1,
                df2=df2,
                reported_p=reported_p,
                computed_p=-1.0,  # sentinel: unchecked
                is_consistent=True,
                decision_error=False,
                reported_str="",
            )

        is_consistent = abs(reported_p - computed_p) <= _P_TOLERANCE

        # Decision error: does the discrepancy flip significance?
        reported_sig = reported_p < _ALPHA
        computed_sig = computed_p < _ALPHA
        decision_error = reported_sig != computed_sig

        return StatCheckResult(
            test_type=test_type,
            test_statistic=statistic,
            df1=df1,
            df2=df2,
            reported_p=reported_p,
            computed_p=round(computed_p, 6),
            is_consistent=is_consistent,
            decision_error=decision_error,
            reported_str="",
        )

    def extract_and_check(self, text: str) -> list[StatCheckResult]:
        """Extract APA statistics from *text* and check each.

        Recognises these patterns (with spacing / Unicode variations):

        - ``t(df) = value, p = value``
        - ``F(df1, df2) = value, p = value``
        - ``chi2(df) = value, p = value``  (also ``χ²``, ``Chi-square``)
        - ``r(df) = value, p = value``
        - ``Z = value, p = value``
        """
        results: list[StatCheckResult] = []

        # --- t-tests ---
        for m in _RE_T.finditer(text):
            df = int(m.group(1))
            stat = float(m.group(2).replace("−", "-"))
            p_op = m.group(3)
            p_val = self._parse_p(m.group(4), p_op)

            r = self.check("t", stat, df1=df, reported_p=p_val)
            r.reported_str = m.group(0)
            results.append(r)

        # --- F-tests ---
        for m in _RE_F.finditer(text):
            df1 = int(m.group(1))
            df2 = int(m.group(2))
            stat = float(m.group(3))
            p_op = m.group(4)
            p_val = self._parse_p(m.group(5), p_op)

            r = self.check("F", stat, df1=df1, df2=df2, reported_p=p_val)
            r.reported_str = m.group(0)
            results.append(r)

        # --- chi-squared ---
        for m in _RE_CHI2.finditer(text):
            df = int(m.group(1))
            stat = float(m.group(2))
            p_op = m.group(3)
            p_val = self._parse_p(m.group(4), p_op)

            r = self.check("chi2", stat, df1=df, reported_p=p_val)
            r.reported_str = m.group(0)
            results.append(r)

        # --- r (correlation) ---
        for m in _RE_R.finditer(text):
            df = int(m.group(1))
            stat = float(m.group(2).replace("−", "-"))
            p_op = m.group(3)
            p_val = self._parse_p(m.group(4), p_op)

            r = self.check("r", stat, df1=df, reported_p=p_val)
            r.reported_str = m.group(0)
            results.append(r)

        # --- Z-tests ---
        for m in _RE_Z.finditer(text):
            stat = float(m.group(1).replace("−", "-"))
            p_op = m.group(2)
            p_val = self._parse_p(m.group(3), p_op)

            r = self.check("Z", stat, df1=0, reported_p=p_val)
            r.reported_str = m.group(0)
            results.append(r)

        return results

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _parse_p(raw: str, operator: str) -> float:
        """Normalise a reported p-value string.

        Handles ``p = .05`` (leading-dot), ``p < 0.001``, etc.
        For ``<`` / ``≤`` operators we return the value as-is — the comparison
        logic in :meth:`check` uses tolerance anyway.

        The regex ``_P_PART`` captures after an optional leading dot, so we
        may receive ``"05"`` (from ``p = .05``) or ``"0.05"`` (from
        ``p = 0.05``).  Disambiguate by checking if the value > 1.
        """
        try:
            val = float(raw)
        except ValueError:
            return 0.0
        # If the captured string omitted the leading zero (e.g. ``05`` from
        # ``p = .05``) and is >= 1, it was probably a decimal without the dot.
        # Use the *string length* to reconstruct the correct magnitude.
        if val >= 1.0:
            # Count significant digits: "05" → 2, "001" → 3
            stripped = raw.lstrip("0") or "0"
            val = val / (10 ** len(raw))
        # P-values must be in [0, 1]
        return max(0.0, min(val, 1.0))


# ---------------------------------------------------------------------------
# Combined verifier
# ---------------------------------------------------------------------------

class StatisticalVerifier:
    """Combined GRIM + statcheck verifier.

    Usage::

        v = StatisticalVerifier()
        report = v.verify(text)
        print(report.risk_score)
    """

    _GRIM_PENALTY = 25
    _STATCHECK_PENALTY = 10
    _DECISION_PENALTY = 20

    def __init__(self) -> None:
        self.grim = GRIMTester()
        self.statcheck = StatChecker()

    def verify(self, text: str) -> StatisticalReport:
        """Run both GRIM and statcheck on *text*.

        Returns a :class:`StatisticalReport` with per-test results
        and an aggregate risk score (0-100).
        """
        grim_results = self.grim.extract_and_test(text)
        statcheck_results = self.statcheck.extract_and_check(text)

        grim_failures = sum(1 for r in grim_results if not r.is_consistent)
        statcheck_errors = sum(
            1 for r in statcheck_results if not r.is_consistent
        )
        decision_errors = sum(
            1 for r in statcheck_results if r.decision_error
        )

        raw_score = (
            grim_failures * self._GRIM_PENALTY
            + statcheck_errors * self._STATCHECK_PENALTY
            + decision_errors * self._DECISION_PENALTY
        )
        risk_score = min(100.0, float(raw_score))

        return StatisticalReport(
            text_length=len(text),
            grim_results=grim_results,
            statcheck_results=statcheck_results,
            grim_failures=grim_failures,
            statcheck_errors=statcheck_errors,
            statcheck_decision_errors=decision_errors,
            risk_score=risk_score,
        )
