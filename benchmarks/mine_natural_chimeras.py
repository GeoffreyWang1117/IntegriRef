"""Mine naturally-occurring metadata chimeras from real reference lists (3tFe-S2).

The paper's chimera evidence is synthetic: author/year swaps injected into
resolving DOIs. Reviewer 3tFe asked for naturally-occurring cases. Publishers
deposit their articles' reference lists into Crossref, and those deposited
entries carry each reference *as printed* — including the citing authors'
mistakes. When a deposited reference carries a DOI plus a year or first author
that contradicts that DOI's own record, it is exactly a metadata chimera: an
identifier that resolves, paired with metadata that does not match.

The hard part is precision, not yield. Three artefact classes masquerade as
citation errors and are all filtered here:

  * legacy backfiles — Wiley/ACS re-deposits carry the digitisation year, so a
    correctly-cited 1981 paper looks 24 years adrift;
  * corporate bylines — "…Study Group" versus the record's first personal
    author is a convention difference, not an error;
  * transliteration — "Gøtzsche" and "Gotzsche" are the same person.

Every candidate is therefore confirmed against OpenAlex as a second,
independent registry: a case survives only when the printed metadata
contradicts *both* registries. Cases where Crossref and OpenAlex disagree with
each other are registry artefacts and are dropped.

Output is a jsonl split in the synthetic chimera splits' schema, so the same
benchmark runner scores it.
"""
import argparse
import html
import json
import logging
import random
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("mine_chimera")
logger.setLevel(logging.INFO)

DATA = ROOT / "benchmarks" / "data"
CRAWLED = DATA / "crawled"
CROSSREF = "https://api.crossref.org/works/"
OPENALEX = "https://api.openalex.org/works/doi:"
MAILTO = "integriref-bench@example.org"
HEADERS = {"User-Agent": f"IntegriRef-bench/1.0 (mailto:{MAILTO})"}
SESSION = requests.Session()
random.seed(20260804)

# Characters that NFKD leaves intact but that ASCII folding must not drop.
TRANSLIT = str.maketrans({
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ß": "ss", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH",
    "ł": "l", "Ł": "L", "ı": "i", "'": "", "’": "", "ʼ": "",
})

GROUP_TOKENS = {
    "group", "consortium", "collaboration", "committee", "society",
    "association", "team", "network", "initiative", "study", "investigators",
    "trialists", "authors", "working", "council", "organization",
    "organisation", "panel", "board", "programme", "program", "project",
    "centre", "center", "institute", "cancer", "health", "trial", "cohort",
    "registry", "foundation", "department", "division", "college", "academy",
    "agency", "ministry", "who", "cdc", "nih", "university", "hospital",
}

# Publishers routinely deposit a journal fragment, section heading or subject
# keyword where the author surname belongs. These strings are never surnames
# in the sense the comparison needs, so they cannot support a verdict.
NON_AUTHOR_TOKENS = {
    "nature", "science", "sciences", "lancet", "bmj", "jama", "cell", "plos",
    "phys", "physics", "chem", "chemistry", "biol", "biology", "med",
    "medicine", "oncol", "oncology", "appl", "applied", "rev", "review",
    "reviews", "lett", "letters", "proc", "proceedings", "acta", "ann",
    "annals", "arch", "archives", "bull", "bulletin", "journal", "trans",
    "transactions", "adv", "advances", "int", "eur", "amer", "american",
    "and", "the", "for", "with", "from", "based", "using", "abstract",
    "tables", "table", "figure", "chapter", "editorial", "erratum", "ibid",
    "anon", "anonymous", "assays", "assay", "assessment", "agents", "agent",
    "pcr", "dna", "rna", "mri", "test", "tests", "data", "results",
    "lymphoma", "leukemia", "carcinoma", "docetaxel", "therapy", "treatment",
    "life-tables", "survival", "analysis", "methods", "method",
}

