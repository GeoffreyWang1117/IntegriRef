"""Government & international org registries — policy, data, reports."""

from .world_bank import WorldBankRegistry
from .un_digital_library import UNDigitalLibraryRegistry
from .imf import IMFRegistry
from .who_gho import WHOGHORegistry

ALL_GOVERNMENT = [
    WorldBankRegistry,
    UNDigitalLibraryRegistry,
    IMFRegistry,
    WHOGHORegistry,
]
