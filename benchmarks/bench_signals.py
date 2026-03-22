"""Signal-level benchmark — test each of the 18 Bayesian signals individually.

Loads signal_test_cases.json and validates that each signal fires (or doesn't)
on the appropriate test data. This is a unit-level complement to the integration
benchmark in bench_integrity.py.

Usage:
    python -m benchmarks.bench_signals                      # All signals
    python -m benchmarks.bench_signals --signal tortured_phrases
    python -m benchmarks.bench_signals --category text stats
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scoring.bayesian import (
    BayesianRiskScorer,
    SignalObservation,
    SIGNAL_DEFINITIONS,
)
from semantic.intent_classifier import IntentClassifier, CitationIntent

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger("bench_signals")

DATA_DIR = Path(__file__).parent / "data"


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class SignalTestResult:
    case_id: str
    signal: str
    expected_fired: bool
    actual_fired: bool
    correct: bool
    details: str = ""


# ── Tortured phrase detector ─────────────────────────────────────────────

# Top tortured phrases from Cabanac & Labbé 2021 + expansions
TORTURED_PHRASES = {
    "profound learning": "deep learning",
    "profound neural network": "deep neural network",
    "profound chain of connections": "deep neural network",
    "sham system": "dummy system",
    "quantity movement": "quantitative",
    "irregular and wonderful": "irregular and remarkable",
    "characteristic building framework": "feature extraction architecture",
    "irregular region highlight": "anomaly detection",
    "property extrication": "feature extraction",
    "breast milk emission": "lactation",
    "counterfeit neural systems": "artificial neural networks",
    "counterfeit neural network": "artificial neural network",
    "enormous amount work": "large-scale",
    "massive amounts of knowledge": "big data",
    "large amounts of information": "big data",
    "man-made neural network": "artificial neural network",
    "neural organization": "neural network",
    "fake neural network": "artificial neural network",
    "phony neural network": "artificial neural network",
    "misleading learning": "adversarial learning",
    "information foundation": "database",
    "calculated scheduling": "computational scheduling",
    "numerical framework": "computational framework",
    "arbitrary forest": "random forest",
    "choice tree": "decision tree",
    "bolster vector machine": "support vector machine",
    "assist vector machine": "support vector machine",
    "underpinning vector machine": "support vector machine",
    "help vector machine": "support vector machine",
    "calculated complexity": "computational complexity",
    "information mining": "data mining",
    "straight line regression": "linear regression",
    "calculated intelligence": "computational intelligence",
    "advanced knowledge mining": "deep data mining",
    "picture acknowledgment": "image recognition",
    "discourse examination": "speech analysis",
    "relational information base": "relational database",
    "information base": "database",
    "repetitive neural system": "recurrent neural network",
    "long haul memory": "long short-term memory",
    "convolution neural network": "convolutional neural network",
    "bunching algorithm": "clustering algorithm",
    "naive bayes": "naive bayes",  # not tortured, control
    "internet of things": "internet of things",  # not tortured, control
}


def detect_tortured_phrases(text: str) -> list[tuple[str, str]]:
    """Detect tortured phrases in text. Returns list of (tortured, original) pairs."""
    text_lower = text.lower()
    found = []
    for tortured, original in TORTURED_PHRASES.items():
        if tortured == original:
            continue  # Skip non-tortured control entries
        if tortured in text_lower:
            found.append((tortured, original))
    return found


# ── GRIM test ────────────────────────────────────────────────────────────

def grim_test(mean: float, n: int, scale: int = 1, dp: int = 2) -> bool:
    """Run the GRIM (Granularity-Related Inconsistency of Means) test.

    Returns True if the mean is CONSISTENT (possible), False if INCONSISTENT.

    Args:
        mean: Reported mean value.
        n: Sample size.
        scale: Scale granularity (1 for integers, 0.5 for half-points, etc.).
        dp: Decimal places in the reported mean.
    """
    # The total (mean * n) should be an integer (or integer * scale)
    total = mean * n
    granularity = scale
    # Check if total is close to a multiple of the granularity
    remainder = total % granularity
    tolerance = 0.5 * 10 ** (-dp)  # Half the last decimal place
    return remainder < tolerance or (granularity - remainder) < tolerance


# ── StatCheck (simplified) ───────────────────────────────────────────────

def _parse_p_value(p_str: str) -> float:
    """Parse a p-value string that may or may not have a leading dot/zero.

    Handles APA-format p-values like '.043', '043', '0.043', '.006', '006'.
    """
    if "." in p_str:
        return float(p_str)
    # No dot — the regex consumed it. Treat as decimal digits.
    # e.g. '043' → 0.043, '006' → 0.006
    return float(f"0.{p_str}")


def statcheck_verify(stat_text: str) -> dict:
    """Simplified statcheck: verify APA-formatted test statistics.

    Returns dict with keys: consistent (bool), computed_p, reported_p, stat_type.
    """
    import re
    from scipy import stats as scipy_stats

    result = {"consistent": True, "computed_p": None, "reported_p": None,
              "stat_type": None, "details": ""}

    # Parse F-test: F(df1, df2) = value, p = value
    m = re.search(
        r'F\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*=\s*([\d.]+)\s*,\s*p\s*[=<]\s*\.?(\d+\.?\d*)',
        stat_text)
    if m:
        df1, df2, f_val, p_str = int(m.group(1)), int(m.group(2)), float(m.group(3)), m.group(4)
        p_reported = _parse_p_value(p_str)
        p_computed = 1 - scipy_stats.f.cdf(f_val, df1, df2)
        result.update({
            "stat_type": "F",
            "computed_p": round(p_computed, 6),
            "reported_p": p_reported,
            "consistent": _p_consistent(p_computed, p_reported),
            "details": f"F({df1},{df2})={f_val}, computed p={p_computed:.6f}",
        })
        return result

    # Parse t-test: t(df) = value, p = value
    m = re.search(
        r't\s*\(\s*(\d+)\s*\)\s*=\s*([-\d.]+)\s*,\s*p\s*[=<]\s*\.?(\d+\.?\d*)',
        stat_text)
    if m:
        df, t_val, p_str = int(m.group(1)), float(m.group(2)), m.group(3)
        p_reported = _parse_p_value(p_str)
        p_computed = 2 * (1 - scipy_stats.t.cdf(abs(t_val), df))  # two-tailed
        result.update({
            "stat_type": "t",
            "computed_p": round(p_computed, 6),
            "reported_p": p_reported,
            "consistent": _p_consistent(p_computed, p_reported),
            "details": f"t({df})={t_val}, computed p={p_computed:.6f}",
        })
        return result

    # Parse chi-square: χ²(df) = value, p = value
    m = re.search(
        r'[χXx]²?\s*\(\s*(\d+)\s*\)\s*=\s*([\d.]+)\s*,\s*p\s*[=<]\s*\.?(\d+\.?\d*)',
        stat_text)
    if m:
        df, chi_val, p_str = int(m.group(1)), float(m.group(2)), m.group(3)
        p_reported = _parse_p_value(p_str)
        p_computed = 1 - scipy_stats.chi2.cdf(chi_val, df)
        result.update({
            "stat_type": "chi2",
            "computed_p": round(p_computed, 6),
            "reported_p": p_reported,
            "consistent": _p_consistent(p_computed, p_reported),
            "details": f"chi2({df})={chi_val}, computed p={p_computed:.6f}",
        })
        return result

    result["details"] = "Could not parse statistical test"
    return result


def _p_consistent(computed: float, reported: float) -> bool:
    """Check if computed and reported p-values are consistent.

    Uses both significance agreement AND proximity checks.
    A large absolute difference flags inconsistency even when both
    values fall on the same side of alpha=0.05.
    """
    if computed is None or reported is None:
        return True

    # Both significant or both non-significant at alpha=0.05
    same_significance = (computed < 0.05) == (reported < 0.05)

    # Absolute proximity check — catches errors even within same significance band
    abs_diff = abs(computed - reported)
    if reported < 0.001:
        tolerance = 0.0005
    elif reported < 0.01:
        tolerance = 0.002
    else:
        tolerance = 0.01

    close_enough = abs_diff < tolerance

    # Inconsistent if: different significance OR values far apart
    return same_significance and close_enough


# ── Suspicious email detector ────────────────────────────────────────────

PAPER_MILL_DOMAINS = {
    # Chinese free email (commonly associated with mills)
    "163.com", "126.com", "qq.com", "sina.com", "sohu.com",
    "yeah.net", "foxmail.com", "tom.com", "21cn.com",
    # Russian free email
    "yandex.ru", "mail.ru", "rambler.ru", "bk.ru", "list.ru",
    # Known mill-associated domains (from Problematic Paper Screener research)
    "123mi.ru",
    # Generic providers that are suspicious for academic submissions
    "protonmail.com", "tutanota.com",
}

LEGITIMATE_DOMAIN_PATTERNS = [
    ".edu", ".ac.", ".edu.", ".gov", ".org",
    "university", "univ.", "institut",
    "hospital", "clinic", "medical",
    "research", "science", "academy",
]


def check_suspicious_email(email: str) -> tuple[bool, str]:
    """Check if an email address is from a suspicious domain.

    Returns (is_suspicious, reason).
    """
    if not email or "@" not in email:
        return False, "no email"

    domain = email.split("@", 1)[1].lower()

    # Check against known mill domains
    if domain in PAPER_MILL_DOMAINS:
        return True, f"known paper mill domain: {domain}"

    # Check for legitimate patterns
    for pat in LEGITIMATE_DOMAIN_PATTERNS:
        if pat in domain:
            return False, f"legitimate domain pattern: {pat}"

    # Free email providers are somewhat suspicious for academic work
    free_providers = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com"}
    if domain in free_providers:
        return False, f"free provider but not mill: {domain}"

    return False, "unknown domain"


# ── Run signal tests ─────────────────────────────────────────────────────

def run_signal_tests(
    signal_filter: list[str] = None,
    category_filter: list[str] = None,
) -> list[SignalTestResult]:
    """Run all signal-specific test cases."""
    path = DATA_DIR / "signal_test_cases.json"
    if not path.exists():
        logger.error("Signal test cases not found: %s", path)
        return []

    with open(path) as f:
        data = json.load(f)

    results: list[SignalTestResult] = []

    # ── Tortured phrases ──
    if _should_run("tortured_phrases", signal_filter, "text", category_filter):
        for case in data.get("tortured_phrases_cases", []):
            text = case.get("text", "")
            expected = case.get("expected_fired", False)
            found = detect_tortured_phrases(text)
            actual = len(found) > 0

            results.append(SignalTestResult(
                case_id=case["id"],
                signal="tortured_phrases",
                expected_fired=expected,
                actual_fired=actual,
                correct=actual == expected,
                details=f"found={[f[0] for f in found]}" if found else "none found",
            ))

    # ── Suspicious email ──
    if _should_run("suspicious_email", signal_filter, "text", category_filter):
        for case in data.get("suspicious_email_cases", []):
            expected = case.get("expected_fired", False)
            emails = []
            for author in case.get("authors", []):
                email = author.get("email", "")
                if email:
                    emails.append(email)

            suspicious = False
            reason = ""
            for email in emails:
                is_sus, r = check_suspicious_email(email)
                if is_sus:
                    suspicious = True
                    reason = r
                    break

            results.append(SignalTestResult(
                case_id=case["id"],
                signal="suspicious_email",
                expected_fired=expected,
                actual_fired=suspicious,
                correct=suspicious == expected,
                details=reason,
            ))

    # ── GRIM test ──
    if _should_run("grim_test_failure", signal_filter, "stats", category_filter):
        for case in data.get("grim_test_cases", []):
            expected = case.get("expected_fired", False)
            mean = case.get("reported_mean", 0)
            n = case.get("sample_size_n", 1)
            scale = case.get("scale", 1)
            dp = case.get("decimal_places", 2)

            consistent = grim_test(mean, n, scale, dp)
            actual_fired = not consistent  # GRIM fires when inconsistent

            results.append(SignalTestResult(
                case_id=case["id"],
                signal="grim_test_failure",
                expected_fired=expected,
                actual_fired=actual_fired,
                correct=actual_fired == expected,
                details=f"mean={mean}, n={n}, total={mean*n:.4f}, consistent={consistent}",
            ))

    # ── StatCheck ──
    if _should_run("statcheck_error", signal_filter, "stats", category_filter):
        try:
            from scipy import stats as _  # noqa
            for case in data.get("statcheck_cases", []):
                expected = case.get("expected_fired", False)
                stat_text = case.get("stat_text", "")

                check = statcheck_verify(stat_text)
                actual_fired = not check["consistent"]

                results.append(SignalTestResult(
                    case_id=case["id"],
                    signal="statcheck_error",
                    expected_fired=expected,
                    actual_fired=actual_fired,
                    correct=actual_fired == expected,
                    details=check["details"],
                ))
        except ImportError:
            logger.warning("scipy not installed — skipping statcheck tests")

    # ── L1 intent classification ──
    if _should_run("citation_misrepresents_source", signal_filter, "L1", category_filter):
        classifier = IntentClassifier(use_model=False)
        for case in data.get("l1_intent_cases", []):
            sentence = case.get("citing_sentence", "")
            expected_intent = case.get("expected_intent", "mentioning")
            citation_key = case.get("citation_key", "")

            result_intent = classifier.classify(sentence, citation_key)

            # Check if intent matches
            intent_correct = result_intent.intent.value == expected_intent

            results.append(SignalTestResult(
                case_id=case["id"],
                signal="l1_intent_classification",
                expected_fired=True,  # We expect a classification
                actual_fired=True,
                correct=intent_correct,
                details=f"expected={expected_intent}, actual={result_intent.intent.value}, "
                        f"confidence={result_intent.confidence:.2f}, cue='{result_intent.cue_phrase}'",
            ))

    # ── Temporal anomaly (data-only check) ──
    if _should_run("temporal_anomaly", signal_filter, "L3", category_filter):
        for case in data.get("temporal_anomaly_cases", []):
            expected = case.get("expected_fired", False)
            citing_year = int(case.get("citing_paper", {}).get("year", 9999))
            cited_year = int(case.get("cited_paper", {}).get("year", 0))

            actual_fired = citing_year < cited_year  # Citing predates cited

            results.append(SignalTestResult(
                case_id=case["id"],
                signal="temporal_anomaly",
                expected_fired=expected,
                actual_fired=actual_fired,
                correct=actual_fired == expected,
                details=f"citing_year={citing_year}, cited_year={cited_year}",
            ))

    # ── Self-citation (data check) ──
    if _should_run("excessive_self_citation", signal_filter, "L3", category_filter):
        for case in data.get("self_citation_cases", []):
            expected = case.get("expected_fired", False)
            rate = case.get("self_citation_rate", 0)
            actual_fired = rate > 0.30  # Threshold: 30%

            results.append(SignalTestResult(
                case_id=case["id"],
                signal="excessive_self_citation",
                expected_fired=expected,
                actual_fired=actual_fired,
                correct=actual_fired == expected,
                details=f"self_citation_rate={rate:.1%}",
            ))

    return results


def _should_run(signal: str, signal_filter: list[str] | None,
                category: str, category_filter: list[str] | None) -> bool:
    if signal_filter and signal not in signal_filter:
        return False
    if category_filter and category not in category_filter:
        return False
    return True


# ── Reporting ────────────────────────────────────────────────────────────

def print_signal_report(results: list[SignalTestResult]):
    """Print a summary of signal test results."""
    by_signal: dict[str, list[SignalTestResult]] = defaultdict(list)
    for r in results:
        by_signal[r.signal].append(r)

    total_correct = 0
    total_cases = 0
    print("\n" + "=" * 70)
    print("SIGNAL-LEVEL BENCHMARK RESULTS")
    print("=" * 70)

    for signal, cases in sorted(by_signal.items()):
        correct = sum(1 for c in cases if c.correct)
        total = len(cases)
        total_correct += correct
        total_cases += total
        pct = correct / total * 100 if total > 0 else 0

        status = "PASS" if correct == total else "FAIL"
        print(f"\n  {signal:35s}  {correct}/{total} ({pct:.0f}%)  [{status}]")

        for c in cases:
            icon = "+" if c.correct else "X"
            print(f"    [{icon}] {c.case_id:20s} expected_fired={c.expected_fired!s:5s} "
                  f"actual_fired={c.actual_fired!s:5s}  {c.details}")

    print(f"\n{'─' * 70}")
    overall_pct = total_correct / total_cases * 100 if total_cases > 0 else 0
    print(f"  OVERALL: {total_correct}/{total_cases} ({overall_pct:.0f}%)")
    print("=" * 70)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Signal-level benchmark")
    parser.add_argument("--signal", nargs="+", default=None,
                        help="Specific signals to test")
    parser.add_argument("--category", nargs="+", default=None,
                        help="Signal categories: L0, L1, L2, L3, text, stats")
    args = parser.parse_args()

    results = run_signal_tests(
        signal_filter=args.signal,
        category_filter=args.category,
    )

    print_signal_report(results)

    # Exit code
    failed = sum(1 for r in results if not r.correct)
    if failed:
        logger.warning("%d/%d signal tests FAILED", failed, len(results))
        sys.exit(1)
    else:
        logger.info("All %d signal tests PASSED", len(results))


if __name__ == "__main__":
    main()
