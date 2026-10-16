"""End-to-end CLI tests. Offline: a stub bibguard and a local --artifact.

No test here touches the network. The arXiv fetch path is exercised separately by
the acceptance script, which is opt-in because it is slow and rate-limited.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from deepcite import cache as K
from deepcite import contract as C
from deepcite.cli import main

REPO = Path(__file__).resolve().parents[2]

STUB_BIBGUARD = '''#!/usr/bin/env python3
"""Stand-in for bibguard: fixed version, fixed resolved_ids, no network."""
import json, sys
if "--version" in sys.argv:
    print("bibguard %s"); sys.exit(0)
out = sys.argv[sys.argv.index("--out") + 1]
json.dump({"version": "%s", "results": [
    {"key": "zhong2023mquake", "title": "MQuAKE", "overall": "OK",
     "resolved_ids": {"doi": "10.18653/v1/2023.emnlp-main.971",
                      "arxiv_id": None, "s2_id": None, "openalex_id": "W1"}},
    {"key": "bg2020", "title": "Background", "overall": "OK",
     "resolved_ids": {"doi": None, "arxiv_id": None, "s2_id": None,
                      "openalex_id": None}}]}, open(out, "w"))
'''


def _stub(tmp_path: Path, version: str = "0.5.0") -> str:
    p = tmp_path / "stub_bibguard.py"
    p.write_text(STUB_BIBGUARD % (version, version))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return f"{sys.executable} {p}"


@pytest.fixture
def paper(tmp_path: Path) -> Path:
    d = tmp_path / "paper"
    d.mkdir()
    (d / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "MQuAKE's native metric is conjunction over all edits"
        "~\\cite{zhong2023mquake}.\n"
        "Knowledge editing has attracted attention~\\cite{bg2020}.\n"
        "\\end{document}\n")
    (d / "refs.bib").write_text(
        "@inproceedings{zhong2023mquake,title={MQuAKE},author={Zhong, Z},year={2023}}\n"
        "@article{bg2020,title={Background},author={B, B},year={2020}}\n")
    return d


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    """Stand-in for the cited work's own LaTeX source."""
    p = tmp_path / "cited" / "main.tex"
    p.parent.mkdir(parents=True)
    p.write_text(
        "\\section{Evaluation}\n"
        "We report the disjunction over paraphrases as our main metric.\n"
        "The conjunction over all edits is reported only as a reference metric.\n"
        "\\section{Data}\nThe benchmark has 3000 instances.\n")
    return p


def _run(args: list[str]) -> int:
    try:
        return main(args)
    except SystemExit as e:                     # _refuse_protected uses sys.exit
        return int(e.code) if isinstance(e.code, int) else 1


def test_run_selects_attribution_and_writes_candidate_evidence(paper, artifact, tmp_path):
    rc = _run(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
               "--main", "main.tex", "--bibguard", _stub(tmp_path),
               "--artifact", f"zhong2023mquake={artifact}"])
    assert rc == C.EXIT_OK
    data = K.read(paper)
    assert data["format_version"] == C.FORMAT_VERSION
    assert data["bibguard_version"] == "0.5.0"
    # The background citation must not be selected.
    assert [r["bib_key"] for r in data["records"]] == ["zhong2023mquake"]
    rec = data["records"][0]
    assert rec["selection"]["family"] == "attribution"
    assert rec["selection"]["claim_type"] == "metric"
    assert rec["status"] == K.CANDIDATE_EVIDENCE
    assert rec["llm_opinion"] is None            # never a verdict from `run`
    assert 0 < len(rec["passages"]) <= 5
    for p in rec["passages"]:
        assert len(p["text"].split()) <= 61      # 60 words plus the ellipsis
    # The passage that actually answers the claim should surface.
    assert any("reference metric" in p["text"] for p in rec["passages"])


def test_offsets_and_ids_round_trip(paper, artifact, tmp_path):
    _run(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
          "--bibguard", _stub(tmp_path), "--artifact", f"zhong2023mquake={artifact}"])
    rec = K.read(paper)["records"][0]
    ci = rec["citing"]
    stripped = C.strip_comments((paper / ci["file"]).read_text())
    assert C.slice_bytes(stripped, ci["byte_start"], ci["byte_end"]) == ci["sentence"]
    assert C.context_sha256(ci["sentence"]) == ci["context_sha256"]
    assert C.record_id(rec["bib_key"], rec["cited_id"], ci["file"],
                       ci["context_sha256"]) == rec["record_id"]
    assert C.classify_staleness(
        K.read(paper)["paper"]["files_sha256"][ci["file"]],
        (paper / ci["file"]).read_bytes(), ci["context_sha256"],
        ci["byte_start"], ci["byte_end"], ci["sentence"]) == C.FRESH


