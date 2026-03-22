"""Patent registries — patent office databases."""

from .uspto import USPTORegistry
from .epo import EPORegistry
from .wipo import WIPORegistry
from .lens import LensRegistry

# Phase 1b: Multilingual
from .kipris import KiprisRegistry

ALL_PATENTS = [
    USPTORegistry,
    EPORegistry,
    WIPORegistry,
    LensRegistry,
    # Phase 1b
    KiprisRegistry,
]