NAME_SIM_THRESHOLD = 0.85   # below this two surnames are different people
TITLE_SIM_THRESHOLD = 0.55  # below this the DOI points at a different work


def fold(text: str) -> str:
    """Fold a name fragment to a comparable ASCII lowercase form."""
    if not text:
        return ""
    text = html.unescape(text).translate(TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z\- ]", " ", text)
    return " ".join(text.lower().split())


def norm_surname(name: str) -> str:
    """Extract a comparable surname from a 'Family, Given' or bare string.

    Crossref deposits the surname alone in a reference entry's `author` field,
    but full records carry family and given separately, so a naive last-token
    rule would compare a surname against a given name.
    """
    if not name:
        return ""
    head = name.split(",")[0] if "," in name else name
    folded = fold(head)
    if not folded:
        return ""
    tokens = [t for t in folded.split() if len(t.replace("-", "")) > 1]
    return tokens[-1] if tokens else folded.split()[-1]


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def same_person(a: str, b: str) -> bool:
    """Whether two folded surnames plausibly denote the same author.

    Substring containment is treated as a match because publishers truncate
    non-ASCII surnames when depositing ("Ertöz" arriving as "Ert"), which is an
    encoding artefact rather than a citation error.
    """
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return similar(a, b) >= NAME_SIM_THRESHOLD


def is_informative(surname: str) -> bool:
    """Whether a string can carry a mismatch verdict at all.

    Rejects initials ("jjm"), fragments, and vowel-less runs, which are how
    truncated or initial-only deposits present.
    """
    core = surname.replace("-", "")
    if len(core) < 4:
        return False
    return any(v in core for v in "aeiouy")


def is_group_name(surname: str) -> bool:
    tokens = surname.split()
    return any(tok in GROUP_TOKENS or tok in NON_AUTHOR_TOKENS
               for tok in tokens)


def given_names(msg):
    """Folded given-name tokens of every author on the record.

    Deposits frequently place the given name where the surname belongs, so a
    "mismatch" against a given name is a name-order artefact, not an error.
    """
    out = set()
    for a in msg.get("author") or []:
        for source in (a.get("given"), a.get("name")):
            for tok in fold(source or "").split():
                if len(tok.replace("-", "")) > 1:
                    out.add(tok)
    return out


def get_json(url: str, sleep: float = 0.3):
    try:
        resp = SESSION.get(url, headers=HEADERS, timeout=25)
    except requests.RequestException:
        return None
    finally:
        time.sleep(sleep)
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def fetch_crossref(doi: str):
    payload = get_json(CROSSREF + quote(doi, safe=""))
    return payload.get("message") if payload else None


def fetch_openalex(doi: str):
    return get_json(OPENALEX + quote(doi, safe=""))


