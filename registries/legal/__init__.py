"""Legal registries — court decisions, legislation, regulations."""

from .courtlistener import CourtListenerRegistry
from .govinfo import GovInfoRegistry
from .eurlex import EURLexRegistry
from .federal_register import FederalRegisterRegistry

# Phase 1b: Multilingual legal
from .legifrance import LegifranceRegistry
from .indian_kanoon import IndianKanoonRegistry
from .open_legal_de import OpenLegalDeRegistry
from .korean_law import KoreanLawRegistry

ALL_LEGAL = [
    CourtListenerRegistry,
    GovInfoRegistry,
    EURLexRegistry,
    FederalRegisterRegistry,
    # Phase 1b
    LegifranceRegistry,
    IndianKanoonRegistry,
    OpenLegalDeRegistry,
    KoreanLawRegistry,
]
