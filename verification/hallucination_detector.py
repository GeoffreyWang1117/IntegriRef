"""AI hallucination citation detector.

Detects citations that are likely fabricated by AI systems. Common patterns:
  1. Valid DOI format but DOI doesn't resolve (structural hallucination)
  2. Author-title mismatch — real author name combined with fake title
  3. Fake venue — plausible-sounding but non-existent journal/conference
  4. Temporal impossibility — paper claims to be from future, or cites
     a venue that didn't exist at the claimed year
  5. Metadata chimera — metadata fields from different real papers combined
  6. "Too perfect" formatting — LLM-generated refs have suspiciously
     consistent formatting patterns

Based on patterns identified in:
  - Alkaissi & McFarlane (2023) "Artificial Hallucinations in ChatGPT"
  - Athaluri et al. (2023) "Exploring the Boundaries of Reality"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .field_comparator import token_similarity, strip_latex


@dataclass
class HallucinationFlag:
    """A single hallucination indicator."""
    flag_type: str  # structural, chimera, temporal, phantom_doi, etc.
    severity: str  # HIGH, MEDIUM, LOW
    detail: str
    confidence: float = 0.0  # 0-1, how confident we are this is hallucinated


@dataclass
class HallucinationReport:
    """Hallucination analysis for a single reference."""
    reference_title: str = ""
    is_likely_hallucinated: bool = False
    hallucination_score: float = 0.0  # 0-100, higher = more likely hallucinated
    flags: list[HallucinationFlag] = field(default_factory=list)

    @property
    def flag_count(self) -> int:
        return len(self.flags)

    def summary(self) -> dict:
        return {
            "title": self.reference_title,
            "is_hallucinated": self.is_likely_hallucinated,
            "score": round(self.hallucination_score, 1),
            "flags": [{"type": f.flag_type, "severity": f.severity,
                        "detail": f.detail} for f in self.flags],
        }


class HallucinationDetector:
    """Detect likely AI-hallucinated citations.

    Uses a multi-signal approach: each signal contributes to a
    hallucination score. If the score exceeds a threshold, the
    citation is flagged.
    """

    # Score contributions for each flag type
    SCORE_WEIGHTS = {
        "phantom_doi": 40,        # DOI format valid but doesn't resolve
        "phantom_arxiv": 35,      # arXiv ID format valid but doesn't exist
        "no_registry_match": 30,  # Not found in any registry
        "author_title_chimera": 25,  # Real author + fake title
        "temporal_impossibility": 20,  # Year/venue temporal mismatch
        "venue_nonexistent": 15,  # Venue doesn't exist
        "metadata_chimera": 20,  # Metadata from multiple real papers
        "format_anomaly": 10,    # Suspicious formatting patterns
    }

    HALLUCINATION_THRESHOLD = 50  # Score above this → likely hallucinated

    def analyze(self, ref: dict,
                registry_results: list[dict] = None,
                all_refs: list[dict] = None) -> HallucinationReport:
        """Analyze a reference for hallucination indicators.

        Args:
            ref: Reference dict with title, authors, year, doi, etc.
            registry_results: Results from registry lookups (None if not found)
            all_refs: All references in the document (for batch analysis)

        Returns:
            HallucinationReport with flags and score.
        """
        report = HallucinationReport(reference_title=ref.get("title", ""))
        registry_results = registry_results or []

        # 1. Phantom DOI: valid format but no registry found it
        self._check_phantom_doi(ref, registry_results, report)

        # 2. Phantom arXiv ID
        self._check_phantom_arxiv(ref, registry_results, report)

        # 3. No registry match at all
        self._check_no_match(ref, registry_results, report)

        # 4. Author-title chimera detection
        self._check_author_title_chimera(ref, registry_results, report)

        # 5. Temporal impossibility
        self._check_temporal(ref, report)

        # 6. Formatting anomalies (batch analysis)
        if all_refs:
            self._check_format_anomalies(ref, all_refs, report)

        # Compute overall score
        total = sum(self.SCORE_WEIGHTS.get(f.flag_type, 10) * f.confidence
                    for f in report.flags)
        report.hallucination_score = min(100.0, total)
        report.is_likely_hallucinated = (
            report.hallucination_score >= self.HALLUCINATION_THRESHOLD)

        return report

    def _check_phantom_doi(self, ref: dict, results: list[dict],
                           report: HallucinationReport):
        """DOI has valid format but doesn't resolve to any registry."""
        doi = ref.get("doi", "")
        if not doi:
            return
        if not re.match(r"^10\.\d{4,}/", doi):
            return  # Not even valid DOI format

        # Check if any registry found this DOI via ID lookup
        doi_found = any(
            r.get("found_by_id", False) for r in results
        )

        if not doi_found:
            # DOI didn't resolve — phantom DOI regardless of search matches.
            # Search may find a similar-titled paper, but that doesn't mean
            # THIS DOI is real. A valid-format DOI that doesn't resolve is
            # one of the strongest hallucination signals.
            confidence = 0.95 if not results else 0.85
            report.flags.append(HallucinationFlag(
                flag_type="phantom_doi",
                severity="HIGH",
                detail=f"DOI '{doi}' has valid format but could not be resolved"
                       + (" (search found similar papers)" if results else ""),
                confidence=confidence,
            ))

    def _check_phantom_arxiv(self, ref: dict, results: list[dict],
                             report: HallucinationReport):
        """arXiv ID has valid format but doesn't exist."""
        arxiv_id = ref.get("arxiv_id", "")
        if not arxiv_id:
            return
        if not re.match(r"^\d{4}\.\d{4,5}", arxiv_id):
            return

        arxiv_found = any(
            r.get("source") == "arxiv" and r.get("found", False)
            for r in results
        )
        if not arxiv_found:
            confidence = 0.90 if not results else 0.80
            report.flags.append(HallucinationFlag(
                flag_type="phantom_arxiv",
                severity="HIGH",
                detail=f"arXiv ID '{arxiv_id}' has valid format but not found"
                       + (" (search found similar papers)" if results else ""),
                confidence=confidence,
            ))

    def _check_no_match(self, ref: dict, results: list[dict],
                        report: HallucinationReport):
        """Reference not found in any registry via ID lookup."""
        title = ref.get("title", "")
        if not title or len(title) < 10:
            return

        # Only ID-based matches count as "really found".
        # Search matches may be false positives (similar but different paper).
        id_found = any(r.get("found_by_id", False) for r in results)
        if id_found:
            return

        if not results:
            # Not found at all — strong signal
            report.flags.append(HallucinationFlag(
                flag_type="no_registry_match",
                severity="MEDIUM",
                detail="Paper not found in any queried registry",
                confidence=0.7,
            ))
        else:
            # Found by search only — weaker signal, the search match
            # could be a different paper with a similar title
            report.flags.append(HallucinationFlag(
                flag_type="no_registry_match",
                severity="LOW",
                detail="Paper not found by ID; search found similar but unconfirmed papers",
                confidence=0.4,
            ))

    def _check_author_title_chimera(self, ref: dict, results: list[dict],
                                    report: HallucinationReport):
        """Detect when API finds the author but with a completely different title.

        This is a hallmark of LLM hallucination: the model knows a real
        researcher's name but invents a plausible-sounding title.
        """
        if not results:
            return

        ref_title = ref.get("title", "")
        ref_authors = ref.get("authors", [])
        if isinstance(ref_authors, str):
            from .field_comparator import FieldComparator
            ref_authors = FieldComparator._parse_bib_authors(ref_authors)

        if not ref_authors or not ref_title:
            return

        for r in results:
            api_authors = r.get("authors", [])
            api_title = r.get("title", "")
            if not api_authors or not api_title:
                continue

            # Check if author matches but title doesn't
            from .field_comparator import strip_accents, FieldComparator
            ref_surname = strip_accents(
                FieldComparator._extract_surname(ref_authors[0]))
            api_surname = strip_accents(
                FieldComparator._extract_surname(api_authors[0]))

            author_match = ref_surname == api_surname
            title_sim = token_similarity(ref_title, api_title)

            if author_match and title_sim < 0.5:
                # Author is real but title is very different — chimera pattern
                report.flags.append(HallucinationFlag(
                    flag_type="author_title_chimera",
                    severity="HIGH",
                    detail=(f"Author '{ref_authors[0]}' found but title differs "
                            f"significantly (sim={title_sim:.2f}). "
                            f"API title: '{api_title[:60]}'"),
                    confidence=0.7,
                ))
                break

    def _check_temporal(self, ref: dict, report: HallucinationReport):
        """Check for temporal impossibilities."""
        year = ref.get("year", "")
        if not year:
            return
        try:
            year_int = int(year)
        except (ValueError, TypeError):
            return

        # Future paper
        import datetime
        current_year = datetime.date.today().year
        if year_int > current_year + 1:
            report.flags.append(HallucinationFlag(
                flag_type="temporal_impossibility",
                severity="HIGH",
                detail=f"Paper year {year_int} is in the future",
                confidence=0.95,
            ))

        # Very old + modern venue
        venue = ref.get("venue", "") or ref.get("booktitle", "") or ref.get("journal", "")
        if venue and year_int < 1990:
            modern_venues = {"neurips", "nips", "iclr", "arxiv"}
            venue_lower = venue.lower()
            for mv in modern_venues:
                if mv in venue_lower and year_int < 2000:
                    report.flags.append(HallucinationFlag(
                        flag_type="temporal_impossibility",
                        severity="MEDIUM",
                        detail=f"Venue '{venue}' unlikely for year {year_int}",
                        confidence=0.6,
                    ))
                    break

    def _check_format_anomalies(self, ref: dict, all_refs: list[dict],
                                report: HallucinationReport):
        """Detect suspicious formatting patterns across the reference list.

        LLM-generated references tend to have:
        - Suspiciously uniform title length
        - Same structure repeated (e.g., always "Title: Subtitle" format)
        - Sequential fake years (2020, 2021, 2022, 2023)
        """
        if len(all_refs) < 5:
            return  # Not enough refs for pattern analysis

        title = ref.get("title", "")
        if not title:
            return

        # Check for "Title: A Subtitle About Something" pattern
        # (common in LLM-generated references)
        colon_count = sum(1 for r in all_refs
                          if ":" in r.get("title", "") and
                          len(r.get("title", "").split(":")) == 2)
        colon_ratio = colon_count / len(all_refs)

        if colon_ratio > 0.7 and ":" in title:
            report.flags.append(HallucinationFlag(
                flag_type="format_anomaly",
                severity="LOW",
                detail=f"{colon_ratio:.0%} of references use 'Title: Subtitle' format",
                confidence=0.3,
            ))
