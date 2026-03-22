"""Fetch retracted papers from Crossref and PubMed for IntegriRef benchmarks.

Data sources:
  1. Crossref REST API — filter=update-type:retraction (Retraction Watch data)
  2. PubMed E-utilities — "Retracted Publication"[pt] filter
  3. Crossref retraction metadata (reason, date, original DOI)

Usage:
    python -m benchmarks.fetch_retracted --source crossref --count 200
    python -m benchmarks.fetch_retracted --source pubmed --count 100
    python -m benchmarks.fetch_retracted --source both --count 300

Output: benchmarks/data/retraction_watch/retracted_{source}.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data" / "retraction_watch"
CONTACT_EMAIL = os.getenv("CROSSREF_EMAIL", "integriref-bench@example.com")

# ── Crossref fetcher ─────────────────────────────────────────────────────

def fetch_crossref_retracted(
    count: int = 200,
    offset: int = 0,
    subject: str = "",
    seed: int = 42,
) -> list[dict]:
    """Fetch retracted papers from Crossref REST API.

    Uses filter=update-type:retraction to get papers that have been retracted.
    The Retraction Watch database was integrated into Crossref in 2023.
    """
    base_url = "https://api.crossref.org/works"
    results = []
    per_page = min(count, 100)  # Crossref max rows=100
    total_fetched = 0

    headers = {
        "User-Agent": f"IntegriRef-Benchmark/1.0 (mailto:{CONTACT_EMAIL})",
    }

    logger.info("Fetching %d retracted papers from Crossref...", count)

    while total_fetched < count:
        params = {
            "filter": "update-type:retraction",
            "rows": per_page,
            "offset": offset + total_fetched,
            "sort": "deposited",
            "order": "desc",
            "select": "DOI,title,author,published-print,published-online,"
                      "container-title,update-to,type,subject",
        }
        if subject:
            params["filter"] += f",subject:{subject}"

        try:
            resp = requests.get(base_url, params=params, headers=headers,
                                timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("Crossref API error at offset %d: %s",
                         offset + total_fetched, e)
            break

        items = data.get("message", {}).get("items", [])
        if not items:
            logger.info("No more results from Crossref at offset %d",
                        offset + total_fetched)
            break

        for item in items:
            # The retraction filter returns retraction NOTICES.
            # Follow update-to to get the ORIGINAL retracted paper's DOI.
            original_dois = []
            for update in item.get("update-to", []):
                odoi = update.get("DOI", "")
                if odoi:
                    original_dois.append(odoi)

            if original_dois:
                # Fetch the original retracted paper
                for odoi in original_dois[:1]:
                    record = _fetch_original_paper(odoi, headers)
                    if record:
                        results.append(record)
                        total_fetched += 1
                        if total_fetched >= count:
                            break
            else:
                # If no update-to, this might be the retracted paper itself
                record = _crossref_to_record(item)
                if record and record["title"] != "Retraction Notice":
                    results.append(record)
                    total_fetched += 1

            if total_fetched >= count:
                break

        logger.info("  Fetched %d/%d retracted papers...",
                    total_fetched, count)

        # Rate limiting — be polite to Crossref
        time.sleep(1.0)

    return results


def _fetch_original_paper(doi: str, headers: dict) -> Optional[dict]:
    """Fetch the original retracted paper by DOI from Crossref."""
    url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        item = resp.json().get("message", {})
        record = _crossref_to_record(item)
        if record:
            record["expected_retracted"] = True
            record["expected_risk"] = "HIGH"
        time.sleep(0.5)  # Rate limit
        return record
    except Exception as e:
        logger.debug("Could not fetch original paper %s: %s", doi, e)
        return None


def _crossref_to_record(item: dict) -> Optional[dict]:
    """Convert a Crossref work item to our benchmark record format."""
    doi = item.get("DOI", "")
    if not doi:
        return None

    # Extract title
    titles = item.get("title", [])
    title = titles[0] if titles else ""
    if not title:
        return None

    # Extract authors
    authors = []
    for auth in item.get("author", []):
        family = auth.get("family", "")
        given = auth.get("given", "")
        if family:
            authors.append(f"{family}, {given}" if given else family)

    # Extract year
    year = ""
    for date_field in ["published-print", "published-online"]:
        date_parts = item.get(date_field, {}).get("date-parts", [[]])
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = str(date_parts[0][0])
            break

    # Extract venue
    containers = item.get("container-title", [])
    venue = containers[0] if containers else ""

    # Extract retraction info
    update_to = item.get("update-to", [])
    retraction_info = {}
    for update in update_to:
        if update.get("type") == "retraction":
            retraction_info = {
                "retracted_doi": update.get("DOI", ""),
                "retraction_date": update.get("updated", {}).get(
                    "date-parts", [[]])[0],
            }
            break

    return {
        "doi": doi,
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "source": "crossref_retraction",
        "expected_retracted": True,
        "expected_risk": "HIGH",
        "retraction_info": retraction_info,
        "subjects": item.get("subject", []),
        "type": item.get("type", ""),
    }


# ── PubMed fetcher ───────────────────────────────────────────────────────

def fetch_pubmed_retracted(
    count: int = 100,
    retstart: int = 0,
) -> list[dict]:
    """Fetch retracted papers from PubMed E-utilities.

    Uses publication type filter "Retracted Publication"[pt].
    """
    esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    results = []

    logger.info("Searching PubMed for %d retracted publications...", count)

    # Step 1: Search for retracted publication PMIDs
    search_params = {
        "db": "pubmed",
        "term": '"Retracted Publication"[pt]',
        "retmax": count,
        "retstart": retstart,
        "retmode": "json",
        "sort": "date",
    }

    try:
        resp = requests.get(esearch_url, params=search_params, timeout=30)
        resp.raise_for_status()
        search_data = resp.json()
    except Exception as e:
        logger.error("PubMed search error: %s", e)
        return []

    pmids = search_data.get("esearchresult", {}).get("idlist", [])
    total_count = int(search_data.get("esearchresult", {}).get("count", 0))
    logger.info("  Found %d total retracted papers, fetching %d...",
                total_count, len(pmids))

    if not pmids:
        return []

    # Step 2: Fetch details in batches
    batch_size = 50
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "retmode": "xml",
            "rettype": "abstract",
        }

        try:
            resp = requests.get(efetch_url, params=fetch_params, timeout=30)
            resp.raise_for_status()
            records = _parse_pubmed_xml(resp.text)
            results.extend(records)
            logger.info("  Parsed %d/%d records...",
                        len(results), len(pmids))
        except Exception as e:
            logger.error("PubMed fetch error for batch %d: %s", i, e)

        time.sleep(0.4)  # NCBI rate limit: 3 requests/sec

    return results


def _parse_pubmed_xml(xml_text: str) -> list[dict]:
    """Parse PubMed XML response into benchmark records.

    Uses stdlib xml.etree since we don't want lxml dependency.
    """
    import xml.etree.ElementTree as ET

    records = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("XML parse error: %s", e)
        return []

    for article in root.findall(".//PubmedArticle"):
        try:
            record = _parse_one_pubmed_article(article)
            if record:
                records.append(record)
        except Exception as e:
            logger.debug("Skipping article: %s", e)

    return records


def _parse_one_pubmed_article(article) -> Optional[dict]:
    """Parse a single PubmedArticle element."""
    medline = article.find("MedlineCitation")
    if medline is None:
        return None

    art = medline.find("Article")
    if art is None:
        return None

    # Title
    title_el = art.find("ArticleTitle")
    title = title_el.text if title_el is not None and title_el.text else ""
    if not title:
        return None

    # PMID
    pmid_el = medline.find("PMID")
    pmid = pmid_el.text if pmid_el is not None else ""

    # Authors
    authors = []
    author_list = art.find("AuthorList")
    if author_list is not None:
        for auth in author_list.findall("Author"):
            last = auth.findtext("LastName", "")
            init = auth.findtext("Initials", "")
            if last:
                authors.append(f"{last}, {init}" if init else last)

    # Year
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

    # Venue (journal title)
    venue = ""
    if journal is not None:
        venue = journal.findtext("Title", "")

    # DOI
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


# ── Fetch real papers (positive controls) ────────────────────────────────

def fetch_crossref_real_papers(
    count: int = 100,
    query: str = "machine learning",
) -> list[dict]:
    """Fetch well-cited real papers from Crossref as positive controls."""
    base_url = "https://api.crossref.org/works"
    results = []

    headers = {
        "User-Agent": f"IntegriRef-Benchmark/1.0 (mailto:{CONTACT_EMAIL})",
    }

    logger.info("Fetching %d real papers from Crossref (query: '%s')...",
                count, query)

    params = {
        "query": query,
        "rows": min(count, 100),
        "sort": "is-referenced-by-count",
        "order": "desc",
        "filter": "type:journal-article,has-abstract:true",
        "select": "DOI,title,author,published-print,published-online,"
                  "container-title,is-referenced-by-count",
    }

    try:
        resp = requests.get(base_url, params=params, headers=headers,
                            timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Crossref API error: %s", e)
        return []

    for item in data.get("message", {}).get("items", []):
        doi = item.get("DOI", "")
        titles = item.get("title", [])
        title = titles[0] if titles else ""
        if not doi or not title:
            continue

        authors = []
        for auth in item.get("author", []):
            family = auth.get("family", "")
            given = auth.get("given", "")
            if family:
                authors.append(f"{family}, {given}" if given else family)

        year = ""
        for date_field in ["published-print", "published-online"]:
            date_parts = item.get(date_field, {}).get("date-parts", [[]])
            if date_parts and date_parts[0]:
                year = str(date_parts[0][0])
                break

        containers = item.get("container-title", [])
        venue = containers[0] if containers else ""

        results.append({
            "doi": doi,
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "source": "crossref_real",
            "expected_retracted": False,
            "expected_found": True,
            "expected_risk": "LOW",
            "citation_count": item.get("is-referenced-by-count", 0),
        })

    logger.info("  Got %d real papers", len(results))
    return results


# ── Main ─────────────────────────────────────────────────────────────────

def save_jsonl(records: list[dict], path: Path):
    """Save records as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("Saved %d records to %s", len(records), path)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch retracted papers for IntegriRef benchmarks")
    parser.add_argument("--source", choices=["crossref", "pubmed", "both"],
                        default="both")
    parser.add_argument("--count", type=int, default=200,
                        help="Number of papers to fetch per source")
    parser.add_argument("--real-count", type=int, default=100,
                        help="Number of real papers to fetch as controls")
    parser.add_argument("--offset", type=int, default=0,
                        help="Starting offset for pagination")
    parser.add_argument("--output-dir", type=str, default=str(DATA_DIR))
    args = parser.parse_args()

    out_dir = Path(args.output_dir)

    if args.source in ("crossref", "both"):
        retracted = fetch_crossref_retracted(
            count=args.count, offset=args.offset)
        save_jsonl(retracted, out_dir / "crossref_retracted.jsonl")

    if args.source in ("pubmed", "both"):
        retracted = fetch_pubmed_retracted(
            count=args.count, retstart=args.offset)
        save_jsonl(retracted, out_dir / "pubmed_retracted.jsonl")

    # Positive controls
    for query in ["deep learning", "natural language processing",
                   "clinical trial", "genomics"]:
        reals = fetch_crossref_real_papers(
            count=args.real_count // 4, query=query)
        save_jsonl(reals, out_dir / f"crossref_real_{query.replace(' ', '_')}.jsonl")

    logger.info("Done! Data saved to %s", out_dir)


if __name__ == "__main__":
    main()
