"""Tests for OAI-PMH base registry adapter."""

import pytest
import xml.etree.ElementTree as ET

from core.entity import EntityType


# Sample OAI-PMH XML responses for testing
SAMPLE_GET_RECORD = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2024-01-01T00:00:00Z</responseDate>
  <GetRecord>
    <record>
      <header>
        <identifier>oai:test:12345</identifier>
        <datestamp>2024-01-01</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Test Paper Title</dc:title>
          <dc:creator>John Smith</dc:creator>
          <dc:creator>Jane Doe</dc:creator>
          <dc:date>2024-03-15</dc:date>
          <dc:identifier>10.1234/test</dc:identifier>
          <dc:identifier>https://example.com/paper</dc:identifier>
          <dc:description>This is a test abstract.</dc:description>
          <dc:publisher>Test Publisher</dc:publisher>
          <dc:subject>Computer Science</dc:subject>
          <dc:language>en</dc:language>
        </oai_dc:dc>
      </metadata>
    </record>
  </GetRecord>
</OAI-PMH>"""


class TestOaiPmhParser:
    """Test the default Dublin Core parser."""

    def test_parse_oai_record(self):
        from core.oai_pmh import OaiPmhRegistry

        class TestOai(OaiPmhRegistry):
            def info(self):
                from core.registry import RegistryInfo
                return RegistryInfo(
                    name="test_oai", domain="academic",
                    base_url="https://test.oai", auth_type="none",
                    rate_limit=1.0, coverage="test",
                    entity_types=["paper"],
                )
            def _oai_base_url(self):
                return "https://test.oai"

        adapter = TestOai()
        root = ET.fromstring(SAMPLE_GET_RECORD)
        ns = {"oai": "http://www.openarchives.org/OAI/2.0/"}
        record = root.find(".//oai:record", ns)
        header = record.find("oai:header", ns)
        metadata = record.find("oai:metadata", ns)

        entity = adapter._parse_oai_record(metadata, header)
        assert entity is not None
        assert entity.title == "Test Paper Title"
        assert len(entity.authors) == 2
        assert "John Smith" in entity.authors
        assert entity.year == "2024"
        assert entity.get_id("doi") == "10.1234/test"
        assert entity.metadata["language"] == "en"
        assert entity.metadata["oai_identifier"] == "oai:test:12345"
