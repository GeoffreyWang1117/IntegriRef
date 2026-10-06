"""Review mode: a submission PDF -> citing sentences mapped to its own reference list.

Offline: the fixture PDFs under fixtures/pdfscan/ are committed (rebuild them with
fixtures/pdfscan/build.sh); only the poppler ``pdftotext`` binary is needed.

Real-paper numbers (2026-10-06, 15 compiled papers of this author across ICML,
NeurIPS, ACL/ARR, COLING, ACM sigconf, IEEEtran and LNCS): the parsed entry count
equalled the .bbl count on all 15, and the cited-entry count was within one of
the LaTeX source on all 15. Those PDFs are not committed; the cases below pin the
specific shapes that broke on them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from deepcite import pdfscan as P

pytestmark = pytest.mark.skipif(shutil.which("pdftotext") is None,
                                reason="pdftotext (poppler) not installed")

FIX = Path(__file__).parent / "fixtures" / "pdfscan"


@pytest.fixture(scope="module")
def numeric():
    return P.scan_pdf(FIX / "numeric.pdf")


@pytest.fixture(scope="module")
def authoryear():
    return P.scan_pdf(FIX / "authoryear.pdf")


def _title(s, i):
    return s.refs[i - 1].title


def _flat(x: str) -> str:
    return " ".join(x.split())


def _cited(s):
    # masked keeps the PDF's line breaks (it is a substitution on the sentence)
    return [(_title(s, c.ref_index).split(":")[0], _flat(c.masked)) for c in s.citations]


# --- numeric (ieeetr + cite) -------------------------------------------------

def test_numeric_reference_list(numeric):
    assert numeric.style == "numeric"
    assert [e.label for e in numeric.refs] == ["1", "2", "3", "4", "5"]
    assert _title(numeric, 1).startswith("MQuAKE: Assessing knowledge editing")
    assert numeric.refs[0].arxiv_id == "2305.14795"
    # IEEE book: no quotes around the title, the author list ends at the comma.
    assert _title(numeric, 3) == "Elements of Information Theory"
    assert numeric.refs[2].surnames == ["cover", "thomas"]
    assert numeric.refs[0].surnames[:2] == ["zhong", "wu"]


def test_numeric_mapping_and_ranges(numeric):
    got = _cited(numeric)
    assert ("MQuAKE", "MQuAKE CITEREF evaluates whether an edit propagates to its "
                      "multi-hop consequences.") in got
    # [2–4] expands to three entries, one sentence.
    range_hits = [t for t, m in got if m.startswith("Earlier benchmarks CITEREF")]
    assert sorted(range_hits) == sorted([_title(numeric, i).split(":")[0] for i in (2, 3, 4)])
    assert ("TALLRec", "We follow the evaluation protocol of CITEREF for all ranking runs.") in got


def test_numeric_intervals_and_math_are_not_citations(numeric):
    # "[0, 1]" (contains 0), "3.3 [2,5]" (a value's interval, though 2 and 5 are
    # valid labels) and "c0 = [1]" (a vector) must all be skipped silently.
    assert len(numeric.citations) == 5
    assert not any("interval" in c.masked or "vector" in c.masked for c in numeric.citations)
    assert numeric.warnings == []


# --- author-year (natbib plainnat) --------------------------------------------

def test_authoryear_reference_list(authoryear):
    assert authoryear.style == "author-year"
    assert len(authoryear.refs) == 6
    by_title = {e.title.split(":")[0]: e for e in authoryear.refs}
    assert by_title["Elements of Information Theory"].surnames == ["cover", "thomas"]
    # "TALL-|Rec" broken at a line end with no other mention in the paper: the
    # hyphen is kept (an all-caps prefix plus a capital is usually a real compound).
    tall = next(e for e in authoryear.refs if e.title.startswith("TALL"))
    assert tall.doi == "10.1145/3604915.3608857"
    assert by_title["MQuAKE"].arxiv_id == "2305.14795"
    assert all(e.label is None for e in authoryear.refs)


def test_authoryear_mapping(authoryear):
    got = _cited(authoryear)
    assert ("MQuAKE", "CITEREF define multi-hop accuracy as correct if any of three "
                      "paraphrased questions is answered.") in got
    assert ("MQuAKE", "MQuAKE CITEREF evaluates whether an edit propagates to its "
                      "multi-hop consequences.") in got
    group = sorted(t for t, m in got if m.startswith("Temporal benchmarks CITEREF"))
    assert group == ["MQuAKE", "TemporalWiki"]
    assert sorted(t for t, m in got if m.startswith("We follow")) == ["TALL-Rec"]
    assert ("Elements of Information Theory",
            "Mutual information follows the definition in CITEREF.") in got


def test_authoryear_suffix_picks_the_right_same_year_entry(authoryear):
    sent = [c for c in authoryear.citations if _flat(c.masked).startswith("The scaling result")]
    by_marker = {c.marker: _title(authoryear, c.ref_index) for c in sent}
    assert by_marker["Smith et al. (2020a)"].startswith("Scaling laws")
    assert by_marker["Smith et al., 2020b"].startswith("Calibrating")


def test_authoryear_appendix_after_references_is_scanned(authoryear):
    assert authoryear.appendix_start > 0
    app = [c for c in authoryear.citations if c.sentence.char_start >= authoryear.appendix_start]
    assert [_flat(c.masked) for c in app] == ["The appendix repeats the protocol of CITEREF "
                                              "on a second dataset."]
    assert "References" not in authoryear.body


def test_unmatched_marker_is_warned_not_mapped(authoryear):
    assert any("Nobody et al., 2019" in w for w in authoryear.warnings)
    assert not any("unknown source" in c.masked for c in authoryear.citations)


# --- offsets: the contract the rest of deepcite relies on ---------------------

@pytest.mark.parametrize("name", ["numeric", "authoryear"])
def test_offsets_slice_back_to_the_sentence(name, request):
    s = request.getfixturevalue(name)
    raw = s.body.encode("utf-8")
    assert s.citations
    for c in s.citations:
        sent = c.sentence
        assert raw[sent.byte_start:sent.byte_end].decode("utf-8") == sent.text
        assert s.body[sent.char_start:sent.char_end] == sent.text
        assert "CITEREF" in c.masked
        assert s.page_of(sent.char_start) == 1


# --- shapes that broke on real papers -------------------------------------------

def test_hyphen_joining_uses_the_document_not_the_split_halves():
    v = P._Vocab(["an edit propa-", "gates to its consequences.", "a four-",
                  "stage pipeline with four stages", "instruction-",
                  "following models; instruction tuning following prior work"])
    assert P._join_hyphen("an edit propa-", "gates to", v) == "an edit propagates to"
    assert P._join_hyphen("a four-", "stage pipeline", v) == "a four-stage pipeline"
    assert P._join_hyphen("instruction-", "following", v) == "instruction-following"
    assert P._join_hyphen("Llama-", "3.1 models", v) == "Llama-3.1 models"


@pytest.mark.parametrize("authors,expected", [
    ("Cover, T. M. and Thomas, J. A.", ["cover", "thomas"]),
    ("Devarangadi Sunil, B., Sinha, I., and Mishra, S.", ["devarangadi sunil", "sinha", "mishra"]),
    # first-last with a middle initial right after the first comma (NeurIPS plainnat)
    ("Shibhansh Dohare, J. Fernando Hernandez-Garcia, and Richard S. Sutton.",
     ["dohare", "hernandez-garcia", "sutton"]),
    ("Aäron van den Oord and Oriol Vinyals.", ["van den oord", "vinyals"]),
    ("T. Schick, J. Dwivedi-Yu, and T. Scialom", ["schick", "dwivedi-yu", "scialom"]),
])
def test_surnames(authors, expected):
    assert P._surnames(authors) == expected


@pytest.mark.parametrize("raw,title,year", [
    # ICML / plainnat surname-first
    ("Chen, X., Lin, M., Schärli, N., and Zhou, D. Teaching large language models to "
     "self-debug. In International Conference on Learning Representations, 2024a.",
     "Teaching large language models to self-debug", "2024"),
    # ACL: year right after the authors; middle initials split the authors into pieces
    ("David F. Ransohoff and Alvan R. Feinstein. 1978. Problems of spectrum and bias in "
     "evaluating the efficacy of diagnostic tests. New England Journal of Medicine.",
     "Problems of spectrum and bias in evaluating the efficacy of diagnostic tests", "1978"),
    # IEEE quoted title
    ("R. Taori, I. Gulrajani, and T. Zhang, “Stanford Alpaca: An instruction-following "
     "LLaMA model,” 2023.", "Stanford Alpaca: An instruction-following LLaMA model", "2023"),
    # LNCS: authors end at "I.:"
    ("Schick, T., Dwivedi-Yu, J.: Toolformer: Language models can teach themselves to use "
     "tools. In: NeurIPS (2023)", "Toolformer: Language models can teach themselves to use tools",
     "2023"),
])
def test_entry_title_and_year(raw, title, year):
    e = P._make_entry(1, None, raw)
    assert e.title == title
    assert e.year == year


def _refs(*raws):
    return [P._make_entry(i, None, r) for i, r in enumerate(raws, 1)]


def test_neurips_square_brackets_with_comma_separated_groups():
    refs = _refs("Aviral Kumar, Rishabh Agarwal, and Sergey Levine. Implicit "
                 "under-parameterization. In ICLR, 2021.",
                 "Clare Lyle, Zeyu Zheng, and Will Dabney. Understanding plasticity in neural "
                 "networks. In ICML, 2023.")
    body = "Networks lose adaptability [Kumar et al., 2021, Lyle et al., 2023]. We test it."
    markers, unmatched = P._find_markers(body, refs, "author-year")
    assert [m.indices for m in markers] == [[1, 2]] and not unmatched


def test_et_al_and_two_author_markers_pick_different_entries():
    refs = _refs("Daniel Russo and Benjamin Van Roy. 2018. Learning to optimize via "
                 "information-directed sampling. Operations Research.",
                 "Daniel J Russo, Benjamin Van Roy, Abbas Kazerouni, Ian Osband, and Zheng Wen. "
                 "2018. A tutorial on thompson sampling. Foundations and Trends.")
    idx = P._AuthorYearIndex(refs)
    assert idx.lookup("Russo et al.", "2018", "") == [2]
    assert idx.lookup("Russo and Van Roy", "2018", "") == [1]
    # an ambiguous marker maps to nothing rather than to a guess
    assert idx.lookup("Russo", "2018", "") == []


def test_math_brackets_are_skipped_but_names_with_operators_are_not():
    refs = [P._make_entry(i, str(i), f"A. Author{i}, \u201cTitle number {i} here,\u201d 2020.")
            for i in range(1, 25)]
    body = ("We set c0 = [1] first. The routing line (Poon et al. [22], MESS+ [23]) "
            "differs. Index x[2] and f(x)[3] are code.")
    markers, _ = P._find_markers(body, refs, "numeric")
    assert sorted(i for m in markers for i in m.indices) == [22, 23]


def test_label_runs_are_not_years():
    assert P._is_label_run([1, 2, 3, 4])
    assert not P._is_label_run([2023, 2024, 2013])
    assert P._expand_numeric("3, 5–7") == ["3", "5", "6", "7"]
    assert P._years_of("2023a,b") == [("2023", "a"), ("2023", "b")]
