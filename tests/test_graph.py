"""Tests for citation graph analysis module."""

import pytest

from core.entity import ICEntity, EntityType


def _make_entity(title: str, year: str = "2023",
                 authors: list[str] = None) -> ICEntity:
    """Helper to create test entities."""
    entity = ICEntity(
        entity_type=EntityType.PAPER,
        title=title,
        authors=authors or ["Test Author"],
        year=year,
    )
    entity.normalize()
    return entity


class TestCitationGraph:
    def test_empty_graph(self):
        from graph.builder import CitationGraph
        g = CitationGraph()
        assert g.node_count == 0
        assert g.edge_count == 0

    def test_add_node(self):
        from graph.builder import CitationGraph, CitationNode
        g = CitationGraph()
        g.add_node(CitationNode(ice_id="ice:test1", title="Paper 1"))
        assert g.node_count == 1
        assert g.get_node("ice:test1").title == "Paper 1"

    def test_add_edge(self):
        from graph.builder import CitationGraph, CitationNode, CitationEdge
        g = CitationGraph()
        g.add_node(CitationNode(ice_id="ice:a", title="A"))
        g.add_node(CitationNode(ice_id="ice:b", title="B"))
        g.add_edge(CitationEdge(source_id="ice:a", target_id="ice:b"))
        assert g.edge_count == 1
        assert "ice:b" in g.get_references("ice:a")
        assert "ice:a" in g.get_citations("ice:b")

    def test_subgraph_density(self):
        from graph.builder import CitationGraph, CitationNode, CitationEdge
        g = CitationGraph()
        # 3 nodes, fully connected = density 1.0
        for i in range(3):
            g.add_node(CitationNode(ice_id=f"ice:{i}"))
        for i in range(3):
            for j in range(3):
                if i != j:
                    g.add_edge(CitationEdge(source_id=f"ice:{i}", target_id=f"ice:{j}"))
        density = g.subgraph_density(["ice:0", "ice:1", "ice:2"])
        assert abs(density - 1.0) < 0.01

    def test_subgraph_density_empty(self):
        from graph.builder import CitationGraph
        g = CitationGraph()
        assert g.subgraph_density([]) == 0.0
        assert g.subgraph_density(["ice:1"]) == 0.0

    def test_connected_components(self):
        from graph.builder import CitationGraph, CitationNode, CitationEdge
        g = CitationGraph()
        # Two disconnected pairs
        g.add_node(CitationNode(ice_id="a"))
        g.add_node(CitationNode(ice_id="b"))
        g.add_node(CitationNode(ice_id="c"))
        g.add_node(CitationNode(ice_id="d"))
        g.add_edge(CitationEdge(source_id="a", target_id="b"))
        g.add_edge(CitationEdge(source_id="c", target_id="d"))
        components = g.connected_components()
        assert len(components) == 2


class TestGraphBuilder:
    def test_build_from_references(self):
        from graph.builder import GraphBuilder
        paper = _make_entity("Main Paper", "2023")
        refs = [
            _make_entity("Ref 1", "2020"),
            _make_entity("Ref 2", "2021"),
            _make_entity("Ref 3", "2022"),
        ]
        builder = GraphBuilder()
        graph = builder.build_from_references(paper, refs)
        assert graph.node_count == 4
        assert graph.edge_count == 3

    def test_build_two_hop(self):
        from graph.builder import GraphBuilder
        paper = _make_entity("Main", "2023")
        ref1 = _make_entity("Ref1", "2020")
        ref2 = _make_entity("Ref2", "2021")
        ref1_ref = _make_entity("Ref1-Ref", "2018")

        builder = GraphBuilder()
        graph = builder.build_two_hop(
            paper, [ref1, ref2],
            {ref1.ice_id: [ref1_ref]}
        )
        assert graph.node_count == 4
        assert graph.edge_count == 3  # main→ref1, main→ref2, ref1→ref1_ref


