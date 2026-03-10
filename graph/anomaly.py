"""Citation graph anomaly detection — identify suspicious patterns.

Implements:
  1. Orphan Cluster Detection — references with zero interconnection
  2. Temporal Anomaly Detection — citations to future papers
  3. Excessive Self-citation Detection
  4. Citation Ring Detection — CIDRE variant for mutual citation clusters
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .builder import CitationGraph


class AnomalyType(str, Enum):
    ORPHAN_CLUSTER = "orphan_cluster"
    TEMPORAL_ANOMALY = "temporal_anomaly"
    COCITATION_DEVIATION = "cocitation_deviation"
    SELF_CITATION_RING = "self_citation_ring"
    EXCESSIVE_SELF_CITATION = "excessive_self_citation"


@dataclass
class GraphAnomaly:
    """A detected anomaly in the citation graph."""
    anomaly_type: AnomalyType
    severity: str
    description: str
    involved_nodes: list[str] = field(default_factory=list)
    score: float = 0.0
    details: dict = field(default_factory=dict)


class AnomalyDetector:
    """Detect anomalies in citation graphs."""

    def __init__(self, graph: CitationGraph):
        self._graph = graph

    def run_all(self) -> list[GraphAnomaly]:
        anomalies = []
        anomalies.extend(self.detect_orphan_clusters())
        anomalies.extend(self.detect_temporal_anomalies())
        anomalies.extend(self.detect_excessive_self_citation())
        anomalies.extend(self.detect_citation_rings())
        return anomalies

    def detect_orphan_clusters(self, density_threshold: float = 0.05) -> list[GraphAnomaly]:
        """Detect references with zero interconnection."""
        anomalies = []
        for node_id, node in self._graph.nodes.items():
            refs = self._graph.get_references(node_id)
            if len(refs) < 5:
                continue
            density = self._graph.subgraph_density(refs)
            if density < density_threshold:
                severity = "high" if density < 0.01 else "medium"
                anomalies.append(GraphAnomaly(
                    anomaly_type=AnomalyType.ORPHAN_CLUSTER,
                    severity=severity,
                    description=(
                        f"References of '{node.title[:60]}' have low interconnection "
                        f"(density={density:.4f}, threshold={density_threshold})"
                    ),
                    involved_nodes=[node_id] + refs,
                    score=1.0 - min(density / density_threshold, 1.0),
                    details={"density": density, "threshold": density_threshold,
                             "reference_count": len(refs)},
                ))
        return anomalies

    def detect_temporal_anomalies(self) -> list[GraphAnomaly]:
        """Detect citations to papers published after the citing paper."""
        anomalies = []
        for edge in self._graph.edges:
            source = self._graph.get_node(edge.source_id)
            target = self._graph.get_node(edge.target_id)
            if not source or not target:
                continue
            try:
                sy = int(source.year) if source.year else 0
                ty = int(target.year) if target.year else 0
            except ValueError:
                continue
            if sy > 0 and ty > 0 and ty > sy:
                diff = ty - sy
                severity = "critical" if diff > 2 else "high" if diff > 1 else "medium"
                anomalies.append(GraphAnomaly(
                    anomaly_type=AnomalyType.TEMPORAL_ANOMALY,
                    severity=severity,
                    description=(
                        f"'{source.title[:50]}' ({sy}) cites "
                        f"'{target.title[:50]}' ({ty}) — {diff}yr newer"
                    ),
                    involved_nodes=[edge.source_id, edge.target_id],
                    score=min(diff / 5.0, 1.0),
                    details={"source_year": sy, "target_year": ty,
                             "year_difference": diff},
                ))
        return anomalies

    def detect_excessive_self_citation(self, threshold: float = 0.3) -> list[GraphAnomaly]:
        """Detect papers with >30% self-citation rate."""
        anomalies = []
        for node_id, node in self._graph.nodes.items():
            refs = self._graph.get_references(node_id)
            if len(refs) < 5:
                continue
            source_surnames = set()
            for author in node.authors:
                parts = author.lower().split()
                if parts:
                    source_surnames.add(parts[-1])
            if not source_surnames:
                continue

            self_cite_count = 0
            for ref_id in refs:
                ref_node = self._graph.get_node(ref_id)
                if not ref_node:
                    continue
                for author in ref_node.authors:
                    parts = author.lower().split()
                    if parts and parts[-1] in source_surnames:
                        self_cite_count += 1
                        break

            rate = self_cite_count / len(refs)
            if rate > threshold:
                severity = "high" if rate > 0.5 else "medium"
                anomalies.append(GraphAnomaly(
                    anomaly_type=AnomalyType.EXCESSIVE_SELF_CITATION,
                    severity=severity,
                    description=(
                        f"'{node.title[:60]}' has {rate:.0%} self-citation "
                        f"({self_cite_count}/{len(refs)})"
                    ),
                    involved_nodes=[node_id],
                    score=min((rate - threshold) / (1.0 - threshold), 1.0),
                    details={"self_citation_rate": rate,
                             "self_citation_count": self_cite_count,
                             "total_references": len(refs),
                             "threshold": threshold},
                ))
        return anomalies

    def detect_citation_rings(self, min_ring_size: int = 3,
                               max_ring_size: int = 10) -> list[GraphAnomaly]:
        """Detect citation rings — cycles of mutual citation (CIDRE variant)."""
        anomalies = []

        # Find mutual citation pairs
        mutual_pairs: set[tuple[str, str]] = set()
        for edge in self._graph.edges:
            if (edge.target_id in self._graph._outgoing and
                    edge.source_id in self._graph._outgoing.get(edge.target_id, [])):
                pair = tuple(sorted([edge.source_id, edge.target_id]))
                mutual_pairs.add(pair)

        if not mutual_pairs:
            return anomalies

        mutual_adj: dict[str, set[str]] = {}
        for a, b in mutual_pairs:
            mutual_adj.setdefault(a, set()).add(b)
            mutual_adj.setdefault(b, set()).add(a)

        visited_clusters: set[frozenset[str]] = set()
        for start_node in mutual_adj:
            cluster = {start_node}
            candidates = mutual_adj.get(start_node, set()).copy()

            while candidates and len(cluster) < max_ring_size:
                best = None
                best_connections = 0
                for c in candidates:
                    connections = len(mutual_adj.get(c, set()) & cluster)
                    if connections > best_connections:
                        best = c
                        best_connections = connections
                if best is None or best_connections < len(cluster) * 0.5:
                    break
                cluster.add(best)
                candidates = candidates & mutual_adj.get(best, set())
                candidates.discard(best)

            if len(cluster) >= min_ring_size:
                frozen = frozenset(cluster)
                if frozen not in visited_clusters:
                    visited_clusters.add(frozen)
                    density = self._graph.subgraph_density(list(cluster))
                    severity = ("critical" if density > 0.5 else
                                "high" if density > 0.3 else "medium")
                    titles = []
                    for nid in list(cluster)[:5]:
                        n = self._graph.get_node(nid)
                        if n:
                            titles.append(n.title[:40])

                    anomalies.append(GraphAnomaly(
                        anomaly_type=AnomalyType.SELF_CITATION_RING,
                        severity=severity,
                        description=(
                            f"Citation ring: {len(cluster)} papers with "
                            f"dense mutual citations (density={density:.3f})"
                        ),
                        involved_nodes=list(cluster),
                        score=density,
                        details={"ring_size": len(cluster), "density": density,
                                 "titles": titles},
                    ))

        return anomalies
