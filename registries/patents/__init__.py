"""Patent registries — patent office databases."""

from .uspto import USPTORegistry
from .epo import EPORegistry
from .wipo import WIPORegistry
from .lens import LensRegistry

ALL_PATENTS = [
    USPTORegistry,
    EPORegistry,
    WIPORegistry,
    LensRegistry,
]
