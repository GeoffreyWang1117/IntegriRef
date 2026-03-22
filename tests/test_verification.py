"""Tests for the L0 verification engine and supporting modules."""

import pytest
from verification.field_comparator import (
    FieldComparator, strip_latex, strip_accents, token_similarity,
    tokenize, normalize_venue, jaccard_similarity, token_set_ratio,
    author_name_similarity, HAS_RAPIDFUZZ,
)
from verification.metadata_validator import MetadataValidator, MetadataValidationReport
from verification.hallucination_detector import HallucinationDetector, HallucinationReport
from verification.engine import (
    VerificationEngine, VerificationReport, ReferenceResult,
    FieldCheck, SourceMatch, CompositeScore, Provenance,
)


# ── FieldComparator tests ─────────────────────────────────────────────────

class TestStripLatex:
    def test_basic(self):
        assert strip_latex("Hello World") == "Hello World"

    def test_accents(self):
        result = strip_latex(r"\'{e}")
        assert "é" in result or "e" in result

    def test_commands(self):
        result = strip_latex(r"\textbf{bold} \textit{italic}")
        assert "bold" in result
        assert "italic" in result

    def test_braces_removed(self):
        assert "{" not in strip_latex("{Hello}")

    def test_empty(self):
        assert strip_latex("") == ""
        assert strip_latex(None) == ""


class TestTokenSimilarity:
    def test_identical(self):
        assert token_similarity("Hello World", "Hello World") == 1.0

    def test_different(self):
        assert token_similarity("Hello", "Goodbye") < 0.2

    def test_partial(self):
        score = token_similarity("deep learning models", "deep learning is great")
        assert 0.3 < score < 0.8

    def test_empty(self):
        assert token_similarity("", "hello") == 0.0

    def test_case_insensitive(self):
        assert token_similarity("HELLO WORLD", "hello world") == 1.0


class TestFieldComparatorTitle:
    def test_exact_match(self):
        status, _ = FieldComparator.match_title(
            "Attention Is All You Need",
            "Attention Is All You Need")
        assert status == "OK"

    def test_partial_match(self):
        status, _ = FieldComparator.match_title(
            "Attention Is All You Need",
            "Attention Is All You Need: Transformers")
        assert status in ("OK", "WARN")

    def test_mismatch(self):
        status, _ = FieldComparator.match_title(
            "Attention Is All You Need",
            "Migration Patterns of Arctic Birds")
        assert status == "FAIL"


class TestFieldComparatorAuthors:
    def test_matching_authors(self):
        status, _ = FieldComparator.match_authors(
            ["Ashish Vaswani"], ["Ashish Vaswani"])
        assert status == "OK"

    def test_bib_string_format(self):
        status, _ = FieldComparator.match_authors(
            "Vaswani, Ashish and Shazeer, Noam",
            ["Ashish Vaswani", "Noam Shazeer"])
        assert status == "OK"

    def test_surname_mismatch(self):
        status, _ = FieldComparator.match_authors(
            ["Alice Smith"], ["Bob Jones"])
        assert status == "FAIL"

    def test_accent_normalization(self):
        status, _ = FieldComparator.match_authors(
            ["José García"], ["Jose Garcia"])
        assert status == "OK"

    def test_et_al(self):
        status, _ = FieldComparator.match_authors(
            "Smith, John and others",
            ["John Smith", "Alice Brown", "Bob Green", "Carol White"])
        assert status == "OK"

    def test_empty_api_authors(self):
        status, _ = FieldComparator.match_authors(["Smith"], [])
        assert status == "WARN"


class TestFieldComparatorYear:
    def test_exact(self):
        status, _ = FieldComparator.match_year("2023", "2023")
        assert status == "OK"

    def test_off_by_one(self):
        status, _ = FieldComparator.match_year("2023", "2022")
        assert status == "WARN"

    def test_mismatch(self):
        status, _ = FieldComparator.match_year("2023", "2019")
        assert status == "FAIL"

    def test_no_api_year(self):
        status, _ = FieldComparator.match_year("2023", None)
        assert status == "WARN"

    def test_unparseable(self):
        status, _ = FieldComparator.match_year("not_a_year", "also_not")
        assert status == "WARN"


