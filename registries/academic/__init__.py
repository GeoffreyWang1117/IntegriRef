"""Academic registries — scholarly publication databases."""

from .crossref import CrossRefRegistry
from .arxiv import ArXivRegistry
from .dblp import DBLPRegistry
from .semantic_scholar import SemanticScholarRegistry
from .openalex import OpenAlexRegistry
from .pubmed import PubMedRegistry
from .orcid import ORCIDRegistry
from .doaj import DOAJRegistry
from .unpaywall import UnpaywallRegistry
from .core_ac import CORERegistry
from .europe_pmc import EuropePMCRegistry
from .datacite import DataCiteRegistry
from .opencitations import OpenCitationsRegistry

# Phase 1a: New English API adapters
from .biorxiv import BioRxivRegistry
from .inspire_hep import INSPIRERegistry
from .zenodo import ZenodoRegistry
from .nasa_ads import NASAADSRegistry
from .zbmath import ZbMATHRegistry
from .clinicaltrials import ClinicalTrialsRegistry

ALL_ACADEMIC = [
    CrossRefRegistry,
    ArXivRegistry,
    DBLPRegistry,
    SemanticScholarRegistry,
    OpenAlexRegistry,
    PubMedRegistry,
    ORCIDRegistry,
    DOAJRegistry,
    UnpaywallRegistry,
    CORERegistry,
    EuropePMCRegistry,
    DataCiteRegistry,
    OpenCitationsRegistry,
    # Phase 1a
    BioRxivRegistry,
    INSPIRERegistry,
    ZenodoRegistry,
    NASAADSRegistry,
    ZbMATHRegistry,
    ClinicalTrialsRegistry,
]
