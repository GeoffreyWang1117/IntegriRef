"""Email domain risk assessment for paper mill detection.

Detects suspicious author email patterns associated with paper mills:
- Free email services (Gmail, Yahoo, QQ, 163.com, etc.) for corresponding authors
- Known paper mill domains
- Domain patterns matching mill fronts (fake hospital/university domains)
- Numbered/random-looking email addresses
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EmailRiskResult:
    """Structured result for a single email risk assessment."""

    email: str
    risk_score: float  # 0-100
    risk_level: str  # "low", "medium", "high"
    flags: list[str] = field(default_factory=list)
    domain: str = ""
    is_free_provider: bool = False
    is_known_mill_domain: bool = False


# ---------------------------------------------------------------------------
# Free email provider list (50+)
# ---------------------------------------------------------------------------

_FREE_PROVIDERS: frozenset[str] = frozenset({
    # Global
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.co.in",
    "yahoo.co.jp",
    "yahoo.fr",
    "yahoo.de",
    "yahoo.es",
    "yahoo.it",
    "hotmail.com",
    "hotmail.co.uk",
    "hotmail.fr",
    "hotmail.de",
    "outlook.com",
    "outlook.fr",
    "outlook.de",
    "live.com",
    "live.co.uk",
    "msn.com",
    "aol.com",
    "protonmail.com",
    "protonmail.ch",
    "proton.me",
    "zoho.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "web.de",
    "mail.com",
    "email.com",
    "ymail.com",
    "rocketmail.com",
    "tutanota.com",
    "tuta.io",
    "fastmail.com",
    "hushmail.com",
    # Chinese
    "qq.com",
    "163.com",
    "126.com",
    "foxmail.com",
    "sina.com",
    "sina.cn",
    "sohu.com",
    "aliyun.com",
    "yeah.net",
    "tom.com",
    "139.com",
    "189.cn",
    # Russian / Eastern Europe
    "yandex.ru",
    "yandex.com",
    "mail.ru",
    "bk.ru",
    "list.ru",
    "inbox.ru",
    "rambler.ru",
    "ukr.net",
    # India
    "rediffmail.com",
    # Japan
    "excite.co.jp",
    # Korea
    "naver.com",
    "daum.net",
    "hanmail.net",
    # Latin America
    "bol.com.br",
    "uol.com.br",
    "terra.com.br",
})

# ---------------------------------------------------------------------------
# Known paper mill domains (based on Retraction Watch / PPS reports)
# ---------------------------------------------------------------------------

_KNOWN_MILL_DOMAINS: frozenset[str] = frozenset({
    # Documented paper mill fronts and associated domains
    # (curated from Retraction Watch reports, PPS investigations,
    #  and published analyses of paper mill activity)
    "internationalpublicationhouse.com",
    "editorialsupport.org",
    "researchassist.net",
    "scientificmanuscripts.com",
    "academicservicesgroup.com",
    "papermill-support.com",
    "manuscriptfactory.com",
    "fastpublish.net",
    "rapidresearchpapers.com",
    "journalsubmission.services",
    "submitpaper.online",
    "pubmedresearch.com",
    "globalresearchhub.com",
    "academicwritingcenter.net",
    "publicationhelpers.com",
    "sciencepaperfactory.com",
    "researchpapershop.com",
    "medicalwritingservices.net",
    "hospitalresearchcenter.com",
    "clinicalpapermill.com",
    "papermills.cn",
    "cheappapers.science",
    "ghostresearch.org",
    "authorship4sale.com",
    "fakeclinical.com",
})

# ---------------------------------------------------------------------------
# Suspicious domain patterns
# ---------------------------------------------------------------------------

# Domains that look institutional but use .com — common mill front tactic.
# Legitimate hospitals/universities almost always use .edu, .ac.xx, .gov, .org,
# or country-specific institutional TLDs.
_INSTITUTIONAL_KEYWORDS = re.compile(
    r"(?:hospital|medical|clinic|health|pharma|university|college|"
    r"institute|research|academy|laboratoire|klinik|"
    r"krankenhaus|hopital|centro|fakultas|"
    r"oncolog|cardio|neurol|pediatr|orthopaed|surg)",
    re.IGNORECASE,
)

_INSTITUTIONAL_TLDS = frozenset({
    ".edu", ".ac.", ".gov", ".mil", ".org",
    ".edu.", ".res.in", ".ernet.in", ".nic.in",
})

# Numbered email pattern: local part ends with or is mostly digits.
_NUMBERED_PATTERN = re.compile(
    r"^[a-z]{0,6}\d{3,}@",  # e.g. author123@, wang12345@, a001@
    re.IGNORECASE,
)

# Random-looking local part: consonant clusters without vowels, or very short
# with high digit ratio — heuristic for auto-generated addresses.
_RANDOM_PATTERN = re.compile(
    r"^(?:[bcdfghjklmnpqrstvwxyz]{5,}|"  # 5+ consonants, no vowels
    r"[a-z\d]{2,4}\d{4,}|"               # short prefix + many digits
    r"\d{6,})@",                          # 6+ digits only
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class EmailRiskDetector:
    """Assess paper mill risk based on author email addresses.

    Parameters
    ----------
    extra_mill_domains : set[str], optional
        Additional domains to treat as known paper mill fronts.
    """

    def __init__(self, extra_mill_domains: set[str] | None = None):
        self._mill_domains: frozenset[str] = (
            _KNOWN_MILL_DOMAINS | frozenset(extra_mill_domains)
            if extra_mill_domains
            else _KNOWN_MILL_DOMAINS
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(email: str) -> tuple[str, str]:
        """Return (local_part, domain) from an email, lowercased."""
        email = email.strip().lower()
        if "@" not in email:
            return (email, "")
        local, _, domain = email.partition("@")
        return (local, domain)

    @staticmethod
    def _is_institutional_tld(domain: str) -> bool:
        """Check if a domain uses a recognised institutional TLD.

        Uses segment-aware matching: ".edu" matches "mit.edu" but not
        "reduced.com"; ".ac." matches "ox.ac.uk" but not "acme.com".
        """
        # Split domain into dot-separated segments for precise matching
        parts = domain.split(".")
        for tld in _INSTITUTIONAL_TLDS:
            tld_parts = [p for p in tld.split(".") if p]
            if not tld_parts:
                continue
            # Check if the TLD segments appear consecutively in the domain
            for i in range(len(parts) - len(tld_parts) + 1):
                if parts[i:i + len(tld_parts)] == tld_parts:
                    return True
        return False

    @staticmethod
    def _looks_institutional(domain: str) -> bool:
        """True if the domain name contains hospital/university keywords."""
        return bool(_INSTITUTIONAL_KEYWORDS.search(domain))

    @staticmethod
    def _risk_level(score: float) -> str:
        if score >= 60:
            return "high"
        if score >= 30:
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(self, email: str) -> EmailRiskResult:
        """Assess risk of a single email address."""
        local, domain = self._parse(email)

        if not domain:
            return EmailRiskResult(
                email=email,
                risk_score=0.0,
                risk_level="low",
                flags=["invalid_email_no_domain"],
                domain="",
                is_free_provider=False,
                is_known_mill_domain=False,
            )

        score = 0.0
        flags: list[str] = []
        is_free = domain in _FREE_PROVIDERS
        is_mill = domain in self._mill_domains

        # --- Known mill domain (highest signal) ---
        if is_mill:
            score += 80
            flags.append("known_paper_mill_domain")

        # --- Free provider ---
        if is_free:
            score += 20
            flags.append("free_email_provider")

        # --- Numbered / auto-generated address ---
        full = f"{local}@"
        if _NUMBERED_PATTERN.match(full):
            score += 15
            flags.append("numbered_email_pattern")
        elif _RANDOM_PATTERN.match(full):
            score += 15
            flags.append("random_looking_address")

        # --- Fake institutional domain (hospital/university on .com) ---
        if (
            self._looks_institutional(domain)
            and not self._is_institutional_tld(domain)
            and not is_mill  # avoid double-counting
        ):
            score += 25
            flags.append("institutional_keyword_non_institutional_tld")

        # Cap at 100.
        score = min(score, 100.0)

        return EmailRiskResult(
            email=email.strip().lower(),
            risk_score=score,
            risk_level=self._risk_level(score),
            flags=flags,
            domain=domain,
            is_free_provider=is_free,
            is_known_mill_domain=is_mill,
        )

    def assess_batch(self, emails: list[str]) -> list[EmailRiskResult]:
        """Assess multiple emails independently."""
        return [self.assess(e) for e in emails]

    def assess_author_list(self, emails: list[str]) -> dict:
        """Assess a paper's author email list.

        Returns an aggregate summary plus individual results:

        .. code-block:: python

            {
                "aggregate_risk_score": float,  # 0-100
                "aggregate_risk_level": str,
                "aggregate_flags": list[str],
                "individual_results": list[EmailRiskResult],
                "free_email_count": int,
                "mill_domain_count": int,
                "total_authors": int,
            }
        """
        if not emails:
            return {
                "aggregate_risk_score": 0.0,
                "aggregate_risk_level": "low",
                "aggregate_flags": [],
                "individual_results": [],
                "free_email_count": 0,
                "mill_domain_count": 0,
                "total_authors": 0,
            }

        results = self.assess_batch(emails)

        free_count = sum(1 for r in results if r.is_free_provider)
        mill_count = sum(1 for r in results if r.is_known_mill_domain)
        agg_flags: list[str] = []

        # Start from the max individual score as a baseline.
        agg_score = max(r.risk_score for r in results)

        # Multiple free emails in author list: +10 per additional beyond 1.
        if free_count > 1:
            bonus = 10 * (free_count - 1)
            agg_score += bonus
            agg_flags.append(
                f"multiple_free_emails({free_count})"
            )

        # All authors share the same free provider.
        free_domains = [r.domain for r in results if r.is_free_provider]
        if (
            len(free_domains) >= 2
            and len(set(free_domains)) == 1
            and len(free_domains) == len(results)
        ):
            agg_score += 15
            agg_flags.append(
                f"all_authors_same_free_provider({free_domains[0]})"
            )

        # Multiple known mill domains — extra flag.
        if mill_count > 1:
            agg_flags.append(f"multiple_mill_domains({mill_count})")

        agg_score = min(agg_score, 100.0)

        return {
            "aggregate_risk_score": agg_score,
            "aggregate_risk_level": self._risk_level(agg_score),
            "aggregate_flags": agg_flags,
            "individual_results": results,
            "free_email_count": free_count,
            "mill_domain_count": mill_count,
            "total_authors": len(results),
        }
