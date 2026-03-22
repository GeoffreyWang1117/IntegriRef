"""Financial & regulatory registries — SEC filings, economic data."""

from .edgar import EDGARRegistry
from .federal_register import FederalRegisterFinRegistry
from .fred import FREDRegistry
from .inpi import INPIRegistry

ALL_FINANCIAL = [
    EDGARRegistry,
    FederalRegisterFinRegistry,
    FREDRegistry,
    INPIRegistry,
]
