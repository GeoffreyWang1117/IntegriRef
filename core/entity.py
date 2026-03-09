"""IntegriRef Canonical Entity (ICE) — the universal reference record.

Every registry adapter normalizes its results into this format.
The entity normalization layer then deduplicates and merges ICE records
across registries.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EntityType(str, Enum):
    PAPER = "paper"
    PATENT = "patent"
    CASE = "case"             # legal case / court decision
    STATUTE = "statute"       # law / regulation / act
    STANDARD = "standard"     # ISO / IEEE / NIST / RFC
    REPORT = "report"         # government white paper, policy doc
    FILING = "filing"         # SEC filing, regulatory submission
    DATASET = "dataset"       # research dataset
    BOOK = "book"
    THESIS = "thesis"
    OTHER = "other"


@dataclass
class ExternalID:
    """A single identifier from an external registry."""
    registry: str     # e.g. "crossref", "uspto", "courtlistener"
    id_type: str      # e.g. "doi", "patent_number", "case_cite"
    id_value: str     # e.g. "10.1234/example"

    @property
    def key(self) -> str:
        return f"{self.registry}:{self.id_type}:{self.id_value}"


@dataclass
class ICEntity:
    """IntegriRef Canonical Entity — unified cross-registry reference record.

    This is the core data model. Every registry adapter produces ICEntity objects.
    The normalization layer merges them by matching external IDs and fuzzy fields.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    ice_id: str = ""                          # "ice:xxxxxxxxxxxx" — assigned after merge
    entity_type: EntityType = EntityType.OTHER

    # ── External identifiers (the key to cross-registry linking) ──────────
    external_ids: list[ExternalID] = field(default_factory=list)

    # ── Normalized fields (for fuzzy matching and display) ────────────────
    title: str = ""
    title_normalized: str = ""                # lowercase, no punctuation, NFC
    authors: list[str] = field(default_factory=list)
    authors_normalized: list[str] = field(default_factory=list)
    year: str = ""
    venue: str = ""                           # journal / court / patent office / standards body

    # ── Type-specific metadata ────────────────────────────────────────────
    metadata: dict = field(default_factory=dict)
    # Examples:
    #   paper:   {"abstract": ..., "citation_count": ..., "is_retracted": ...}
    #   patent:  {"claims_count": ..., "legal_status": ..., "priority_date": ...}
    #   case:    {"court": ..., "jurisdiction": ..., "disposition": ...}
    #   statute: {"public_law_number": ..., "effective_date": ...}
    #   standard: {"status": "active|withdrawn|revised", "ics_code": ...}

    # ── Provenance ────────────────────────────────────────────────────────
    source_registries: list[str] = field(default_factory=list)
    verification_status: str = "unverified"   # unverified / confirmed / conflict / not_found
    confidence: float = 0.0                   # 0.0 – 1.0

    # ── Methods ───────────────────────────────────────────────────────────

    def add_external_id(self, registry: str, id_type: str, id_value: str):
        eid = ExternalID(registry=registry, id_type=id_type, id_value=id_value)
        if eid.key not in {e.key for e in self.external_ids}:
            self.external_ids.append(eid)

    def get_id(self, id_type: str) -> Optional[str]:
        """Get the first matching external ID by type."""
        for eid in self.external_ids:
            if eid.id_type == id_type:
                return eid.id_value
        return None

    def generate_ice_id(self) -> str:
        """Generate a stable ICE ID from the best available identifier."""
        # Prefer deterministic IDs: DOI > arXiv > patent# > case cite > title hash
        for id_type in ("doi", "arxiv", "pmid", "patent_number", "case_cite",
                        "iso_number", "rfc_number"):
            val = self.get_id(id_type)
            if val:
                h = hashlib.sha256(f"{id_type}:{val}".encode()).hexdigest()[:12]
                self.ice_id = f"ice:{h}"
                return self.ice_id
        # Fallback: hash normalized title + year
        raw = f"{self.title_normalized}:{self.year}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:12]
        self.ice_id = f"ice:{h}"
        return self.ice_id

    @staticmethod
    def normalize_title(title: str) -> str:
        """Normalize title for fuzzy matching."""
        t = unicodedata.normalize("NFC", title)
        t = t.lower()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def normalize_author(name: str) -> str:
        """Normalize author name: strip accents, lowercase."""
        n = unicodedata.normalize("NFKD", name)
        n = n.replace("\u0131", "i")  # dotless i
        n = "".join(c for c in n if not unicodedata.combining(c))
        return n.lower().strip()

    def normalize(self):
        """Compute all normalized fields."""
        self.title_normalized = self.normalize_title(self.title)
        self.authors_normalized = [self.normalize_author(a) for a in self.authors]
        if not self.ice_id:
            self.generate_ice_id()
