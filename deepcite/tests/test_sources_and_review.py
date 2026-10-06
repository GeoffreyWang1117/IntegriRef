"""2026-10-06 additions, all offline: refusals vs answers, title matching, the
bib, main/bib detection, the quotation check, batch annotate, and review mode.

The network is replaced by stubs; any unexpected request fails the test.
"""

from __future__ import annotations

import io
import json
import stat
import sys
import urllib.error
from pathlib import Path

import pytest

from deepcite import cache as K
from deepcite import net as N
from deepcite import resolve as R
from deepcite import texscan as T
from deepcite.cli import main

FIX = Path(__file__).resolve().parent / "fixtures" / "pdfscan"


def _run(args):
    try:
        return main(args)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1


@pytest.fixture
def offline(monkeypatch):
    """Every source refuses, as arXiv/S2/OpenAlex did on 2026-10-06."""
    def refuse(url, source, *a, **k):
        raise N.SourceRefused(source, "HTTP 429")
    monkeypatch.setattr(N, "http_get", refuse)
    monkeypatch.setattr(R, "http_get", refuse)
    import deepcite.fetch as F
    monkeypatch.setattr(F, "http_get", refuse)
    monkeypatch.setattr(R, "_memo_load", lambda: {})
    monkeypatch.setattr(R, "_memo_save", lambda memo: None)


# --- http_get: an answer is not a refusal -------------------------------------

def _urlopen_raising(code, headers=None):
    def f(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "x", headers or {}, io.BytesIO(b""))
    return f


def test_404_is_an_answer_and_429_is_a_refusal(monkeypatch):
    monkeypatch.setattr(N.time, "sleep", lambda s: None)
    monkeypatch.setattr(N.urllib.request, "urlopen", _urlopen_raising(404))
    assert N.http_get("https://x", "arxiv") is None
    monkeypatch.setattr(N.urllib.request, "urlopen", _urlopen_raising(429, {"Retry-After": "54614"}))
    with pytest.raises(N.SourceRefused) as e:
        N.http_get("https://x", "openalex")
    assert "retry after" in e.value.reason


def test_datacite_miss_with_arxiv_refusal_is_a_noted_miss_not_cached(monkeypatch):
    monkeypatch.setattr(R, "datacite_lookup", lambda t, timeout=60: None)
    def refuse(t, timeout=60):
        raise N.SourceRefused("arxiv", "HTTP 429")
    monkeypatch.setattr(R, "arxiv_api_lookup", refuse)
    memo = {}
    got, note = R.locate_arxiv("Some Title That Is Long Enough", memo=memo)
    assert got is None and "did not answer" in note and memo == {}


def test_every_source_refusing_raises(monkeypatch):
    def refuse(t, timeout=60):
        raise N.SourceRefused("x", "HTTP 429")
    monkeypatch.setattr(R, "datacite_lookup", refuse)
    monkeypatch.setattr(R, "arxiv_api_lookup", refuse)
    with pytest.raises(N.SourceRefused):
        R.locate_arxiv("Some Title That Is Long Enough", memo={})


# --- titles --------------------------------------------------------------------

@pytest.mark.parametrize("a,b,same", [
    # one-word edit between venue and arXiv versions (HackAPrompt)
    ("Ignore this title and HackAPrompt: Exposing systemic vulnerabilities of LLMs "
     "through a global prompt hacking competition",
     "Ignore This Title and HackAPrompt: Exposing Systemic Vulnerabilities of LLMs "
     "through a Global Scale Prompt Hacking Competition", True),
    # a PDF reference list's line-break hyphen (Greshake et al.)
    ("Not what you've signed up for: Com-promising real-world LLM-integrated applications",
     "Not what you've signed up for: Compromising Real-World LLM-Integrated Applications", True),
    ("{MQ}u{AKE}: Assessing Knowledge Editing", "MQuAKE: Assessing Knowledge Editing", True),
    ("Attention is all you need", "Attention is not all you need", False),
    ("Locating and Editing Factual Associations in GPT",
     "Mass-Editing Memory in a Transformer", False),
])
def test_titles_match(a, b, same):
    assert R._titles_match(a, b) is same


def test_query_words_skip_operators_and_hyphen_fragments():
    w = R._query_words("Not what you've signed up for: Com-promising real-world "
                       "applications with indirect prompt injection")
    assert "not" not in w and "com" not in w and "promising" not in w
    assert {"signed", "indirect", "injection"} <= set(w)