def crossref_year(msg) -> str:
    """Earliest known publication year.

    `issued` is Crossref's earliest-known date; preferring print/online fields
    instead surfaces backfile re-deposit dates, which look like decades of
    drift on legacy articles that are in fact cited correctly.
    """
    for key in ("issued", "published", "published-print", "published-online"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return ""


def crossref_authors(msg):
    out = []
    for a in msg.get("author") or []:
        family, given = a.get("family") or "", a.get("given") or ""
        if family:
            out.append(f"{family}, {given}".strip().rstrip(","))
        elif a.get("name"):
            out.append(a["name"])
    return out


def crossref_first_surname(msg) -> str:
    for a in msg.get("author") or []:
        if a.get("family"):
            folded = fold(a["family"])
            return folded.split()[-1] if folded else ""
        if a.get("name"):
            return norm_surname(a["name"])
    return ""


def openalex_year(work) -> str:
    year = work.get("publication_year")
    return str(year) if year else ""


def openalex_first_surname(work) -> str:
    for authorship in work.get("authorships") or []:
        name = (authorship.get("author") or {}).get("display_name") or ""
        if name:
            return norm_surname(name)
    return ""


def printed_first_author(entry) -> str:
    """Surname as printed in the deposited reference entry."""
    if entry.get("author"):
        return norm_surname(str(entry["author"]))
    unstructured = entry.get("unstructured") or ""
    if unstructured:
        head = re.split(r"[.,(]", unstructured.strip())[0]
        return norm_surname(head)
    return ""


def citing_dois(limit_per_topic: int):
    """DOIs of real papers to walk as citing works.

    The topic corpora alone hold under a thousand works, which exhausts before
    the target yield, so the real controls of the large splits are appended as
    a second source. Only genuine (non-retracted, non-synthetic) records are
    used, since a citing work's own status is irrelevant here --- we read its
    deposited reference list, not its metadata.
    """
    seen, out = set(), []

    def add(doi):
        doi = (doi or "").strip().lower()
        if doi and doi not in seen:
            seen.add(doi)
            out.append(doi)

    for path in sorted(CRAWLED.glob("crossref_real_*.jsonl")):
        rows = [json.loads(line) for line in open(path) if line.strip()]
        rows = [r for r in rows if r.get("doi")]
        rows.sort(key=lambda r: int(r.get("citation_count", 0) or 0),
                  reverse=True)
        for r in rows[:limit_per_topic]:
            add(r["doi"])
    topic_only = len(out)

    hf = DATA / "hf_export"
    for name, keep in [("reference_verification.jsonl", {"real"}),
                       ("retracted_papers.jsonl", {"real_control"})]:
        path = hf / name
        if not path.exists():
            continue
        for line in open(path):
            row = json.loads(line)
            if row.get("category") in keep:
                add(row.get("doi"))

    logger.info("citing pool: %d from topic corpora + %d from split controls",
                topic_only, len(out) - topic_only)
    random.shuffle(out)
    return out


def classify(entry, msg, printed_year, printed_surname):
    """Return (reasons, diagnostics) for a candidate against Crossref only."""
    true_year = crossref_year(msg)
    true_authors = crossref_authors(msg)
    true_surname = crossref_first_surname(msg)
    true_title = " ".join(msg.get("title") or [])
    if not true_title or not true_authors or not true_year:
        return None, None

    # If the entry names a title, it must be the same work — otherwise the DOI
    # points somewhere else entirely, which is a different failure mode.
    printed_title = html.unescape(str(entry.get("article-title") or "")).strip()
    if printed_title and similar(fold(printed_title),
                                 fold(true_title)) < TITLE_SIM_THRESHOLD:
        return None, None

    # A DOI that embeds the printed year is a legacy backfile whose deposited
    # date is the digitisation year, not the publication year.
    doi_embeds_printed_year = bool(printed_year) and printed_year in msg.get(
        "DOI", "")

    comparable_authors = (
        is_informative(printed_surname) and is_informative(true_surname)
        and not is_group_name(printed_surname)
        and not is_group_name(true_surname))
    # Require the printed surname to match no author of the record: a hit
    # further down the list is an author-ordering difference, not an error.
    author_hit = max((similar(printed_surname, norm_surname(a))
                      for a in true_authors), default=0.0)
    matches_surname = any(same_person(printed_surname, norm_surname(a))
                          for a in true_authors)
    # ...and to match no author's *given* name either, which is how
    # "Kumar, Nirmalya" arrives deposited as author="Nirmalya".
    matches_given = any(same_person(printed_surname, g)
                        for g in given_names(msg))
    # A "surname" that occurs in the work's own title or venue is a title word
    # or journal fragment deposited into the author field. This generalises
    # past any blocklist of subject keywords.
    context_words = set(fold(true_title).split()) | set(
        fold((msg.get("container-title") or [""])[0]).split())
    looks_like_context = printed_surname in context_words
    author_mismatch = (comparable_authors and not matches_surname
                       and not matches_given and not looks_like_context)

    year_off = None
    if printed_year.isdigit() and true_year.isdigit():
        year_off = abs(int(printed_year) - int(true_year))

    reasons = []
    if (year_off is not None and 2 <= year_off <= 60
            and not doi_embeds_printed_year):
        reasons.append(f"year printed={printed_year} crossref={true_year}")
    if author_mismatch:
        reasons.append(f"author printed={printed_surname} "
                       f"crossref={true_surname}")

    diag = {"true_year": true_year, "true_authors": true_authors,
            "true_surname": true_surname, "true_title": true_title,
            "venue": (msg.get("container-title") or [""])[0],
            "comparable_authors": comparable_authors,
            "author_best_sim": round(author_hit, 3)}
    return reasons, diag


def confirm_with_openalex(doi, reasons, printed_year, printed_surname, diag):
    """Keep only mismatches that a second registry also reports.

    Returns (confirmed_reasons, note). A reason is dropped when OpenAlex sides
    with the printed metadata — that makes Crossref the outlier, i.e. a
    registry artefact rather than a citation error.
    """
    work = fetch_openalex(doi)
    if not work:
        return [], "openalex: not found"
    oa_year = openalex_year(work)
    oa_surname = openalex_first_surname(work)
    confirmed, notes = [], []

    for reason in reasons:
        if reason.startswith("year"):
            if not (oa_year.isdigit() and printed_year.isdigit()):
                notes.append("year: openalex year unavailable")
                continue
            if abs(int(oa_year) - int(printed_year)) <= 1:
                notes.append(f"year: openalex={oa_year} backs printed "
                             f"-> crossref artefact")
                continue
            confirmed.append(f"{reason} openalex={oa_year}")
        else:
            if not is_informative(oa_surname):
                notes.append("author: openalex author unavailable")
                continue
            if same_person(printed_surname, oa_surname):
                notes.append(f"author: openalex={oa_surname} backs printed "
                             f"-> crossref artefact")
                continue
            confirmed.append(f"{reason} openalex={oa_surname}")

    diag["openalex_year"] = oa_year
    diag["openalex_first_surname"] = oa_surname
    return confirmed, "; ".join(notes)


def build_case(doi, diag, entry, printed_year, printed_surname,
               reasons, citing_doi, index, category):
    """Assemble a bench case carrying the metadata AS PRINTED."""
    authors = list(diag["true_authors"])
    if any(r.startswith("author") for r in reasons) and authors:
        authors = [printed_surname.title()] + authors[1:]
    return {
        "id": f"{'natchim' if category == 'chimera' else 'natreal'}_{index:03d}",
        "category": category,
        "doi": doi,
        "title": diag["true_title"],
        "authors": json.dumps(authors),
        "year": printed_year or diag["true_year"],
        "venue": diag["venue"],
        "expected_found": True,
        "expected_retracted": False,
        "expected_risk": "high" if category == "chimera" else "low",
        "citing_doi": citing_doi,
        "crossref_year": diag["true_year"],
        "crossref_first_author": diag["true_surname"],
        "openalex_year": diag.get("openalex_year", ""),
        "openalex_first_author": diag.get("openalex_first_surname", ""),
        "printed_first_author": printed_surname,
        "mismatch_reasons": reasons,
    }


def mine(target, max_citing, limit_per_topic):
    chimeras, controls, rejected = [], [], []
    cache = {}
    scanned = 0

    # The seed corpora hold only ~1k works, which exhausts before the target
    # yield, so the queue snowballs: every reference we resolve is itself a
    # work with its own deposited reference list.
    queue = citing_dois(limit_per_topic)
    queued = set(queue)
    cursor = 0

    while cursor < len(queue) and scanned < max_citing:
        citing_doi = queue[cursor]
        cursor += 1
        if len(chimeras) >= target and len(controls) >= target:
            break
        citing = fetch_crossref(citing_doi)
        if not citing:
            continue
        scanned += 1
        if scanned % 100 == 0:
            logger.info("scanned %d citing works, queue %d, kept %d",
                        scanned, len(queue) - cursor, len(chimeras))
        refs = citing.get("reference") or []
        candidates = [r for r in refs if r.get("DOI")
                      and (r.get("year") or printed_first_author(r))]
        random.shuffle(candidates)
        for entry in candidates[:12]:  # cap so one bibliography cannot dominate
            if len(chimeras) >= target and len(controls) >= target:
                break
            doi = entry["DOI"].lower().strip()
            if doi in cache:
                msg = cache[doi]
            else:
                msg = fetch_crossref(doi)
                cache[doi] = msg
            if not msg:
                continue  # unresolvable DOI is a phantom, not a chimera
            # Snowball: this resolved work can serve as a citing work later.
            if doi not in queued and msg.get("reference-count", 0):
                queued.add(doi)
                queue.append(doi)

            printed_year = str(entry.get("year") or "").strip()[:4]
            printed_surname = printed_first_author(entry)
            reasons, diag = classify(entry, msg, printed_year, printed_surname)
            if reasons is None:
                continue

            if reasons:
                if len(chimeras) >= target:
                    continue
                confirmed, note = confirm_with_openalex(
                    doi, reasons, printed_year, printed_surname, diag)
                if not confirmed:
                    rejected.append({"doi": doi, "reasons": reasons,
                                     "dropped_because": note})
                    logger.info("drop  %s  (%s)", doi, note)
                    continue
                chimeras.append(build_case(doi, diag, entry, printed_year,
                                           printed_surname, confirmed,
                                           citing_doi, len(chimeras),
                                           "chimera"))
                logger.info("keep  %d/%d  %s  (%s)", len(chimeras), target,
                            doi, "; ".join(confirmed))
            elif (diag["comparable_authors"] and len(controls) < target
                  and printed_year.isdigit()
                  and printed_year == diag["true_year"]):
                controls.append(build_case(doi, diag, entry, printed_year,
                                           printed_surname, [], citing_doi,
                                           len(controls), "real_control"))

    return chimeras, controls, rejected, scanned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=60,
                    help="chimeras to mine (the commitment is >=50)")
    ap.add_argument("--max-citing", type=int, default=1200)
    ap.add_argument("--limit-per-topic", type=int, default=45)
    args = ap.parse_args()

    t0 = time.monotonic()
    chimeras, controls, rejected, scanned = mine(
        args.target, args.max_citing, args.limit_per_topic)

    out_path = DATA / "natural_chimeras.jsonl"
    with open(out_path, "w") as fh:
        for case in chimeras + controls:
            fh.write(json.dumps(case) + "\n")

    def only(kind):
        return sum(1 for c in chimeras
                   if len(c["mismatch_reasons"]) == 1
                   and c["mismatch_reasons"][0].startswith(kind))

    meta = {
        "n_chimeras": len(chimeras),
        "n_controls": len(controls),
        "citing_works_scanned": scanned,
        "candidates_rejected_by_openalex": len(rejected),
        "breakdown": {"year_only": only("year"), "author_only": only("author"),
                      "both": len(chimeras) - only("year") - only("author")},
        "elapsed_s": round(time.monotonic() - t0, 1),
        "source": "Crossref publisher-deposited reference lists, "
                  "confirmed against OpenAlex",
        "filters": {
            "name_similarity_threshold": NAME_SIM_THRESHOLD,
            "title_similarity_threshold": TITLE_SIM_THRESHOLD,
            "year_window": "2..60 years",
            "excluded": ["corporate/consortium bylines", "bare initials",
                         "DOIs embedding the printed year (legacy backfiles)",
                         "mismatches OpenAlex does not corroborate"],
        },
    }
    (DATA / "natural_chimeras_meta.json").write_text(json.dumps(meta, indent=2))
    (DATA / "natural_chimeras_rejected.json").write_text(
        json.dumps(rejected, indent=2))
    print(json.dumps(meta, indent=2))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
