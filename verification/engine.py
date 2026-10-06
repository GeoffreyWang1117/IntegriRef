"""Unified verification engine — multi-registry cascade with metadata validation.

This replaces the monolithic refcheck.py with a modular engine that leverages
all 38+ RegistryAdapter instances through RegistryDiscovery.

Key capabilities:
  - Multi-registry cascade verification (ID-based → search-based)
  - Intelligent registry ordering (domain-aware priority)
  - Cross-registry metadata consistency checking
  - Retraction detection (CrossRef update-to, OpenAlex is_retracted)
  - AI hallucination scoring
  - Structured VerificationReport output
  - Per-source confidence-weighted match evaluation
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from core.entity import ICEntity
from core.discovery import RegistryDiscovery
from .field_comparator import (
    FieldComparator, token_similarity, strip_latex,
    token_set_ratio, author_name_similarity, strip_accents,
)
from .metadata_validator import MetadataValidator, MetadataValidationReport
from .hallucination_detector import HallucinationDetector, HallucinationReport


@dataclass
class FieldCheck:
    """A single field comparison result."""
    field: str      # title, authors, year, venue, retraction
    status: str     # OK, WARN, FAIL
    detail: str
    source: str = ""
    score: float = 0.0  # 0-1 raw similarity score for this field


@dataclass
class Provenance:
    """Structured audit trail for a single verification decision."""
    timestamp: float = 0.0
    registry: str = ""
    query_type: str = ""   # "id_lookup" or "search"
    query_params: dict = field(default_factory=dict)
    found: bool = False
    match_score: float = 0.0
    field_scores: dict = field(default_factory=dict)  # {field: score}
    decision: str = ""     # OK, WARN, FAIL

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "registry": self.registry,
            "query_type": self.query_type,
            "query_params": self.query_params,
            "found": self.found,
            "match_score": round(self.match_score, 3),
            "field_scores": {k: round(v, 3) for k, v in self.field_scores.items()},
            "decision": self.decision,
        }


@dataclass
class CompositeScore:
    """Composite reference-level confidence score (spec formula).

    overall = 0.35*existence + 0.25*title + 0.15*author
              + 0.10*venue + 0.15*context_intent
    """
    existence: float = 0.0    # 0-1: found in registry
    title_sim: float = 0.0    # 0-1: best title match
    author_sim: float = 0.0   # 0-1: best author match
    year_penalty: float = 1.0  # max(0, 1 - 0.5*|year_diff|)
    venue_sim: float = 0.0    # 0-1: venue match
    context_intent: float = 0.5  # 1=support, 0.5=neutral, 0=contradiction

    W_EXIST = 0.35
    W_TITLE = 0.25
    W_AUTHOR = 0.15
    W_VENUE = 0.10
    W_INTENT = 0.15

    @property
    def overall(self) -> float:
        return (self.W_EXIST * self.existence +
                self.W_TITLE * self.title_sim +
                self.W_AUTHOR * self.author_sim +
                self.W_VENUE * self.venue_sim +
                self.W_INTENT * self.context_intent)

    @property
    def label(self) -> str:
        s = self.overall
        if s >= 0.85:
            return "supported"
        elif s >= 0.60:
            return "possible"
        return "suspicious"

    def to_dict(self) -> dict:
        return {
            "existence": round(self.existence, 3),
            "title_sim": round(self.title_sim, 3),
            "author_sim": round(self.author_sim, 3),
            "year_penalty": round(self.year_penalty, 3),
            "venue_sim": round(self.venue_sim, 3),
            "context_intent": round(self.context_intent, 3),
            "overall": round(self.overall, 3),
            "label": self.label,
        }


@dataclass
class SourceMatch:
    """Match result from a single registry source."""
    registry: str
    found: bool = False
    found_by_id: bool = False
    entity: Optional[ICEntity] = None
    checks: list[FieldCheck] = field(default_factory=list)
    match_score: float = 0.0  # 0-100 aggregate
    is_retracted: bool = False

    @property
    def is_confirmed(self) -> bool:
        """A source confirms a reference if title + author both OK."""
        title_ok = any(c.field == "title" and c.status == "OK"
                       for c in self.checks)
        author_ok = any(c.field == "authors" and c.status in ("OK", "WARN")
                        for c in self.checks)
        return title_ok and author_ok


@dataclass
class ReferenceResult:
    """Complete verification result for a single reference."""
    key: str = ""
    title: str = ""
    overall: str = "PENDING"  # OK, WARN, FAIL
    sources_tried: list[str] = field(default_factory=list)
    sources_hit: list[str] = field(default_factory=list)
    source_matches: list[SourceMatch] = field(default_factory=list)
    all_checks: list[FieldCheck] = field(default_factory=list)
    is_retracted: bool = False
    suggested_fixes: dict = field(default_factory=dict)
    consistency: Optional[MetadataValidationReport] = None
    hallucination: Optional[HallucinationReport] = None
    composite: Optional[CompositeScore] = None
    provenance: list[Provenance] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # Top-K candidates
    intent: str = ""  # support/mention/contrast/uncertain
    needs_l2_escalation: bool = False

    @property
    def is_confirmed(self) -> bool:
        return any(m.is_confirmed for m in self.source_matches)

    @property
    def best_match_score(self) -> float:
        if not self.source_matches:
            return 0.0
        return max(m.match_score for m in self.source_matches)

    def summary(self) -> dict:
        return {
            "key": self.key,
            "title": self.title[:80],
            "overall": self.overall,
            "sources_tried": len(self.sources_tried),
            "sources_hit": len(self.sources_hit),
            "is_retracted": self.is_retracted,
            "best_score": round(self.best_match_score, 1),
            "composite_score": (
                round(self.composite.overall, 3)
                if self.composite else None),
            "composite_label": (
                self.composite.label if self.composite else None),
            "intent": self.intent or None,
            "needs_l2": self.needs_l2_escalation,
            "hallucination_score": (
                round(self.hallucination.hallucination_score, 1)
                if self.hallucination else None),
            "candidates": len(self.candidates),
        }

    def api_response(self) -> dict:
        """Structured API contract response (spec section IV)."""
        existence_status = "not_found"
        if self.sources_hit:
            if self.is_confirmed:
                existence_status = "found"
            else:
                existence_status = "ambiguous"

        return {
            "citation_id": self.key,
            "existence": {
                "status": existence_status,
                "sources": self.sources_hit,
                "candidates": self.candidates[:3],
            },
            "scores": self.composite.to_dict() if self.composite else {},
            "intent": self.intent or "uncertain",
            "is_retracted": self.is_retracted,
            "provenance": [p.to_dict() for p in self.provenance],
        }


@dataclass
class VerificationReport:
    """Aggregate report for batch verification."""
    results: list[ReferenceResult] = field(default_factory=list)
    total: int = 0
    ok_count: int = 0
    warn_count: int = 0
    fail_count: int = 0
    retracted_count: int = 0
    hallucinated_count: int = 0
    registries_used: set = field(default_factory=set)

    @property
    def coverage(self) -> float:
        """Percentage of references that matched at least one registry."""
        if self.total == 0:
            return 100.0
        matched = sum(1 for r in self.results if r.sources_hit)
        return (matched / self.total) * 100

    def summary(self) -> dict:
        return {
            "total": self.total,
            "ok": self.ok_count,
            "warn": self.warn_count,
            "fail": self.fail_count,
            "coverage": round(self.coverage, 1),
            "retracted": self.retracted_count,
            "likely_hallucinated": self.hallucinated_count,
            "registries_used": sorted(self.registries_used),
        }


class VerificationEngine:
    """Multi-registry cascade verification engine.

    Usage:
        discovery = RegistryDiscovery()
        discovery.register_all(ALL_ACADEMIC + ALL_PATENTS + ...)
        engine = VerificationEngine(discovery)

        # Verify a single reference
        result = engine.verify_reference({
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", ...],
            "year": "2017",
            "doi": "10.5555/3295222.3295349",
        })

        # Batch verify
        report = engine.verify_batch(references)
    """

    # Registry priority order by domain for search queries
    ACADEMIC_PRIORITY = [
        "crossref", "semantic_scholar", "openalex", "dblp",
        "arxiv", "pubmed", "europe_pmc", "core",
    ]

    def __init__(self, discovery: RegistryDiscovery,
                 enable_hallucination_check: bool = True,
                 enable_consistency_check: bool = True,
                 enable_cache: bool = True):
        self._discovery = discovery
        self._comparator = FieldComparator()
        self._meta_validator = MetadataValidator()
        self._hallucination_detector = HallucinationDetector()
        self._enable_hallucination = enable_hallucination_check
        self._enable_consistency = enable_consistency_check

        # Response cache (in-memory LRU, optional Redis)
        self._cache = None
        if enable_cache:
            try:
                import os
                from core.cache import ResponseCache
                redis_url = os.getenv("REDIS_URL", "")
                self._cache = ResponseCache(redis_url=redis_url)
            except Exception:
                pass

    def verify_reference(self, ref: dict,
                         domains: list[str] = None,
                         parallel: bool = True) -> ReferenceResult:
        """Verify a single reference against all available registries.

        Args:
            ref: Dict with keys: title, authors (list or str), year, doi,
                 arxiv_id, venue/journal/booktitle, key (bib key)
            domains: Optional domain filter (e.g., ["academic"])
            parallel: Use parallel I/O for registry queries (default True).
                      Set to False for sequential execution.

        Returns:
            ReferenceResult with detailed verification info.
        """
        result = ReferenceResult(
            key=ref.get("key", ""),
            title=ref.get("title", ""),
        )

        # ── Cache lookup ────────────────────────────────────────────
        cache_key = None
        if self._cache:
            doi = ref.get("doi", "")
            title = ref.get("title", "")
            cache_id = doi if doi else title
            if cache_id:
                from core.cache import ResponseCache
                cache_key = ResponseCache.make_key(
                    "engine", "verify", cache_id)
                cached = self._cache.get(cache_key)
                if cached is not None:
                    # Reconstruct ReferenceResult from cached data
                    result.overall = cached.get("overall", "")
                    result.sources_tried = cached.get("sources_tried", [])
                    result.sources_hit = cached.get("sources_hit", [])
                    result.is_retracted = cached.get("is_retracted", False)
                    result.needs_l2_escalation = cached.get(
                        "needs_l2_escalation", False)
                    if cached.get("composite"):
                        result.composite = CompositeScore(
                            existence=cached["composite"].get("existence", 0),
                            title_sim=cached["composite"].get("title_sim", 0),
                            author_sim=cached["composite"].get("author_sim", 0),
                            venue_sim=cached["composite"].get("venue_sim", 0),
                        )
                    if cached.get("hallucination_score") is not None:
                        result.hallucination = HallucinationReport(
                            hallucination_score=cached["hallucination_score"],
                            is_likely_hallucinated=cached.get(
                                "is_likely_hallucinated", False),
                        )
                    return result

        entities_found = []
        registry_names = []

        # Phase 1: ID-based lookup (fast, precise)
        if parallel:
            self._verify_by_id_parallel(ref, result, entities_found,
                                        registry_names, domains)
        else:
            self._verify_by_id(ref, result, entities_found,
                               registry_names, domains)

        # Phase 2: Search-based lookup (broader, slower)
        # Only search if ID lookup didn't confirm the reference
        if not result.is_confirmed:
            if parallel:
                self._verify_by_search_parallel(ref, result, entities_found,
                                                registry_names, domains)
            else:
                self._verify_by_search(ref, result, entities_found,
                                       registry_names, domains)

        # Phase 3: Cross-registry consistency check
        if self._enable_consistency and len(entities_found) >= 2:
            result.consistency = self._meta_validator.validate(
                entities_found, registry_names)

        # Phase 4: Retraction check
        result.is_retracted = any(m.is_retracted for m in result.source_matches)

        # Phase 5: Hallucination analysis
        if self._enable_hallucination:
            registry_results = [
                {"source": m.registry, "found": m.found,
                 "found_by_id": m.found_by_id,
                 "title": m.entity.title if m.entity else "",
                 "authors": m.entity.authors if m.entity else []}
                for m in result.source_matches
            ]
            result.hallucination = self._hallucination_detector.analyze(
                ref, registry_results if result.source_matches else None)

        # Phase 6: Build candidates list (top-K)
        for m in sorted(result.source_matches,
                        key=lambda x: x.match_score, reverse=True)[:3]:
            result.candidates.append({
                "registry": m.registry,
                "title": m.entity.title if m.entity else "",
                "authors": (m.entity.authors[:3] if m.entity else []),
                "year": m.entity.year if m.entity else "",
                "match_score": round(m.match_score, 1),
                "found_by_id": m.found_by_id,
            })

        # Phase 7: Build structured provenance
        for m in result.source_matches:
            prov = Provenance(
                timestamp=time.time(),
                registry=m.registry,
                query_type="id_lookup" if m.found_by_id else "search",
                query_params={
                    "title": ref.get("title", "")[:80],
                    "doi": ref.get("doi", ""),
                },
                found=m.found,
                match_score=m.match_score,
                field_scores=getattr(m, "_field_scores", {}),
                decision=result.overall,
            )
            result.provenance.append(prov)

        # Phase 8: Compute composite confidence score
        result.composite = self._compute_composite(result)

        # Compute overall status
        self._compute_overall(result)

        # Phase 9: L2 escalation decision
        result.needs_l2_escalation = self._should_escalate(result)

        # ── Cache write ─────────────────────────────────────────────
        if self._cache and cache_key:
            try:
                from core.cache import ResponseCache
                cache_data = {
                    "overall": result.overall,
                    "sources_tried": result.sources_tried,
                    "sources_hit": result.sources_hit,
                    "is_retracted": result.is_retracted,
                    "needs_l2_escalation": result.needs_l2_escalation,
                }
                if result.composite:
                    cache_data["composite"] = {
                        "existence": result.composite.existence,
                        "title_sim": result.composite.title_sim,
                        "author_sim": result.composite.author_sim,
                        "venue_sim": result.composite.venue_sim,
                    }
                if result.hallucination:
                    cache_data["hallucination_score"] = (
                        result.hallucination.hallucination_score)
                    cache_data["is_likely_hallucinated"] = (
                        result.hallucination.is_likely_hallucinated)
                ttl = (ResponseCache.TTL_ID_LOOKUP
                       if ref.get("doi") else ResponseCache.TTL_SEARCH)
                self._cache.set(cache_key, cache_data, ttl=ttl)
            except Exception:
                pass

        return result

    def verify_batch(self, references: list[dict],
                     domains: list[str] = None) -> VerificationReport:
        """Verify a batch of references.

        Args:
            references: List of reference dicts
            domains: Optional domain filter

        Returns:
            VerificationReport with aggregate stats.
        """
        report = VerificationReport(total=len(references))

        for ref in references:
            r = self.verify_reference(ref, domains=domains)
            report.results.append(r)
            report.registries_used.update(r.sources_hit)

            if r.overall == "OK":
                report.ok_count += 1
            elif r.overall == "WARN":
                report.warn_count += 1
            else:
                report.fail_count += 1

            if r.is_retracted:
                report.retracted_count += 1
            if (r.hallucination and
                    r.hallucination.is_likely_hallucinated):
                report.hallucinated_count += 1

        return report

    # ── Internal methods ──────────────────────────────────────────────

    def _verify_by_id(self, ref: dict, result: ReferenceResult,
                      entities: list, names: list,
                      domains: list[str] = None):
        """Phase 1: ID-based lookups (DOI, arXiv, PMID, etc.)."""

        # Collect all IDs from the reference
        id_lookups = []
        doi = ref.get("doi", "")
        if doi:
            id_lookups.append(("doi", doi))
        arxiv = ref.get("arxiv_id", "")
        if arxiv:
            id_lookups.append(("arxiv", arxiv))
        pmid = ref.get("pmid", "")
        if pmid:
            id_lookups.append(("pmid", pmid))

        # Also try to detect ID from any 'identifier' field
        identifier = ref.get("identifier", "")
        if identifier:
            id_type = self._discovery.detect_id_type(identifier)
            if id_type:
                id_lookups.append((id_type, identifier))

        for id_type, id_value in id_lookups:
            for adapter in self._discovery._registries:
                info = adapter.info()
                if domains and info.domain not in domains:
                    continue
                if info.name not in result.sources_tried:
                    result.sources_tried.append(info.name)

                entity = adapter.query_by_id(id_type, id_value)
                if entity is None:
                    continue

                # Validate this is actually the right paper
                api_data = {
                    "title": entity.title,
                    "authors": entity.authors,
                    "year": entity.year,
                }
                if not FieldComparator.validate_api_match(ref, api_data):
                    continue

                # It's a match — record it
                match = self._build_source_match(
                    ref, entity, info.name, found_by_id=True)
                result.source_matches.append(match)
                result.all_checks.extend(match.checks)
                entities.append(entity)
                names.append(info.name)

                if info.name not in result.sources_hit:
                    result.sources_hit.append(info.name)

    def _verify_by_search(self, ref: dict, result: ReferenceResult,
                          entities: list, names: list,
                          domains: list[str] = None):
        """Phase 2: Title-based search with author disambiguation."""
        title = ref.get("title", "")
        if not title:
            return

        # Extract author info for disambiguation
        authors = ref.get("authors", [])
        if isinstance(authors, str):
            authors = FieldComparator._parse_bib_authors(authors)
        author_str = authors[0] if authors else ""
        year = ref.get("year", "")

        # Build search title variants
        search_titles = [title]
        alt = re.sub(r"(\b[A-Z]+)(\d+)\b", r"\1", title)
        if alt != title:
            search_titles.append(alt)

        # Determine which registries to try
        registries = self._get_ordered_registries(domains)

        for adapter in registries:
            info = adapter.info()
            if info.name in result.sources_hit:
                continue  # Already found via ID lookup

            if info.name not in result.sources_tried:
                result.sources_tried.append(info.name)

            # Try each search title variant
            best_entity = None
            for st in search_titles:
                hits = adapter.search(st, author=author_str, year=year)
                if not hits:
                    continue
                # Find best match among hits
                for entity in hits:
                    api_data = {
                        "title": entity.title,
                        "authors": entity.authors,
                        "year": entity.year,
                    }
                    if FieldComparator.validate_api_match(ref, api_data):
                        sim = token_similarity(title, entity.title)
                        if sim >= FieldComparator.SEARCH_MATCH_THRESHOLD:
                            # Extra guard: if DOI was provided but didn't
                            # resolve, require author match for search hits
                            if ref.get("doi") and not result.sources_hit:
                                if not self._search_author_matches(ref, entity):
                                    continue
                            best_entity = entity
                            break
                if best_entity:
                    break

            if best_entity is None:
                continue

            match = self._build_source_match(
                ref, best_entity, info.name, found_by_id=False)
            result.source_matches.append(match)
            result.all_checks.extend(match.checks)
            entities.append(best_entity)
            names.append(info.name)

            if info.name not in result.sources_hit:
                result.sources_hit.append(info.name)

            # Early stop if we have a confirmed match from 2+ sources
            # BUT do not exit if any field has FAIL (possible chimera)
            confirmed = sum(1 for m in result.source_matches if m.is_confirmed)
            if confirmed >= 2:
                has_field_fail = any(
                    c.status == "FAIL"
                    for m in result.source_matches
                    for c in m.checks
                    if c.field not in ("retraction",)
                )
                if not has_field_fail:
                    break

    def _verify_by_id_parallel(self, ref: dict, result: ReferenceResult,
                               entities: list, names: list,
                               domains: list[str] = None):
        """Phase 1 (parallel): ID-based lookups using ThreadPoolExecutor."""

        # Collect all IDs from the reference
        id_lookups = []
        doi = ref.get("doi", "")
        if doi:
            id_lookups.append(("doi", doi))
        arxiv = ref.get("arxiv_id", "")
        if arxiv:
            id_lookups.append(("arxiv", arxiv))
        pmid = ref.get("pmid", "")
        if pmid:
            id_lookups.append(("pmid", pmid))

        identifier = ref.get("identifier", "")
        if identifier:
            id_type = self._discovery.detect_id_type(identifier)
            if id_type:
                id_lookups.append((id_type, identifier))

        if not id_lookups:
            return

        # Filter adapters by domain
        adapters = list(self._discovery._registries)
        if domains:
            adapters = [a for a in adapters
                        if a.info().domain in domains]

        # Mark all as tried
        for adapter in adapters:
            name = adapter.info().name
            if name not in result.sources_tried:
                result.sources_tried.append(name)

        # Fire all (id_type, id_value, adapter) combinations in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _lookup(adapter, id_type, id_value):
            return adapter, id_type, id_value, adapter.query_by_id(id_type, id_value)

        tasks = []
        for id_type, id_value in id_lookups:
            for adapter in adapters:
                tasks.append((adapter, id_type, id_value))

        if not tasks:
            return

        confirmed_count = 0
        with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
            futures = {
                executor.submit(_lookup, adapter, idt, idv): (adapter, idt, idv)
                for adapter, idt, idv in tasks
            }
            try:
                for future in as_completed(futures, timeout=15.0):
                    try:
                        adapter, id_type, id_value, entity = future.result(timeout=1.0)
                    except Exception:
                        continue

                    if entity is None:
                        continue

                    info = adapter.info()
                    api_data = {
                        "title": entity.title,
                        "authors": entity.authors,
                        "year": entity.year,
                    }
                    if not FieldComparator.validate_api_match(ref, api_data):
                        continue

                    match = self._build_source_match(
                        ref, entity, info.name, found_by_id=True)
                    result.source_matches.append(match)
                    result.all_checks.extend(match.checks)
                    entities.append(entity)
                    names.append(info.name)

                    if info.name not in result.sources_hit:
                        result.sources_hit.append(info.name)

                    # Early-stop: cancel remaining if 3+ confirmed matches
                    # (keep threshold high enough to get retraction data
                    #  from OpenAlex even if Crossref/S2 respond faster)
                    confirmed_count += 1
                    if confirmed_count >= 3:
                        for f in futures:
                            f.cancel()
                        break
            except TimeoutError:
                for f in futures:
                    f.cancel()

    def _verify_by_search_parallel(self, ref: dict, result: ReferenceResult,
                                    entities: list, names: list,
                                    domains: list[str] = None):
        """Phase 2 (parallel): Title-based search using ThreadPoolExecutor."""
        title = ref.get("title", "")
        if not title:
            return

        authors = ref.get("authors", [])
        if isinstance(authors, str):
            authors = FieldComparator._parse_bib_authors(authors)
        author_str = authors[0] if authors else ""
        year = ref.get("year", "")

        # Determine which registries to try (exclude already-hit ones)
        registries = self._get_ordered_registries(domains)
        registries = [a for a in registries
                      if a.info().name not in result.sources_hit]

        if not registries:
            return

        # Mark as tried
        for adapter in registries:
            name = adapter.info().name
            if name not in result.sources_tried:
                result.sources_tried.append(name)

        # Fire all searches in parallel
        search_results = self._discovery.parallel_search(
            title, author=author_str, year=year,
            adapters=registries, max_workers=8, timeout=15.0)

        # Process results — same matching logic as sequential path
        for adapter, hits in search_results:
            info = adapter.info()
            if info.name in result.sources_hit:
                continue

            best_entity = None
            for entity in hits:
                api_data = {
                    "title": entity.title,
                    "authors": entity.authors,
                    "year": entity.year,
                }
                if FieldComparator.validate_api_match(ref, api_data):
                    sim = token_similarity(title, entity.title)
                    if sim >= FieldComparator.SEARCH_MATCH_THRESHOLD:
                        # Extra guard for search phase: if a DOI was provided
                        # but didn't resolve in Phase 1, require author match
                        # for search results to count — prevents hallucinated
                        # DOI + similar title from being "found"
                        if ref.get("doi") and not result.sources_hit:
                            if not self._search_author_matches(ref, entity):
                                continue
                        best_entity = entity
                        break

            if best_entity is None:
                continue

            match = self._build_source_match(
                ref, best_entity, info.name, found_by_id=False)
            result.source_matches.append(match)
            result.all_checks.extend(match.checks)
            entities.append(best_entity)
            names.append(info.name)

            if info.name not in result.sources_hit:
                result.sources_hit.append(info.name)

    def _build_source_match(self, ref: dict, entity: ICEntity,
                            registry_name: str,
                            found_by_id: bool) -> SourceMatch:
        """Build a SourceMatch with field comparisons and raw similarity scores."""
        match = SourceMatch(
            registry=registry_name,
            found=True,
            found_by_id=found_by_id,
            entity=entity,
        )
        field_scores = {}

        # Title check — record raw similarity
        ref_title = ref.get("title", "")
        title_sim = 0.0
        if ref_title and entity.title:
            title_sim = token_similarity(ref_title, entity.title)
            status, detail = FieldComparator.match_title(ref_title, entity.title)
            match.checks.append(FieldCheck(
                "title", status, detail, registry_name, score=title_sim))
            field_scores["title"] = title_sim

        # Author check — record raw similarity
        ref_authors = ref.get("authors", ref.get("author", []))
        author_sim = 0.0
        if ref_authors and entity.authors:
            status, detail = FieldComparator.match_authors(
                ref_authors, entity.authors)
            # Compute raw author similarity
            if isinstance(ref_authors, str):
                ref_list = FieldComparator._parse_bib_authors(ref_authors)
            else:
                ref_list = ref_authors
            if ref_list and entity.authors:
                author_sim = author_name_similarity(
                    FieldComparator._extract_surname(ref_list[0]),
                    FieldComparator._extract_surname(entity.authors[0]))
            match.checks.append(FieldCheck(
                "authors", status, detail, registry_name, score=author_sim))
            field_scores["author"] = author_sim

        # Year check — compute penalty
        ref_year = ref.get("year", "")
        year_penalty = 1.0
        if ref_year and entity.year:
            status, detail = FieldComparator.match_year(
                ref_year, entity.year, found_by_id=found_by_id)
            try:
                year_diff = abs(int(ref_year) - int(entity.year))
                year_penalty = max(0.0, 1.0 - 0.5 * year_diff)
            except (ValueError, TypeError):
                year_penalty = 0.5
            match.checks.append(FieldCheck(
                "year", status, detail, registry_name, score=year_penalty))
            field_scores["year"] = year_penalty

        # Venue check — record similarity
        ref_venue = (ref.get("venue", "") or ref.get("booktitle", "")
                     or ref.get("journal", ""))
        venue_sim = 0.0
        if ref_venue and entity.venue:
            status, detail = FieldComparator.match_venue(ref_venue, entity.venue)
            venue_sim = token_similarity(ref_venue, entity.venue)
            if status == "OK":
                venue_sim = max(venue_sim, 0.8)  # Abbreviation match → high sim
            match.checks.append(FieldCheck(
                "venue", status, detail, registry_name, score=venue_sim))
            field_scores["venue"] = venue_sim

        # Retraction check (is_retracted lives in entity.metadata for papers)
        if getattr(entity, "is_retracted", False) or entity.metadata.get("is_retracted", False):
            match.is_retracted = True
            match.checks.append(FieldCheck(
                "retraction", "FAIL", "RETRACTED PAPER!", registry_name))

        # Compute match score
        check_tuples = [(c.status, c.detail) for c in match.checks
                        if c.field != "retraction"]
        match.match_score = FieldComparator.compute_match_score(check_tuples)

        # Collect suggested fixes
        if not ref.get("doi") and entity.get_id("doi"):
            match.checks.append(FieldCheck(
                "suggested_fix", "OK",
                f"DOI available: {entity.get_id('doi')}", registry_name))

        # Store raw scores for composite calculation
        match._field_scores = field_scores
        match._title_sim = title_sim
        match._author_sim = author_sim
        match._year_penalty = year_penalty
        match._venue_sim = venue_sim

        return match

    def _get_ordered_registries(self, domains: list[str] = None):
        """Get registries in priority order for search."""
        registries = list(self._discovery._registries)

        # Filter by domain if specified
        if domains:
            registries = [r for r in registries
                          if r.info().domain in domains]

        # Sort by priority (known high-quality sources first)
        priority_names = self.ACADEMIC_PRIORITY

        def sort_key(adapter):
            name = adapter.info().name
            try:
                return priority_names.index(name)
            except ValueError:
                return len(priority_names)

        registries.sort(key=sort_key)
        return registries

    def _compute_composite(self, result: ReferenceResult) -> CompositeScore:
        """Compute composite confidence using spec formula:
        0.35*existence + 0.25*title + 0.15*author + 0.10*venue + 0.15*intent
        """
        cs = CompositeScore()

        if not result.source_matches:
            return cs

        # Existence: 1.0 if found by ID, 0.9 if strong search match, 0.6 if fuzzy
        best = max(result.source_matches, key=lambda m: m.match_score)
        if best.found_by_id:
            cs.existence = 1.0
        elif best.match_score >= 85:
            cs.existence = 0.9
        elif best.match_score >= 60:
            cs.existence = 0.6
        else:
            cs.existence = 0.3

        # Use best raw similarity scores across all sources
        cs.title_sim = max(
            (getattr(m, "_title_sim", 0.0) for m in result.source_matches),
            default=0.0)
        cs.author_sim = max(
            (getattr(m, "_author_sim", 0.0) for m in result.source_matches),
            default=0.0)
        cs.year_penalty = max(
            (getattr(m, "_year_penalty", 1.0) for m in result.source_matches),
            default=1.0)
        cs.venue_sim = max(
            (getattr(m, "_venue_sim", 0.0) for m in result.source_matches),
            default=0.0)

        # Context intent: default neutral (0.5), override if classified
        # Will be set by orchestrator when intent classifier runs
        cs.context_intent = 0.5

        return cs

    def _should_escalate(self, result: ReferenceResult) -> bool:
        """Determine if this reference should be escalated to L2 semantic check.

        Escalation criteria:
          - Composite confidence in [0.55, 0.85] (ambiguous zone)
          - Hallucination score > 30 (moderate risk)
          - No confirmed source but found some matches
          - Intent classified as uncertain
        """
        if result.composite:
            score = result.composite.overall
            if 0.55 <= score <= 0.85:
                return True

        if (result.hallucination and
                result.hallucination.hallucination_score > 30):
            return True

        if result.source_matches and not result.is_confirmed:
            return True

        return False

    def _compute_overall(self, result: ReferenceResult):
        """Compute overall verification status using source-aware logic."""

        # No sources matched at all
        if not result.source_matches:
            result.overall = "FAIL"
            result.all_checks.append(FieldCheck(
                "verification", "FAIL",
                f"NO MATCH — paper may be hallucinated or title differs "
                f"(tried: {', '.join(result.sources_tried[:5])}...)"))
            return

        # If any source confirmed (title + author OK), use confirmed logic
        confirmed_sources = [m for m in result.source_matches if m.is_confirmed]

        if confirmed_sources:
            # Start optimistic, only downgrade for confirmed source issues
            result.overall = "OK"
            for match in confirmed_sources:
                for c in match.checks:
                    if c.field == "retraction":
                        result.overall = "FAIL"
                        return
                    if c.status == "FAIL" and result.overall != "FAIL":
                        result.overall = "FAIL"
                    elif c.status == "WARN" and result.overall == "OK":
                        result.overall = "WARN"

            # If a confirmed source has year FAIL but title+author OK,
            # downgrade to WARN (preprint/publication date difference).
            # The preprint tolerance only applies to search-matched sources:
            # when the DOI/arXiv ID resolved, a year FAIL is a genuine metadata
            # inconsistency (year-only chimera), so it must survive here and
            # reach the chimera_detected signal in L4.
            if result.overall == "FAIL":
                fail_checks = [(m, c) for m in confirmed_sources
                               for c in m.checks if c.status == "FAIL"]
                downgradable = all(c.field == "year" and not m.found_by_id
                                   for m, c in fail_checks)
                if downgradable:
                    result.overall = "WARN"
                    for _m, c in fail_checks:
                        c.status = "WARN"
                        c.detail += " (downgraded: title+author match)"
        else:
            # No confirmed source — use all checks
            result.overall = "OK"
            for m in result.source_matches:
                for c in m.checks:
                    if c.status == "FAIL":
                        result.overall = "FAIL"
                    elif c.status == "WARN" and result.overall == "OK":
                        result.overall = "WARN"

        # Retraction overrides
        if result.is_retracted:
            result.overall = "FAIL"

    def _search_author_matches(self, ref: dict, entity: ICEntity) -> bool:
        """Check if first author surname matches between ref and search result.

        Used as an extra guard when a DOI was provided but didn't resolve —
        prevents hallucinated-DOI papers with similar titles from being
        incorrectly "found" via search.
        """
        ref_authors = ref.get("authors", [])
        if isinstance(ref_authors, str):
            ref_authors = FieldComparator._parse_bib_authors(ref_authors)
        if not ref_authors or not entity.authors:
            return True  # Can't reject without author data
        bib_surname = strip_accents(
            FieldComparator._extract_surname(ref_authors[0]))
        api_surname = strip_accents(
            FieldComparator._extract_surname(entity.authors[0]))
        return bib_surname == api_surname
