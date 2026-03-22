"""IntegriRef dataset crawlers.

Registry of all available crawlers and CLI entry point.

Usage:
    python -m benchmarks.crawlers --list
    python -m benchmarks.crawlers retracted --count 10000
    python -m benchmarks.crawlers reference --count 2000
    python -m benchmarks.crawlers pmc --count 5000
    python -m benchmarks.crawlers grim --count 1000
    python -m benchmarks.crawlers statcheck --count 2000
    python -m benchmarks.crawlers temporal --count 2000
    python -m benchmarks.crawlers scifact
    python -m benchmarks.crawlers all-s           # S-tier only
    python -m benchmarks.crawlers all             # S + A tier
"""

from __future__ import annotations

from .retracted_papers import RetractedPapersCrawler
from .reference_verification import ReferenceVerificationCrawler
from .pmc_fulltext import PMCFullTextCrawler
from .grim_crawler import GRIMCrawler
from .statcheck_crawler import StatCheckCrawler
from .temporal_anomaly import TemporalAnomalyCrawler
from .scifact_converter import SciFActConverter

CRAWLERS = {
    # S-tier (ground truth)
    "retracted": RetractedPapersCrawler,
    "reference": ReferenceVerificationCrawler,
    # A-tier (deterministic signals)
    "pmc": PMCFullTextCrawler,
    "grim": GRIMCrawler,
    "statcheck": StatCheckCrawler,
    "temporal": TemporalAnomalyCrawler,
    "scifact": SciFActConverter,
}

S_TIER = ["retracted", "reference"]
A_TIER = ["pmc", "grim", "statcheck", "temporal", "scifact"]

__all__ = [
    "CRAWLERS", "S_TIER", "A_TIER",
    "RetractedPapersCrawler", "ReferenceVerificationCrawler",
    "PMCFullTextCrawler", "GRIMCrawler", "StatCheckCrawler",
    "TemporalAnomalyCrawler", "SciFActConverter",
]
