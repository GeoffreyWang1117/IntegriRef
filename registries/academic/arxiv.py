"""arXiv registry — open-access preprint server.

API: http://export.arxiv.org/api/query
Auth: None
Rate: 3s between requests (official guideline)
Coverage: 2.5M+ papers (physics, CS, math, bio, econ, etc.)
"""

from __future__ import annotations

import re
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class ArXivRegistry(RegistryAdapter):

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="arxiv",
            domain="academic",
            base_url="http://export.arxiv.org/api/query",
            auth_type="none",
            rate_limit=3.0,
            coverage="2.5M+ open-access preprints",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type != "arxiv":
            return None
        resp = self._get(
            "http://export.arxiv.org/api/query",
            params={"id_list": id_value},
        )
        if not resp:
            return None
        return self._parse_atom(resp.text, id_value)

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        parts = []
        if title:
            parts.append(f'ti:"{title}"')
        if author:
            surname = author.split()[-1] if author.split() else author
            parts.append(f"au:{surname}")
        query = " AND ".join(parts) if parts else title
        resp = self._get(
            "http://export.arxiv.org/api/query",
            params={"search_query": query, "max_results": 5},
        )
        if not resp:
            return []
        return self._parse_atom_multi(resp.text)

    def _parse_atom(self, xml: str, arxiv_id: str) -> Optional[ICEntity]:
        """Parse single arXiv Atom entry."""
        titles = re.findall(r"<title[^>]*>(.*?)</title>", xml, re.DOTALL)
        if len(titles) < 2:
            return None
        title = re.sub(r"\s+", " ", titles[1]).strip()
        if title.lower().startswith("error"):
            return None

        authors = re.findall(r"<name>(.*?)</name>", xml)
        published = re.search(r"<published>(.*?)</published>", xml)
        year = published.group(1)[:4] if published else ""

        # Extract categories
        categories = re.findall(r'<category[^>]*term="([^"]+)"', xml)

        # Extract DOI if present
        doi_m = re.search(r'<link[^>]*href="http[s]?://dx\.doi\.org/([^"]+)"', xml)
        doi = doi_m.group(1) if doi_m else ""

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            metadata={
                "categories": categories,
                "preprint": True,
            },
            source_registries=["arxiv"],
        )
        entity.add_external_id("arxiv", "arxiv", arxiv_id)
        if doi:
            entity.add_external_id("arxiv", "doi", doi)
        entity.normalize()
        return entity

    def _parse_atom_multi(self, xml: str) -> list[ICEntity]:
        """Parse multiple entries from arXiv search results."""
        entries = re.split(r"<entry>", xml)[1:]  # skip feed header
        results = []
        for entry_xml in entries:
            # Extract arXiv ID
            id_m = re.search(r"<id>http://arxiv\.org/abs/(.+?)</id>", entry_xml)
            if not id_m:
                continue
            arxiv_id = id_m.group(1)
            # Wrap in entry tags for parser
            e = self._parse_atom(f"<feed><title>feed</title><entry>{entry_xml}</entry></feed>",
                                 arxiv_id)
            if e:
                results.append(e)
        return results