# --- the bib -------------------------------------------------------------------

BIB = r"""@string{emnlp = "EMNLP"}
@inproceedings{zhong2023mquake,
  title={{MQ}u{AKE}: Assessing Knowledge Editing},
  booktitle=emnlp, year={2023}, journal={arXiv preprint arXiv:2305.14795}}
@article{bg2020, title = "Background", year = 2020}
"""


def test_split_bib_fields_and_arxiv_id():
    b = R.split_bib(BIB)
    assert set(b) == {"zhong2023mquake", "bg2020", "@strings"}
    assert R.bib_field(b["zhong2023mquake"], "title") == "{MQ}u{AKE}: Assessing Knowledge Editing"
    assert R.bib_field(b["bg2020"], "title") == "Background"
    assert R.bib_arxiv_id(b["zhong2023mquake"]) == "2305.14795"
    assert R.bib_arxiv_id(b["bg2020"]) is None


# --- detection -----------------------------------------------------------------

def test_find_main_prefers_the_compiled_document(tmp_path):
    for n in ("main_aistats.tex", "main_aistats_probe.tex"):
        (tmp_path / n).write_text("\\documentclass{article}\\begin{document}x\\end{document}")
    (tmp_path / "main_aistats.pdf").write_bytes(b"%PDF-1.5")
    assert T.find_main(tmp_path) == "main_aistats.tex"


def test_only_files_the_main_inputs_are_scanned(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n\\input{sections/intro}\n"
        "% \\input{sections/old}\n\\end{document}\n")
    (tmp_path / "sections" / "intro.tex").write_text("Intro.\n")
    (tmp_path / "sections" / "old.tex").write_text("Old.\n")
    (tmp_path / "example_paper.tex").write_text("Template example.\n")
    assert T.tex_files(tmp_path) == ["main.tex", "sections/intro.tex"]


# --- run, end to end with a stub bibguard ----------------------------------------

STUB = r'''#!/usr/bin/env python3
import json, re, sys
if "--version" in sys.argv:
    print("bibguard 0.6.0"); sys.exit(0)
bib = open(sys.argv[1]).read()
keys = re.findall(r"@\w+\{([^,\s]+),", bib)
open(sys.argv[1] + ".keys", "w").write(",".join(keys))
json.dump({"version": "0.6.0", "results": [
    {"key": k, "title": k, "overall": "OK",
     "resolved_ids": {"doi": None, "arxiv_id": None, "s2_id": None, "openalex_id": None}}
    for k in keys]}, open(sys.argv[sys.argv.index("--out") + 1], "w"))
'''


@pytest.fixture
def stub(tmp_path, monkeypatch):
    p = tmp_path / "bg.py"
    p.write_text(STUB)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    seen = {}
    real = R.resolve_bib

    def spy(bib, cmd, timeout=900):
        out = real(bib, cmd, timeout)
        seen["keys"] = Path(str(bib) + ".keys").read_text().split(",")
        return out
    monkeypatch.setattr(R, "resolve_bib", spy)
    return f"{sys.executable} {p}", seen


@pytest.fixture
def quoted_paper(tmp_path):
    d = tmp_path / "paper"
    d.mkdir()
    (d / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        "\\citet{arnal} report a buffer that ``drastically reduces inference compute "
        "without degrading'' performance.\n"
        "\\citet{arnal2} report a buffer that ``drastically reduce inference compute "
        "without degrading'' performance.\n"
        "Background work exists~\\cite{bg}.\n"
        "\\bibliography{refs}\n\\end{document}\n")
    (d / "refs.bib").write_text("@article{arnal,title={A},year={2026}}\n"
                                "@article{arnal2,title={A2},year={2026}}\n"
                                "@article{bg,title={B},year={2020}}\n")
    art = tmp_path / "cited.tex"
    art.write_text("Replay can drastically reduce inference compute without degrading---and "
                   "in some cases even improving---final model performance.\n")
    return d, art


def test_quotation_checked_verbatim_and_bib_subset(quoted_paper, stub, offline, tmp_path):
    d, art = quoted_paper
    cmd, seen = stub
    rc = _run(["run", "--paper", str(d), "--bibguard", cmd,
               "--artifact", f"arnal={art}", "--artifact", f"arnal2={art}",
               "--cache-dir", str(tmp_path / "c")])
    assert rc == 0
    assert sorted(seen["keys"]) == ["arnal", "arnal2"]       # bg was never selected
    data = json.loads((tmp_path / "c" / "ref_check_deep.json").read_text())
    q = {r["bib_key"]: r["quotes_checked"][0]["found"] for r in data["records"]}
    assert q == {"arnal": False, "arnal2": True}             # "reduces" is not "reduce"
    md = (tmp_path / "c" / "ref_check_deep.md").read_text()
    assert "Quotation not found verbatim" in md and "NOT FOUND verbatim" in md


