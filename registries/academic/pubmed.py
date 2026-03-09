"""PubMed / NCBI E-utilities registry — biomedical literature.

API: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/
Auth: None (API key recommended for higher rate: 10 req/s vs 3 req/s)
Rate: 3 req/s without key
Coverage: 37M+ biomedical citations (PubMed + PMC)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

from core.entity import EntityType, ICEntity
from core.registry import RegistryAdapter, RegistryInfo


class PubMedRegistry(RegistryAdapter):

    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def info(self) -> RegistryInfo:
        return RegistryInfo(
            name="pubmed",
            domain="academic",
            base_url=self.BASE,
            auth_type="none",
            rate_limit=1.0,
            coverage="37M+ biomedical citations",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type: str, id_value: str) -> Optional[ICEntity]:
        if id_type == "pmid":
            return self._fetch_by_pmid(id_value)
        if id_type == "doi":
            return self._search_doi(id_value)
        return None

    def search(self, title: str, author: str = "",
               year: str = "", **kwargs) -> list[ICEntity]:
        # Step 1: esearch to get PMIDs
        query_parts = [title]
        if author:
            query_parts.append(f"{author}[Author]")
        if year:
            query_parts.append(f"{year}[Date - Publication]")
        query = " AND ".join(query_parts)

        resp = self._get(
            f"{self.BASE}/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmax": 5, "retmode": "json"},
        )
        if not resp:
            return []
        try:
            id_list = resp.json().get("esearchresult", {}).get("idlist", [])
        except (ValueError, AttributeError):
            return []
        if not id_list:
            return []

        # Step 2: efetch to get details
        return [e for pmid in id_list
                if (e := self._fetch_by_pmid(pmid)) is not None]

    def _search_doi(self, doi: str) -> Optional[ICEntity]:
        resp = self._get(
            f"{self.BASE}/esearch.fcgi",
            params={"db": "pubmed", "term": f"{doi}[DOI]", "retmax": 1, "retmode": "json"},
        )
        if not resp:
            return None
        try:
            ids = resp.json().get("esearchresult", {}).get("idlist", [])
        except (ValueError, AttributeError):
            return None
        if ids:
            return self._fetch_by_pmid(ids[0])
        return None

    def _fetch_by_pmid(self, pmid: str) -> Optional[ICEntity]:
        resp = self._get(
            f"{self.BASE}/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "xml"},
        )
        if not resp:
            return None
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return None

        article = root.find(".//PubmedArticle/MedlineCitation/Article")
        if article is None:
            return None

        title_el = article.find("ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""

        authors = []
        for author_el in article.findall(".//AuthorList/Author"):
            last = author_el.findtext("LastName", "")
            first = author_el.findtext("ForeName", "")
            if last:
                authors.append(f"{first} {last}".strip())

        year = ""
        pub_date = article.find(".//Journal/JournalIssue/PubDate")
        if pub_date is not None:
            year = pub_date.findtext("Year", "")
            if not year:
                medline = pub_date.findtext("MedlineDate", "")
                m = re.search(r"((?:19|20)\d{2})", medline)
                if m:
                    year = m.group(1)

        venue = article.findtext(".//Journal/Title", "")

        # DOI
        doi = ""
        for eid in root.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if eid.get("IdType") == "doi":
                doi = (eid.text or "").strip()

        # PMC ID
        pmc = ""
        for eid in root.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if eid.get("IdType") == "pmc":
                pmc = (eid.text or "").strip()

        # Abstract
        abstract_parts = []
        for abs_el in article.findall(".//Abstract/AbstractText"):
            abstract_parts.append("".join(abs_el.itertext()))
        abstract = " ".join(abstract_parts)

        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            metadata={
                "abstract": abstract,
                "mesh_terms": [
                    mh.findtext("DescriptorName", "")
                    for mh in root.findall(".//MeshHeadingList/MeshHeading")
                ],
            },
            source_registries=["pubmed"],
        )
        entity.add_external_id("pubmed", "pmid", pmid)
        if doi:
            entity.add_external_id("pubmed", "doi", doi)
        if pmc:
            entity.add_external_id("pubmed", "pmc", pmc)
        entity.normalize()
        return entity
