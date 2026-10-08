"""Tests for the arXiv S3 bulk layer. No AWS, no network.

Everything that decides what gets downloaded, and what it will cost, is testable
without credentials: manifest parsing, range and budget selection, and the plan's
arithmetic. Only the transfer itself needs AWS, and that is the part a test should
not be doing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepcite.eval import arxiv_bulk as A

MANIFEST = """<?xml version='1.0' encoding='utf-8'?>
<arXivSRC>
  <file>
    <filename>src/arXiv_src_2304_001.tar</filename>
    <size>500000000</size><num_items>1800</num_items>
    <yymm>2304</yymm><md5sum>aaaa</md5sum>
  </file>
  <file>
    <filename>src/arXiv_src_2305_001.tar</filename>
    <size>500000000</size><num_items>2000</num_items>
    <yymm>2305</yymm><md5sum>bbbb</md5sum>
  </file>
  <file>
    <filename>src/arXiv_src_2305_002.tar</filename>
    <size>250000000</size><num_items>900</num_items>
    <yymm>2305</yymm><md5sum>cccc</md5sum>
  </file>
  <file>
    <filename>src/arXiv_src_2306_001.tar</filename>
    <size>500000000</size><num_items>2100</num_items>
    <yymm>2306</yymm><md5sum>dddd</md5sum>
  </file>
  <file>
    <filename>src/broken.tar</filename>
    <size>not-a-number</size><num_items>1</num_items>
    <yymm>2306</yymm><md5sum>eeee</md5sum>
  </file>
</arXivSRC>
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "arXiv_src_manifest.xml").write_text(MANIFEST, encoding="utf-8")
    return tmp_path


def test_manifest_parses_and_skips_unparseable_entries(root):
    tars = A.manifest(root)
    assert [t.yymm for t in tars] == ["2304", "2305", "2305", "2306"]
    assert tars[1].num_items == 2000 and tars[1].md5sum == "bbbb"
    # The malformed size must drop the entry rather than crash the run or, worse,
    # become a zero-byte tarball that reads downstream as an empty corpus.
    assert all("broken" not in t.filename for t in tars)


def test_manifest_is_cached_so_replanning_costs_nothing(root, monkeypatch):
    def boom():
        raise AssertionError("manifest refetched; replanning would be billed")
    monkeypatch.setattr(A, "_client", boom)
    assert len(A.manifest(root)) == 4


def test_select_honours_the_yymm_range(root):
    tars = A.manifest(root)
    picked = A.select(tars, since="2305", until="2305", budget_gb=99)
    assert {t.yymm for t in picked} == {"2305"}


def test_select_never_exceeds_the_budget(root):
    tars = A.manifest(root)
    for budget in (0.2, 0.5, 0.9, 1.1, 99):
        picked = A.select(tars, budget_gb=budget)
        assert sum(t.gb for t in picked) <= budget, budget


def test_select_prefers_the_newest_months(root):
    """Recent citing papers cite recent papers, which is what makes a local
    corpus able to hold both sides of a citation."""
    picked = A.select(A.manifest(root), budget_gb=0.6)
    assert picked and picked[0].yymm == "2306"


def test_select_can_return_nothing_without_erroring(root):
    assert A.select(A.manifest(root), since="9901") == []
    assert A.select(A.manifest(root), budget_gb=0.0) == []


def test_plan_reports_size_papers_and_a_cost_estimate(root):
    p = A.plan(since="2305", until="2306", budget_gb=1.1, root=root)
    assert p["selected"] == 2
    assert p["papers"] == 2100 + 900          # 2306_001 then 2305_002 fits
    assert p["total_gb"] == pytest.approx(0.75, abs=0.01)
    assert p["egress_usd_estimate"] == pytest.approx(0.75 * A.EGRESS_USD_PER_GB,
                                                     abs=0.01)
    # The estimate must not be mistaken for the bill.
    assert "per request" in p["egress_note"]
    assert A.REGION in p["egress_note"]


def test_plan_is_empty_and_cheap_when_nothing_matches(root):
    p = A.plan(since="9901", until="", budget_gb=99, root=root)
    assert p["selected"] == 0 and p["total_gb"] == 0 and p["egress_usd_estimate"] == 0
    assert p["yymm_range"] == []


def test_id_pattern_matches_both_arxiv_id_schemes():
    for stem, want in [("2305.14795", "2305.14795"),
                       ("1012.3615", "1012.3615"),
                       ("math.GT0309136", "math.GT0309136"),
                       ("hep-th9901001", "hep-th9901001")]:
        m = A._ID_IN_MEMBER.search(stem)
        assert m and m.group(0) == want, stem


def test_load_index_is_empty_rather_than_raising_when_absent(tmp_path):
    assert A.load_index(tmp_path) == {}
    (tmp_path / "index.json").write_text("{ not json", encoding="utf-8")
    assert A.load_index(tmp_path) == {}


def test_load_index_round_trips(tmp_path):
    (tmp_path / "index.json").write_text(
        json.dumps({"2305.14795": "/x/2305.14795"}), encoding="utf-8")
    assert A.load_index(tmp_path)["2305.14795"] == "/x/2305.14795"
