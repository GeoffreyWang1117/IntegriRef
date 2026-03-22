"""Async IntegriRef Pipeline — non-blocking L0-L4 orchestrator.

Wraps the sync pipeline layers with async execution:
  - L0: Uses AsyncRegistryDiscovery for concurrent registry I/O
  - L1-L2: CPU-bound, run in thread executor to avoid blocking event loop
  - L3: Uses async graph builder (or executor-wrapped sync)
  - L4: Pure computation, runs inline

This enables the FastAPI endpoints to serve multiple concurrent requests
without blocking on registry I/O.

Usage:
    pipeline = AsyncIntegriRefPipeline(async_discovery)
    report = await pipeline.verify(ref_dict)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from core.async_discovery import AsyncRegistryDiscovery
from core.pipeline import (
    PipelineReport,
    SignalExtractor,
)
from scoring.bayesian import (
    BayesianRiskScorer,
    SignalObservation,
    RiskTier,
)
from semantic.intent_classifier import IntentClassifier, CitationIntent
from semantic.nli_verifier import NLIVerifier, AlignmentLabel

logger = logging.getLogger(__name__)


class AsyncIntegriRefPipeline:
    """Async L0-L4 pipeline orchestrator.

    Wraps the sync IntegriRefPipeline in an executor for non-blocking
    execution within async contexts (FastAPI endpoints, etc.).
    Uses a shared ThreadPoolExecutor to avoid blocking the event loop.
    """

    def __init__(
        self,
        discovery: AsyncRegistryDiscovery,
        layers: list[str] = None,
        domain: str = "default",
        nli_verifier: NLIVerifier = None,
        intent_classifier: IntentClassifier = None,
        enable_l2_only_on_escalation: bool = True,
    ):
        self._discovery = discovery
        self._layers = layers or ["L0", "L1", "L2", "L3", "L4"]
        self._domain = domain
        self._l2_escalation_only = enable_l2_only_on_escalation
        self._nli_verifier = nli_verifier
        self._intent_classifier = intent_classifier

        # Build a sync RegistryDiscovery from the same adapters
        # (the sync engine does proper field validation + hallucination detection)
        from core.discovery import RegistryDiscovery
        self._sync_discovery = RegistryDiscovery()
        for adapter in discovery.all_adapters:
            self._sync_discovery.register(adapter)

        # Sync pipeline (runs in executor)
        from core.pipeline import IntegriRefPipeline
        self._sync_pipeline = IntegriRefPipeline(
            discovery=self._sync_discovery,
            layers=self._layers,
            domain=self._domain,
            nli_verifier=nli_verifier,
            intent_classifier=intent_classifier,
            enable_l2_only_on_escalation=enable_l2_only_on_escalation,
        )

        # Thread pool for non-blocking execution
        self._executor = None

    def _get_executor(self):
        if self._executor is None:
            from concurrent.futures import ThreadPoolExecutor
            self._executor = ThreadPoolExecutor(max_workers=4)
        return self._executor

    async def verify(
        self,
        ref: dict,
        citing_sentence: str = "",
        full_text: str = "",
        abstract: str = "",
        domains: list[str] = None,
    ) -> PipelineReport:
        """Run the full L0-L4 pipeline in a thread executor (non-blocking)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._get_executor(),
            lambda: self._sync_pipeline.verify(
                ref=ref,
                citing_sentence=citing_sentence,
                full_text=full_text,
                abstract=abstract,
                domains=domains,
            ),
        )

    async def verify_batch(
        self,
        references: list[dict],
        citing_sentences: list[str] = None,
        full_text: str = "",
        abstracts: list[str] = None,
        domains: list[str] = None,
        max_concurrency: int = 8,
    ) -> list[PipelineReport]:
        """Verify multiple references concurrently using asyncio."""
        n = len(references)
        if n == 0:
            return []

        sentences = citing_sentences or [""] * n
        abs_list = abstracts or [""] * n

        sem = asyncio.Semaphore(max_concurrency)

        async def _verify_one(i: int) -> PipelineReport:
            async with sem:
                try:
                    return await self.verify(
                        references[i],
                        citing_sentence=sentences[i] if i < len(sentences) else "",
                        full_text=full_text,
                        abstract=abs_list[i] if i < len(abs_list) else "",
                        domains=domains,
                    )
                except Exception as e:
                    logger.error("Batch verify error for ref %d: %s", i, e)
                    return PipelineReport(
                        reference=references[i], risk_tier="UNKNOWN")

        tasks = [_verify_one(i) for i in range(n)]
        reports = await asyncio.gather(*tasks)
        return list(reports)
