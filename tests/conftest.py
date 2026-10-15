"""Pytest configuration and shared fixtures."""

import os
import socket
import sys

import pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- network isolation -------------------------------------------------------
#
# `tests/test_integrity_benchmark.py` runs the detector against live registries
# -- 48 distinct hosts were observed in one run, from Crossref and OpenAlex to
# PatentsView and courtlistener. Whether it passes depends on whether those
# services are throttling at the time, which makes a bare "N passed" from the
# full suite unreproducible: two consecutive full runs gave 10 and then 9
# failures with no code change in between, from 30 HTTP 429s and 6 DNS failures.
# Those assertions fail for a real reason -- a registry that will not answer
# drops the evidence that would have raised a risk tier -- but it is the network
# failing, not the detector.
#
#     pytest -m "not network"                  # the deterministic subset
#     pytest -m "not network" --block-network  # ... and prove it is offline
#
# Run the pair as the offline regression gate. `--block-network` installs a
# socket guard so that a test added later which reaches for a registry without
# the marker fails at once, instead of turning intermittently red months from
# now. A marker list nobody enforces goes stale silently; this one cannot.
#
# **The list below was measured, and naive measurement gets it wrong.** A probe
# hooked `socket.getaddrinfo` and `socket.socket.connect` and recorded which
# tests reached for a non-local host: **51 of this module's 60 tests, and
# nothing anywhere else in the suite.**
#
# An earlier pass of the same probe found only 6, and the reason is worth
# keeping. That pass also neutralised `time.sleep` to skip retry backoff, which
# let the registry client's circuit breaker trip almost immediately; once it
# opens, later tests never attempt a socket at all -- they just degrade silently,
# and a real paper scores HIGH instead of LOW because nothing resolved. So
# socket-attempt undercounts network dependence whenever a breaker sits in front
# of it, and the measurement has to leave the backoff alone to see past it. The
# marker is applied per module rather than per observed connection for the same
# reason: 42 of the 60 fail when blocked whether or not they personally
# connected.
#
# Two things the same probe ruled out, so they are not marked:
#   * `test_acceleration.py` looked network-dependent (3 failures) but passes
#     70/70 with the network blocked once `time.sleep` is left alone -- its
#     breaker tests are timing-dependent, so the probe's own shortcut broke
#     them. Measurement needs its own control.
#   * `test_integration.py::TestBatchIntegration::test_batch_mixed_papers`
#     failed in a full blocked run but passes 12/12 in isolation while
#     attempting no connection. That is order contamination: circuit-breaker
#     state is global and leaks between tests. Worth fixing on its own; it is
#     not a network dependency.
#
# `test_verification.py::TestRapidFuzzIntegration::test_rapidfuzz_available`
# also fails here, and is neither: `rapidfuzz>=3.0.0` is declared in
# requirements.txt but is not installed in this environment. Install it rather
# than mark it.

NETWORK_MODULES = frozenset({
    "tests/test_integrity_benchmark.py",
})

# Individual tests outside those modules, if any are ever found. Kept separate
# so the two kinds of evidence stay distinguishable.
NETWORK_TESTS = frozenset()

_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_CONNECT = socket.socket.connect
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", ""})


class NetworkBlocked(OSError):
    """A test reached for the network under --block-network."""


def pytest_addoption(parser):
    parser.addoption(
        "--block-network", action="store_true", default=False,
        help="Fail any test that opens a non-local socket. Pair with "
             "-m 'not network' to prove the offline subset is really offline.")


def _guard_getaddrinfo(host, port, *args, **kwargs):
    # getaddrinfo is hooked as well as connect because a host that does not
    # resolve never reaches connect -- PatentsView's does not resolve from here,
    # so a connect-only guard would wave those calls through.
    if host in _LOCAL_HOSTS or host is None:
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)
    raise NetworkBlocked(
        f"--block-network: resolving {host!r}. Mark this test "
        f"@pytest.mark.network (or add it to NETWORK_MODULES in "
        f"tests/conftest.py), or stub the registry call.")


def _guard_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host in _LOCAL_HOSTS or host is None:
        return _REAL_CONNECT(self, address)
    raise NetworkBlocked(
        f"--block-network: connecting to {host!r}. Mark this test "
        f"@pytest.mark.network (or add it to NETWORK_MODULES in "
        f"tests/conftest.py), or stub the registry call.")


def pytest_configure(config):
    if config.getoption("--block-network"):
        socket.getaddrinfo = _guard_getaddrinfo
        socket.socket.connect = _guard_connect


def pytest_unconfigure(config):
    socket.getaddrinfo = _REAL_GETADDRINFO
    socket.socket.connect = _REAL_CONNECT


def pytest_collection_modifyitems(config, items):
    for item in items:
        module = item.nodeid.split("::", 1)[0]
        if module in NETWORK_MODULES or item.nodeid in NETWORK_TESTS:
            item.add_marker(pytest.mark.network)


# --- test isolation ----------------------------------------------------------
#
# `core.registry` memoises its CircuitBreakerRegistry in a module-level global
# (`_circuit_breakers`), created once and never reset. So a test that makes
# registry calls fail leaves those breakers OPEN for every later test in the
# same process, and the later test fails with no visible connection to the
# cause. That is how `test_integration.py::test_batch_mixed_papers` came to fail
# in a full run while passing 12/12 in isolation.
#
# Resetting between tests is the test harness's job, not the library's: the
# memo is correct in production, where one process is one run.

@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    try:
        from core import registry as _registry
    except Exception:
        yield
        return
    _registry._circuit_breakers = None
    yield
    _registry._circuit_breakers = None