def test_nothing_selected_still_writes_cache_and_exits_zero(tmp_path):
    d = tmp_path / "quiet"
    d.mkdir()
    (d / "main.tex").write_text("Background only~\\cite{bg2020}.\n")
    (d / "r.bib").write_text("@article{bg2020,title={B},author={B},year={2020}}\n")
    rc = _run(["run", "--paper", str(d), "--bib", str(d / "r.bib"),
               "--bibguard", _stub(tmp_path)])
    assert rc == C.EXIT_OK
    data = K.read(d)
    assert data["records"] == []
    # Provenance must still be there, or the reader cannot tell this from "never ran".
    assert data["paper"]["files_sha256"]
    assert data["selection_regex_sha256"]


def test_missing_bib_exits_two(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    assert _run(["run", "--paper", str(d), "--bib", str(d / "nope.bib")]) == C.EXIT_NO_BIB


def test_old_bibguard_exits_three_and_never_falls_back(paper, tmp_path):
    rc = _run(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
               "--bibguard", _stub(tmp_path, version="0.4.0")])
    assert rc == C.EXIT_BIBGUARD_TOO_OLD
    assert not K.cache_path(paper).exists()


def test_refuses_to_write_into_a_backup_snapshot(paper, tmp_path):
    fake_backup = tmp_path / "data" / "ProjectsBackup" / "snapshots" / "monthly" / "x"
    fake_backup.mkdir(parents=True)
    rc = _run(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
               "--bibguard", _stub(tmp_path), "--cache-dir", str(fake_backup)])
    assert rc != C.EXIT_OK
    assert not (fake_backup / "ref_check_deep.json").exists()


def test_claim_absent_when_artifact_lacks_the_terms(paper, tmp_path):
    empty = tmp_path / "unrelated.tex"
    empty.write_text("\\section{Intro}\nThis paper is about compilers.\n")
    _run(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
          "--bibguard", _stub(tmp_path), "--artifact", f"zhong2023mquake={empty}"])
    rec = K.read(paper)["records"][0]
    assert rec["status"] == K.CLAIM_ABSENT_FROM_ARTIFACT
    # The terms that were tried must be recorded, or the absence is unauditable.
    assert rec["search_terms"]


# --- annotate ---------------------------------------------------------------

def _prepare(paper, artifact, tmp_path):
    _run(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
          "--bibguard", _stub(tmp_path), "--artifact", f"zhong2023mquake={artifact}"])
    return K.read(paper)["records"][0]["record_id"]


def test_annotate_accepts_a_real_quote(paper, artifact, tmp_path):
    rid = _prepare(paper, artifact, tmp_path)
    op = tmp_path / "op.json"
    op.write_text(json.dumps({
        "text": "The paper calls conjunction a reference metric, not the main one.",
        "quotes": [{"text": "reported only as a reference metric"}]}))
    rc = _run(["annotate", "--paper", str(paper), "--record", rid,
               "--opinion-file", str(op)])
    assert rc == C.EXIT_OK
    rec = K.read(paper)["records"][0]
    assert rec["llm_opinion"]["quotes"][0]["verified"] is True
    assert rec["llm_opinion"]["quotes"][0]["line"] > 0


def test_annotate_rejects_a_fabricated_quote(paper, artifact, tmp_path):
    rid = _prepare(paper, artifact, tmp_path)
    op = tmp_path / "op.json"
    op.write_text(json.dumps({
        "text": "whatever",
        "quotes": [{"text": "the native metric is conjunction over all edits"}]}))
    assert _run(["annotate", "--paper", str(paper), "--record", rid,
                 "--opinion-file", str(op)]) == C.EXIT_QUOTE_REJECTED
    assert K.read(paper)["records"][0]["llm_opinion"] is None


def test_annotate_rejects_an_opinion_with_no_quote(paper, artifact, tmp_path):
    rid = _prepare(paper, artifact, tmp_path)
    op = tmp_path / "op.json"
    op.write_text(json.dumps({"text": "trust me", "quotes": []}))
    assert _run(["annotate", "--paper", str(paper), "--record", rid,
                 "--opinion-file", str(op)]) == C.EXIT_QUOTE_REJECTED


def test_annotate_refuses_when_context_went_stale(paper, artifact, tmp_path):
    rid = _prepare(paper, artifact, tmp_path)
    # Rewrite the citing sentence: the opinion must not outlive what it judged.
    (paper / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "MQuAKE's native metric is a disjunction over paraphrases"
        "~\\cite{zhong2023mquake}.\n\\end{document}\n")
    op = tmp_path / "op.json"
    op.write_text(json.dumps({
        "text": "x", "quotes": [{"text": "reported only as a reference metric"}]}))
    assert _run(["annotate", "--paper", str(paper), "--record", rid,
                 "--opinion-file", str(op)]) == C.EXIT_QUOTE_REJECTED


def test_unknown_format_version_is_refused_not_guessed(paper, artifact, tmp_path):
    _prepare(paper, artifact, tmp_path)
    p = K.cache_path(paper)
    data = json.loads(p.read_text())
    data["format_version"] = 99
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="format_version"):
        K.read(paper)


