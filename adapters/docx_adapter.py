"""DOCX adapter — extracts references from Word documents.

Strategy:
1. Look for a "References" / "Bibliography" heading
2. Extract all paragraphs after that heading
3. Parse each paragraph as a reference entry
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .base import Reference, ReferenceAdapter


class DocxAdapter(ReferenceAdapter):

    @staticmethod
    def supported_extensions() -> set[str]:
        return {".docx"}

    def extract(self, file_path: Path) -> list[Reference]:
        try:
            from docx import Document  # python-docx
        except ImportError:
            return []

        doc = Document(str(file_path))
        paragraphs = [p.text.strip() for p in doc.paragraphs]

        # Find references section
        ref_start = None
        for i, text in enumerate(paragraphs):
            if re.match(r"^\s*(?:References|Bibliography|REFERENCES|BIBLIOGRAPHY|Works Cited)\s*$",
                        text, re.IGNORECASE):
                ref_start = i + 1
                break

        if ref_start is None:
            # Try looking in the last 40% of the document
            cutoff = int(len(paragraphs) * 0.6)
            ref_paragraphs = [p for p in paragraphs[cutoff:] if len(p) > 30]
        else:
            ref_paragraphs = [p for p in paragraphs[ref_start:] if len(p) > 20]

        refs = []
        for idx, text in enumerate(ref_paragraphs[:500]):
            ref = self._parse_paragraph(text, idx)
            if ref:
                refs.append(ref)
        return refs

    @staticmethod
    def _parse_paragraph(text: str, idx: int) -> Optional[Reference]:
        """Parse a single paragraph as a reference."""
        text = re.sub(r"\s+", " ", text).strip()

        # Skip obvious non-references
        if len(text) < 20:
            return None
        if text.startswith(("Figure", "Table", "Appendix", "Chapter")):
            return None

        # Strip leading [N] numbering
        text = re.sub(r"^\[\d+\]\s*", "", text)
        text = re.sub(r"^\d+\.\s+", "", text)

        # Year
        year = ""
        year_m = re.search(r"[\(,]\s*((?:19|20)\d{2}[a-z]?)\s*[\),.]", text)
        if year_m:
            year = re.sub(r"[a-z]$", "", year_m.group(1))

        # Title (quoted or between first two periods)
        title = ""
        title_m = re.search(r'["\u201c](.+?)["\u201d]', text)
        if title_m:
            title = title_m.group(1).strip()
        else:
            parts = text.split(".")
            if len(parts) >= 3:
                candidate = parts[1].strip()
                if 10 < len(candidate) < 300:
                    title = candidate

        # Authors
        author = ""
        if year_m:
            author = text[:year_m.start()].strip().rstrip(",.(")
        elif title_m:
            author = text[:title_m.start()].strip().rstrip(",.(")

        # DOI
        doi = ""
        doi_m = re.search(r"(10\.\d{4,}/\S+)", text)
        if doi_m:
            doi = doi_m.group(1).rstrip(".,;)")

        # arXiv
        arxiv_id = None
        arxiv_m = re.search(r"arXiv[:\s]*(\d{4}\.\d{4,5})", text, re.IGNORECASE)
        if arxiv_m:
            arxiv_id = arxiv_m.group(1)

        if not title and not doi and not arxiv_id:
            return None

        return Reference(
            key=f"docx_ref_{idx + 1}",
            title=title,
            title_raw=title,
            author=author,
            author_clean=author,
            year=year,
            doi=doi,
            arxiv_id=arxiv_id,
        )
