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
]
