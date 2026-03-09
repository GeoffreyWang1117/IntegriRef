"""Legal registries — court decisions, legislation, regulations."""

from .courtlistener import CourtListenerRegistry
from .govinfo import GovInfoRegistry
from .eurlex import EURLexRegistry
from .federal_register import FederalRegisterRegistry

ALL_LEGAL = [
    CourtListenerRegistry,
    GovInfoRegistry,
    EURLexRegistry,
    FederalRegisterRegistry,
]
