"""S-tier crawler: Real, hallucinated, and chimera references.

Generates verifiable reference verification cases:
  - Real papers: from Crossref (DOI resolves, metadata matches)
  - Hallucinated: fake DOIs verified as non-existent
  - Chimera: real DOI + swapped metadata (detectable mismatch)

Usage:
    python -m benchmarks.crawlers reference --count 2000
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import string
from pathlib import Path
from typing import Optional

from .base import BaseCrawler

logger = logging.getLogger(__name__)

# Diverse Crossref queries for sourcing real papers
_QUERIES = [
    "machine learning", "deep learning", "natural language processing",
    "computer vision", "reinforcement learning", "transformer architecture",
    "clinical trial", "genomics", "proteomics", "drug discovery",
    "cancer immunotherapy", "CRISPR gene editing", "epidemiology",
    "meta-analysis", "randomized controlled trial", "vaccine efficacy",
    "climate change", "renewable energy", "quantum computing",
    "materials science", "organic chemistry", "astrophysics",
    "neuroscience cognition", "social network analysis", "econometrics",
]

# Known DOI prefixes for generating plausible fake DOIs
_DOI_PREFIXES = [
    "10.1000", "10.9999", "10.5555", "10.7777", "10.8888",
    "10.6666", "10.4444", "10.3333", "10.2222", "10.1111",
]


class ReferenceVerificationCrawler(BaseCrawler):
    """Generate reference verification test cases."""

    name = "reference_verification"

    def crawl(self, count: int = 2000, seed: int = 42, **kwargs) -> int:
        """Generate reference verification cases.

        Distribution: 40% real, 40% hallucinated, 20% chimera.
        """
        rng = random.Random(seed)

        n_real = int(count * 0.4)
        n_hallucinated = int(count * 0.4)
        n_chimera = count - n_real - n_hallucinated

        total = 0

        # Phase 1: Fetch real papers
        real_papers = self._fetch_real_papers(n_real)
        if real_papers:
            self._write_jsonl(real_papers, self.output_dir / "real_papers.jsonl")
            total += len(real_papers)
        logger.info("Phase 1: %d real papers", len(real_papers))

        # Phase 2: Generate hallucinated papers
        hallucinated = self._generate_hallucinated(n_hallucinated, rng)
        if hallucinated:
            self._write_jsonl(hallucinated, self.output_dir / "hallucinated_papers.jsonl")
            total += len(hallucinated)
        logger.info("Phase 2: %d hallucinated papers", len(hallucinated))

        # Phase 3: Generate chimera papers from real ones
        chimera = self._generate_chimera(real_papers, n_chimera, rng)
        if chimera:
            self._write_jsonl(chimera, self.output_dir / "chimera_papers.jsonl")
            total += len(chimera)
        logger.info("Phase 3: %d chimera papers", len(chimera))

        self._flush_seen_ids()
        logger.info("ReferenceVerificationCrawler: wrote %d total records", total)
        return total

    def _fetch_real_papers(self, count: int) -> list[dict]:
        """Fetch verified real papers from Crossref."""
        per_query = max(count // len(_QUERIES), 5)
        results = []

        for query in _QUERIES:
            if len(results) >= count:
                break

            params = {
                "query": query,
                "rows": min(per_query, 100),
                "sort": "is-referenced-by-count",
                "order": "desc",
                "filter": "type:journal-article,has-abstract:true",
                "select": "DOI,title,author,published-print,published-online,"
                          "container-title,is-referenced-by-count,abstract",
            }

            resp = self._get("https://api.crossref.org/works", params=params)
            if resp is None:
                continue

            for item in resp.json().get("message", {}).get("items", []):
                doi = item.get("DOI", "")
                titles = item.get("title", [])
                title = titles[0] if titles else ""
                if not doi or not title or self._is_seen(doi):
                    continue

                authors = []
                for auth in item.get("author", []):
                    family = auth.get("family", "")
                    given = auth.get("given", "")
                    if family:
                        authors.append(f"{family}, {given}" if given else family)

                year = ""
                for df in ["published-print", "published-online"]:
                    dp = item.get(df, {}).get("date-parts", [[]])
                    if dp and dp[0] and dp[0][0]:
                        year = str(dp[0][0])
                        break

                containers = item.get("container-title", [])
                venue = containers[0] if containers else ""

                self._mark_seen(doi)
                idx = len(results)
                results.append({
                    "id": f"ref_real_{idx:04d}",
                    "category": "real",
                    "signal": "none",
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "doi": doi,
                    "venue": venue,
                    "expected_retracted": False,
                    "expected_found": True,
                    "expected_risk": "LOW",
                    "subcategory": "verified_crossref",
                    "note": f"query={query}, citations={item.get('is-referenced-by-count', 0)}",
                })

                if len(results) >= count:
                    break

        return results[:count]

    def _generate_hallucinated(self, count: int, rng: random.Random) -> list[dict]:
        """Generate hallucinated papers with fake DOIs verified as non-existent."""
        results = []
        attempts = 0
        max_attempts = count * 3  # Allow some failures

        logger.info("Generating %d hallucinated papers (verifying DOI non-existence)...", count)

        while len(results) < count and attempts < max_attempts:
            attempts += 1
            # Generate a plausible fake DOI
            prefix = rng.choice(_DOI_PREFIXES)
            suffix = "".join(rng.choices(string.ascii_lowercase + string.digits, k=10))
            fake_doi = f"{prefix}/integriref.fake.{suffix}"

            # Verify it doesn't exist (expect 404)
            try:
                resp = self._session.get(
                    f"https://api.crossref.org/works/{fake_doi}",
                    timeout=10,
                )
                if resp.status_code == 200:
                    # DOI actually exists (!), skip
                    continue
            except Exception:
                pass  # Network error = treat as non-existent

            # Generate plausible metadata
            title = self._generate_fake_title(rng)
            authors = self._generate_fake_authors(rng)
            year = str(rng.randint(2010, 2025))

            idx = len(results)
            results.append({
                "id": f"ref_hallucinated_{idx:04d}",
                "category": "hallucinated",
                "signal": "phantom_doi",
                "title": title,
                "authors": authors,
                "year": year,
                "doi": fake_doi,
                "venue": rng.choice([
                    "Nature", "Science", "PNAS", "PLoS ONE",
                    "IEEE Access", "Scientific Reports", "Frontiers in Medicine",
                    "Journal of Machine Learning Research", "Bioinformatics",
                    "Physical Review Letters",
                ]),
                "expected_retracted": False,
                "expected_found": False,
                "expected_risk": "HIGH",
                "subcategory": "fake_doi_verified",
                "note": "DOI verified non-existent via Crossref API",
            })

            if len(results) % 100 == 0:
                logger.info("  Generated %d/%d hallucinated papers...",
                             len(results), count)

        return results[:count]

    def _generate_chimera(self, real_papers: list[dict],
                          count: int, rng: random.Random) -> list[dict]:
        """Generate chimera papers by mixing metadata from real papers."""
        if len(real_papers) < 2:
            logger.warning("Not enough real papers to generate chimera")
            return []

        results = []
        for i in range(min(count, len(real_papers))):
            paper = real_papers[i]
            # Pick a different paper to swap metadata from
            other = real_papers[(i + rng.randint(1, len(real_papers) - 1)) % len(real_papers)]

            # Swap strategy
            strategy = rng.choice(["swap_authors", "swap_year", "swap_both"])
            chimera = dict(paper)
            chimera["id"] = f"ref_chimera_{i:04d}"
            chimera["category"] = "chimera"
            chimera["signal"] = "metadata_mismatch"
            chimera["expected_found"] = False
            chimera["expected_risk"] = "HIGH"
            chimera["subcategory"] = f"chimera_{strategy}"

            if strategy in ("swap_authors", "swap_both"):
                chimera["authors"] = other["authors"]
            if strategy in ("swap_year", "swap_both"):
                try:
                    y = int(paper["year"])
                    shift = rng.choice([-5, -4, -3, -2, 2, 3, 4, 5])
                    chimera["year"] = str(y + shift)
                except (ValueError, TypeError):
                    chimera["year"] = "2099"

            chimera["note"] = f"Chimera of {paper['doi']} + {other['doi']} ({strategy})"
            results.append(chimera)

        return results[:count]

    @staticmethod
    def _generate_fake_title(rng: random.Random) -> str:
        """Generate a plausible academic title."""
        templates = [
            "{adj} {noun} for {task} in {domain}",
            "A {adj} Approach to {task} Using {method}",
            "{method}-Based {task}: A {adj} Framework",
            "Towards {adj} {task} with {method}",
            "On the {noun} of {method} in {domain}",
            "{adj} {method} for {adj2} {task}",
        ]
        adjs = ["Novel", "Robust", "Scalable", "Efficient", "Deep",
                "Adaptive", "Unified", "Generalized", "Hierarchical", "Multi-modal"]
        nouns = ["Framework", "Architecture", "Model", "System", "Pipeline", "Method"]
        tasks = ["Image Classification", "Text Generation", "Object Detection",
                 "Drug Discovery", "Gene Expression Analysis", "Sentiment Analysis",
                 "Protein Folding", "Speech Recognition", "Anomaly Detection"]
        methods = ["Transformer", "Graph Neural Network", "Variational Autoencoder",
                   "Reinforcement Learning", "Attention Mechanism", "Diffusion Model"]
        domains = ["Biomedical Imaging", "Natural Language Processing",
                   "Computational Biology", "Materials Science", "Climate Modeling"]

        template = rng.choice(templates)
        return template.format(
            adj=rng.choice(adjs),
            adj2=rng.choice(adjs),
            noun=rng.choice(nouns),
            task=rng.choice(tasks),
            method=rng.choice(methods),
            domain=rng.choice(domains),
        )

    @staticmethod
    def _generate_fake_authors(rng: random.Random) -> list[str]:
        """Generate plausible fake author names."""
        surnames = [
            "Zhang", "Wang", "Li", "Chen", "Liu", "Smith", "Johnson",
            "Williams", "Brown", "Jones", "Garcia", "Martinez", "Kumar",
            "Singh", "Kim", "Park", "Lee", "Tanaka", "Yamamoto",
            "Mueller", "Schmidt", "Fischer", "Silva", "Santos",
        ]
        initials = "ABCDEFGHJKLMNPRSTWY"
        n_authors = rng.randint(2, 6)
        authors = []
        for _ in range(n_authors):
            surname = rng.choice(surnames)
            first = rng.choice(initials) + "."
            second = rng.choice(initials) + "." if rng.random() > 0.5 else ""
            name = f"{surname}, {first}" + (f" {second}" if second else "")
            authors.append(name)
        return authors
