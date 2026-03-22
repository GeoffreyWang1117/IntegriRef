"""Batch processor — concurrent verification of multiple references.

Provides asyncio-based batch processing for the VerificationEngine,
processing references concurrently with configurable parallelism.

Usage:
    processor = BatchProcessor(engine, concurrency=16)
    report = await processor.verify_batch(references)

    # Or with progress callback:
    async def on_progress(done, total, result):
        print(f"{done}/{total}: {result.overall}")

    report = await processor.verify_batch(references, on_progress=on_progress)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Concurrent batch reference verification.

    Uses asyncio.Semaphore to control concurrency level while
    processing all references concurrently.
    """

    def __init__(self, engine, concurrency: int = 16,
                 timeout_per_ref: float = 30.0):
        """
        Args:
            engine: VerificationEngine instance
            concurrency: Max concurrent verifications
            timeout_per_ref: Timeout per single reference verification
        """
        self._engine = engine
        self._concurrency = concurrency
        self._timeout = timeout_per_ref

    async def verify_batch(
        self, references: list[dict],
        domains: list[str] = None,
        on_progress: Callable = None,
    ) -> dict:
        """Verify a batch of references concurrently.

        Args:
            references: List of reference dicts
            domains: Optional domain filter
            on_progress: async callback(done_count, total, result)

        Returns:
            Report dict with results and stats.
        """
        if not references:
            return self._empty_report()

        total = len(references)
        semaphore = asyncio.Semaphore(self._concurrency)
        results = [None] * total
        done_count = 0
        start_time = time.monotonic()

        async def _verify_one(idx: int, ref: dict):
            nonlocal done_count
            async with semaphore:
                try:
                    loop = asyncio.get_event_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, self._engine.verify_reference,
                            ref, domains),
                        timeout=self._timeout)
                    results[idx] = result
                except asyncio.TimeoutError:
                    results[idx] = self._timeout_result(ref)
                except Exception as e:
                    logger.error("Verification error for ref %d: %s", idx, e)
                    results[idx] = self._error_result(ref, str(e))

                done_count += 1
                if on_progress:
                    try:
                        await on_progress(done_count, total, results[idx])
                    except Exception:
                        pass

        # Launch all tasks
        tasks = [
            asyncio.create_task(_verify_one(i, ref))
            for i, ref in enumerate(references)
        ]
        await asyncio.gather(*tasks)

        elapsed = time.monotonic() - start_time

        # Build report
        valid_results = [r for r in results if r is not None]
        ok_count = sum(1 for r in valid_results
                       if getattr(r, 'overall', '') == "OK")
        warn_count = sum(1 for r in valid_results
                         if getattr(r, 'overall', '') == "WARN")
        fail_count = sum(1 for r in valid_results
                         if getattr(r, 'overall', '') == "FAIL")
        retracted = sum(1 for r in valid_results
                        if getattr(r, 'is_retracted', False))

        registries_used = set()
        for r in valid_results:
            if hasattr(r, 'sources_hit'):
                registries_used.update(r.sources_hit)

        return {
            "total": total,
            "ok": ok_count,
            "warn": warn_count,
            "fail": fail_count,
            "retracted": retracted,
            "registries_used": sorted(registries_used),
            "elapsed_seconds": round(elapsed, 2),
            "throughput_refs_per_sec": round(total / elapsed, 1) if elapsed > 0 else 0,
            "results": valid_results,
        }

    def _empty_report(self) -> dict:
        return {
            "total": 0, "ok": 0, "warn": 0, "fail": 0,
            "retracted": 0, "registries_used": [],
            "elapsed_seconds": 0, "throughput_refs_per_sec": 0,
            "results": [],
        }

    def _timeout_result(self, ref: dict):
        """Create a stub result for timed-out verification."""
        from verification.engine import ReferenceResult
        return ReferenceResult(
            key=ref.get("key", ""),
            title=ref.get("title", ""),
            overall="FAIL",
        )

    def _error_result(self, ref: dict, error: str):
        from verification.engine import ReferenceResult
        return ReferenceResult(
            key=ref.get("key", ""),
            title=ref.get("title", ""),
            overall="FAIL",
        )
