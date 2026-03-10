"""Abstract fetcher — retrieves abstracts for cited papers.

Uses a cascade: S2 → Europe PMC → PubMed → OpenAlex.
Needed for L2 claim-abstract alignment verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.discovery import RegistryDiscovery
from core.entity import ICEntity


@dataclass
class AbstractResult:
    """Result of abstract fetching."""
    abstract: str
    source: str
    entity: ICEntity
    title: str = ""


class AbstractFetcher:
    """Fetch abstracts using available registries."""

    ABSTRACT_SOURCES = [
        "semantic_scholar",
        "europe_pmc",
        "pubmed",
        "openalex",
    ]

    def __init__(self, discovery: RegistryDiscovery):
        self._discovery = discovery

    def fetch_abstract(self, identifier: str = "", title: str = "",
                       author: str = "", year: str = "") -> Optional[AbstractResult]:
        """Fetch abstract via ID lookup first, then title search."""
        entities = []

        if identifier:
            entities = self._discovery.query_by_id(identifier)

        if not entities and title:
            entities = self._discovery.search(
                title, author=author, year=year,
                domains=["academic"]
            )

        if not entities:
            return None

        for entity in entities:
            abstract = entity.metadata.get("abstract", "")
            if abstract and len(abstract) > 50:
                return AbstractResult(
                    abstract=abstract,
                    source=entity.source_registries[0] if entity.source_registries else "unknown",
                    entity=entity,
                    title=entity.title,
                )

        return None

    def fetch_abstracts_batch(self, references: list[dict]) -> dict[str, Optional[AbstractResult]]:
        """Fetch abstracts for multiple references."""
        results = {}
        for ref in references:
            key = ref.get("identifier") or ref.get("title", "")
            results[key] = self.fetch_abstract(
                identifier=ref.get("identifier", ""),
                title=ref.get("title", ""),
                author=ref.get("author", ""),
                year=ref.get("year", ""),
            )
        return results
