"""PDF adapter — extracts references via GROBID service.

GROBID (https://github.com/kermitt2/grobid) is the gold standard for
academic PDF parsing. It returns structured TEI XML with parsed references.

Fallback: if GROBID is unavailable, uses PyMuPDF to extract raw text
and attempts regex-based reference parsing.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests

from ..config import GROBID_URL
from .base import Reference, ReferenceAdapter


_TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


class PdfAdapter(ReferenceAdapter):

    @staticmethod
    def supported_extensions() -> set[str]:
        return {".pdf"}

    def extract(self, file_path: Path) -> list[Reference]:
        refs = self._try_grobid(file_path)
        if refs is not None:
            return refs
        return self._fallback_pymupdf(file_path)

    # ── GROBID path ───────────────────────────────────────────────────────

    def _try_grobid(self, file_path: Path) -> Optional[list[Reference]]:
        """Send PDF to GROBID and parse TEI XML response."""
        url = f"{GROBID_URL}/api/processReferences"
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    url,
                    files={"input": (file_path.name, f, "application/pdf")},
                    data={"consolidateCitations": "1"},
                    timeout=120,
                )
            if resp.status_code != 200:
                return None
        except (requests.ConnectionError, requests.Timeout):
            return None

        return self._parse_tei_references(resp.text)

    def _parse_tei_references(self, xml_text: str) -> list[Reference]:
        """Parse GROBID TEI XML into Reference objects."""
        refs = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return refs

        bibl_list = root.findall(".//tei:biblStruct", _TEI_NS)
        for i, bibl in enumerate(bibl_list):
            ref = self._parse_bibl_struct(bibl, idx=i)
            if ref and ref.title:
                refs.append(ref)
        return refs

    def _parse_bibl_struct(self, bibl: ET.Element, idx: int) -> Optional[Reference]:
        """Parse a single <biblStruct> element."""
        # Title
        title_el = bibl.find(".//tei:analytic/tei:title[@type='main']", _TEI_NS)
        if title_el is None:
            title_el = bibl.find(".//tei:monogr/tei:title", _TEI_NS)
        title = (title_el.text or "").strip() if title_el is not None else ""

        # Authors
        authors = []
        for author_el in bibl.findall(".//tei:analytic/tei:author", _TEI_NS):
            name = self._parse_author(author_el)
            if name:
                authors.append(name)
        if not authors:
            for author_el in bibl.findall(".//tei:monogr/tei:author", _TEI_NS):
                name = self._parse_author(author_el)
                if name:
                    authors.append(name)
        author_str = " and ".join(authors)

        # Year
        date_el = bibl.find(".//tei:monogr/tei:imprint/tei:date[@type='published']", _TEI_NS)
        year = ""
        if date_el is not None:
            year = date_el.get("when", "")[:4]

        # Venue
        venue_el = bibl.find(".//tei:monogr/tei:title[@level='j']", _TEI_NS)
        if venue_el is None:
            venue_el = bibl.find(".//tei:monogr/tei:title[@level='m']", _TEI_NS)
        venue = (venue_el.text or "").strip() if venue_el is not None else ""

        # DOI
        doi = ""
        for idno in bibl.findall(".//tei:idno[@type='DOI']", _TEI_NS):
            doi = (idno.text or "").strip()

        # Volume / pages
        volume_el = bibl.find(".//tei:monogr/tei:imprint/tei:biblScope[@unit='volume']", _TEI_NS)
        volume = (volume_el.text or "").strip() if volume_el is not None else ""
        page_el = bibl.find(".//tei:monogr/tei:imprint/tei:biblScope[@unit='page']", _TEI_NS)
        pages = ""
        if page_el is not None:
            fr = page_el.get("from", "")
            to = page_el.get("to", "")
            pages = f"{fr}--{to}" if fr and to else (page_el.text or "").strip()

        # arXiv ID from idno
        arxiv_id = None
        for idno in bibl.findall(".//tei:idno", _TEI_NS):
            text = (idno.text or "").strip()
            m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", text)
            if m:
                arxiv_id = m.group(1)
                break

        key = f"pdf_ref_{idx + 1}"
        xml_id = bibl.get("{http://www.w3.org/XML/1998/namespace}id", "")
        if xml_id:
            key = xml_id

        return Reference(
            key=key,
            title=title,
            title_raw=title,
            author=author_str,
            author_clean=author_str,
            year=year,
            journal=venue if not venue_el or venue_el.get("level") == "j" else "",
            booktitle=venue if venue_el is not None and venue_el.get("level") == "m" else "",
            volume=volume,
            pages=pages,
            doi=doi,
            arxiv_id=arxiv_id,
            venue=venue,
        )

    @staticmethod
    def _parse_author(author_el: ET.Element) -> str:
        """Extract author name from TEI <author> element."""
        persname = author_el.find("tei:persName", _TEI_NS)
        if persname is None:
            return ""
        forename = persname.find("tei:forename", _TEI_NS)
        surname = persname.find("tei:surname", _TEI_NS)
        parts = []
        if forename is not None and forename.text:
            parts.append(forename.text.strip())
        if surname is not None and surname.text:
            parts.append(surname.text.strip())
        return " ".join(parts)

    # ── PyMuPDF fallback ──────────────────────────────────────────────────

    def _fallback_pymupdf(self, file_path: Path) -> list[Reference]:
        """Extract references from PDF text using PyMuPDF + regex."""
        try:
            import pymupdf
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                return []

        doc = pymupdf.open(str(file_path))
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        doc.close()

        return _parse_references_from_text(full_text)


def _parse_references_from_text(text: str) -> list[Reference]:
    """Best-effort extraction of references from plain text.

    Looks for a "References" / "Bibliography" section and splits entries
    by numbered patterns like [1], [2], ... or author-year patterns.
    """
    # Find references section
    ref_match = re.search(
        r"(?:^|\n)\s*(?:References|Bibliography|REFERENCES|BIBLIOGRAPHY)\s*\n",
        text,
    )
    if not ref_match:
        return []
    ref_text = text[ref_match.end():]

    # Try numbered references: [1] ... [2] ...
    entries = re.split(r"\n\s*\[(\d+)\]\s*", ref_text)
    refs = []

    if len(entries) > 2:
        # entries = ['preamble', '1', 'text1', '2', 'text2', ...]
        for i in range(1, len(entries) - 1, 2):
            num = entries[i]
            body = entries[i + 1].strip()
            if not body:
                continue
            ref = _parse_single_ref_text(body, key=f"ref_{num}")
            if ref:
                refs.append(ref)
    else:
        # Try splitting by blank lines or "Author (Year)" patterns
        chunks = re.split(r"\n\s*\n", ref_text)
        for idx, chunk in enumerate(chunks[:200]):
            chunk = chunk.strip()
            if len(chunk) < 20:
                continue
            ref = _parse_single_ref_text(chunk, key=f"ref_{idx + 1}")
            if ref:
                refs.append(ref)

    return refs


def _parse_single_ref_text(text: str, key: str) -> Optional[Reference]:
    """Parse a single reference from unstructured text."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 15:
        return None

    # Try to extract: Authors. "Title." Venue, Year.
    # or: Authors (Year). Title. Venue.
    year = ""
    year_m = re.search(r"[\(,]\s*((?:19|20)\d{2})\s*[\),.]", text)
    if year_m:
        year = year_m.group(1)

    # Try to find title in quotes or after first period
    title = ""
    title_m = re.search(r'["\u201c](.+?)["\u201d]', text)
    if title_m:
        title = title_m.group(1).strip()
    else:
        # Heuristic: title is between first and second period
        parts = text.split(".")
        if len(parts) >= 2:
            candidate = parts[1].strip() if parts[0] and len(parts[0]) < 150 else parts[0].strip()
            if 10 < len(candidate) < 300:
                title = candidate

    # Extract authors (text before the year or title)
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
        key=key,
        title=title,
        title_raw=title,
        author=author,
        author_clean=author,
        year=year,
        doi=doi,
        arxiv_id=arxiv_id,
    )
