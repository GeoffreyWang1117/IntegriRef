"""Standards & engineering registries — ISO, IEEE, NIST, IETF, W3C."""

from .nist import NISTRegistry
from .ietf_rfc import IETFRFCRegistry
from .wikidata import WikidataRegistry
from .open_library import OpenLibraryRegistry

ALL_STANDARDS = [
    NISTRegistry,
    IETFRFCRegistry,
    WikidataRegistry,
    OpenLibraryRegistry,
]