class TestFieldComparatorVenue:
    def test_exact_match(self):
        status, _ = FieldComparator.match_venue("NeurIPS", "NeurIPS")
        assert status == "OK"

    def test_abbreviation(self):
        status, _ = FieldComparator.match_venue(
            "ICML",
            "International Conference on Machine Learning")
        assert status == "OK"

    def test_arxiv_match(self):
        status, _ = FieldComparator.match_venue("arXiv preprint", "CoRR")
        assert status == "OK"

    def test_dblp_abbreviation(self):
        status, _ = FieldComparator.match_venue(
            "JMLR",
            "J. Mach. Learn. Res.")
        assert status == "OK"

    def test_no_venue(self):
        status, _ = FieldComparator.match_venue("", "NeurIPS")
        assert status == "WARN"


class TestValidateApiMatch:
    def test_matching_reference(self):
        ref = {"title": "Test Paper", "authors": ["Smith"], "year": "2023"}
        api = {"title": "Test Paper", "authors": ["John Smith"], "year": "2023"}
        assert FieldComparator.validate_api_match(ref, api) is True

    def test_wrong_author_and_year(self):
        ref = {"title": "Test Paper", "authors": ["Smith"], "year": "2023"}
        api = {"title": "Other Paper", "authors": ["Jones"], "year": "2019"}
        assert FieldComparator.validate_api_match(ref, api) is False

    def test_empty_data(self):
        assert FieldComparator.validate_api_match({}, {}) is False
        assert FieldComparator.validate_api_match({}, None) is False


class TestComputeMatchScore:
    def test_all_ok(self):
        checks = [("OK", ""), ("OK", ""), ("OK", "")]
        assert FieldComparator.compute_match_score(checks) == 100.0

    def test_all_fail(self):
        checks = [("FAIL", ""), ("FAIL", "")]
        assert FieldComparator.compute_match_score(checks) == 0.0

    def test_mixed(self):
        checks = [("OK", ""), ("WARN", ""), ("FAIL", "")]
        score = FieldComparator.compute_match_score(checks)
        assert 40 < score < 60  # (100 + 60 + 0) / 3 ≈ 53.3

    def test_empty(self):
        assert FieldComparator.compute_match_score([]) == 0.0


# ── MetadataValidator tests ───────────────────────────────────────────────

class TestMetadataValidator:
    def setup_method(self):
        self.validator = MetadataValidator()

    def test_single_entity(self):
        from core.entity import ICEntity
        e = ICEntity(title="Test Paper")
        report = self.validator.validate([e])
        assert report.overall_consistency == 100.0

    def test_consistent_entities(self):
        from core.entity import ICEntity
        e1 = ICEntity(title="Test Paper", authors=["Smith"], year="2023",
                       source_registries=["crossref"])
        e2 = ICEntity(title="Test Paper", authors=["John Smith"], year="2023",
                       source_registries=["openalex"])
        report = self.validator.validate([e1, e2])
        assert report.is_consistent

    def test_inconsistent_titles(self):
        from core.entity import ICEntity
        e1 = ICEntity(title="Paper About Dogs", authors=["Smith"], year="2023",
                       source_registries=["crossref"])
        e2 = ICEntity(title="Paper About Cats", authors=["Smith"], year="2023",
                       source_registries=["openalex"])
        report = self.validator.validate([e1, e2])
        # Titles are partially different
        title_checks = [c for c in report.consistency_checks if c.field == "title"]
        assert len(title_checks) > 0

    def test_inconsistent_years(self):
        from core.entity import ICEntity
        e1 = ICEntity(title="Test Paper", year="2020", source_registries=["a"])
        e2 = ICEntity(title="Test Paper", year="2023", source_registries=["b"])
        report = self.validator.validate([e1, e2])
        year_checks = [c for c in report.consistency_checks if c.field == "year"]
        assert any(c.status == "FAIL" for c in year_checks)

    def test_empty_list(self):
        report = self.validator.validate([])
        assert report.overall_consistency == 100.0


