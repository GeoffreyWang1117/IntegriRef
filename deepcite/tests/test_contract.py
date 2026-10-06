"""Contract tests: reproduce the reader side's hand-written fixture exactly.

The fixture lives in a separate repo (~/Tools/self-review) written by the other
implementation of the same spec. If these tests fail, the two sides have drifted
and that must be settled before either writes more code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepcite import contract as C
from deepcite import texscan as T

FIXTURE = Path.home() / "Tools/self-review/tests/fixtures/citations"
pytestmark = pytest.mark.skipif(not FIXTURE.exists(),
                                reason="reader-side fixture not present")


def _vectors():
    return json.loads((FIXTURE / "TEST_VECTORS.json").read_text())


def _cache():
    return json.loads((FIXTURE / "paper/.cache/ref_check_deep.json").read_text())


@pytest.mark.parametrize("vec", _vectors() if FIXTURE.exists() else [])
def test_norm_and_hash_match_reader(vec):
    assert C.norm(vec["input"]) == vec["norm"]
    assert C.context_sha256(vec["input"]) == vec["context_sha256"]


def test_file_sha256_is_over_raw_bytes():
    cache, root = _cache(), FIXTURE / "paper"
    for rel, want in cache["paper"]["files_sha256"].items():
        got = C.file_sha256((root / rel).read_bytes())
        if rel == "sections/c.tex":
            # Deliberately recorded as an older version to drive the stale path.
            assert got != want
        else:
            assert got == want, rel


def _scan_index(root: Path):
    idx: dict[tuple[str, str], list] = {}
    for rel in T.tex_files(root, "main.tex"):
        for cit in T.scan_file(root, rel).citations:
            idx.setdefault((rel, cit.bib_key), []).append(cit.sentence)
    return idx


def test_records_reproduce_hash_offsets_and_id():
    """Records in unchanged files must reproduce hash, offsets, line and id."""
    cache, root = _cache(), FIXTURE / "paper"
    idx = _scan_index(root)
    unchanged = {rel for rel, want in cache["paper"]["files_sha256"].items()
                 if C.file_sha256((root / rel).read_bytes()) == want}
    checked = 0
    for rec in cache["records"]:
        ci = rec["citing"]
        if ci["file"] not in unchanged:
            continue
        cands = idx.get((ci["file"], rec["bib_key"]), [])
        match = [s for s in cands
                 if C.context_sha256(s.text) == ci["context_sha256"]]
        assert match, f'{rec["bib_key"]}@{ci["file"]}: my scanner found no sentence'
        sent = match[0]
        assert (sent.byte_start, sent.byte_end) == (ci["byte_start"], ci["byte_end"])
        assert sent.line == ci["line"]
        assert C.record_id(rec["bib_key"], rec["cited_id"], ci["file"],
                           ci["context_sha256"]) == rec["record_id"]
        assert C.slice_bytes(T.scan_file(root, ci["file"]).stripped,
                             ci["byte_start"], ci["byte_end"]) == sent.text
        checked += 1
    assert checked == 3, f"expected the 3 records in unchanged files, got {checked}"


def test_record_ids_all_reproduce():
    """record_id is derivable for every record, stale ones included."""
    for rec in _cache()["records"]:
        ci = rec["citing"]
        assert C.record_id(rec["bib_key"], rec["cited_id"], ci["file"],
                           ci["context_sha256"]) == rec["record_id"]


def test_three_way_staleness_matches_reader():
    """Reproduce the reader's four outcomes on the deliberately-staled file."""
    cache, root = _cache(), FIXTURE / "paper"
    shas = cache["paper"]["files_sha256"]
    got = {}
    for rec in cache["records"]:
        ci = rec["citing"]
        got[(rec["bib_key"], ci["file"])] = C.classify_staleness(
            shas.get(ci["file"]), (root / ci["file"]).read_bytes(),
            ci["context_sha256"], ci["byte_start"], ci["byte_end"], ci["sentence"])
    assert got[("bao2023tallrec", "sections/c.tex")] == C.FRESH
    assert got[("zhong2023mquake", "sections/c.tex")] == C.FRESH
    assert got[("other2021", "sections/c.tex")] == C.MOVED
    assert got[("other2020", "sections/c.tex")] == C.STALE
    for key, state in got.items():
        if key[1] != "sections/c.tex":
            assert state == C.FRESH, key


def test_citet_citep_hash_identically():
    a = "Following the protocol of \\citet{k}, we report accuracy."
    b = "Following the protocol of \\citep[Sec.~3]{k}, we report accuracy."
    assert C.context_sha256(a) == C.context_sha256(b)


def test_escaped_percent_survives_into_hash():
    """95\\% must not be truncated at the % -- the number IS the claim."""
    s = "MQuAKE reports 95\\% accuracy on two-hop chains."
    assert "95" in C.norm(s)
    assert C.norm(s).endswith("accuracy on two-hop chains.")


def test_double_backslash_then_comment_is_stripped():
    s = "The budget is fixed\\\\% a comment\nat 512 tokens."
    assert "a comment" not in C.norm(s)
    assert "512" in C.norm(s)


def test_byte_offsets_differ_from_char_offsets_on_non_ascii():
    """The reason the fields are named byte_*, not char_*."""
    s = "naïve Ångström"
    assert len(s) != len(s.encode("utf-8"))
    stripped = "x " + s
    bs, be = C.byte_span(stripped, 2, len(stripped))
    assert C.slice_bytes(stripped, bs, be) == s