def test_refusals_are_source_unavailable_never_unresolved(stub, offline, tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "main.tex").write_text("\\documentclass{article}\\begin{document}\n"
                                "\\citet{k} show that rephrased samples evade decontamination.\n"
                                "\\end{document}\n")
    (d / "refs.bib").write_text("@article{k,title={Rethinking Benchmark and Contamination},year={2023}}\n")
    rc = _run(["run", "--paper", str(d), "--bibguard", stub[0], "--cache-dir", str(tmp_path / "c")])
    assert rc == 4
    rec = json.loads((tmp_path / "c" / "ref_check_deep.json").read_text())["records"][0]
    assert rec["status"] == K.SOURCE_UNAVAILABLE and "re-run later" in rec["error"]


def test_batch_annotate_with_assessments(quoted_paper, stub, offline, tmp_path):
    d, art = quoted_paper
    c = tmp_path / "c"
    _run(["run", "--paper", str(d), "--bibguard", stub[0], "--artifact", f"arnal={art}",
          "--artifact", f"arnal2={art}", "--cache-dir", str(c)])
    recs = json.loads((c / "ref_check_deep.json").read_text())["records"]
    ops = tmp_path / "ops.jsonl"
    ops.write_text("\n".join(json.dumps(o) for o in [
        {"record": recs[0]["record_id"], "assessment": "mismatch", "text": "verb changed",
         "quotes": [{"text": "drastically reduce inference compute without degrading"}]},
        {"record": recs[1]["record_id"], "assessment": "unclear", "searched": ["buffer"]},
    ]) + "\n")
    assert _run(["annotate", "--paper", str(d), "--cache-dir", str(c), "--opinions", str(ops)]) == 0
    got = {r["record_id"]: r["llm_opinion"] for r in
           json.loads((c / "ref_check_deep.json").read_text())["records"]}
    assert got[recs[0]["record_id"]]["assessment"] == "mismatch"
    assert got[recs[1]["record_id"]]["searched"] == ["buffer"]
    ops.write_text(json.dumps({"record": recs[0]["record_id"], "assessment": "supported",
                               "quotes": [{"text": "drastically reduce inference compute"}]}) + "\n")
    assert _run(["annotate", "--paper", str(d), "--cache-dir", str(c), "--opinions", str(ops)]) == 5


def test_pdf_artifact_is_searched_as_text(stub, offline, tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "main.tex").write_text("\\documentclass{article}\\begin{document}\n"
                                "\\citet{k} define multi-hop accuracy over paraphrased questions.\n"
                                "\\end{document}\n")
    (d / "refs.bib").write_text("@article{k,title={K},year={2023}}\n")
    rc = _run(["run", "--paper", str(d), "--bibguard", stub[0],
               "--artifact", f"k={FIX / 'authoryear.pdf'}", "--cache-dir", str(tmp_path / "c")])
    assert rc == 0
    rec = json.loads((tmp_path / "c" / "ref_check_deep.json").read_text())["records"][0]
    assert rec["status"] == K.CANDIDATE_EVIDENCE
    assert all(p["file"].endswith(".txt") for p in rec["passages"])
    assert "%PDF" not in json.dumps(rec["passages"])


# --- review: a PDF and nothing else --------------------------------------------

def test_review_a_pdf_without_tex_or_bib(offline, tmp_path):
    out = tmp_path / "rev"
    art = tmp_path / "mquake.txt"
    art.write_text("Multi-hop accuracy counts a case as correct if any of the three "
                   "paraphrased questions is answered correctly.\n" * 3)
    rc = _run(["review", str(FIX / "authoryear.pdf"), "--out", str(out),
               "--artifact", f"ref6={art}"])
    data = json.loads((out / "ref_check_deep.json").read_text())
    assert data["paper"]["kind"] == "pdf" and (out / "body.txt").exists()
    assert data["records"], "nothing selected from the fixture PDF"
    assert all(r["citing"]["page"] for r in data["records"])
    by_status = {r["status"] for r in data["records"]}
    assert K.SOURCE_UNAVAILABLE in by_status            # the stubbed network refused
    body = (out / "body.txt").read_text()
    for r in data["records"]:
        c = r["citing"]
        assert body.encode()[c["byte_start"]:c["byte_end"]].decode() == c["sentence"]
    assert rc in (0, 4)
    assert _run(["report", "--paper", str(out), "--out", str(out / "r.md")]) == 0
    assert "deepcite worklist" in (out / "r.md").read_text()


