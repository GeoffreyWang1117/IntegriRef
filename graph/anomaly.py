"""Citation graph anomaly detection — identify suspicious patterns.

Implements:
  1. Orphan Cluster Detection — references with zero interconnection
  2. Temporal Anomaly Detection — citations to future papers
  3. Excessive Self-citation Detection
  4. Citation Ring Detection — CIDRE variant for mutual citation clusters
  5. Benford's Law Violation — first-digit distribution anomaly (Campanario & Coslado 2011)
  6. Reciprocal Citation Detection — bidirectional citation patterns between authors
  7. Citation Burst Detection — sudden spikes from concentrated sources
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum

from .builder import CitationGraph


class AnomalyType(str, Enum):
    ORPHAN_CLUSTER = "orphan_cluster"
    TEMPORAL_ANOMALY = "temporal_anomaly"
    COCITATION_DEVIATION = "cocitation_deviation"
    SELF_CITATION_RING = "self_citation_ring"
    EXCESSIVE_SELF_CITATION = "excessive_self_citation"
    CITATION_BURST = "citation_burst"
    BENFORD_VIOLATION = "benford_violation"
    RECIPROCAL_CITATION = "reciprocal_citation"
    CROSS_FIELD_ANOMALY = "cross_field_anomaly"


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
        anomalies.extend(self.detect_benford_violation())
        anomalies.extend(self.detect_reciprocal_citations())
        anomalies.extend(self.detect_citation_burst())
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

    def detect_benford_violation(self) -> list[GraphAnomaly]:
        """Detect first-digit distribution anomaly in citation counts.

        Uses Benford's law: P(d) = log10(1 + 1/d) for d in 1..9.
        Compares observed vs expected using chi-squared test.
        Reference: Campanario & Coslado (2011).
        """
        anomalies = []

        # Collect citation counts, filtering out zeros
        counts = [node.citation_count for node in self._graph.nodes.values()
                  if node.citation_count > 0]
        if len(counts) < 20:
            # Need sufficient sample size for meaningful test
            return anomalies

        # Extract first digits
        first_digits = []
        for c in counts:
            first_char = str(abs(c))[0]
            if first_char.isdigit() and first_char != "0":
                first_digits.append(int(first_char))

        if len(first_digits) < 20:
            return anomalies

        n = len(first_digits)
        observed = Counter(first_digits)

        # Benford expected proportions: P(d) = log10(1 + 1/d)
        expected_props = {d: math.log10(1 + 1 / d) for d in range(1, 10)}

        # Chi-squared statistic
        chi2 = 0.0
        for d in range(1, 10):
            observed_count = observed.get(d, 0)
            expected_count = expected_props[d] * n
            if expected_count > 0:
                chi2 += (observed_count - expected_count) ** 2 / expected_count

        # Chi-squared critical value for df=8, alpha=0.05 is 15.507
        # For df=8, alpha=0.01 is 20.090
        chi2_critical_005 = 15.507
        chi2_critical_001 = 20.090

        if chi2 > chi2_critical_005:
            severity = "high" if chi2 > chi2_critical_001 else "medium"
            score = min(chi2 / (chi2_critical_005 * 3), 1.0)

            # Build distribution details
            observed_dist = {d: observed.get(d, 0) / n for d in range(1, 10)}

            anomalies.append(GraphAnomaly(
                anomaly_type=AnomalyType.BENFORD_VIOLATION,
                severity=severity,
                description=(
                    f"Citation count first-digit distribution deviates from "
                    f"Benford's law (chi2={chi2:.2f}, critical={chi2_critical_005})"
                ),
                involved_nodes=list(self._graph.nodes.keys()),
                score=score,
                details={
                    "chi_squared": chi2,
                    "critical_value_005": chi2_critical_005,
                    "sample_size": n,
                    "observed_distribution": observed_dist,
                    "expected_distribution": {d: round(p, 4)
                                              for d, p in expected_props.items()},
                    "reference": "Campanario & Coslado 2011",
                },
            ))

        return anomalies

    def detect_reciprocal_citations(self, min_citations: int = 3,
                                     ratio_threshold: float = 0.5) -> list[GraphAnomaly]:
        """Detect high bidirectional citation rates between author pairs.

        Simplified CIDRE-inspired approach: for each author pair, count
        A->B and B->A citations. Flag if both directions >= min_citations
        and the ratio min(A->B, B->A)/max(A->B, B->A) > ratio_threshold.
        """
        anomalies = []

        # Build author -> node mapping (using surname as key)
        author_nodes: dict[str, list[str]] = defaultdict(list)
        for node_id, node in self._graph.nodes.items():
            for author in node.authors:
                parts = author.lower().split()
                if parts:
                    surname = parts[-1]
                    author_nodes[surname].append(node_id)

        # Count directed citations between author pairs
        # pair_citations[(a, b)] = count of citations from a's papers to b's papers
        pair_citations: dict[tuple[str, str], int] = defaultdict(int)
        for edge in self._graph.edges:
            source = self._graph.get_node(edge.source_id)
            target = self._graph.get_node(edge.target_id)
            if not source or not target:
                continue

            source_surnames = set()
            for author in source.authors:
                parts = author.lower().split()
                if parts:
                    source_surnames.add(parts[-1])

            target_surnames = set()
            for author in target.authors:
                parts = author.lower().split()
                if parts:
                    target_surnames.add(parts[-1])

            for s_name in source_surnames:
                for t_name in target_surnames:
                    if s_name != t_name:  # Exclude self-citation
                        pair_citations[(s_name, t_name)] += 1

        # Check for reciprocal patterns
        checked_pairs: set[tuple[str, str]] = set()
        for (a, b), ab_count in pair_citations.items():
            pair_key = tuple(sorted([a, b]))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)

            ba_count = pair_citations.get((b, a), 0)
            if ab_count >= min_citations and ba_count >= min_citations:
                ratio = min(ab_count, ba_count) / max(ab_count, ba_count)
                if ratio > ratio_threshold:
                    total = ab_count + ba_count
                    severity = "high" if total > 10 else "medium"

                    # Collect involved nodes
                    involved = set()
                    a_nodes = author_nodes.get(a, [])
                    b_nodes = author_nodes.get(b, [])
                    involved.update(a_nodes[:5])
                    involved.update(b_nodes[:5])

                    anomalies.append(GraphAnomaly(
                        anomaly_type=AnomalyType.RECIPROCAL_CITATION,
                        severity=severity,
                        description=(
                            f"Reciprocal citation pattern: '{a}' <-> '{b}' "
                            f"({ab_count} + {ba_count} citations, ratio={ratio:.2f})"
                        ),
                        involved_nodes=list(involved),
                        score=min(ratio * total / 20.0, 1.0),
                        details={
                            "author_a": a,
                            "author_b": b,
                            "a_to_b": ab_count,
                            "b_to_a": ba_count,
                            "ratio": ratio,
                            "total_citations": total,
                        },
                    ))

        return anomalies

    def detect_citation_burst(self, spike_factor: float = 3.0,
                               concentration_threshold: float = 0.5,
                               min_sources_for_concentration: int = 3) -> list[GraphAnomaly]:
        """Detect sudden citation spikes from concentrated sources.

        Flags two patterns:
        1. Year-over-year spike: citations increase >spike_factor from a small
           source set (requires counts_by_year in node metadata).
        2. Source concentration: >concentration_threshold of incoming citations
           come from <min_sources_for_concentration unique sources.
        """
        anomalies = []

        for node_id, node in self._graph.nodes.items():
            incoming = self._graph.get_citations(node_id)
            if len(incoming) < 5:
                continue

            flagged = False
            burst_details: dict = {}

            # Pattern 1: Year-over-year spike from metadata
            counts_by_year = node.metadata.get("counts_by_year", {})
            if counts_by_year and isinstance(counts_by_year, dict):
                sorted_years = sorted(counts_by_year.keys())
                for i in range(1, len(sorted_years)):
                    prev_year = sorted_years[i - 1]
                    curr_year = sorted_years[i]
                    prev_count = counts_by_year[prev_year]
                    curr_count = counts_by_year[curr_year]
                    if prev_count > 0 and curr_count > spike_factor * prev_count:
                        flagged = True
                        burst_details["spike_year"] = curr_year
                        burst_details["spike_ratio"] = curr_count / prev_count
                        burst_details["prev_count"] = prev_count
                        burst_details["curr_count"] = curr_count
                        break

            # Pattern 2: Source concentration
            source_counter = Counter(incoming)
            total_citations = len(incoming)
            unique_sources = len(source_counter)

            if unique_sources > 0:
                # Sort by frequency, check if top sources dominate
                top_sources = source_counter.most_common(
                    min_sources_for_concentration)
                top_count = sum(c for _, c in top_sources)
                concentration = top_count / total_citations

                if (concentration > concentration_threshold and
                        unique_sources >= min_sources_for_concentration):
                    flagged = True
                    burst_details["concentration"] = concentration
                    burst_details["top_source_count"] = len(top_sources)
                    burst_details["total_citations"] = total_citations
                    burst_details["unique_sources"] = unique_sources

            if flagged:
                score_val = 0.0
                if "spike_ratio" in burst_details:
                    score_val = max(score_val,
                                    min((burst_details["spike_ratio"] - spike_factor)
                                        / (spike_factor * 2), 1.0))
                if "concentration" in burst_details:
                    score_val = max(score_val,
                                    min(burst_details["concentration"], 1.0))

                severity = "high" if score_val > 0.7 else "medium"
                desc_parts = []
                if "spike_year" in burst_details:
                    desc_parts.append(
                        f"{burst_details['spike_ratio']:.1f}x spike in "
                        f"{burst_details['spike_year']}")
                if "concentration" in burst_details:
                    desc_parts.append(
                        f"{burst_details['concentration']:.0%} from "
                        f"<{min_sources_for_concentration} sources")

                anomalies.append(GraphAnomaly(
                    anomaly_type=AnomalyType.CITATION_BURST,
                    severity=severity,
                    description=(
                        f"Citation burst for '{node.title[:50]}': "
                        + ", ".join(desc_parts)
                    ),
                    involved_nodes=[node_id] + incoming[:10],
                    score=score_val,
                    details=burst_details,
                ))

        return anomalies