# --- an empty worklist has three readings, and they are not the same fact ----
#
# Before 2026-10-15 a paper directory with no bibliography made `run` print
# "bib file not found: (none named or present)" and exit 2 without writing
# anything. The reader (self_review.py citations) then said, correctly, that
# ref-check "has not been run" -- and could not tell that from "ran, and there
# was nothing to check". Writing the cache entry by hand instead is worse: a
# fabricated "no bibliography" record makes the next reader believe the
# references were examined.


def _no_bib_paper(tmp_path: Path, name: str, body: str | None = None) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "REPORT.md").write_text(body or "# A report\n\nNo citations anywhere.\n")
    return d


def test_no_bibliography_and_no_citations_is_recorded_not_refused(tmp_path):
    d = _no_bib_paper(tmp_path, "artifact")
    assert not K.cache_path(d).exists()          # "never ran" is the before state
    assert _run(["run", "--paper", str(d)]) == C.EXIT_OK
    assert K.cache_path(d).exists()              # and now it is distinguishable
    data = K.read(d)
    assert data["records"] == []
    assert data["scan"]["nothing_to_check"] == K.NO_BIBLIOGRAPHY_NO_CITATIONS
    assert data["scan"]["tex_files"] == 0
    assert data["scan"]["cite_commands"] == 0
    assert data["scan"]["bib_files"] == []
    # additive: an older reader must still accept the file
    assert data["format_version"] == C.FORMAT_VERSION


def test_the_report_says_which_kind_of_empty_it_is(tmp_path):
    d = _no_bib_paper(tmp_path, "artifact2")
    _run(["run", "--paper", str(d)])
    md = K.cache_path(d).with_suffix(".md").read_text()
    assert "Nothing to check" in md
    assert "0 .tex file(s)" in md
    # ...and must not claim anything about the references themselves
    assert "not a statement that the references are sound" in md
    assert "wrong directory" in md


def test_zero_tex_files_warns_that_it_may_be_the_wrong_directory(tmp_path, capsys):
    d = _no_bib_paper(tmp_path, "maybe_wrong")
    _run(["run", "--paper", str(d)])
    err = capsys.readouterr().err
    assert "0 .tex file(s)" in err and "wrong directory" in err


def test_a_paper_that_cites_with_no_bib_is_still_an_error(tmp_path):
    # The opposite direction, and the one that must NOT become "nothing to
    # check": citations exist, so the references were not examined.
    d = tmp_path / "cites_no_bib"
    d.mkdir()
    (d / "main.tex").write_text(
        "MQuAKE's native metric is conjunction over all edits"
        "~\\cite{zhong2023mquake}.\n")
    assert _run(["run", "--paper", str(d)]) == C.EXIT_NO_BIB
    assert not K.cache_path(d).exists()


def test_the_citation_count_is_named_when_there_is_no_bib(tmp_path, capsys):
    d = tmp_path / "cites_no_bib2"
    d.mkdir()
    (d / "main.tex").write_text("A claim of 94.2 accuracy~\\cite{a}.\n")
    _run(["run", "--paper", str(d)])
    err = capsys.readouterr().err
    assert "1 citation(s)" in err
    assert "the references were not examined" in err


def test_a_named_bib_that_does_not_exist_records_nothing(tmp_path):
    d = _no_bib_paper(tmp_path, "typo")
    assert _run(["run", "--paper", str(d), "--bib", str(d / "nope.bib")]) == C.EXIT_NO_BIB
    assert not K.cache_path(d).exists()


def test_no_citable_claim_records_the_reason_and_the_counts(tmp_path):
    d = tmp_path / "quiet2"
    d.mkdir()
    (d / "main.tex").write_text("Background only~\\cite{bg2020}.\n")
    (d / "r.bib").write_text("@article{bg2020,title={B},author={B},year={2020}}\n")
    assert _run(["run", "--paper", str(d), "--bib", str(d / "r.bib"),
                 "--bibguard", _stub(tmp_path)]) == C.EXIT_OK
    sc = K.read(d)["scan"]
    assert sc["nothing_to_check"] == K.NO_CITABLE_CLAIM
    assert sc["cite_commands"] == 1             # it cites; the claim is not checkable
    assert sc["citable_claims"] == 0
    assert sc["bib_files"] and sc["tex_files"] == 1


def test_a_normal_run_carries_the_counts_and_no_reason(paper, artifact, tmp_path):
    _run(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
          "--main", "main.tex", "--bibguard", _stub(tmp_path),
          "--artifact", f"zhong2023mquake={artifact}"])
    sc = K.read(paper)["scan"]
    assert sc["nothing_to_check"] is None
    assert sc["cite_commands"] == 2 and sc["citable_claims"] == 1
    assert sc["tex_files"] == 1 and len(sc["bib_files"]) == 1


def test_scan_refuses_a_reason_it_does_not_define():
    with pytest.raises(AssertionError):
        K.scan(1, 1, [], 0, "NOTHING_WRONG_HERE")
