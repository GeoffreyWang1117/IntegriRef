"""deepcite -- does a paper's description of a cited work match that work's text?

An advanced mode for /ref-check. Independent of the L0-L4 pipeline by design: it
never writes risk tiers and never feeds the Bayesian fusion, because those are
calibrated on the published 8,817-case pool.

Spec: docs/DEEPCITE_SPEC_v1.1.md
"""

from .contract import TOOL_VERSION as __version__

__all__ = ["__version__"]
