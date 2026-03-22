"""Registry discovery — find and query the right registries for any reference.

This is the intelligence layer that decides which registries to query
based on identifier type, entity type, and domain signals.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .entity import ICEntity
from .registry import RegistryAdapter

logger = logging.getLogger(__name__)


class RegistryDiscovery:
    """Automatically routes verification queries to the right registries."""

    def __init__(self):
        self._registries: list[RegistryAdapter] = []
        self._id_type_map: dict[str, list[RegistryAdapter]] = {}
        self._domain_map: dict[str, list[RegistryAdapter]] = {}

    def register(self, adapter: RegistryAdapter):
        """Add a registry adapter to the discovery pool."""
        self._registries.append(adapter)
        info = adapter.info()
        self._domain_map.setdefault(info.domain, []).append(adapter)

    def register_all(self, adapters: list[type[RegistryAdapter]]):
        """Register multiple adapter classes (instantiates each)."""
        for cls in adapters:
            self.register(cls())

    def list_registries(self) -> list[dict]:
        """List all registered adapters with their info."""
        return [
            {
                "name": a.info().name,
                "domain": a.info().domain,
                "coverage": a.info().coverage,
                "auth": a.info().auth_type,
                "rate_limit": a.info().rate_limit,
            }
            for a in self._registries
        ]

    # ── Identifier detection ──────────────────────────────────────────────

    @staticmethod
    def detect_id_type(identifier: str) -> Optional[str]:
        """Auto-detect the type of an identifier string."""
        s = identifier.strip()

        # DOI: 10.xxxx/...
        if re.match(r"^10\.\d{4,}/", s):
            return "doi"

        # arXiv: 2301.12345 or hep-th/0401234
        if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", s):
            return "arxiv"
        if re.match(r"^[a-z\-]+/\d{7}$", s):
            return "arxiv"

        # PMID: pure digits, 1-9 digits
        if re.match(r"^\d{1,9}$", s) and len(s) <= 9:
            return "pmid"

        # Patent: US1234567, EP1234567, WO2023/012345
        if re.match(r"^(US|EP|WO|CN|JP|KR|DE|FR|GB)\s*\d", s, re.IGNORECASE):
            return "patent_number"

        # RFC: RFC 1234 or rfc1234
        if re.match(r"^rfc\s*\d+$", s, re.IGNORECASE):
            return "rfc_number"

        # CVE: CVE-2023-12345
        if re.match(r"^CVE-\d{4}-\d+$", s, re.IGNORECASE):
            return "cve"

        # CELEX (EU law): 32022R2065
        if re.match(r"^\d[0-9]{4}[A-Z]\d+$", s):
            return "celex"

        # ISO: ISO 12345 or ISO/IEC 27001
        if re.match(r"^ISO", s, re.IGNORECASE):
            return "iso_number"

        # ISBN: 10 or 13 digits (possibly with hyphens)
        isbn = re.sub(r"[\s\-]", "", s)
        if re.match(r"^(97[89])?\d{9}[\dXx]$", isbn):
            return "isbn"

        # Case citation: 410 U.S. 113
        if re.match(r"^\d+\s+[A-Z]\.\S*\s+\d+", s):
            return "case_cite"

        # ORCID: 0000-0000-0000-0000
        if re.match(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$", s):
            return "orcid"

        # CIK (SEC): 10-digit number starting with 00
        if re.match(r"^0{2,}\d+$", s) and len(s) == 10:
            return "cik"

        return None

    # ── Smart query routing ───────────────────────────────────────────────

    def query_by_id(self, identifier: str,
                    id_type: Optional[str] = None) -> list[ICEntity]:
        """Query all relevant registries for a given identifier.

        Auto-detects ID type if not provided.
        Returns all matches from all registries (for cross-validation).
        """
        if id_type is None:
            id_type = self.detect_id_type(identifier)
        if id_type is None:
            return []

        results = []
        for adapter in self._registries:
            entity = adapter.query_by_id(id_type, identifier)
            if entity:
                results.append(entity)
        return results

    def search(self, title: str, author: str = "", year: str = "",
               domains: list[str] = None, **kwargs) -> list[ICEntity]:
        """Search across registries (optionally filtered by domain).

        Args:
            domains: e.g. ["academic", "patents"] to limit search scope
        """
        results = []
        for adapter in self._registries:
            info = adapter.info()
            if domains and info.domain not in domains:
                continue
            hits = adapter.search(title, author=author, year=year, **kwargs)
            results.extend(hits)
        return results

    # ── Parallel query methods ─────────────────────────────────────────

    def parallel_query_by_id(self, id_type: str, id_value: str,
                              adapters: list[RegistryAdapter] | None = None,
                              max_workers: int = 8,
                              timeout: float = 10.0) -> list[tuple[RegistryAdapter, ICEntity]]:
        """Query matching adapters in parallel. Returns (adapter, entity) pairs.

        Args:
            id_type: Identifier type (e.g. "doi", "arxiv", "pmid")
            id_value: The identifier value
            adapters: Specific adapters to query (defaults to all registered)
            max_workers: Max concurrent threads
            timeout: Overall timeout in seconds

        Returns:
            List of (adapter, entity) tuples for successful lookups.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        targets = adapters if adapters is not None else self._registries
        if not targets:
            return []

        results = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
            futures = {
                executor.submit(adapter.query_by_id, id_type, id_value): adapter
                for adapter in targets
            }
            try:
                for future in as_completed(futures, timeout=timeout):
                    adapter = futures[future]
                    try:
                        entity = future.result(timeout=1.0)
                        if entity:
                            results.append((adapter, entity))
                    except Exception as exc:
                        logger.debug("Adapter %s failed for %s=%s: %s",
                                     adapter.info().name, id_type, id_value, exc)
                        continue
            except TimeoutError:
                logger.warning("parallel_query_by_id timed out after %.1fs",
                               timeout)
                for f in futures:
                    f.cancel()
        return results

    def parallel_search(self, title: str, author: str = "", year: str = "",
                         adapters: list[RegistryAdapter] | None = None,
                         domains: list[str] | None = None,
                         max_workers: int = 8,
                         timeout: float = 15.0) -> list[tuple[RegistryAdapter, list[ICEntity]]]:
        """Search across registries in parallel.

        Args:
            title: Search title
            author: Author filter
            year: Year filter
            adapters: Specific adapters to query (defaults to all registered)
            domains: Domain filter (only used when adapters is None)
            max_workers: Max concurrent threads
            timeout: Overall timeout in seconds

        Returns:
            List of (adapter, hits) tuples for adapters that returned results.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if adapters is not None:
            targets = adapters
        else:
            targets = self._registries
            if domains:
                targets = [a for a in targets if a.info().domain in domains]

        if not targets:
            return []

        def _do_search(adapter):
            return adapter.search(title, author=author, year=year)

        results = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
            futures = {
                executor.submit(_do_search, adapter): adapter
                for adapter in targets
            }
            try:
                for future in as_completed(futures, timeout=timeout):
                    adapter = futures[future]
                    try:
                        hits = future.result(timeout=1.0)
                        if hits:
                            results.append((adapter, hits))
                    except Exception as exc:
                        logger.debug("Search failed for %s: %s",
                                     adapter.info().name, exc)
                        continue
            except TimeoutError:
                logger.warning("parallel_search timed out after %.1fs "
                               "(%d results collected)", timeout, len(results))
                for f in futures:
                    f.cancel()
        return results

    def verify_reference(self, title: str = "", identifier: str = "",
                         author: str = "", year: str = "",
                         domains: list[str] = None) -> dict:
        """High-level verification: try ID lookup first, then search.

        Returns {
            "found": bool,
            "matches": list[ICEntity],
            "registries_tried": list[str],
            "registries_hit": list[str],
        }
        """
        registries_tried = []
        registries_hit = []
        matches = []

        # Step 1: ID-based lookup (fast, precise)
        if identifier:
            id_type = self.detect_id_type(identifier)
            if id_type:
                for adapter in self._registries:
                    info = adapter.info()
                    if domains and info.domain not in domains:
                        continue
                    registries_tried.append(info.name)
                    entity = adapter.query_by_id(id_type, identifier)
                    if entity:
                        registries_hit.append(info.name)
                        matches.append(entity)

        # Step 2: Title-based search (slower, broader)
        if not matches and title:
            for adapter in self._registries:
                info = adapter.info()
                if domains and info.domain not in domains:
                    continue
                if info.name not in registries_tried:
                    registries_tried.append(info.name)
                hits = adapter.search(title, author=author, year=year)
                if hits:
                    registries_hit.append(info.name)
                    matches.extend(hits)

        return {
            "found": len(matches) > 0,
            "matches": matches,
            "registries_tried": registries_tried,
            "registries_hit": registries_hit,
        }
