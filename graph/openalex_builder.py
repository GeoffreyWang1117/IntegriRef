"""OpenAlex 2-hop citation graph builder.

Builds citation graphs by querying OpenAlex API:
1. Start with a paper (DOI or OpenAlex ID)
2. Fetch its referenced_works (hop 1)
3. Batch-fetch metadata for all referenced works
4. For each reference, fetch THEIR referenced_works (hop 2)
5. Build CitationGraph with full metadata

Uses OpenAlex batch API (filter by IDs) for efficiency:
- Single paper: 1 API call
- Batch metadata for N refs: ceil(N/50) calls
- 2nd hop references: ceil(N/50) calls
- Total for typical paper (30 refs): ~3 API calls

Also fetches counts_by_year for Benford's law analysis.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

import config
from graph.builder import CitationEdge, CitationGraph, CitationNode

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.openalex.org"

_SELECT_FIELDS = (
    "id,title,authorships,publication_year,primary_location,doi,"
    "cited_by_count,referenced_works,counts_by_year,concepts,"
    "is_retracted,type"
)


class OpenAlexGraphBuilder:
    """Build 2-hop citation graphs from OpenAlex API data."""

    def __init__(self, email: str = ""):
        """Initialise builder.

        Args:
            email: Contact email for OpenAlex polite pool.  Falls back to
                ``config.CROSSREF_MAILTO`` then a default placeholder.
        """
        self._email = email or config.CROSSREF_MAILTO or "integriref@example.com"
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": f"IntegriRef/1.0 (mailto:{self._email})",
            "Accept": "application/json",
        })
        self._last_call: float = 0.0
        self._api_calls: int = 0
        self._api_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_from_doi(self, doi: str, two_hop: bool = True) -> CitationGraph:
        """Build graph starting from a DOI."""
        data = self._fetch_work(f"doi:{doi}")
        if data is None:
            return CitationGraph()
        ref_ids = data.get("referenced_works", [])
        return self.build_from_references(data, ref_ids, two_hop=two_hop)

    def build_from_openalex_id(self, oa_id: str, two_hop: bool = True) -> CitationGraph:
        """Build graph starting from an OpenAlex ID."""
        data = self._fetch_work(oa_id)
        if data is None:
            return CitationGraph()
        ref_ids = data.get("referenced_works", [])
        return self.build_from_references(data, ref_ids, two_hop=two_hop)

    def build_from_references(
        self,
        paper_data: dict,
        reference_ids: list[str],
        two_hop: bool = True,
    ) -> CitationGraph:
        """Build graph from pre-fetched paper data and reference IDs.

        Args:
            paper_data: OpenAlex JSON dict for the root paper.
            reference_ids: List of OpenAlex work URLs/IDs for the references.
            two_hop: If True, also fetch references-of-references.

        Returns:
            Populated ``CitationGraph``.
        """
        graph = CitationGraph()

        # Root node
        root_node = self._parse_to_node(paper_data)
        if root_node is None:
            return graph
        graph.add_node(root_node)

        if not reference_ids:
            return graph

        # Hop 1 — batch-fetch all referenced works
        ref_works = self._fetch_works_batch(reference_ids)
        hop1_nodes: dict[str, CitationNode] = {}
        hop2_ref_map: dict[str, list[str]] = {}  # node_id -> referenced_work ids

        for rw in ref_works:
            node = self._parse_to_node(rw)
            if node is None:
                continue
            hop1_nodes[node.ice_id] = node
            graph.add_node(node)
            graph.add_edge(CitationEdge(
                source_id=root_node.ice_id,
                target_id=node.ice_id,
                source_title=root_node.title,
                target_title=node.title,
                year_source=root_node.year,
                year_target=node.year,
                edge_type="cites",
            ))
            if two_hop:
                hop2_ref_map[node.ice_id] = rw.get("referenced_works", [])

        # Hop 2 — fetch references of references
        if two_hop and hop2_ref_map:
            # Collect all unique hop-2 IDs not already in the graph
            all_hop2_ids: list[str] = []
            seen: set[str] = set()
            for ref_list in hop2_ref_map.values():
                for rid in ref_list:
                    if rid not in seen:
                        seen.add(rid)
                        all_hop2_ids.append(rid)

            # Remove IDs already in the graph (root + hop1)
            existing_oa_ids = self._collect_oa_ids(graph)
            all_hop2_ids = [rid for rid in all_hop2_ids if rid not in existing_oa_ids]

            # Batch-fetch hop-2 metadata
            hop2_works_by_id: dict[str, dict] = {}
            if all_hop2_ids:
                hop2_works = self._fetch_works_batch(all_hop2_ids)
                for w in hop2_works:
                    oa_id = w.get("id", "")
                    if oa_id:
                        hop2_works_by_id[oa_id] = w

            # Add hop-2 nodes and edges
            for hop1_id, ref_list in hop2_ref_map.items():
                for rid in ref_list:
                    # Check if this reference is already in graph (could be root or hop1)
                    if rid in existing_oa_ids:
                        target_id = existing_oa_ids[rid]
                        target_node = graph.get_node(target_id)
                        if target_node:
                            hop1_node = graph.get_node(hop1_id)
                            graph.add_edge(CitationEdge(
                                source_id=hop1_id,
                                target_id=target_id,
                                source_title=hop1_node.title if hop1_node else "",
                                target_title=target_node.title,
                                year_source=hop1_node.year if hop1_node else "",
                                year_target=target_node.year,
                                edge_type="cites",
                            ))
                        continue

                    if rid in hop2_works_by_id:
                        node = self._parse_to_node(hop2_works_by_id[rid])
                        if node is None:
                            continue
                        if node.ice_id not in graph.nodes:
                            graph.add_node(node)
                        hop1_node = graph.get_node(hop1_id)
                        graph.add_edge(CitationEdge(
                            source_id=hop1_id,
                            target_id=node.ice_id,
                            source_title=hop1_node.title if hop1_node else "",
                            target_title=node.title,
                            year_source=hop1_node.year if hop1_node else "",
                            year_target=node.year,
                            edge_type="cites",
                        ))

        return graph

    @property
    def stats(self) -> dict:
        """Return API call statistics."""
        return {
            "api_calls": self._api_calls,
            "api_time_seconds": round(self._api_time, 3),
            "avg_latency_ms": (
                round(self._api_time / self._api_calls * 1000, 1)
                if self._api_calls > 0 else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Enforce 100ms minimum gap between API calls."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        self._last_call = time.monotonic()

    def _fetch_work(self, identifier: str) -> Optional[dict]:
        """Fetch single work from OpenAlex.

        Args:
            identifier: DOI (``doi:10.xxx/yyy``), OpenAlex ID (``W123``),
                or full URL (``https://openalex.org/W123``).

        Returns:
            Parsed JSON dict or ``None`` on failure.
        """
        url = f"{_BASE_URL}/works/{identifier}"
        params = {
            "select": _SELECT_FIELDS,
            "mailto": self._email,
        }
        self._rate_limit()
        t0 = time.monotonic()
        try:
            resp = self._session.get(url, params=params, timeout=20)
            self._api_calls += 1
            self._api_time += time.monotonic() - t0
            if resp.status_code != 200:
                logger.warning("OpenAlex fetch %s returned %s", identifier, resp.status_code)
                return None
            return resp.json()
        except Exception as exc:
            self._api_time += time.monotonic() - t0
            logger.warning("OpenAlex fetch %s failed: %s", identifier, exc)
            return None

    def _fetch_works_batch(self, oa_ids: list[str]) -> list[dict]:
        """Batch-fetch works using OpenAlex filter API.

        Uses ``GET /works?filter=openalex:id1|id2|...|id50&per_page=50``
        Processes in chunks of 50.

        Args:
            oa_ids: List of OpenAlex work IDs/URLs.

        Returns:
            List of work dicts (may be shorter than input on errors).
        """
        results: list[dict] = []
        # Normalise IDs to short form (W123456)
        short_ids = [self._normalise_oa_id(oid) for oid in oa_ids]
        short_ids = [s for s in short_ids if s]

        chunk_size = 50
        for i in range(0, len(short_ids), chunk_size):
            chunk = short_ids[i : i + chunk_size]
            filter_value = "openalex:" + "|".join(chunk)
            params = {
                "filter": filter_value,
                "per_page": str(chunk_size),
                "select": _SELECT_FIELDS,
                "mailto": self._email,
            }
            self._rate_limit()
            t0 = time.monotonic()
            try:
                resp = self._session.get(
                    f"{_BASE_URL}/works", params=params, timeout=30,
                )
                self._api_calls += 1
                self._api_time += time.monotonic() - t0
                if resp.status_code != 200:
                    logger.warning(
                        "OpenAlex batch fetch returned %s for chunk starting at %d",
                        resp.status_code, i,
                    )
                    continue
                data = resp.json()
                results.extend(data.get("results", []))
            except Exception as exc:
                self._api_time += time.monotonic() - t0
                logger.warning("OpenAlex batch fetch failed: %s", exc)
                continue

        return results

    def _parse_to_node(self, data: dict) -> Optional[CitationNode]:
        """Parse OpenAlex JSON to CitationNode with full metadata.

        Extracts: title, year, authors, venue, citation_count,
        reference_count, field (from concepts), counts_by_year,
        is_retracted, etc.
        """
        title = data.get("title", "") or ""
        if not title:
            return None

        oa_id = data.get("id", "") or ""
        ice_id = oa_id or f"openalex:unknown:{id(data)}"

        # Authors
        authors: list[str] = []
        for a in data.get("authorships", []):
            name = a.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)

        # Year
        pub_year = data.get("publication_year")
        year = str(pub_year) if pub_year else ""

        # Venue
        primary_loc = data.get("primary_location") or {}
        source = primary_loc.get("source") or {}
        venue = source.get("display_name", "") or ""

        # Counts
        cited_by_count = data.get("cited_by_count", 0) or 0
        ref_count = len(data.get("referenced_works", []))

        # Field from concepts
        primary_field = self._extract_field(data)

        # DOI
        doi = data.get("doi", "") or ""
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]

        # Normalize counts_by_year from OpenAlex list format
        # [{"year": 2023, "cited_by_count": 15}, ...] → {2023: 15, ...}
        raw_counts = data.get("counts_by_year", [])
        counts_by_year: dict[int, int] = {}
        if isinstance(raw_counts, list):
            for entry in raw_counts:
                if isinstance(entry, dict) and "year" in entry:
                    counts_by_year[entry["year"]] = entry.get("cited_by_count", 0)
        elif isinstance(raw_counts, dict):
            counts_by_year = raw_counts

        # Metadata for downstream analysis
        metadata: dict = {
            "openalex_id": oa_id,
            "doi": doi,
            "is_retracted": data.get("is_retracted", False),
            "counts_by_year": counts_by_year,
            "work_type": data.get("type", ""),
        }

        return CitationNode(
            ice_id=ice_id,
            title=title,
            year=year,
            authors=authors,
            venue=venue,
            entity_type="paper",
            citation_count=cited_by_count,
            reference_count=ref_count,
            field=primary_field,
            metadata=metadata,
        )

    def _extract_field(self, data: dict) -> str:
        """Extract primary field from concepts.

        Returns the highest-score concept display_name, or empty string.
        """
        concepts = data.get("concepts", [])
        if not concepts:
            return ""
        # Concepts are usually sorted by score descending, but be safe
        best = max(concepts, key=lambda c: c.get("score", 0))
        return best.get("display_name", "")

    @staticmethod
    def _normalise_oa_id(oa_id: str) -> str:
        """Normalise an OpenAlex ID to short form (e.g. ``W2345``).

        Handles full URLs like ``https://openalex.org/W2345`` and bare IDs.
        """
        if not oa_id:
            return ""
        if "/" in oa_id:
            return oa_id.rsplit("/", 1)[-1]
        return oa_id

    @staticmethod
    def _collect_oa_ids(graph: CitationGraph) -> dict[str, str]:
        """Build a mapping of OpenAlex URL/ID -> graph node ice_id.

        Used to detect when a hop-2 reference is already present.
        """
        mapping: dict[str, str] = {}
        for node_id, node in graph.nodes.items():
            oa_id = node.metadata.get("openalex_id", "")
            if oa_id:
                mapping[oa_id] = node_id
        return mapping