# ── HallucinationDetector tests ──────────────────────────────────────────

class TestHallucinationDetector:
    def setup_method(self):
        self.detector = HallucinationDetector()

    def test_phantom_doi(self):
        ref = {"title": "Fake Paper", "doi": "10.1234/fake.doi.2023"}
        report = self.detector.analyze(ref, registry_results=None)
        doi_flags = [f for f in report.flags if f.flag_type == "phantom_doi"]
        assert len(doi_flags) == 1
        assert doi_flags[0].severity == "HIGH"

    def test_no_match(self):
        ref = {"title": "A paper that definitely does not exist anywhere"}
        report = self.detector.analyze(ref, registry_results=None)
        no_match = [f for f in report.flags if f.flag_type == "no_registry_match"]
        assert len(no_match) == 1

    def test_found_reference(self):
        ref = {"title": "Real Paper"}
        results = [{"source": "crossref", "found": True, "title": "Real Paper",
                     "authors": ["Smith"]}]
        report = self.detector.analyze(ref, registry_results=results)
        assert not report.is_likely_hallucinated

    def test_future_year(self):
        ref = {"title": "Future Paper", "year": "2099"}
        report = self.detector.analyze(ref)
        temporal = [f for f in report.flags if f.flag_type == "temporal_impossibility"]
        assert len(temporal) >= 1

    def test_hallucination_score_threshold(self):
        # A ref with phantom DOI + no match should score high
        ref = {"title": "Completely fake paper by nobody", "doi": "10.9999/fake"}
        report = self.detector.analyze(ref, registry_results=None)
        assert report.hallucination_score > 0

    def test_format_anomaly(self):
        # Create refs with suspicious colon pattern
        all_refs = [{"title": f"Topic {i}: A Comprehensive Analysis"} for i in range(10)]
        ref = all_refs[0]
        report = self.detector.analyze(ref, all_refs=all_refs)
        format_flags = [f for f in report.flags if f.flag_type == "format_anomaly"]
        assert len(format_flags) >= 1


# ── SourceMatch tests ─────────────────────────────────────────────────────

class TestSourceMatch:
    def test_confirmed_with_title_author_ok(self):
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "OK", "match"),
        ]
        assert m.is_confirmed

    def test_not_confirmed_with_title_fail(self):
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "FAIL", "mismatch"),
            FieldCheck("authors", "OK", "match"),
        ]
        assert not m.is_confirmed

    def test_confirmed_with_author_warn(self):
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "WARN", "count differs"),
        ]
        assert m.is_confirmed


# ── ReferenceResult tests ────────────────────────────────────────────────

class TestReferenceResult:
    def test_is_confirmed(self):
        r = ReferenceResult(key="test", title="Test")
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "OK", "match"),
        ]
        r.source_matches = [m]
        assert r.is_confirmed

    def test_best_match_score(self):
        r = ReferenceResult()
        m1 = SourceMatch(registry="a", match_score=80)
        m2 = SourceMatch(registry="b", match_score=95)
        r.source_matches = [m1, m2]
        assert r.best_match_score == 95

    def test_summary(self):
        r = ReferenceResult(key="k", title="T", overall="OK")
        s = r.summary()
        assert s["key"] == "k"
        assert s["overall"] == "OK"


# ── VerificationReport tests ─────────────────────────────────────────────

class TestVerificationReport:
    def test_coverage_all_matched(self):
        r = VerificationReport(total=3)
        r.results = [
            ReferenceResult(sources_hit=["crossref"]),
            ReferenceResult(sources_hit=["openalex"]),
            ReferenceResult(sources_hit=["dblp"]),
        ]
        assert r.coverage == 100.0

    def test_coverage_none_matched(self):
        r = VerificationReport(total=2)
        r.results = [
            ReferenceResult(sources_hit=[]),
            ReferenceResult(sources_hit=[]),
        ]
        assert r.coverage == 0.0

    def test_summary(self):
        r = VerificationReport(total=5, ok_count=3, warn_count=1, fail_count=1)
        s = r.summary()
        assert s["total"] == 5
        assert s["ok"] == 3


