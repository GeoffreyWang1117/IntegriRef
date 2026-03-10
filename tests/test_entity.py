"""Tests for ICEntity data model."""

import pytest
from core.entity import ICEntity, EntityType, ExternalID


class TestExternalID:
    def test_key_format(self):
        eid = ExternalID(registry="crossref", id_type="doi", id_value="10.1234/test")
        assert eid.key == "crossref:doi:10.1234/test"

    def test_key_uniqueness(self):
        eid1 = ExternalID("crossref", "doi", "10.1234/a")
        eid2 = ExternalID("crossref", "doi", "10.1234/b")
        assert eid1.key != eid2.key


class TestICEntity:
    def test_creation_defaults(self):
        entity = ICEntity()
        assert entity.entity_type == EntityType.OTHER
        assert entity.ice_id == ""
        assert entity.external_ids == []
        assert entity.confidence == 0.0

    def test_creation_with_fields(self):
        entity = ICEntity(
            entity_type=EntityType.PAPER,
            title="Test Paper",
            authors=["Alice Smith", "Bob Jones"],
            year="2023",
            venue="NeurIPS",
        )
        assert entity.entity_type == EntityType.PAPER
        assert entity.title == "Test Paper"
        assert len(entity.authors) == 2

    def test_add_external_id(self):
        entity = ICEntity()
        entity.add_external_id("crossref", "doi", "10.1234/test")
        assert len(entity.external_ids) == 1
        assert entity.external_ids[0].id_value == "10.1234/test"

    def test_add_external_id_dedup(self):
        entity = ICEntity()
        entity.add_external_id("crossref", "doi", "10.1234/test")
        entity.add_external_id("crossref", "doi", "10.1234/test")
        assert len(entity.external_ids) == 1

    def test_add_different_registries_same_id(self):
        entity = ICEntity()
        entity.add_external_id("crossref", "doi", "10.1234/test")
        entity.add_external_id("openalex", "doi", "10.1234/test")
        assert len(entity.external_ids) == 2

    def test_get_id(self):
        entity = ICEntity()
        entity.add_external_id("crossref", "doi", "10.1234/test")
        entity.add_external_id("arxiv", "arxiv", "2301.12345")
        assert entity.get_id("doi") == "10.1234/test"
        assert entity.get_id("arxiv") == "2301.12345"
        assert entity.get_id("pmid") is None

    def test_normalize_title(self):
        result = ICEntity.normalize_title("Hello, World!")
        assert "hello" in result
        assert "world" in result
        assert result == result.lower()  # must be lowercase

    def test_normalize_title_unicode(self):
        # NFC normalization preserves base characters
        result = ICEntity.normalize_title("café")
        assert "caf" in result

    def test_normalize_author(self):
        assert ICEntity.normalize_author("José García") == "jose garcia"
        assert ICEntity.normalize_author("MÜLLER") == "muller"
        # Turkish dotless i
        assert ICEntity.normalize_author("İstanbul") == "istanbul"

    def test_normalize(self):
        entity = ICEntity(
            title="Attention Is All You Need",
            authors=["Ashish Vaswani"],
            year="2017",
        )
        entity.add_external_id("crossref", "doi", "10.5555/3295222.3295349")
        entity.normalize()
        assert entity.title_normalized == "attention is all you need"
        assert entity.authors_normalized == ["ashish vaswani"]
        assert entity.ice_id.startswith("ice:")
        assert len(entity.ice_id) == 16  # "ice:" + 12 hex chars

    def test_generate_ice_id_doi_priority(self):
        entity = ICEntity(title="Test")
        entity.add_external_id("crossref", "doi", "10.1234/test")
        entity.add_external_id("arxiv", "arxiv", "2301.12345")
        entity.normalize()
        # DOI should be used over arXiv for ice_id generation
        ice_id_with_doi = entity.ice_id

        entity2 = ICEntity(title="Test")
        entity2.add_external_id("crossref", "doi", "10.1234/test")
        entity2.normalize()
        assert entity2.ice_id == ice_id_with_doi

    def test_generate_ice_id_title_fallback(self):
        entity = ICEntity(title="No ID Paper", year="2023")
        entity.normalize()
        assert entity.ice_id.startswith("ice:")

    def test_entity_types(self):
        for et in EntityType:
            entity = ICEntity(entity_type=et)
            assert entity.entity_type == et


class TestEntityType:
    def test_all_types_exist(self):
        expected = {"paper", "patent", "case", "statute", "standard",
                    "report", "filing", "dataset", "book", "thesis", "other"}
        actual = {e.value for e in EntityType}
        assert actual == expected

    def test_string_enum(self):
        assert EntityType.PAPER == "paper"
        assert EntityType.PATENT == "patent"
