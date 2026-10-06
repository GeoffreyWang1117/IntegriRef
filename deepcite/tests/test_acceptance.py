"""Acceptance tests from docs/DEEPCITE_SPEC_v1.1.md §8. Opt-in: they use the network.

Run with:
    DEEPCITE_NETWORK=1 python -m pytest deepcite/tests/test_acceptance.py -v -s

They are slow by design -- arXiv is rate-limited to one request per three seconds
and e-prints are downloaded once per version into the shared artifact cache, so a
second run is fast.

The CIKM case is read from a monthly backup snapshot. It is COPIED to a temporary
directory first and the snapshot is never written to; deepcite also refuses to
write under any path containing ProjectsBackup/snapshots, flags included.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from deepcite import cache as K
from deepcite import select as S
from deepcite import texscan as T
from deepcite.cli import main

NETWORK = os.environ.get("DEEPCITE_NETWORK") == "1"
pytestmark = pytest.mark.skipif(not NETWORK, reason="set DEEPCITE_NETWORK=1 to run")

# bibguard 0.5.0 is committed but unpublished, so point at the local checkout.
LOCAL_BIBGUARD = Path.home() / "Engineering/ref-check/src"
BIBGUARD_CMD = f"env PYTHONPATH={LOCAL_BIBGUARD} python3 -m bibguard.cli"

CIKM_V17 = Path("/data/ProjectsBackup/snapshots/monthly/monthly.2026-06"
                "/GraphLLMRec/submissions/cikm2026")

MQUAKE_TEX = r"""\documentclass{article}
\begin{document}
MQuAKE's native metric is conjunction over all edits~\cite{zhong2023mquake}.
Knowledge editing has attracted much attention recently~\cite{bg2020}.
\end{document}
"""

MQUAKE_BIB = r"""@inproceedings{zhong2023mquake,
  title={MQuAKE: Assessing Knowledge Editing in Language Models via Multi-Hop Questions},
  author={Zhong, Zexuan and Wu, Zhengxuan and Manning, Christopher D and Potts,
          Christopher and Chen, Danqi},
  booktitle={EMNLP}, year={2023}
}
@article{bg2020, title={A Survey of Something}, author={Bee, Bob}, year={2020}}
"""


def test_mquake_metric_claim_retrieves_the_real_definition(tmp_path):
    """§8: the attribution family must select it, and one of the top 5 passages
    must be where MQuAKE states its main metric.

    This is the ROA-LLM failure: the citing paper calls conjunction MQuAKE's
    native metric, while MQuAKE's own text defines multi-hop accuracy as correct
    if ANY of three paraphrases is answered -- a disjunction.
    """
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(MQUAKE_TEX)
    (paper / "refs.bib").write_text(MQUAKE_BIB)

    rc = main(["run", "--paper", str(paper), "--bib", str(paper / "refs.bib"),
               "--main", "main.tex", "--bibguard", BIBGUARD_CMD])
    assert rc == 0

    recs = K.read(paper)["records"]
    assert [r["bib_key"] for r in recs] == ["zhong2023mquake"], \
        "background citation must not be selected"
    rec = recs[0]
    assert rec["selection"]["family"] == "attribution"
    assert rec["cited_id"].startswith("arXiv:2305.14795v"), rec["cited_id"]
    assert rec["artifact"]["kind"] == "arxiv_source"
    assert rec["status"] == K.CANDIDATE_EVIDENCE
    assert 0 < len(rec["passages"]) <= 5

    blob = " ".join(p["text"].lower() for p in rec["passages"])
    assert "multi-hop accuracy" in blob, \
        f"the metric definition did not surface; got: {blob[:400]}"
    # The decisive wording: any of the three questions, i.e. a disjunction.
    assert "any of the three" in blob or "correctly answered" in blob, \
        f"the disjunction wording did not surface; got: {blob[:400]}"


@pytest.mark.skipif(not CIKM_V17.is_dir(), reason="CIKM v17 snapshot not mounted")
def test_cikm_v17_protocol_attribution_is_flagged(tmp_path):
    """§8: the protocol-attribution sentence must land in the worklist.

    The one blocking item in CIKM's real reviews was a protocol credited to the
    wrong paper, and the internal panel missed it entirely. The sentence that
    carries that attribution is experimental_setup.tex:61 ("this is the protocol
    of \\cite{hou2024large,sun2023chatgpt}").
    """
    work = tmp_path / "cikm17"
    shutil.copytree(CIKM_V17, work)
    before = {p: p.stat().st_mtime for p in CIKM_V17.rglob("*") if p.is_file()}

    rc = main(["run", "--paper", str(work), "--bib", str(work / "references.bib"),
               "--main", "main.tex", "--bibguard", BIBGUARD_CMD])
    assert rc == 0

    # The snapshot must be untouched.
    after = {p: p.stat().st_mtime for p in CIKM_V17.rglob("*") if p.is_file()}
    assert before == after, "the backup snapshot was modified"
    assert not (CIKM_V17 / ".cache").exists()

    recs = K.read(work)["records"]
    protocol = [r for r in recs
                if r["selection"]["claim_type"] == "protocol"
                and r["citing"]["file"] == "experimental_setup.tex"]
    assert protocol, f"protocol attribution not selected; got " \
                     f"{[(r['bib_key'], r['selection']) for r in recs]}"
    assert {r["bib_key"] for r in protocol} == {"hou2024large", "sun2023chatgpt"}, \
        "both papers the protocol is credited to must be checked separately"
    for r in protocol:
        assert r["status"] in (K.CANDIDATE_EVIDENCE, K.CLAIM_ABSENT_FROM_ARTIFACT,
                               K.NO_FULLTEXT, K.UNRESOLVED)
        if r["status"] == K.CLAIM_ABSENT_FROM_ARTIFACT:
            assert r["search_terms"], "an absence claim must record what was tried"


@pytest.mark.skipif(not CIKM_V17.is_dir(), reason="CIKM v17 snapshot not mounted")
def test_cikm_worklist_stays_small_enough_to_read(tmp_path):
    """Offline sanity on worklist size -- the number that decides whether a human
    actually works through it. 56 bib entries should not yield dozens of records."""
    work = tmp_path / "cikm17"
    shutil.copytree(CIKM_V17, work)
    n = sum(1 for rel in T.tex_files(work)
            for cit in T.scan_file(work, rel).citations
            if S.select(cit.sentence.text))
    assert n <= 15, f"{n} selected from a 56-entry bib is too noisy to read"
    assert n >= 5, f"only {n} selected -- selection may have regressed"


@pytest.mark.skipif(not CIKM_V17.is_dir(), reason="CIKM v17 snapshot not mounted")
def test_background_citations_in_a_real_paper_are_not_selected(tmp_path):
    """Negative fixtures from a real bibliography: TALLRec appears only in
    background lists here, so it must not be selected at all."""
    work = tmp_path / "cikm17"
    shutil.copytree(CIKM_V17, work)
    picked = {cit.bib_key for rel in T.tex_files(work)
              for cit in T.scan_file(work, rel).citations
              if S.select(cit.sentence.text)}
    assert "bao2023tallrec" not in picked, \
        "TALLRec is only cited as background in this paper"
    assert "wei2022chain" not in picked
