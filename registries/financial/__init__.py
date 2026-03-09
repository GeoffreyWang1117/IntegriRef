"""Financial & regulatory registries — SEC filings, regulatory docs."""

from .edgar import EDGARRegistry
from .federal_register import FederalRegisterFinRegistry

ALL_FINANCIAL = [
    EDGARRegistry,
    FederalRegisterFinRegistry,
]
