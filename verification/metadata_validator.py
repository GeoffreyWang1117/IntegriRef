"""Cross-registry metadata consistency validator.

When a reference is found in multiple registries, this module checks
that the metadata is consistent across sources. Inconsistencies can
indicate data quality issues, version differences, or hallucinated data.

Key checks:
  - Title consistency across registries (Jaccard > 0.85)
  - Author list agreement (first author surname + count)
  - Year consistency (exact or ±1)
  - Venue consistency
  - DOI → metadata reverse validation
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.entity import ICEntity
from .field_comparator import FieldComparator, token_similarity, strip_accents


@dataclass
class ConsistencyResult:
    """Result of cross-registry consistency check."""
    field: str
    status: str  # OK, WARN, FAIL
    detail: str
    registries_compared: list[str] = field(default_factory=list)


@dataclass
class MetadataValidationReport:
    """Complete metadata validation report for a reference."""
    reference_title: str = ""
    registries_found: list[str] = field(default_factory=list)
    consistency_checks: list[ConsistencyResult] = field(default_factory=list)
    overall_consistency: float = 100.0  # 0-100
    warnings: list[str] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return self.overall_consistency >= 80.0


class MetadataValidator:
    """Validate metadata consistency across multiple registry results."""

    def validate(self, entities: list[ICEntity],
                 registry_names: list[str] = None) -> MetadataValidationReport:
        """Validate consistency across entities from different registries.

        Args:
            entities: ICEntity objects from different registries for the same ref
            registry_names: Names of source registries (parallel to entities)
        """
        report = MetadataValidationReport()

        if len(entities) < 2:
            if entities:
                report.reference_title = entities[0].title
                report.registries_found = (
                    registry_names[:1] if registry_names
                    else [r for r in (entities[0].source_registries or ["unknown"])])
            return report

        report.reference_title = entities[0].title
        if registry_names:
            report.registries_found = registry_names
        else:
            report.registries_found = [
                (e.source_registries[0] if e.source_registries else f"source_{i}")
                for i, e in enumerate(entities)
            ]

        # Cross-validate each pair
        checks = []
        checks.extend(self._check_titles(entities, report.registries_found))
        checks.extend(self._check_authors(entities, report.registries_found))
        checks.extend(self._check_years(entities, report.registries_found))
        checks.extend(self._check_venues(entities, report.registries_found))
        checks.extend(self._check_ids(entities, report.registries_found))

        report.consistency_checks = checks

        # Compute overall consistency
        if checks:
            score_map = {"OK": 100, "WARN": 60, "FAIL": 0}
            total = sum(score_map.get(c.status, 0) for c in checks)
            report.overall_consistency = total / len(checks)

        # Generate warnings
        for c in checks:
            if c.status == "FAIL":
                report.warnings.append(f"INCONSISTENT {c.field}: {c.detail}")
            elif c.status == "WARN":
                report.warnings.append(f"UNCERTAIN {c.field}: {c.detail}")

        return report

    def _check_titles(self, entities: list[ICEntity],
                      names: list[str]) -> list[ConsistencyResult]:
        results = []
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                t1, t2 = entities[i].title, entities[j].title
                if not t1 or not t2:
                    continue
                score = token_similarity(t1, t2)
                pair = [names[i], names[j]]
                if score >= 0.85:
                    results.append(ConsistencyResult(
                        "title", "OK",
                        f"titles consistent ({score:.2f})", pair))
                elif score >= 0.70:
                    results.append(ConsistencyResult(
                        "title", "WARN",
                        f"titles partially differ ({score:.2f}): "
                        f"'{t1[:40]}' vs '{t2[:40]}'", pair))
                else:
                    results.append(ConsistencyResult(
                        "title", "FAIL",
                        f"titles inconsistent ({score:.2f}): "
                        f"'{t1[:40]}' vs '{t2[:40]}'", pair))
        return results

    def _check_authors(self, entities: list[ICEntity],
                       names: list[str]) -> list[ConsistencyResult]:
        results = []
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                a1, a2 = entities[i].authors, entities[j].authors
                if not a1 or not a2:
                    continue
                pair = [names[i], names[j]]
                # Compare first author surnames
                s1 = FieldComparator._extract_surname(a1[0])
                s2 = FieldComparator._extract_surname(a2[0])
                if strip_accents(s1) == strip_accents(s2):
                    count_diff = abs(len(a1) - len(a2))
                    if count_diff <= 2:
                        results.append(ConsistencyResult(
                            "authors", "OK",
                            f"first author '{s1}' matches, counts {len(a1)} vs {len(a2)}",
                            pair))
                    else:
                        results.append(ConsistencyResult(
                            "authors", "WARN",
                            f"first author matches but counts differ: "
                            f"{len(a1)} vs {len(a2)}", pair))
                else:
                    results.append(ConsistencyResult(
                        "authors", "FAIL",
                        f"first author mismatch: '{s1}' vs '{s2}'", pair))
        return results

    def _check_years(self, entities: list[ICEntity],
                     names: list[str]) -> list[ConsistencyResult]:
        results = []
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                y1, y2 = entities[i].year, entities[j].year
                if not y1 or not y2:
                    continue
                pair = [names[i], names[j]]
                try:
                    diff = abs(int(y1) - int(y2))
                except (ValueError, TypeError):
                    results.append(ConsistencyResult(
                        "year", "WARN", f"unparseable: {y1} vs {y2}", pair))
                    continue
                if diff == 0:
                    results.append(ConsistencyResult(
                        "year", "OK", f"years match ({y1})", pair))
                elif diff <= 1:
                    results.append(ConsistencyResult(
                        "year", "WARN",
                        f"years off by 1: {y1} vs {y2} (preprint/publication)", pair))
                else:
                    results.append(ConsistencyResult(
                        "year", "FAIL",
                        f"years inconsistent: {y1} vs {y2}", pair))
        return results

    def _check_venues(self, entities: list[ICEntity],
                      names: list[str]) -> list[ConsistencyResult]:
        results = []
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                v1, v2 = entities[i].venue, entities[j].venue
                if not v1 or not v2:
                    continue
                pair = [names[i], names[j]]
                status, detail = FieldComparator.match_venue(v1, v2)
                results.append(ConsistencyResult("venue", status, detail, pair))
        return results

    def _check_ids(self, entities: list[ICEntity],
                   names: list[str]) -> list[ConsistencyResult]:
        """Check that external IDs are consistent across registries."""
        results = []
        # Collect DOIs from all entities
        dois = []
        for i, e in enumerate(entities):
            doi = e.get_id("doi")
            if doi:
                dois.append((i, doi.lower()))

        # Check DOI consistency
        if len(dois) >= 2:
            first_doi = dois[0][1]
            for idx, doi in dois[1:]:
                pair = [names[dois[0][0]], names[idx]]
                if doi == first_doi:
                    results.append(ConsistencyResult(
                        "doi", "OK", f"DOIs match: {first_doi}", pair))
                else:
                    results.append(ConsistencyResult(
                        "doi", "FAIL",
                        f"DOIs differ: {first_doi} vs {doi}", pair))

        return results
