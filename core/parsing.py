"""Common parsing utilities — reduce duplication across registry adapters.

Centralizes the repeated patterns for extracting title, authors, year,
DOI, and other fields from heterogeneous API responses.
"""

from __future__ import annotations

import re
from typing import Any, Optional


def extract_title(data: dict, *keys: str) -> str:
    """Extract title from data dict, trying multiple keys.

    Handles str, list[str], and list[dict] formats.

    Args:
        data: Response dict
        *keys: Keys to try in order (default: "title")

    Returns:
        Cleaned title string or "".
    """
    if not keys:
        keys = ("title",)

    for key in keys:
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            val = val[0] if val else ""
            if isinstance(val, dict):
                val = val.get("value", val.get("text", val.get("title", "")))
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def extract_authors(data: dict, *keys: str) -> list[str]:
    """Extract author list from data dict.

    Handles these formats:
      - str: "Smith, J and Doe, A" or "Smith, J; Doe, A"
      - list[str]: ["Smith, J", "Doe, A"]
      - list[dict]: [{"name": "Smith, J"}, {"given": "J", "family": "Smith"}]
      - Single dict with 'name' key

    Args:
        data: Response dict
        *keys: Keys to try (default: "authors", "author", "creators")
    """
    if not keys:
        keys = ("authors", "author", "creators", "dc:creator",
                "authFullName_s", "creator")

    for key in keys:
        val = data.get(key)
        if val is None:
            continue

        if isinstance(val, str) and val.strip():
            # "Smith, J and Doe, A" or semicolon-separated
            if " and " in val:
                return [a.strip() for a in val.split(" and ") if a.strip()]
            if ";" in val:
                return [a.strip() for a in val.split(";") if a.strip()]
            return [val.strip()]

        if isinstance(val, list):
            authors = []
            for item in val:
                if isinstance(item, str) and item.strip():
                    authors.append(item.strip())
                elif isinstance(item, dict):
                    name = _extract_author_name(item)
                    if name:
                        authors.append(name)
            if authors:
                return authors

        if isinstance(val, dict):
            name = _extract_author_name(val)
            if name:
                return [name]

    return []


def _extract_author_name(author_dict: dict) -> str:
    """Extract author name from a dict with various key conventions."""
    # Try 'name' directly
    name = author_dict.get("name", "")
    if name and isinstance(name, str):
        return name.strip()

    # Try given + family
    given = author_dict.get("given", author_dict.get("first", ""))
    family = author_dict.get("family", author_dict.get("last",
                              author_dict.get("surname", "")))
    if family:
        if given:
            return f"{given} {family}".strip()
        return family.strip()

    # Try fullname, full_name, etc.
    for key in ("fullname", "full_name", "display_name", "label"):
        val = author_dict.get(key, "")
        if val and isinstance(val, str):
            return val.strip()

    return ""


def extract_year(data: dict, *keys: str) -> str:
    """Extract 4-digit year from data dict.

    Handles:
      - int: 2017
      - str: "2017", "2017-01-15", "January 2017"
      - list: ["2017"] (take first)

    Args:
        data: Response dict
        *keys: Keys to try (default: "year", "date", "publicationDate", etc.)
    """
    if not keys:
        keys = ("year", "date", "publicationDate", "publication_date",
                "published", "dcyear", "dc:date", "producedDateY_i",
                "issued", "created")

    for key in keys:
        val = data.get(key)
        if val is None:
            continue

        if isinstance(val, list):
            val = val[0] if val else ""

        if isinstance(val, dict):
            # Handle {"date-parts": [[2017, 1, 15]]}
            parts = val.get("date-parts", [])
            if parts and isinstance(parts[0], list) and parts[0]:
                return str(parts[0][0])
            val = val.get("value", val.get("text", ""))

        val = str(val).strip()
        if not val:
            continue

        # Pure 4-digit year
        if re.match(r"^\d{4}$", val):
            return val

        # Extract year from longer string
        m = re.search(r"(\d{4})", val)
        if m:
            year = int(m.group(1))
            if 1800 <= year <= 2100:
                return str(year)

    return ""


def extract_doi(data: dict, *keys: str) -> str:
    """Extract DOI from data dict.

    Handles:
      - "10.1234/test"
      - "https://doi.org/10.1234/test"
      - Nested in identifiers list

    Args:
        data: Response dict
        *keys: Keys to try
    """
    if not keys:
        keys = ("doi", "DOI", "doiId_s", "digital_object_identifier",
                "dc:identifier")

    for key in keys:
        val = data.get(key)
        if val is None:
            continue

        if isinstance(val, list):
            for item in val:
                item_str = str(item).strip()
                doi = _clean_doi(item_str)
                if doi:
                    return doi
            continue

        doi = _clean_doi(str(val).strip())
        if doi:
            return doi

    return ""


def _clean_doi(s: str) -> str:
    """Extract clean DOI from a string."""
    if not s:
        return ""
    # Remove URL prefix
    s = re.sub(r"^https?://doi\.org/", "", s)
    s = re.sub(r"^https?://dx\.doi\.org/", "", s)
    # Validate DOI format
    if re.match(r"^10\.\d{4,}/\S+$", s):
        return s
    return ""


def extract_venue(data: dict, *keys: str) -> str:
    """Extract venue/journal name.

    Handles str, dict with 'name' or 'title' key.
    """
    if not keys:
        keys = ("venue", "journal", "container-title",
                "journalTitle_s", "publisher", "booktitle",
                "source", "publication_name")

    for key in keys:
        val = data.get(key)
        if val is None:
            continue

        if isinstance(val, list):
            val = val[0] if val else ""

        if isinstance(val, dict):
            val = val.get("name", val.get("title", val.get("value", "")))

        if isinstance(val, str) and val.strip():
            return val.strip()

    return ""


def safe_get(data: dict, *path: str, default: Any = "") -> Any:
    """Safely navigate nested dicts.

    Usage:
        safe_get(data, "response", "docs", 0, "title")
    """
    current = data
    for key in path:
        if isinstance(current, dict):
            current = current.get(key, None)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(key)]
            except (IndexError, ValueError, TypeError):
                return default
        else:
            return default
        if current is None:
            return default
    return current
