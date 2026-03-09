"""Abstract base for format adapters.

Every adapter converts a specific file format into a list of Reference objects
that the existing verification engine can consume.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Reference:
    """Unified reference object — mirrors the dict schema in refcheck.parse_bib."""
    key: str
    type: str = "article"
    title: str = ""
    title_raw: str = ""
    author: str = ""
    author_clean: str = ""
    year: str = ""
    journal: str = ""
    booktitle: str = ""
    volume: str = ""
    pages: str = ""
    doi: str = ""
    url: str = ""
    arxiv_id: Optional[str] = None
    venue: str = ""
    _raw: dict = field(default_factory=dict)

    def to_entry_dict(self) -> dict:
        """Convert to the dict format expected by refcheck.verify_entry."""
        return {
            "key": self.key,
            "type": self.type,
            "title": self.title,
            "title_raw": self.title_raw or self.title,
            "author": self.author,
            "author_clean": self.author_clean or self.author,
            "year": self.year,
            "journal": self.journal,
            "booktitle": self.booktitle,
            "volume": self.volume,
            "pages": self.pages,
            "doi": self.doi,
            "url": self.url,
            "arxiv_id": self.arxiv_id,
            "venue": self.venue or self.booktitle or self.journal,
            "_raw": self._raw,
        }


class ReferenceAdapter(abc.ABC):
    """Base class for all format adapters."""

    @abc.abstractmethod
    def extract(self, file_path: Path) -> list[Reference]:
        """Extract references from the given file.

        Returns a list of Reference objects ready for verification.
        """
        ...

    @staticmethod
    def supported_extensions() -> set[str]:
        """File extensions this adapter handles (e.g. {'.pdf'})."""
        return set()
