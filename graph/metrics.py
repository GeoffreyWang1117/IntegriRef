"""Citation graph metrics — compute health indicators for reference networks."""

from __future__ import annotations

from dataclasses import dataclass, field

from .builder import CitationGraph
from .anomaly import AnomalyDetector, AnomalyType, GraphAnomaly


@dataclass
class GraphHealthReport:
    """Health assessment of a citation graph."""
    node_count: int = 0
    edge_count: int = 0
    density: float = 0.0
    component_count: int = 0
    largest_component_ratio: float = 0.0
    avg_reference_count: float = 0.0
    avg_citation_count: float = 0.0
    temporal_span_years: int = 0
    self_citation_rate: float = 0.0
    anomalies: list[GraphAnomaly] = field(default_factory=list)
    interconnection_score: float = 100.0
    temporal_consistency_score: float = 100.0
    self_citation_score: float = 100.0
    overall_health_score: float = 100.0

    def summary(self) -> dict:
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "density": round(self.density, 4),
            "component_count": self.component_count,
            "anomaly_count": len(self.anomalies),
            "interconnection_score": round(self.interconnection_score, 1),
            "temporal_consistency_score": round(self.temporal_consistency_score, 1),
            "self_citation_score": round(self.self_citation_score, 1),
            "overall_health_score": round(self.overall_health_score, 1),
        }


class GraphMetrics:
    """Compute citation graph health metrics."""

    def __init__(self, graph: CitationGraph):
        self._graph = graph

    def compute_health(self) -> GraphHealthReport:
        report = GraphHealthReport()
        report.node_count = self._graph.node_count
        report.edge_count = self._graph.edge_count

        if report.node_count == 0:
            return report

        all_ids = list(self._graph.nodes.keys())
        report.density = self._graph.subgraph_density(all_ids)

        components = self._graph.connected_components()
        report.component_count = len(components)
        if components:
            largest = max(len(c) for c in components)
            report.largest_component_ratio = largest / report.node_count

        years = []
        for node in self._graph.nodes.values():
            try:
                y = int(node.year) if node.year else 0
                if y > 1900:
                    years.append(y)
            except ValueError:
                pass
        if years:
            report.temporal_span_years = max(years) - min(years)

        ref_counts = [len(self._graph.get_references(nid))
                      for nid in self._graph.nodes]
        cite_counts = [len(self._graph.get_citations(nid))
                       for nid in self._graph.nodes]
        report.avg_reference_count = (sum(ref_counts) / len(ref_counts)
                                      if ref_counts else 0)
        report.avg_citation_count = (sum(cite_counts) / len(cite_counts)
                                     if cite_counts else 0)

        detector = AnomalyDetector(self._graph)
        report.anomalies = detector.run_all()

        report.interconnection_score = self._score_interconnection(report)
        report.temporal_consistency_score = self._score_temporal(report)
        report.self_citation_score = self._score_self_citation(report)

        report.overall_health_score = (
            report.interconnection_score * 0.35 +
            report.temporal_consistency_score * 0.35 +
            report.self_citation_score * 0.30
        )
        return report

    def _score_interconnection(self, report: GraphHealthReport) -> float:
        orphans = [a for a in report.anomalies
                   if a.anomaly_type == AnomalyType.ORPHAN_CLUSTER]
        if not orphans:
            return 100.0
        return max(0.0, 100.0 - max(a.score for a in orphans) * 60)

    def _score_temporal(self, report: GraphHealthReport) -> float:
        temporals = [a for a in report.anomalies
                     if a.anomaly_type == AnomalyType.TEMPORAL_ANOMALY]
        if not temporals:
            return 100.0
        penalty = sum(a.score * 20 for a in temporals)
        return max(0.0, 100.0 - penalty)

    def _score_self_citation(self, report: GraphHealthReport) -> float:
        self_cites = [a for a in report.anomalies
                      if a.anomaly_type in (AnomalyType.EXCESSIVE_SELF_CITATION,
                                            AnomalyType.SELF_CITATION_RING)]
        if not self_cites:
            return 100.0
        max_score = max(a.score for a in self_cites)
        ring_bonus = 20 if any(a.anomaly_type == AnomalyType.SELF_CITATION_RING
                               for a in self_cites) else 0
        return max(0.0, 100.0 - max_score * 40 - ring_bonus)