def test_run_in_headings_are_not_glued_to_the_next_sentence():
    # reported by the IntegriRef session: "\paragraph{X.}" with a period inside the
    # braces was read as prose and merged into the following sentence
    s = [x.text for x in T.segment(
        "\\paragraph{Self-Consistency variants.}\n"
        "\\citet{w} introduced Self-Consistency.\n\n"
        "\\paragraph{Knowledge editing.} ROME edits associations~\\citep{r}.\n"
        "\\textbf{Agents.} The ReAct paradigm~\\cite{y} established agents.\n"
        "We use \\textbf{bold} words inside a sentence~\\cite{z}.")]
    assert s == ["\\citet{w} introduced Self-Consistency.",
                 "ROME edits associations~\\citep{r}.",
                 "The ReAct paradigm~\\cite{y} established agents.",
                 "We use \\textbf{bold} words inside a sentence~\\cite{z}."]


def test_a_quote_found_only_in_a_commented_out_line_is_not_found(tmp_path):
    from deepcite.annotate import locate
    f = tmp_path / "cited.tex"
    f.write_text("% Our method drastically reduces compute in every setting.\n"
                 "Our method reduces compute in most settings.\n")
    assert locate("drastically reduces compute in every setting", [f], tmp_path) is None
    assert locate("reduces compute in most settings", [f], tmp_path).line == 2


def test_duplicate_copies_in_an_eprint_are_searched_once(tmp_path):
    from deepcite.fetch import unique_files
    a = tmp_path / "iclr2023" / "main.tex"
    b = tmp_path / "iclr2023 2_arXiv" / "main.tex"
    c = tmp_path / "iclr2023" / "appendix.tex"
    for f, s in ((a, "same text"), (b, "same text"), (c, "other text")):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(s)
    assert unique_files([b, c, a]) == [c, a]


def test_openreview_browser_challenge_is_unresolved_with_a_download_hint(monkeypatch, stub, tmp_path):
    import deepcite.fetch as F
    monkeypatch.setattr(R, "_memo_load", lambda: {})
    monkeypatch.setattr(R, "_memo_save", lambda memo: None)
    monkeypatch.setattr(R, "locate_arxiv", lambda title, memo=None: (None, "no arXiv paper"))
    monkeypatch.setattr(R, "openreview_lookup", lambda title, memo=None: ("abc123", "/pdf/x.pdf"))

    def challenge(url, source, *a, **k):
        raise N.SourceRefused("openreview", "HTTP 403")
    monkeypatch.setattr(F, "http_get", challenge)
    d = tmp_path / "p"
    d.mkdir()
    (d / "main.tex").write_text("\\documentclass{article}\\begin{document}\n"
                                "\\citet{k} show that the remastered labels remove most errors.\n"
                                "\\end{document}\n")
    (d / "refs.bib").write_text("@inproceedings{k,title={A Paper Only On OpenReview},year={2025}}\n")
    _run(["run", "--paper", str(d), "--bibguard", stub[0], "--cache-dir", str(tmp_path / "c"),
          "--artifact-cache", str(tmp_path / "a")])
    rec = json.loads((tmp_path / "c" / "ref_check_deep.json").read_text())["records"][0]
    assert rec["status"] == K.UNRESOLVED                    # re-running would not help
    assert "openreview.net/forum?id=abc123" in rec["error"] and "--artifact k=" in rec["error"]


def test_escaped_percent_in_the_source_matches_a_plain_quote(tmp_path):
    from deepcite.annotate import locate
    f = tmp_path / "twiki.tex"
    f.write_text("\\textbf{Rule \\#3}: We limit the proportion of single \\textsc{Subject} to "
                 "have 1\\% of the total, and \\textsc{Relation} and \\textsc{Object} by 5\\% of the total.\n")
    q = ("We limit the proportion of single Subject to have 1% of the total, "
         "and Relation and Object by 5% of the total")
    assert locate(q, [f], tmp_path) is not None
    assert locate(q.replace("5%", "10%"), [f], tmp_path) is None