# ── VerificationEngine tests (unit, no network) ──────────────────────────

class TestVerificationEngineUnit:
    def test_instantiation(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        assert engine._discovery is discovery

    def test_verify_empty_ref(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        result = engine.verify_reference({"title": "", "key": "empty"})
        assert result.overall == "FAIL"

    def test_verify_batch_empty(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        report = engine.verify_batch([])
        assert report.total == 0

    def test_compute_overall_no_matches(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        result = ReferenceResult(key="test", title="Test")
        engine._compute_overall(result)
        assert result.overall == "FAIL"

    def test_compute_overall_confirmed(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)

        result = ReferenceResult(key="test", title="Test")
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "OK", "match"),
            FieldCheck("year", "OK", "match"),
        ]
        result.source_matches = [m]
        engine._compute_overall(result)
        assert result.overall == "OK"

    def test_compute_overall_retracted(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)

        result = ReferenceResult(key="test", title="Test", is_retracted=True)
        m = SourceMatch(registry="crossref", found=True, is_retracted=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "OK", "match"),
            FieldCheck("retraction", "FAIL", "RETRACTED!"),
        ]
        result.source_matches = [m]
        engine._compute_overall(result)
        assert result.overall == "FAIL"

    def test_compute_overall_year_downgrade(self):
        """When title+author match but year FAILs, downgrade to WARN."""
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)

        result = ReferenceResult(key="test", title="Test")
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "OK", "match"),
            FieldCheck("year", "FAIL", "year mismatch"),
        ]
        result.source_matches = [m]
        engine._compute_overall(result)
        assert result.overall == "WARN"

    def test_get_ordered_registries(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        # Should not error with empty registry list
        ordered = engine._get_ordered_registries()
        assert isinstance(ordered, list)

    def test_verify_empty_ref_has_composite(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        result = engine.verify_reference({"title": "", "key": "empty"})
        assert result.composite is not None
        # context_intent defaults to 0.5, so minimum overall = 0.15 * 0.5 = 0.075
        assert result.composite.overall < 0.1

    def test_should_escalate_ambiguous(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        result = ReferenceResult()
        # Need overall in [0.55, 0.85] to trigger escalation
        # 0.35*0.8 + 0.25*0.9 + 0.15*0.5 + 0.10*0.3 + 0.15*0.5 = 0.28+0.225+0.075+0.03+0.075 = 0.685
        result.composite = CompositeScore(existence=0.8, title_sim=0.9, author_sim=0.5, venue_sim=0.3)
        assert engine._should_escalate(result) is True

    def test_should_not_escalate_confident(self):
        from core.discovery import RegistryDiscovery
        discovery = RegistryDiscovery()
        engine = VerificationEngine(discovery)
        result = ReferenceResult()
        result.composite = CompositeScore(
            existence=1.0, title_sim=1.0, author_sim=1.0,
            venue_sim=0.8, context_intent=1.0)
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "OK", "match"),
        ]
        result.source_matches = [m]
        assert engine._should_escalate(result) is False


# ── RapidFuzz integration tests ──────────────────────────────────────────

class TestRapidFuzzIntegration:
    def test_rapidfuzz_available(self):
        assert HAS_RAPIDFUZZ is True, "rapidfuzz should be installed"

    def test_token_set_ratio(self):
        # token_set_ratio handles reordering better than Jaccard
        a = "Attention Is All You Need"
        b = "All You Need Is Attention"
        score = token_set_ratio(a, b)
        assert score > 0.9  # Should be near-perfect with token_set_ratio

    def test_jaccard_vs_token_set_ratio(self):
        # Jaccard should also work for identical strings
        a = "Deep Learning for NLP"
        b = "Deep Learning for NLP"
        assert jaccard_similarity(a, b) == 1.0
        assert token_set_ratio(a, b) > 0.99

    def test_author_name_similarity_exact(self):
        assert author_name_similarity("Smith", "Smith") == 1.0

    def test_author_name_similarity_accent(self):
        score = author_name_similarity("García", "Garcia")
        assert score >= 0.9

    def test_author_name_similarity_different(self):
        score = author_name_similarity("Smith", "Jones")
        assert score < 0.7

    def test_author_name_jaro_winkler(self):
        # Jaro-Winkler should handle typos gracefully
        score = author_name_similarity("Vaswani", "Vaswany")
        assert score > 0.85


# ── CompositeScore tests ─────────────────────────────────────────────────

class TestCompositeScore:
    def test_perfect_score(self):
        cs = CompositeScore(
            existence=1.0, title_sim=1.0, author_sim=1.0,
            venue_sim=1.0, context_intent=1.0)
        assert cs.overall == 1.0
        assert cs.label == "supported"

    def test_zero_score(self):
        cs = CompositeScore()
        assert cs.overall == pytest.approx(0.075, abs=0.01)
        # 0.35*0 + 0.25*0 + 0.15*0 + 0.10*0 + 0.15*0.5 = 0.075

    def test_label_boundaries(self):
        cs = CompositeScore(existence=1.0, title_sim=1.0, author_sim=1.0,
                            venue_sim=0.5, context_intent=0.5)
        assert cs.label in ("supported", "possible")

        cs2 = CompositeScore(existence=0.3, title_sim=0.3)
        assert cs2.label == "suspicious"

    def test_to_dict(self):
        cs = CompositeScore(existence=0.9)
        d = cs.to_dict()
        assert "existence" in d
        assert "overall" in d
        assert "label" in d

    def test_formula_weights_sum_to_one(self):
        cs = CompositeScore()
        total = cs.W_EXIST + cs.W_TITLE + cs.W_AUTHOR + cs.W_VENUE + cs.W_INTENT
        assert total == pytest.approx(1.0)


# ── Provenance tests ─────────────────────────────────────────────────────

class TestProvenance:
    def test_to_dict(self):
        p = Provenance(
            timestamp=1234567890.0,
            registry="crossref",
            query_type="id_lookup",
            query_params={"doi": "10.1234/test"},
            found=True,
            match_score=95.0,
            field_scores={"title": 0.95, "author": 0.88},
            decision="OK",
        )
        d = p.to_dict()
        assert d["registry"] == "crossref"
        assert d["found"] is True
        assert d["field_scores"]["title"] == 0.95

    def test_default_provenance(self):
        p = Provenance()
        d = p.to_dict()
        assert d["timestamp"] == 0.0
        assert d["found"] is False


# ── API response contract tests ──────────────────────────────────────────

class TestAPIResponse:
    def test_api_response_not_found(self):
        r = ReferenceResult(key="test", title="Missing Paper")
        r.composite = CompositeScore()
        resp = r.api_response()
        assert resp["existence"]["status"] == "not_found"
        assert resp["citation_id"] == "test"
        assert "scores" in resp
        assert "provenance" in resp

    def test_api_response_found(self):
        r = ReferenceResult(key="test", title="Found Paper",
                            sources_hit=["crossref"])
        m = SourceMatch(registry="crossref", found=True)
        m.checks = [
            FieldCheck("title", "OK", "match"),
            FieldCheck("authors", "OK", "match"),
        ]
        r.source_matches = [m]
        r.composite = CompositeScore(existence=1.0, title_sim=0.95)
        resp = r.api_response()
        assert resp["existence"]["status"] == "found"
        assert resp["scores"]["existence"] == 1.0

    def test_api_response_ambiguous(self):
        r = ReferenceResult(key="test", title="Ambiguous Paper",
                            sources_hit=["openalex"])
        m = SourceMatch(registry="openalex", found=True)
        m.checks = [
            FieldCheck("title", "WARN", "partial"),
            FieldCheck("authors", "FAIL", "mismatch"),
        ]
        r.source_matches = [m]
        r.composite = CompositeScore(existence=0.6)
        resp = r.api_response()
        assert resp["existence"]["status"] == "ambiguous"
