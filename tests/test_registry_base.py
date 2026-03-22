"""Tests for RegistryAdapter base class and RegistryInfo."""

import pytest
from core.registry import RegistryAdapter, RegistryInfo
from core.entity import ICEntity, EntityType


class TestRegistryInfo:
    def test_creation(self):
        info = RegistryInfo(
            name="test",
            domain="academic",
            base_url="https://example.com",
            auth_type="none",
            rate_limit=1.0,
            coverage="test coverage",
            entity_types=["paper"],
        )
        assert info.name == "test"
        assert info.domain == "academic"
        assert info.rate_limit == 1.0


class DummyRegistry(RegistryAdapter):
    """Concrete implementation for testing the base class."""

    def info(self):
        return RegistryInfo(
            name="dummy",
            domain="test",
            base_url="https://dummy.test",
            auth_type="none",
            rate_limit=0.0,
            coverage="test only",
            entity_types=["paper"],
        )

    def query_by_id(self, id_type, id_value):
        if id_type == "test" and id_value == "found":
            entity = ICEntity(
                entity_type=EntityType.PAPER,
                title="Found Paper",
                year="2023",
                source_registries=["dummy"],
            )
            entity.normalize()
            return entity
        return None

    def search(self, title, author="", year="", **kwargs):
        if "match" in title.lower():
            entity = ICEntity(
                entity_type=EntityType.PAPER,
                title=title,
                year=year,
                source_registries=["dummy"],
            )
            entity.normalize()
            return [entity]
        return []


class TestRegistryAdapter:
    def test_instantiation(self):
        adapter = DummyRegistry()
        assert adapter.info().name == "dummy"

    def test_repr(self):
        adapter = DummyRegistry()
        r = repr(adapter)
        assert "dummy" in r
        assert "test" in r

    def test_query_by_id_found(self):
        adapter = DummyRegistry()
        entity = adapter.query_by_id("test", "found")
        assert entity is not None
        assert entity.title == "Found Paper"

    def test_query_by_id_not_found(self):
        adapter = DummyRegistry()
        assert adapter.query_by_id("test", "missing") is None

    def test_search_match(self):
        adapter = DummyRegistry()
        results = adapter.search("match this")
        assert len(results) == 1

    def test_search_no_match(self):
        adapter = DummyRegistry()
        results = adapter.search("nothing here")
        assert results == []

    def test_verify_exists(self):
        adapter = DummyRegistry()
        assert adapter.verify_exists("test", "found") is True
        assert adapter.verify_exists("test", "missing") is False

    def test_session_has_user_agent(self):
        adapter = DummyRegistry()
        ua = adapter._session.headers.get("User-Agent", "")
        assert "IntegriRef" in ua


class TestAllRegistriesLoad:
    """Verify all 31+ registry adapters can be instantiated."""

    def test_academic_adapters(self):
        from registries.academic import ALL_ACADEMIC
        for cls in ALL_ACADEMIC:
            adapter = cls()
            info = adapter.info()
            assert info.name, f"{cls.__name__} has no name"
            assert info.domain == "academic"
            assert info.base_url.startswith("http")

    def test_patent_adapters(self):
        from registries.patents import ALL_PATENTS
        for cls in ALL_PATENTS:
            adapter = cls()
            info = adapter.info()
            assert info.name
            assert info.domain == "patents"

    def test_legal_adapters(self):
        from registries.legal import ALL_LEGAL
        for cls in ALL_LEGAL:
            adapter = cls()
            info = adapter.info()
            assert info.name
            assert info.domain == "legal"

    def test_government_adapters(self):
        from registries.government import ALL_GOVERNMENT
        for cls in ALL_GOVERNMENT:
            adapter = cls()
            info = adapter.info()
            assert info.name
            assert info.domain == "government"

    def test_financial_adapters(self):
        from registries.financial import ALL_FINANCIAL
        for cls in ALL_FINANCIAL:
            adapter = cls()
            info = adapter.info()
            assert info.name
            # FederalRegister is re-exported from legal domain
            assert info.domain in ("financial", "legal")

    def test_standards_adapters(self):
        from registries.standards import ALL_STANDARDS
        for cls in ALL_STANDARDS:
            adapter = cls()
            info = adapter.info()
            assert info.name
            assert info.domain == "standards"

    def test_total_adapter_count(self):
        from registries.academic import ALL_ACADEMIC
        from registries.patents import ALL_PATENTS
        from registries.legal import ALL_LEGAL
        from registries.government import ALL_GOVERNMENT
        from registries.financial import ALL_FINANCIAL
        from registries.standards import ALL_STANDARDS

        total = (len(ALL_ACADEMIC) + len(ALL_PATENTS) + len(ALL_LEGAL) +
                 len(ALL_GOVERNMENT) + len(ALL_FINANCIAL) + len(ALL_STANDARDS))
        assert total >= 60, f"Expected >= 60 adapters, got {total}"
