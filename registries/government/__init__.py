"""Government & international org registries — policy, data, reports."""

from .world_bank import WorldBankRegistry
from .un_digital_library import UNDigitalLibraryRegistry
from .imf import IMFRegistry
from .who_gho import WHOGHORegistry

# Phase 1b: Multilingual
from .egov_japan import EGovJapanRegistry
from .estat_japan import EStatJapanRegistry

ALL_GOVERNMENT = [
    WorldBankRegistry,
    UNDigitalLibraryRegistry,
    IMFRegistry,
    WHOGHORegistry,
    # Phase 1b
    EGovJapanRegistry,
    EStatJapanRegistry,
]
