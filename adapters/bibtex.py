"""BibTeX adapter — thin wrapper around the existing refcheck.parse_bib."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root so we can import refcheck
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from refcheck import parse_bib  # noqa: E402

from .base import Reference, ReferenceAdapter


class BibtexAdapter(ReferenceAdapter):

    @staticmethod
    def supported_extensions() -> set[str]:
        return {".bib"}

    def extract(self, file_path: Path) -> list[Reference]:
        entries = parse_bib(str(file_path))
        refs = []
        for e in entries:
            refs.append(Reference(
                key=e["key"],
                type=e["type"],
                title=e["title"],
                title_raw=e["title_raw"],
                author=e["author"],
                author_clean=e["author_clean"],
                year=e["year"],
                journal=e["journal"],
                booktitle=e["booktitle"],
                volume=e["volume"],
                pages=e["pages"],
                doi=e["doi"],
                url=e["url"],
                arxiv_id=e["arxiv_id"],
                venue=e["venue"],
                _raw=e["_raw"],
            ))
        return refs
