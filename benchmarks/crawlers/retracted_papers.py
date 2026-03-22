"""S-tier crawler: Bulk retracted papers from Crossref + PubMed.

Scales retracted_papers split from 250 to 10,000+ with ground truth labels.

Usage:
    python -m benchmarks.crawlers retracted --count 10000
    python -m benchmarks.crawlers retracted --count 5000 --source crossref
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .base import BaseCrawler, NCBI_API_KEY

logger = logging.getLogger(__name__)

# Diverse subject queries for Crossref retraction search
CROSSREF_SUBJECTS = [
    "",  # no filter = all subjects
    "medicine", "biology", "chemistry", "psychology",
    "engineering", "environmental science", "materials science",
    "neuroscience", "pharmacology", "public health",
]

# Diverse queries for real paper controls
REAL_PAPER_QUERIES = [
    "deep learning", "natural language processing", "clinical trial",
    "genomics", "cancer treatment", "machine learning",
    "drug discovery", "protein structure", "epidemiology",
    "renewable energy", "materials science", "neural network",
    "meta-analysis", "randomized controlled trial", "quantum computing",
    "climate change", "immunotherapy", "CRISPR", "microbiome",
    "computer vision",
]


class RetractedPapersCrawler(BaseCrawler):
    """Crawl retracted papers from Crossref and PubMed."""

    name = "retracted_papers"

    def crawl(self, count: int = 10000, source: str = "both",
              real_ratio: float = 0.2, **kwargs) -> int:
        """Fetch retracted papers and real controls.

        Args:
            count: Target number of retracted papers.
            source: 'crossref', 'pubmed', or 'both'.
            real_ratio: Ratio of real control papers to retracted papers.
        """
        total = 0
        cp = self._load_checkpoint()
        start_offset = cp.get("last_offset", 0)

        if source in ("crossref", "both"):
            n = self._crawl_crossref(count, start_offset)
            total += n

        if source in ("pubmed", "both"):
            n = self._crawl_pubmed(count)
            total += n

        # Real controls
        real_count = int(count * real_ratio)
        if real_count > 0:
            n = self._crawl_real_controls(real_count)
            total += n

        self._flush_seen_ids()
        self._clear_checkpoint()
        logger.info("RetractedPapersCrawler: wrote %d total records", total)
        return total

    def _crawl_crossref(self, count: int, start_offset: int = 0) -> int:
        """Fetch retracted papers from Crossref using cursor-based pagination."""
        out_path = self.output_dir / "crossref_retracted.jsonl"
        written = 0
        per_page = 100
        cursor = "*"
        offset = start_offset

        logger.info("Crawling Crossref retracted papers (target: %d)...", count)

        while written < count:
            params = {
                "filter": "update-type:retraction",
                "rows": per_page,
                "cursor": cursor,
                "select": "DOI,title,author,published-print,published-online,"
                          "container-title,update-to,type,subject",
            }

            resp = self._get("https://api.crossref.org/works", params=params)
            if resp is None:
                break

            data = resp.json()
            msg = data.get("message", {})
            items = msg.get("items", [])
            next_cursor = msg.get("next-cursor")

            if not items:
                logger.info("No more Crossref results at offset %d", offset)
                break

            batch = []
            for item in items:
                # Retraction notices point to original papers via update-to
                original_dois = []
                for update in item.get("update-to", []):
                    odoi = update.get("DOI", "")
                    if odoi:
                        original_dois.append(odoi)

                if original_dois:
                    for odoi in original_dois[:1]:
                        if self._is_seen(odoi):
                            continue
                        record = self._fetch_original_paper(odoi)
                        if record:
                            self._mark_seen(odoi)
                            batch.append(record)
                            written += 1
                            if written >= count:
                                break
                else:
                    record = self._crossref_to_record(item)
                    if record and record["title"] != "Retraction Notice":
                        doi = record["doi"]
                        if not self._is_seen(doi):
                            self._mark_seen(doi)
                            batch.append(record)
                            written += 1

                if written >= count:
                    break

            if batch:
                self._append_jsonl(batch, out_path)

            offset += len(items)
            logger.info("  Crossref: %d/%d retracted papers...", written, count)
            self._save_checkpoint(offset, written, cursor=cursor)

            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return written

    def _fetch_original_paper(self, doi: str) -> Optional[dict]:
        """Fetch original retracted paper by DOI."""
        resp = self._get(f"https://api.crossref.org/works/{doi}")
        if resp is None:
            return None
        item = resp.json().get("message", {})
        record = self._crossref_to_record(item)
        if record:
            record["expected_retracted"] = True
            record["expected_risk"] = "HIGH"
        return record

    @staticmethod
    def _crossref_to_record(item: dict) -> Optional[dict]:
        """Convert Crossref work item to benchmark record."""
        doi = item.get("DOI", "")
        if not doi:
            return None

        titles = item.get("title", [])
        title = titles[0] if titles else ""
        if not title or len(title) < 15:
            return None

        # Skip retraction notices (not the original paper)
        title_lower = title.lower()
        if title_lower.startswith("retract") or title_lower.startswith("withdrawal"):
            return None

        authors = []
        for auth in item.get("author", []):
            family = auth.get("family", "")
            given = auth.get("given", "")
            if family:
                authors.append(f"{family}, {given}" if given else family)

        year = ""
        for date_field in ["published-print", "published-online"]:
            date_parts = item.get(date_field, {}).get("date-parts", [[]])
            if date_parts and date_parts[0] and date_parts[0][0]:
                year = str(date_parts[0][0])
                break

        containers = item.get("container-title", [])
        venue = containers[0] if containers else ""

        # Require at least one author
        if not authors:
            return None

        return {
            "doi": doi,
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "source": "crossref_retraction",
            "expected_retracted": True,
            "expected_risk": "HIGH",
            "subjects": item.get("subject", []),
            "type": item.get("type", ""),
        }

    def _crawl_pubmed(self, count: int) -> int:
        """Fetch retracted papers from PubMed in batches."""
        out_path = self.output_dir / "pubmed_retracted.jsonl"
        written = 0
        batch_size = 500
        retstart = 0

        logger.info("Crawling PubMed retracted papers (target: %d)...", count)

        # Step 1: Get total count and PMIDs
        search_params = {
            "db": "pubmed",
            "term": '"Retracted Publication"[pt]',
            "retmax": 0,
            "retmode": "json",
        }
        if NCBI_API_KEY:
            search_params["api_key"] = NCBI_API_KEY

        resp = self._get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                          params=search_params)
        if resp is None:
            return 0

        total_available = int(resp.json().get("esearchresult", {}).get("count", 0))
        target = min(count, total_available)
        logger.info("  PubMed has %d retracted publications, targeting %d",
                     total_available, target)

        while written < target:
            # Search for PMIDs
            search_params = {
                "db": "pubmed",
                "term": '"Retracted Publication"[pt]',
                "retmax": min(batch_size, target - written),
                "retstart": retstart,
                "retmode": "json",
                "sort": "date",
            }
            if NCBI_API_KEY:
                search_params["api_key"] = NCBI_API_KEY

            resp = self._get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params=search_params)
            if resp is None:
                break

            pmids = resp.json().get("esearchresult", {}).get("idlist", [])
            if not pmids:
                break

            # Fetch details in sub-batches of 200
            for i in range(0, len(pmids), 200):
                sub_batch = pmids[i:i + 200]
                fetch_params = {
                    "db": "pubmed",
                    "id": ",".join(sub_batch),
                    "retmode": "xml",
                    "rettype": "abstract",
                }
                if NCBI_API_KEY:
                    fetch_params["api_key"] = NCBI_API_KEY

                resp = self._get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    params=fetch_params, timeout=60)
                if resp is None:
                    continue

                records = self._parse_pubmed_xml(resp.text)
                # Dedup
                new_records = []
                for rec in records:
                    key = rec.get("doi") or rec.get("pmid", "")
                    if key and not self._is_seen(key):
                        self._mark_seen(key)
                        new_records.append(rec)

                if new_records:
                    self._append_jsonl(new_records, out_path)
                    written += len(new_records)

            retstart += len(pmids)
            logger.info("  PubMed: %d/%d retracted papers...", written, target)
            self._save_checkpoint(retstart, written)

        return written

    def _parse_pubmed_xml(self, xml_text: str) -> list[dict]:
        """Parse PubMed XML into benchmark records."""
        records = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error("XML parse error: %s", e)
            return []

        for article in root.findall(".//PubmedArticle"):
            try:
                record = self._parse_one_article(article)
                if record:
                    records.append(record)
            except Exception as e:
                logger.debug("Skipping article: %s", e)

        return records

    @staticmethod
    def _parse_one_article(article) -> Optional[dict]:
        """Parse a single PubmedArticle element."""
        medline = article.find("MedlineCitation")
        if medline is None:
            return None

        art = medline.find("Article")
        if art is None:
            return None

        title_el = art.find("ArticleTitle")
        title = title_el.text if title_el is not None and title_el.text else ""
        if not title:
            return None

        pmid_el = medline.find("PMID")
        pmid = pmid_el.text if pmid_el is not None else ""

        authors = []
        author_list = art.find("AuthorList")
        if author_list is not None:
            for auth in author_list.findall("Author"):
                last = auth.findtext("LastName", "")
                init = auth.findtext("Initials", "")
                if last:
                    authors.append(f"{last}, {init}" if init else last)

        year = ""
        journal = art.find("Journal")
        if journal is not None:
            ji = journal.find("JournalIssue")
            if ji is not None:
                pd = ji.find("PubDate")
                if pd is not None:
                    year = pd.findtext("Year", "")
                    if not year:
                        medline_date = pd.findtext("MedlineDate", "")
                        if medline_date:
                            year = medline_date[:4]

        venue = ""
        if journal is not None:
            venue = journal.findtext("Title", "")

        doi = ""
        article_data = article.find("PubmedData")
        if article_data is not None:
            for aid in article_data.findall(".//ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text or ""
                    break

        return {
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "source": "pubmed_retracted",
            "expected_retracted": True,
            "expected_risk": "HIGH",
        }

    def _crawl_real_controls(self, count: int) -> int:
        """Fetch real non-retracted papers as controls."""
        per_query = max(count // len(REAL_PAPER_QUERIES), 5)
        written = 0

        logger.info("Crawling %d real control papers...", count)

        for query in REAL_PAPER_QUERIES:
            if written >= count:
                break

            params = {
                "query": query,
                "rows": min(per_query, 100),
                "sort": "is-referenced-by-count",
                "order": "desc",
                "filter": "type:journal-article,has-abstract:true",
                "select": "DOI,title,author,published-print,published-online,"
                          "container-title,is-referenced-by-count",
            }

            resp = self._get("https://api.crossref.org/works", params=params)
            if resp is None:
                continue

            batch = []
            for item in resp.json().get("message", {}).get("items", []):
                doi = item.get("DOI", "")
                titles = item.get("title", [])
                title = titles[0] if titles else ""
                if not doi or not title:
                    continue
                if self._is_seen(doi):
                    continue

                authors = []
                for auth in item.get("author", []):
                    family = auth.get("family", "")
                    given = auth.get("given", "")
                    if family:
                        authors.append(f"{family}, {given}" if given else family)

                year = ""
                for df in ["published-print", "published-online"]:
                    dp = item.get(df, {}).get("date-parts", [[]])
                    if dp and dp[0] and dp[0][0]:
                        year = str(dp[0][0])
                        break

                containers = item.get("container-title", [])
                venue = containers[0] if containers else ""

                self._mark_seen(doi)
                batch.append({
                    "doi": doi,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "venue": venue,
                    "source": f"crossref_real_{query.replace(' ', '_')}",
                    "expected_retracted": False,
                    "expected_found": True,
                    "expected_risk": "LOW",
                    "citation_count": item.get("is-referenced-by-count", 0),
                })
                written += 1
                if written >= count:
                    break

            if batch:
                slug = query.replace(" ", "_")
                self._append_jsonl(batch, self.output_dir / f"crossref_real_{slug}.jsonl")

            logger.info("  Real controls: %d/%d (query: %s)", written, count, query)

        return written
