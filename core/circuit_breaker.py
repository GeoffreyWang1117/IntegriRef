"""Circuit breaker — prevent cascading failures from dead registries.

States:
  CLOSED   → normal operation, requests pass through
  OPEN     → registry is considered down, requests fail fast
  HALF_OPEN → trial period, one request allowed to test recovery

Transitions:
  CLOSED → OPEN: failure_count >= failure_threshold within window
  OPEN → HALF_OPEN: after recovery_timeout seconds
  HALF_OPEN → CLOSED: if trial request succeeds
  HALF_OPEN → OPEN: if trial request fails
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    """Statistics for a single circuit breaker."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    state_changes: int = 0
    consecutive_failures: int = 0


class CircuitBreaker:
    """Per-registry circuit breaker with sliding window failure tracking."""

    def __init__(self, name: str,
                 failure_threshold: int = 5,
                 recovery_timeout: float = 30.0,
                 window_size: float = 60.0,
                 half_open_max_calls: int = 1):
        """
        Args:
            name: Registry name (for logging)
            failure_threshold: Failures within window to trip the breaker
            recovery_timeout: Seconds to wait before trying again (OPEN → HALF_OPEN)
            window_size: Sliding window in seconds for counting failures
            half_open_max_calls: Max concurrent calls in HALF_OPEN state
        """
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._window_size = window_size
        self._half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at: float = 0.0
        self._half_open_calls: int = 0
        self._lock = Lock()
        self._stats = CircuitStats()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._opened_at >= self._recovery_timeout:
                    self._transition(CircuitState.HALF_OPEN)
            return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        with self._lock:
            self._stats.total_calls += 1

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                if time.time() - self._opened_at >= self._recovery_timeout:
                    self._transition(CircuitState.HALF_OPEN)
                    self._half_open_calls = 1
                    return True
                self._stats.rejected_calls += 1
                return False

            # HALF_OPEN
            if self._half_open_calls < self._half_open_max_calls:
                self._half_open_calls += 1
                return True
            self._stats.rejected_calls += 1
            return False

    def record_success(self):
        """Record a successful call."""
        with self._lock:
            self._stats.successful_calls += 1
            self._stats.last_success_time = time.time()
            self._stats.consecutive_failures = 0

            if self._state == CircuitState.HALF_OPEN:
                self._transition(CircuitState.CLOSED)
                self._failures.clear()

    def record_failure(self):
        """Record a failed call."""
        now = time.time()
        with self._lock:
            self._stats.failed_calls += 1
            self._stats.last_failure_time = now
            self._stats.consecutive_failures += 1

            if self._state == CircuitState.HALF_OPEN:
                self._transition(CircuitState.OPEN)
                return

            # Sliding window: remove old failures
            self._failures.append(now)
            cutoff = now - self._window_size
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()

            if len(self._failures) >= self._failure_threshold:
                self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState):
        old = self._state
        self._state = new_state
        self._stats.state_changes += 1
        if new_state == CircuitState.OPEN:
            self._opened_at = time.time()
        elif new_state == CircuitState.CLOSED:
            self._half_open_calls = 0
        logger.info("Circuit breaker [%s]: %s → %s", self.name, old.value, new_state.value)

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "total_calls": self._stats.total_calls,
            "successful": self._stats.successful_calls,
            "failed": self._stats.failed_calls,
            "rejected": self._stats.rejected_calls,
            "consecutive_failures": self._stats.consecutive_failures,
            "failure_rate": round(
                self._stats.failed_calls / max(1, self._stats.total_calls), 3),
        }

    def reset(self):
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures.clear()
            self._opened_at = 0.0
            self._half_open_calls = 0


class CircuitBreakerRegistry:
    """Manage circuit breakers for all registries."""

    def __init__(self, failure_threshold: int = 5,
                 recovery_timeout: float = 30.0,
                 window_size: float = 60.0):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._window_size = window_size
        self._lock = Lock()

    def get(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=self._failure_threshold,
                    recovery_timeout=self._recovery_timeout,
                    window_size=self._window_size,
                )
            return self._breakers[name]

    def all_stats(self) -> list[dict]:
        return [cb.stats for cb in self._breakers.values()]

    def open_circuits(self) -> list[str]:
        return [name for name, cb in self._breakers.items()
                if cb.state == CircuitState.OPEN]

    def reset_all(self):
        for cb in self._breakers.values():
            cb.reset()
