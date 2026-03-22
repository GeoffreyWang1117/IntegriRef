"""Unified IntegriRef Pipeline — L0-L4 orchestrator.

Combines all five verification layers into a single call:
  L0: Reference existence + field matching + hallucination + retraction
  L1: Citation intent classification
  L2: Semantic claim verification (NLI)
  L3: Citation graph anomaly detection
  L4: Bayesian risk scoring

Usage:
    from core.pipeline import IntegriRefPipeline

    pipeline = IntegriRefPipeline(discovery)
    report = pipeline.verify(reference_dict)
    print(report.risk_tier)        # "LOW" / "ELEVATED" / "HIGH" / "CRITICAL"
    print(report.l4.posterior_probability)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from core.discovery import RegistryDiscovery
from scoring.bayesian import (
    BayesianRiskScorer,
    BayesianRiskReport,
    SignalObservation,
    RiskTier,
    SIGNAL_DEFINITIONS,
)
from semantic.intent_classifier import IntentClassifier, IntentResult, CitationIntent
from semantic.nli_verifier import NLIVerifier, NLIResult, AlignmentLabel
from verification.engine import VerificationEngine, ReferenceResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline report
# ---------------------------------------------------------------------------

@dataclass
class PipelineReport:
    """Complete L0-L4 verification report for a single reference."""

    # Input
    reference: dict = field(default_factory=dict)

    # Per-layer results
    l0: Optional[ReferenceResult] = None
    l1: Optional[IntentResult] = None
    l2: Optional[NLIResult] = None
    l3_anomalies: list = field(default_factory=list)
    l3_health_score: Optional[float] = None
    l4: Optional[BayesianRiskReport] = None

    # Aggregated
    signals: list[SignalObservation] = field(default_factory=list)
    risk_tier: str = "UNKNOWN"
    risk_probability: float = 0.0
    layers_run: list[str] = field(default_factory=list)
    processing_time_ms: float = 0.0

    def summary(self) -> dict:
        return {
            "title": self.reference.get("title", "")[:80],
            "risk_tier": self.risk_tier,
            "risk_probability": round(self.risk_probability, 4),
            "layers_run": self.layers_run,
            "signals_fired": [s.signal_name for s in self.signals if s.fired],
            "l0_overall": self.l0.overall if self.l0 else None,
            "l0_found": bool(self.l0 and self.l0.sources_hit),
            "l1_intent": self.l1.intent.value if self.l1 else None,
            "l2_label": self.l2.label.value if self.l2 else None,
            "l3_anomaly_count": len(self.l3_anomalies),
            "l3_health_score": (
                round(self.l3_health_score, 2)
                if self.l3_health_score is not None else None),
            "l4_posterior": (
                round(self.l4.posterior_probability, 4)
                if self.l4 else None),
            "processing_time_ms": round(self.processing_time_ms, 1),
        }

    def api_response(self) -> dict:
        """Structured API response."""
        resp = {
            "status": "success",
            "risk_tier": self.risk_tier,
            "risk_probability": round(self.risk_probability, 4),
            "layers_run": self.layers_run,
            "signals_fired": [
                {"name": s.signal_name, "confidence": s.confidence,
                 "details": s.details}
                for s in self.signals if s.fired
            ],
            "processing_time_ms": round(self.processing_time_ms, 1),
        }
        if self.l0:
            resp["l0"] = self.l0.api_response()
        if self.l1:
            resp["l1"] = {
                "intent": self.l1.intent.value,
                "confidence": round(self.l1.confidence, 3),
            }
        if self.l2:
            resp["l2"] = {
                "label": self.l2.label.value,
                "confidence": round(self.l2.confidence, 3),
                "entailment_score": round(self.l2.entailment_score, 3),
                "contradiction_score": round(self.l2.contradiction_score, 3),
                "neutral_score": round(self.l2.neutral_score, 3),
            }
        if self.l3_anomalies:
            resp["l3"] = {
                "anomalies": [
                    {"type": a.anomaly_type.value if hasattr(a.anomaly_type, 'value') else str(a.anomaly_type),
                     "severity": round(a.severity, 2),
                     "description": a.description}
                    for a in self.l3_anomalies
                ],
                "health_score": (
                    round(self.l3_health_score, 2)
                    if self.l3_health_score is not None else None),
            }
        if self.l4:
            resp["l4"] = self.l4.summary()
        return resp


# ---------------------------------------------------------------------------
# Signal extractor — auto-collects signals from each layer's output
# ---------------------------------------------------------------------------

class SignalExtractor:
    """Extract Bayesian signals from L0-L3 layer outputs."""

    @staticmethod
    def from_l0(result: ReferenceResult, ref: dict = None,
                full_text: str = "") -> list[SignalObservation]:
        """Extract signals from L0 verification result + optional detectors."""
        signals = []

        # reference_not_found
        found = bool(result.sources_hit)
        signals.append(SignalObservation(
            signal_name="reference_not_found",
            fired=not found,
            confidence=0.9 if not found else 1.0,
            details=f"sources_tried={len(result.sources_tried)}, "
                    f"sources_hit={len(result.sources_hit)}",
        ))

        # phantom_doi
        hall = result.hallucination
        if hall:
            phantom = hall.hallucination_score >= 50
            signals.append(SignalObservation(
                signal_name="phantom_doi",
                fired=phantom,
                confidence=min(hall.hallucination_score / 100, 1.0),
                details=f"hallucination_score={hall.hallucination_score:.0f}",
            ))

        # metadata_mismatch
        if result.source_matches:
            best = max(result.source_matches, key=lambda m: m.match_score)
            mismatch = best.match_score < 70
            # Also fire if L0 overall is FAIL despite finding the reference
            # (e.g. title matches but author/year clearly wrong — chimera)
            if not mismatch and found and result.overall == "FAIL":
                mismatch = True
            signals.append(SignalObservation(
                signal_name="metadata_mismatch",
                fired=mismatch,
                confidence=0.8 if mismatch else 0.9,
                details=f"best_match_score={best.match_score:.0f}, "
                        f"l0_overall={result.overall}",
            ))

        # no_id_match — found by search only, no DOI/arXiv ID resolved
        has_id_match = any(
            m.found_by_id for m in result.source_matches
        ) if result.source_matches else False
        no_id_fired = found and not has_id_match
        signals.append(SignalObservation(
            signal_name="no_id_match",
            fired=no_id_fired,
            confidence=0.85 if no_id_fired else 0.9,
            details=f"found={found}, has_id_match={has_id_match}",
        ))

        # retracted_citation
        signals.append(SignalObservation(
            signal_name="retracted_citation",
            fired=result.is_retracted,
            confidence=1.0,
        ))

        # — Additional detectors (tortured phrases, email, GRIM, sneaked) —

        # Tortured phrases (if full_text provided)
        if full_text:
            try:
                from verification.tortured_phrases import TorturedPhraseDetector
                tp = TorturedPhraseDetector()
                tp_result = tp.scan(full_text)
                score = getattr(tp_result, 'tortured_score', 0)
                fired = score > 0
                signals.append(SignalObservation(
                    signal_name="tortured_phrases",
                    fired=fired,
                    confidence=min(score / 100, 1.0) if score > 0 else 0.8,
                    details=f"matches={getattr(tp_result, 'match_count', 0)}, "
                            f"score={score:.0f}",
                ))
            except Exception as e:
                logger.debug("Tortured phrase detection skipped: %s", e)

        # Email risk (if authors with emails provided)
        if ref and ref.get("author_emails"):
            try:
                from verification.email_risk import EmailRiskDetector
                er = EmailRiskDetector()
                er_result = er.assess_author_list(ref["author_emails"])
                # er_result is a dict with 'aggregate_risk_score'
                risk_score = er_result.get("aggregate_risk_score", 0)
                fired = risk_score > 30
                signals.append(SignalObservation(
                    signal_name="suspicious_email",
                    fired=fired,
                    confidence=min(risk_score / 100, 1.0),
                    details=f"email_risk_score={risk_score:.0f}",
                ))
            except Exception as e:
                logger.debug("Email risk detection skipped: %s", e)

        # GRIM + statcheck (if full_text provided)
        if full_text:
            try:
                from verification.statistical import StatisticalVerifier
                sv = StatisticalVerifier()
                sv_result = sv.verify(full_text)

                # GRIM
                grim_failures = [r for r in sv_result.grim_results
                                 if not r.is_consistent]
                signals.append(SignalObservation(
                    signal_name="grim_test_failure",
                    fired=len(grim_failures) > 0,
                    confidence=0.9 if grim_failures else 0.8,
                    details=f"grim_failures={len(grim_failures)}",
                ))

                # statcheck
                stat_errors = [r for r in sv_result.statcheck_results
                               if not r.is_consistent]
                signals.append(SignalObservation(
                    signal_name="statcheck_error",
                    fired=len(stat_errors) > 0,
                    confidence=0.85 if stat_errors else 0.8,
                    details=f"statcheck_errors={len(stat_errors)}",
                ))
            except Exception as e:
                logger.debug("Statistical verification skipped: %s", e)

        return signals

    @staticmethod
    def from_l1(result: IntentResult) -> list[SignalObservation]:
        """Extract signals from L1 intent classification."""
        signals = []

        # citation_misrepresents_source — contrasting intent with low confidence
        # (intent says contrasting, which means the citation may misrepresent)
        if result.intent == CitationIntent.CONTRASTING:
            signals.append(SignalObservation(
                signal_name="citation_misrepresents_source",
                fired=True,
                confidence=result.confidence,
                details=f"intent={result.intent.value}",
            ))
        else:
            signals.append(SignalObservation(
                signal_name="citation_misrepresents_source",
                fired=False,
                confidence=result.confidence,
            ))

        # all_citations_mentioning — only meaningful for batch, skip for single
        return signals

    @staticmethod
    def from_l2(result: NLIResult) -> list[SignalObservation]:
        """Extract signals from L2 semantic verification."""
        signals = []

        # claim_contradicted
        signals.append(SignalObservation(
            signal_name="claim_contradicted",
            fired=result.label == AlignmentLabel.CONTRADICTED,
            confidence=result.confidence,
            details=f"label={result.label.value}, con={result.contradiction_score:.2f}",
        ))

        # claim_unsupported
        signals.append(SignalObservation(
            signal_name="claim_unsupported",
            fired=result.label == AlignmentLabel.UNSUPPORTED,
            confidence=result.confidence,
            details=f"label={result.label.value}, ent={result.entailment_score:.2f}",
        ))

        return signals

    @staticmethod
    def from_l3(anomalies: list) -> list[SignalObservation]:
        """Extract signals from L3 graph anomaly detection."""
        signals = []

        # Build a map of anomaly types to max severity
        anomaly_map = {}
        for a in anomalies:
            atype = a.anomaly_type.value if hasattr(a.anomaly_type, 'value') else str(a.anomaly_type)
            if atype not in anomaly_map or a.severity > anomaly_map[atype]:
                anomaly_map[atype] = a.severity

        # Map anomaly types to signal names
        type_to_signal = {
            "SELF_CITATION_RING": "citation_ring_detected",
            "EXCESSIVE_SELF_CITATION": "excessive_self_citation",
            "TEMPORAL_ANOMALY": "temporal_anomaly",
            "ORPHAN_CLUSTER": "orphan_cluster",
        }

        for atype, signal_name in type_to_signal.items():
            if signal_name in SIGNAL_DEFINITIONS:
                severity = anomaly_map.get(atype, 0)
                signals.append(SignalObservation(
                    signal_name=signal_name,
                    fired=severity > 0,
                    confidence=min(severity, 1.0),
                    details=f"severity={severity:.2f}" if severity > 0 else "",
                ))

        return signals


# ---------------------------------------------------------------------------
# Unified pipeline
# ---------------------------------------------------------------------------

class IntegriRefPipeline:
    """Unified L0-L4 verification pipeline.

    Orchestrates all five layers and produces a single PipelineReport
    with Bayesian risk scoring.

    Args:
        discovery: RegistryDiscovery instance (with adapters registered).
        layers: Which layers to run. Default all: ["L0","L1","L2","L3","L4"].
        domain: Domain prior for Bayesian scoring (default "default").
        nli_verifier: Optional pre-initialised NLIVerifier (avoids reload).
        intent_classifier: Optional pre-initialised IntentClassifier.
        enable_l2_only_on_escalation: If True, only run L2 when L0 flags
            needs_l2_escalation. Default True.
    """

    def __init__(
        self,
        discovery: RegistryDiscovery,
        layers: list[str] = None,
        domain: str = "default",
        nli_verifier: NLIVerifier = None,
        intent_classifier: IntentClassifier = None,
        enable_l2_only_on_escalation: bool = True,
        early_stop: bool = False,
        early_stop_high: float = 0.80,   # skip L1-L3 if posterior > this
        early_stop_low: float = 0.01,    # skip L1-L3 if posterior < this
    ):
        self._discovery = discovery
        self._layers = set(layers or ["L0", "L1", "L2", "L3", "L4"])
        self._domain = domain
        self._l2_escalation_only = enable_l2_only_on_escalation
        self._early_stop = early_stop
        self._early_stop_high = early_stop_high
        self._early_stop_low = early_stop_low

        # L0
        self._engine = VerificationEngine(discovery)

        # L1
        self._intent_classifier = intent_classifier or (
            IntentClassifier() if "L1" in self._layers else None)

        # L2 — lazy init (heavy model)
        self._nli_verifier = nli_verifier
        self._nli_loaded = nli_verifier is not None

        # Signal extractor
        self._extractor = SignalExtractor()

    def _ensure_nli(self) -> NLIVerifier:
        if not self._nli_loaded:
            self._nli_verifier = NLIVerifier()
            self._nli_loaded = True
        return self._nli_verifier

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def verify(
        self,
        ref: dict,
        citing_sentence: str = "",
        full_text: str = "",
        abstract: str = "",
        domains: list[str] = None,
    ) -> PipelineReport:
        """Run the full L0-L4 pipeline on a single reference.

        Args:
            ref: Reference dict with keys: title, authors, year, doi,
                 arxiv_id, venue, key, author_emails (optional).
            citing_sentence: The sentence that cites this reference (for L1).
            full_text: Full text of the citing paper (for tortured phrases,
                       GRIM, statcheck).
            abstract: Abstract of the cited paper (for L2 NLI). If empty,
                      L2 is skipped (abstract fetching is caller's
                      responsibility for now).
            domains: Optional domain filter for registry queries.

        Returns:
            PipelineReport with all layer results and Bayesian risk score.
        """
        start = time.monotonic()
        report = PipelineReport(reference=ref)
        all_signals: list[SignalObservation] = []

        # ── L0: Existence verification ────────────────────────────────
        if "L0" in self._layers:
            logger.debug("[Pipeline] Running L0 for: %s",
                         ref.get("title", "")[:60])
            report.l0 = self._engine.verify_reference(
                ref, domains=domains, parallel=True)
            report.layers_run.append("L0")

            # Extract L0 signals (including tortured/email/GRIM if available)
            l0_signals = self._extractor.from_l0(
                report.l0, ref=ref, full_text=full_text)
            all_signals.extend(l0_signals)

        # ── Early stopping: skip L1-L3 if L0 is decisive ──────────────
        skip_l1_l3 = False
        if self._early_stop and "L0" in report.layers_run and "L4" in self._layers:
            # Run an intermediate L4 on L0 signals only
            interim_scorer = BayesianRiskScorer(domain=self._domain)
            seen_interim = set()
            for s in all_signals:
                if s.signal_name not in seen_interim:
                    seen_interim.add(s.signal_name)
                    interim_scorer.observe(
                        s.signal_name, s.fired, s.confidence, s.details)
            interim = interim_scorer.compute()
            if interim.posterior_probability > self._early_stop_high:
                skip_l1_l3 = True
                logger.debug(
                    "[Pipeline] Early stop HIGH: L0 posterior=%.3f > %.2f, "
                    "skipping L1-L3", interim.posterior_probability,
                    self._early_stop_high)
            elif interim.posterior_probability < self._early_stop_low:
                skip_l1_l3 = True
                logger.debug(
                    "[Pipeline] Early stop LOW: L0 posterior=%.3f < %.2f, "
                    "skipping L1-L3", interim.posterior_probability,
                    self._early_stop_low)

        # ── L1: Citation intent ───────────────────────────────────────
        if "L1" in self._layers and citing_sentence and self._intent_classifier and not skip_l1_l3:
            logger.debug("[Pipeline] Running L1")
            report.l1 = self._intent_classifier.classify(
                citing_sentence,
                citation_key=ref.get("key", ""),
            )
            report.layers_run.append("L1")

            l1_signals = self._extractor.from_l1(report.l1)
            all_signals.extend(l1_signals)

        # ── L2: Semantic verification (conditional) ───────────────────
        run_l2 = "L2" in self._layers and abstract and not skip_l1_l3
        if run_l2 and self._l2_escalation_only:
            # Only run L2 if L0 flagged escalation
            if report.l0 and not report.l0.needs_l2_escalation:
                run_l2 = False
                logger.debug("[Pipeline] L2 skipped — L0 did not escalate")

        if run_l2:
            logger.debug("[Pipeline] Running L2")
            claim = citing_sentence or ref.get("title", "")
            verifier = self._ensure_nli()
            report.l2 = verifier.verify(claim=claim, abstract=abstract)
            report.layers_run.append("L2")

            l2_signals = self._extractor.from_l2(report.l2)
            all_signals.extend(l2_signals)

        # ── L3: Graph anomaly detection ───────────────────────────────
        if "L3" in self._layers and not skip_l1_l3:
            doi = ref.get("doi", "")
            if doi:
                try:
                    report.l3_anomalies, report.l3_health_score = \
                        self._run_l3(doi)
                    report.layers_run.append("L3")

                    l3_signals = self._extractor.from_l3(report.l3_anomalies)
                    all_signals.extend(l3_signals)
                except Exception as e:
                    logger.debug("[Pipeline] L3 skipped: %s", e)

        # ── L4: Bayesian risk scoring ─────────────────────────────────
        if "L4" in self._layers:
            scorer = BayesianRiskScorer(domain=self._domain)

            # De-duplicate signals (keep first occurrence of each signal_name)
            seen = set()
            unique_signals = []
            for s in all_signals:
                if s.signal_name not in seen:
                    seen.add(s.signal_name)
                    unique_signals.append(s)

            scorer.observe_batch(unique_signals)
            report.l4 = scorer.compute()
            report.layers_run.append("L4")

            report.risk_tier = report.l4.risk_tier.value
            report.risk_probability = report.l4.posterior_probability

        report.signals = all_signals
        report.processing_time_ms = (time.monotonic() - start) * 1000

        # Fallback risk tier if L4 not run
        if "L4" not in report.layers_run:
            report.risk_tier = self._fallback_risk_tier(report)

        # ── Kill-shot override ──────────────────────────────────────
        # If L0 hallucination detector is highly confident, override
        # the Bayesian risk tier upward. A phantom DOI or complete
        # non-existence is ground truth — Bayesian scoring should not
        # soften it below HIGH.
        if report.l0 and report.l0.hallucination:
            h = report.l0.hallucination
            if h.is_likely_hallucinated and h.hallucination_score >= 50:
                risk_order = {"LOW": 0, "ELEVATED": 1, "HIGH": 2, "CRITICAL": 3}
                if risk_order.get(report.risk_tier, 0) < 2:  # below HIGH
                    report.risk_tier = "HIGH"
                    report.risk_probability = max(
                        report.risk_probability,
                        h.hallucination_score / 100.0)
                    logger.debug(
                        "[Pipeline] Kill-shot override: hallucination_score=%.0f "
                        "→ risk_tier=HIGH", h.hallucination_score)

        return report

    # ------------------------------------------------------------------
    # Batch verification
    # ------------------------------------------------------------------

    def verify_batch(
        self,
        references: list[dict],
        citing_sentences: list[str] = None,
        full_text: str = "",
        abstracts: list[str] = None,
        domains: list[str] = None,
        max_workers: int = 8,
    ) -> list[PipelineReport]:
        """Verify multiple references through the full pipeline.

        Uses ThreadPoolExecutor for concurrent verification when
        multiple references are provided.

        Args:
            references: List of reference dicts.
            citing_sentences: Parallel list of citing sentences (for L1).
            full_text: Full text of the citing paper (shared across refs).
            abstracts: Parallel list of cited paper abstracts (for L2).
            domains: Optional domain filter.
            max_workers: Max concurrent verifications (default 8).

        Returns:
            List of PipelineReport, one per reference.
        """
        n = len(references)
        if n == 0:
            return []

        sentences = citing_sentences or [""] * n
        abs_list = abstracts or [""] * n

        def _verify_one(i: int) -> PipelineReport:
            return self.verify(
                references[i],
                citing_sentence=sentences[i] if i < len(sentences) else "",
                full_text=full_text,
                abstract=abs_list[i] if i < len(abs_list) else "",
                domains=domains,
            )

        # Concurrent verification
        reports = [None] * n
        workers = min(max_workers, n)

        if workers <= 1 or n == 1:
            # Single reference — no thread overhead
            for i in range(n):
                reports[i] = _verify_one(i)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_verify_one, i): i
                    for i in range(n)
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        reports[idx] = future.result()
                    except Exception as e:
                        logger.error("Batch verify error for ref %d: %s",
                                     idx, e)
                        reports[idx] = PipelineReport(
                            reference=references[idx],
                            risk_tier="UNKNOWN",
                        )

        # Check all_citations_mentioning after individual classification
        l1_results = [r.l1 for r in reports if r and r.l1]

        # Post-hoc: check if ALL citations are mentioning
        if l1_results and len(l1_results) >= 3:
            all_mentioning = all(
                r.intent == CitationIntent.MENTIONING for r in l1_results)
            if all_mentioning:
                # Add signal to all reports that have L4
                for report in reports:
                    obs = SignalObservation(
                        signal_name="all_citations_mentioning",
                        fired=True,
                        confidence=0.7,
                        details=f"all {len(l1_results)} citations are mentioning",
                    )
                    report.signals.append(obs)
                    # Recompute L4 if it was run
                    if report.l4:
                        scorer = BayesianRiskScorer(domain=self._domain)
                        seen = set()
                        for s in report.signals:
                            if s.signal_name not in seen:
                                seen.add(s.signal_name)
                                scorer.observe(s.signal_name, s.fired,
                                               s.confidence, s.details)
                        report.l4 = scorer.compute()
                        report.risk_tier = report.l4.risk_tier.value
                        report.risk_probability = report.l4.posterior_probability

        return reports

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_l3(self, doi: str) -> tuple[list, Optional[float]]:
        """Run L3 graph analysis. Returns (anomalies, health_score)."""
        from graph.openalex_builder import OpenAlexGraphBuilder
        from graph.anomaly import AnomalyDetector
        from graph.metrics import GraphMetrics

        builder = OpenAlexGraphBuilder()
        graph = builder.build_from_doi(doi, two_hop=False)

        if not graph or len(graph.nodes) < 3:
            return [], None

        detector = AnomalyDetector(graph)
        anomalies = []
        anomalies.extend(detector.detect_orphan_clusters())
        anomalies.extend(detector.detect_temporal_anomalies())
        anomalies.extend(detector.detect_excessive_self_citation())
        anomalies.extend(detector.detect_citation_rings())
        anomalies.extend(detector.detect_benford_violation())
        anomalies.extend(detector.detect_reciprocal_citations())
        anomalies.extend(detector.detect_citation_burst())

        metrics = GraphMetrics(graph)
        health = metrics.compute_health()
        health_score = health.overall_health_score

        return anomalies, health_score

    @staticmethod
    def _fallback_risk_tier(report: PipelineReport) -> str:
        """Heuristic risk tier when L4 is not run."""
        if report.l0 and report.l0.overall == "FAIL":
            return RiskTier.HIGH.value
        if report.l0 and report.l0.overall == "WARN":
            return RiskTier.ELEVATED.value
        if report.l2 and report.l2.label == AlignmentLabel.CONTRADICTED:
            return RiskTier.HIGH.value
        return RiskTier.LOW.value
