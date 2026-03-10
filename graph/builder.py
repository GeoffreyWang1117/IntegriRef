"""Citation graph builder — constructs reference networks from registry data.

Uses OpenAlex, Semantic Scholar, and OpenCitations to build
citation graphs for anomaly detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.entity import ICEntity


@dataclass
class CitationEdge:
    """Directed citation edge: source → target."""
    source_id: str
    target_id: str
    source_title: str = ""
    target_title: str = ""
    year_source: str = ""
    year_target: str = ""
    edge_type: str = "cites"
    weight: float = 1.0


@dataclass
class CitationNode:
    """A node in the citation graph."""
    ice_id: str
    title: str = ""
    year: str = ""
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    entity_type: str = "paper"
    citation_count: int = 0
    reference_count: int = 0
    metadata: dict = field(default_factory=dict)


class CitationGraph:
    """In-memory citation graph."""

    def __init__(self):
        self.nodes: dict[str, CitationNode] = {}
        self.edges: list[CitationEdge] = []
        self._outgoing: dict[str, list[str]] = {}
        self._incoming: dict[str, list[str]] = {}

    def add_node(self, node: CitationNode):
        self.nodes[node.ice_id] = node

    def add_edge(self, edge: CitationEdge):
        self.edges.append(edge)
        self._outgoing.setdefault(edge.source_id, []).append(edge.target_id)
        self._incoming.setdefault(edge.target_id, []).append(edge.source_id)

    def get_references(self, ice_id: str) -> list[str]:
        return self._outgoing.get(ice_id, [])

    def get_citations(self, ice_id: str) -> list[str]:
        return self._incoming.get(ice_id, [])

    def get_node(self, ice_id: str) -> Optional[CitationNode]:
        return self.nodes.get(ice_id)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def subgraph_density(self, node_ids: list[str]) -> float:
        """Edge density of a subgraph. density = edges / n*(n-1)."""
        if len(node_ids) < 2:
            return 0.0
        node_set = set(node_ids)
        actual_edges = sum(
            1 for e in self.edges
            if e.source_id in node_set and e.target_id in node_set
        )
        n = len(node_ids)
        possible = n * (n - 1)
        return actual_edges / possible if possible > 0 else 0.0

    def connected_components(self) -> list[set[str]]:
        """Find connected components (undirected)."""
        visited: set[str] = set()
        components = []

        adj: dict[str, set[str]] = {}
        for edge in self.edges:
            adj.setdefault(edge.source_id, set()).add(edge.target_id)
            adj.setdefault(edge.target_id, set()).add(edge.source_id)

        for node_id in self.nodes:
            if node_id not in visited:
                component: set[str] = set()
                stack = [node_id]
                while stack:
                    current = stack.pop()
                    if current in visited:
                        continue
                    visited.add(current)
                    component.add(current)
                    for neighbor in adj.get(current, set()):
                        if neighbor not in visited:
                            stack.append(neighbor)
                components.append(component)

        return components


class GraphBuilder:
    """Build citation graphs from ICEntity collections."""

    def __init__(self):
        self._graph = CitationGraph()

    @property
    def graph(self) -> CitationGraph:
        return self._graph

    def add_paper(self, entity: ICEntity) -> CitationNode:
        node = CitationNode(
            ice_id=entity.ice_id,
            title=entity.title,
            year=entity.year,
            authors=entity.authors[:],
            venue=entity.venue,
            entity_type=entity.entity_type.value,
            citation_count=(
                entity.metadata.get("citation_count", 0) or
                entity.metadata.get("is_referenced_by_count", 0) or 0
            ),
            reference_count=entity.metadata.get("reference_count", 0) or 0,
        )
        self._graph.add_node(node)
        return node

    def add_citation(self, citing: ICEntity, cited: ICEntity,
                     edge_type: str = "cites"):
        if citing.ice_id not in self._graph.nodes:
            self.add_paper(citing)
        if cited.ice_id not in self._graph.nodes:
            self.add_paper(cited)

        edge = CitationEdge(
            source_id=citing.ice_id,
            target_id=cited.ice_id,
            source_title=citing.title,
            target_title=cited.title,
            year_source=citing.year,
            year_target=cited.year,
            edge_type=edge_type,
        )
        self._graph.add_edge(edge)

    def build_from_references(self, paper: ICEntity,
                               references: list[ICEntity]) -> CitationGraph:
        """Build a one-hop citation graph."""
        self.add_paper(paper)
        for ref in references:
            self.add_citation(paper, ref)
        return self._graph

    def build_two_hop(self, paper: ICEntity, references: list[ICEntity],
                      ref_references: dict[str, list[ICEntity]]) -> CitationGraph:
        """Build a two-hop citation graph."""
        self.build_from_references(paper, references)
        for ref in references:
            for ref_ref in ref_references.get(ref.ice_id, []):
                self.add_citation(ref, ref_ref)
        return self._graph
