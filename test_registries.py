#!/usr/bin/env python3
"""Quick smoke test — verify all registries load and ID detection works."""

import sys
sys.path.insert(0, ".")

from core.entity import ICEntity, EntityType
from core.discovery import RegistryDiscovery


def test_id_detection():
    """Test automatic identifier type detection."""
    cases = [
        ("10.1038/nature12373", "doi"),
        ("2301.12345", "arxiv"),
        ("hep-th/0401234", "arxiv"),
        ("39876543", "pmid"),
        ("US11234567", "patent_number"),
        ("EP3456789", "patent_number"),
        ("WO2023012345", "patent_number"),
        ("RFC 8446", "rfc_number"),
        ("CVE-2023-44487", "cve"),
        ("32022R2065", "celex"),
        ("ISO 27001", "iso_number"),
        ("978-0-13-468599-1", "isbn"),
        ("410 U.S. 113", "case_cite"),
        ("0000-0002-1825-0097", "orcid"),
        ("random string", None),
    ]
    passed = 0
    for identifier, expected in cases:
        result = RegistryDiscovery.detect_id_type(identifier)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            print(f"  {status}: '{identifier}' → got '{result}', expected '{expected}'")
        else:
            passed += 1
    print(f"ID detection: {passed}/{len(cases)} passed")


def test_entity_model():
    """Test ICEntity creation and normalization."""
    entity = ICEntity(
        entity_type=EntityType.PAPER,
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        year="2017",
        venue="NeurIPS",
    )
    entity.add_external_id("crossref", "doi", "10.5555/3295222.3295349")
    entity.add_external_id("arxiv", "arxiv", "1706.03762")
    entity.normalize()

    assert entity.ice_id.startswith("ice:"), f"Bad ICE ID: {entity.ice_id}"
    assert entity.title_normalized == "attention is all you need"
    assert len(entity.external_ids) == 2
    assert entity.get_id("doi") == "10.5555/3295222.3295349"
    assert entity.get_id("arxiv") == "1706.03762"
    print("Entity model: PASS")


def test_registry_loading():
    """Test that all registry adapters can be instantiated."""
    from registries.academic import ALL_ACADEMIC
    from registries.patents import ALL_PATENTS
    from registries.legal import ALL_LEGAL
    from registries.government import ALL_GOVERNMENT
    from registries.financial import ALL_FINANCIAL
    from registries.standards import ALL_STANDARDS

    discovery = RegistryDiscovery()

    all_adapters = (ALL_ACADEMIC + ALL_PATENTS + ALL_LEGAL +
                    ALL_GOVERNMENT + ALL_FINANCIAL + ALL_STANDARDS)

    for cls in all_adapters:
        try:
            adapter = cls()
            info = adapter.info()
            discovery.register(adapter)
        except Exception as e:
            print(f"  FAIL: {cls.__name__}: {e}")
            continue

    registries = discovery.list_registries()
    print(f"Registry loading: {len(registries)} adapters loaded")

    # Print summary table
    print(f"\n{'Name':<25s} {'Domain':<12s} {'Auth':<15s} {'Rate(s)':<8s} Coverage")
    print("-" * 110)
    for r in sorted(registries, key=lambda x: (x["domain"], x["name"])):
        print(f"{r['name']:<25s} {r['domain']:<12s} {r['auth']:<15s} "
              f"{r['rate_limit']:<8.1f} {r['coverage'][:55]}")


def test_live_query():
    """Test a live API query (requires network)."""
    from registries.academic.crossref import CrossRefRegistry

    print("\nLive query test (CrossRef DOI lookup)...")
    cr = CrossRefRegistry()
    entity = cr.query_by_id("doi", "10.5555/3295222.3295349")
    if entity:
        print(f"  Title: {entity.title}")
        print(f"  Authors: {', '.join(entity.authors[:3])}")
        print(f"  Year: {entity.year}")
        print(f"  IDs: {[e.key for e in entity.external_ids]}")
        print("  Live query: PASS")
    else:
        print("  Live query: SKIP (no network or API issue)")


if __name__ == "__main__":
    print("=" * 60)
    print("IntegriRef Registry Smoke Tests")
    print("=" * 60)

    test_id_detection()
    print()
    test_entity_model()
    print()
    test_registry_loading()

    if "--live" in sys.argv:
        test_live_query()

    print("\nDone.")