class TestAnomalyDetector:
    def test_detect_temporal_anomaly(self):
        from graph.builder import CitationGraph, CitationNode, CitationEdge
        from graph.anomaly import AnomalyDetector, AnomalyType

        g = CitationGraph()
        g.add_node(CitationNode(ice_id="old", title="Old Paper", year="2020"))
        g.add_node(CitationNode(ice_id="future", title="Future Paper", year="2025"))
        g.add_edge(CitationEdge(source_id="old", target_id="future",
                                year_source="2020", year_target="2025"))

        detector = AnomalyDetector(g)
        anomalies = detector.detect_temporal_anomalies()
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == AnomalyType.TEMPORAL_ANOMALY

    def test_no_temporal_anomaly_normal(self):
        from graph.builder import CitationGraph, CitationNode, CitationEdge
        from graph.anomaly import AnomalyDetector

        g = CitationGraph()
        g.add_node(CitationNode(ice_id="new", title="New", year="2023"))
        g.add_node(CitationNode(ice_id="old", title="Old", year="2020"))
        g.add_edge(CitationEdge(source_id="new", target_id="old",
                                year_source="2023", year_target="2020"))

        detector = AnomalyDetector(g)
        anomalies = detector.detect_temporal_anomalies()
        assert len(anomalies) == 0

    def test_detect_excessive_self_citation(self):
        from graph.builder import CitationGraph, CitationNode, CitationEdge
        from graph.anomaly import AnomalyDetector, AnomalyType

        g = CitationGraph()
        g.add_node(CitationNode(ice_id="main", title="Main",
                                authors=["John Smith"], year="2023"))
        # 5 self-citations out of 6
        for i in range(5):
            g.add_node(CitationNode(ice_id=f"self{i}", title=f"Self {i}",
                                    authors=["John Smith"], year="2020"))
            g.add_edge(CitationEdge(source_id="main", target_id=f"self{i}"))
        g.add_node(CitationNode(ice_id="other", title="Other",
                                authors=["Jane Doe"], year="2021"))
        g.add_edge(CitationEdge(source_id="main", target_id="other"))

        detector = AnomalyDetector(g)
        anomalies = detector.detect_excessive_self_citation(threshold=0.3)
        assert len(anomalies) >= 1
        assert anomalies[0].anomaly_type == AnomalyType.EXCESSIVE_SELF_CITATION

    def test_detect_citation_ring(self):
        from graph.builder import CitationGraph, CitationNode, CitationEdge
        from graph.anomaly import AnomalyDetector, AnomalyType

        g = CitationGraph()
        # Create a ring: A↔B, B↔C, A↔C
        for name in ["A", "B", "C"]:
            g.add_node(CitationNode(ice_id=name, title=f"Paper {name}"))
        for a, b in [("A", "B"), ("B", "A"), ("B", "C"), ("C", "B"), ("A", "C"), ("C", "A")]:
            g.add_edge(CitationEdge(source_id=a, target_id=b))

        detector = AnomalyDetector(g)
        anomalies = detector.detect_citation_rings(min_ring_size=3)
        assert len(anomalies) >= 1
        assert anomalies[0].anomaly_type == AnomalyType.SELF_CITATION_RING

    def test_run_all(self):
        from graph.builder import CitationGraph
        from graph.anomaly import AnomalyDetector

        g = CitationGraph()
        detector = AnomalyDetector(g)
        anomalies = detector.run_all()
        assert isinstance(anomalies, list)


class TestGraphMetrics:
    def test_empty_graph_health(self):
        from graph.builder import CitationGraph
        from graph.metrics import GraphMetrics

        g = CitationGraph()
        metrics = GraphMetrics(g)
        report = metrics.compute_health()
        assert report.node_count == 0
        assert report.overall_health_score == 100.0

    def test_health_summary(self):
        from graph.builder import GraphBuilder
        from graph.metrics import GraphMetrics

        paper = _make_entity("Main Paper", "2023")
        refs = [_make_entity(f"Ref {i}", str(2020 + i)) for i in range(5)]

        builder = GraphBuilder()
        graph = builder.build_from_references(paper, refs)
        metrics = GraphMetrics(graph)
        report = metrics.compute_health()

        summary = report.summary()
        assert "node_count" in summary
        assert "overall_health_score" in summary
        assert summary["node_count"] == 6
