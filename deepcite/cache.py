"""Read and write ``<paper>/.cache/ref_check_deep.json`` -- the contract file.

The writer records provenance and facts only. It never marks a record stale:
staleness is the reader's decision, computed from the current files (§5), because
the writer cannot know what will be edited after it runs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .contract import FORMAT_VERSION, TOOL_NAME, TOOL_VERSION

CACHE_RELPATH = Path(".cache") / "ref_check_deep.json"

# Statuses (§5). There is deliberately no SUPPORTED: a machine never concludes
# that a citation is accurate, it only produces candidate evidence for a human.
CANDIDATE_EVIDENCE = "CANDIDATE_EVIDENCE"
CLAIM_ABSENT_FROM_ARTIFACT = "CLAIM_ABSENT_FROM_ARTIFACT"
NO_FULLTEXT = "NO_FULLTEXT"
WRONG_ARTIFACT_KIND = "WRONG_ARTIFACT_KIND"
UNRESOLVED = "UNRESOLVED"
# A lookup or fetch source refused (429, timeout). Retryable, and never evidence
# about the citation. Added 2026-10-06: until then a refusal read as UNRESOLVED
# "no arXiv artifact found; supply one with --artifact".
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

FAILED_STATUSES = {NO_FULLTEXT, UNRESOLVED, SOURCE_UNAVAILABLE}

# `scan.nothing_to_check` (additive 2026-10-15; format_version stays 1, an older
# reader ignores the key). Why it exists: a run that found no bibliography at
# all used to exit 2 and write nothing, so the reader could not tell "ran, and
# there was nothing to check" from "never ran" -- two different facts about a
# submission. Writing a cache entry by hand instead would be worse: a
# fabricated "no bibliography" record makes the next reader believe the
# references were examined.
#
# These are reasons, not statuses: no record is produced, so there is nothing to
# carry a status. A reader must print the counts beside the reason, because a
# directory with no .tex in it produces the same empty worklist as a paper that
# cites nothing -- and the first one means the tool was pointed at the wrong
# place.
NO_BIBLIOGRAPHY_NO_CITATIONS = "NO_BIBLIOGRAPHY_NO_CITATIONS"
NO_CITABLE_CLAIM = "NO_CITABLE_CLAIM"
NOTHING_TO_CHECK_REASONS = (NO_BIBLIOGRAPHY_NO_CITATIONS, NO_CITABLE_CLAIM)

# llm_opinion.assessment (optional, additive 2026-10-06). Deliberately not
# "supported": an opinion can report that its quotes show no mismatch, never
# that the citation is right.
ASSESSMENTS = ("mismatch", "no_mismatch_found", "unclear")


def cache_path(paper: Path) -> Path:
    return paper / CACHE_RELPATH


def scan(tex_files: int, cite_commands: int, bib_files: list[str],
         citable_claims: int, nothing_to_check: str | None = None) -> dict:
    """What the run looked at, so an empty worklist can be read.

    `nothing_to_check` is one of NOTHING_TO_CHECK_REASONS, or None when records
    were produced. The counts are always present and are the part that
    distinguishes an empty paper from a wrong --paper.
    """
    assert nothing_to_check in (None,) + NOTHING_TO_CHECK_REASONS, nothing_to_check
    return {"tex_files": tex_files, "cite_commands": cite_commands,
            "bib_files": sorted(bib_files), "citable_claims": citable_claims,
            "nothing_to_check": nothing_to_check}


def build(paper_root: Path, main_tex: str | None, files_sha256: dict[str, str],
          records: list[dict], selection_regex_sha256: str,
          bibguard_version: str, scan_block: dict | None = None) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "selection_regex_sha256": selection_regex_sha256,
        "bibguard_version": bibguard_version,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "paper": {"root": str(paper_root.resolve()),
                  "main_tex": main_tex,
                  "files_sha256": files_sha256},
        "scan": scan_block,
        "records": records,
    }


def write(paper: Path, payload: dict, cache_dir: Path | None = None) -> Path:
    out = (Path(cache_dir) / "ref_check_deep.json") if cache_dir else cache_path(paper)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    return out


def read(paper: Path, cache_dir: Path | None = None) -> dict:
    p = (Path(cache_dir) / "ref_check_deep.json") if cache_dir else cache_path(paper)
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            f"unknown format_version {data.get('format_version')!r} in {p}; "
            f"this build understands {FORMAT_VERSION}. Refusing to guess.")
    return data


def record(bib_key: str, cited_id: str | None, artifact: dict, citing: dict,
           selection: dict, status: str, search_terms: list[str],
           passages: list[dict], record_id: str,
           llm_opinion=None, error: str | None = None,
           cited_title: str | None = None) -> dict:
    return {
        "record_id": record_id,
        "bib_key": bib_key,
        "cited_id": cited_id,
        "cited_title": cited_title,
        "artifact": artifact,
        "citing": citing,
        "selection": selection,
        "status": status,
        "search_terms": search_terms,
        "passages": passages,
        "llm_opinion": llm_opinion,
        "error": error,
    }
