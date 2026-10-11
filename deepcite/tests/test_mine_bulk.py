"""Tests for mining a local arXiv corpus slice. No AWS, no network.

A synthetic corpus exercises the whole path, so the route is known to work before
any S3 bytes are paid for. The parts that matter are the ones that replaced
network calls: reading the cited work's arXiv id out of the citing paper's own
bibliography, and taking the abstract from the cited source rather than an API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepcite.eval import mine_fulltext_cases as M

CITED_BODY = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We introduce MQuAKE, a benchmark for knowledge editing built from multi-hop
questions over Wikidata. We release the dataset and the evaluation code.
\end{abstract}
\section{Evaluation Metrics}
We use the following metrics to measure whether the edits are made successfully.
Multi-hop accuracy measures the accuracy of the original and edited models on
multi-hop questions. In our datasets, there are three generated multi-hop
questions for each instance. If any of the three questions is correctly answered
by the model, we regard it as accurate.
The conjunction over all edits is reported only as a reference metric.
\end{document}
"""

CITING_TEX = r"""\documentclass{article}
\begin{document}
MQuAKE's native metric is conjunction over all edits~\cite{mquake}.
Knowledge editing has attracted much attention recently~\cite{other}.
\end{document}
"""

# Most arXiv submissions ship a .bbl rather than a .bib, so the default fixture
# uses one; the .bib path has its own test.
CITING_BBL = r"""\begin{thebibliography}{9}
\bibitem{mquake}
Z.~Zhong, Z.~Wu, C.~Manning, C.~Potts, D.~Chen.
\newblock MQuAKE: Assessing knowledge editing via multi-hop questions.
\newblock {\em arXiv:2305.14795}, 2023.
\bibitem{other}
A.~Bee. A survey of something. 2020.
\end{thebibliography}
"""


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "bulk"
    src = root / "src"
    citing, cited = src / "2401.00001", src / "2305.14795"
    citing.mkdir(parents=True)
    cited.mkdir(parents=True)
    (citing / "main.tex").write_text(CITING_TEX)
    (citing / "main.bbl").write_text(CITING_BBL)
    (cited / "main.tex").write_text(CITED_BODY)
    (root / "index.json").write_text(json.dumps(
        {"2401.00001": str(citing), "2305.14795": str(cited)}))
    return root


def test_bbl_arxiv_ids_binds_an_id_to_the_entry_that_printed_it():
    got = M.bbl_arxiv_ids(CITING_BBL)
    assert got == {"mquake": "2305.14795"}, got
    # The entry without an id must not inherit its neighbour's.
    assert "other" not in got


def test_bbl_arxiv_ids_handles_both_id_schemes():
    bbl = (r"\bibitem{a} Foo. \newblock arXiv:1012.3615v2, 2010." "\n"
           r"\bibitem{b} Bar. \newblock arXiv:hep-th/9901001, 1999.")
    got = M.bbl_arxiv_ids(bbl)
    assert got["a"] == "1012.3615v2" and got["b"] == "hep-th/9901001"


def test_corpus_cited_ids_reads_a_bib_when_present(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "refs.bib").write_text(
        "@inproceedings{mquake, title={MQuAKE}, author={Zhong, Z},\n"
        " eprint={2305.14795}, archivePrefix={arXiv}, year={2023}}\n")
    assert M.corpus_cited_ids(d) == {"mquake": "2305.14795"}


def test_corpus_cited_ids_prefers_the_bib_over_the_bbl(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "refs.bib").write_text(
        "@article{k, title={T}, eprint={1111.1111}, archivePrefix={arXiv}}\n")
    (d / "main.bbl").write_text(r"\bibitem{k} T. \newblock arXiv:2222.2222.")
    assert M.corpus_cited_ids(d)["k"] == "1111.1111"


def test_corpus_abstract_comes_from_the_source(corpus):
    abs_ = M.corpus_abstract(corpus / "src" / "2305.14795")
    assert "benchmark for knowledge editing" in abs_
    # The body must not leak into the abstract, or the body-only test is void.
    assert "Multi-hop accuracy measures" not in abs_


def test_corpus_abstract_is_empty_not_an_error_when_absent(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "main.tex").write_text("\\documentclass{article}\nNo abstract here.\n")
    assert M.corpus_abstract(d) == ""


def test_corpus_body_strips_comments_and_citations(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "main.tex").write_text(
        "Live text about accuracy~\\cite{k}.\n% deleted claim about variance\n")
    body = M.corpus_body(d)
    assert "variance" not in body, "a comment reached the body"
    assert "cite{k}" not in body
    assert "Live text about accuracy" in body


def test_mine_bulk_finds_the_body_only_case_with_no_network(corpus):
    kept, rejected = M.mine_bulk(corpus)
    assert len(kept) == 1, [r["reason"] for r in rejected]
    c = kept[0]
    assert c.cited_arxiv_id == "2305.14795"
    assert c.family == "attribution" and c.claim_type == "metric"
    assert "conjunction" in c.citing_sentence
    # The retrieved passage must be the one that actually bears on the claim.
    # The citing sentence says conjunction is MQuAKE's *native* metric; the
    # sentence that settles it is the cited paper calling conjunction a
    # *reference* metric. A passage merely about metrics in general would leave a
    # human no better off, so this asserts the decisive sentence, not a topical one.
    assert "reference metric" in c.fulltext_passage, c.fulltext_passage
    assert "conjunction" in c.fulltext_passage
    assert c.fulltext_locator.startswith("body:")


def test_mine_bulk_records_why_a_citation_was_skipped(corpus):
    _, rejected = M.mine_bulk(corpus)
    why = {r["reason"] for r in rejected}
    # `other` is cited by a background sentence and has no arXiv id; either
    # reason is legitimate, but it must be recorded rather than vanish.
    assert why, "nothing explained the citations that produced no candidate"


def test_mine_bulk_reports_a_cited_work_outside_the_slice(tmp_path):
    root = tmp_path / "bulk"
    citing = root / "src" / "2401.00002"
    citing.mkdir(parents=True)
    (citing / "main.tex").write_text(CITING_TEX)
    (citing / "main.bbl").write_text(CITING_BBL)
    (root / "index.json").write_text(json.dumps({"2401.00002": str(citing)}))
    kept, rejected = M.mine_bulk(root)
    assert kept == []
    assert any(r["reason"] == "cited work outside the corpus slice"
               for r in rejected), [r["reason"] for r in rejected]


def test_mine_bulk_refuses_without_an_index(tmp_path):
    with pytest.raises(FileNotFoundError, match="no corpus index"):
        M.mine_bulk(tmp_path)


def test_mine_bulk_emits_candidates_as_it_finds_them(corpus, tmp_path):
    out = tmp_path / "cands.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        M.mine_bulk(corpus, sink=fh)
    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["cited_arxiv_id"] == "2305.14795"
