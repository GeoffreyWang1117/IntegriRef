"""Tests for RegistryDiscovery — ID detection and smart routing."""

import pytest
from core.discovery import RegistryDiscovery


class TestIDDetection:
    """Test automatic identifier type detection."""

    @pytest.mark.parametrize("identifier,expected", [
        # DOI
        ("10.1038/nature12373", "doi"),
        ("10.1000/xyz123", "doi"),
        ("10.48550/arXiv.2301.12345", "doi"),
        # arXiv
        ("2301.12345", "arxiv"),
        ("2301.12345v2", "arxiv"),
        ("hep-th/0401234", "arxiv"),
        ("math-ph/0612345", "arxiv"),
        # PMID
        ("39876543", "pmid"),
        ("1", "pmid"),
        # Patent numbers
        ("US11234567", "patent_number"),
        ("EP3456789", "patent_number"),
        ("WO2023012345", "patent_number"),
        ("CN112345678", "patent_number"),
        ("JP2023012345", "patent_number"),
        ("KR1020230012345", "patent_number"),
        # RFC
        ("RFC 8446", "rfc_number"),
        ("rfc1234", "rfc_number"),
        ("RFC8446", "rfc_number"),
        # CVE
        ("CVE-2023-44487", "cve"),
        ("CVE-2021-3156", "cve"),
        # CELEX
        ("32022R2065", "celex"),
        # ISO
        ("ISO 27001", "iso_number"),
        ("ISO/IEC 27001:2022", "iso_number"),
        # ISBN
        ("978-0-13-468599-1", "isbn"),
        ("0-13-468599-X", "isbn"),
        # Case citation
        ("410 U.S. 113", "case_cite"),
        # ORCID
        ("0000-0002-1825-0097", "orcid"),
        # Unknown
        ("random string", None),
        ("hello world", None),
        ("", None),
    ])
    def test_detect_id_type(self, identifier, expected):
        result = RegistryDiscovery.detect_id_type(identifier)
        assert result == expected, f"'{identifier}': got '{result}', expected '{expected}'"


class TestRegistryDiscovery:
    def test_empty_discovery(self):
        discovery = RegistryDiscovery()
        assert discovery.list_registries() == []

    def test_register_and_list(self):
        from registries.academic.crossref import CrossRefRegistry
        discovery = RegistryDiscovery()
        discovery.register(CrossRefRegistry())
        regs = discovery.list_registries()
        assert len(regs) == 1
        assert regs[0]["name"] == "crossref"

    def test_register_all(self):
        from registries.academic import ALL_ACADEMIC
        discovery = RegistryDiscovery()
        discovery.register_all(ALL_ACADEMIC)
        regs = discovery.list_registries()
        assert len(regs) == len(ALL_ACADEMIC)

    def test_verify_reference_empty(self):
        discovery = RegistryDiscovery()
        result = discovery.verify_reference(title="nonexistent")
        assert result["found"] is False
        assert result["matches"] == []

    def test_query_unknown_id(self):
        discovery = RegistryDiscovery()
        results = discovery.query_by_id("not an identifier")
        assert results == []
