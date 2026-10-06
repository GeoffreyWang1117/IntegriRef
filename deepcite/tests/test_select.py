"""Selection tests: the two families must fire on the real failures and stay
quiet on ordinary background citations.

The four motivating cases (docs/DEEPCITE_SPEC_v1.1.md §1) are all represented:
MQuAKE's metric, ChroKnowBench's snapshot count, TALLRec's protocol, and a
split attribution.
"""

from __future__ import annotations

import pytest

from deepcite.select import classify_claim_type, mask_cites, select, search_terms

ATTRIBUTION = [
    # MQuAKE: the ROA-LLM failure. No first-person verb anywhere -- this is the
    # shape the vendored L1 USING cues cannot see.
    r"MQuAKE's native metric is conjunction over all edits~\cite{zhong2023mquake}.",
    # ChroKnowBench: a number attributed to the cited work.
    r"ChroKnowBench~\cite{park2025chroknowledge} spans 13 yearly snapshots of facts.",
    r"The metric of \citet{other2020} is precision at 5.",
    r"\citet{zhong2023mquake} reports 95\% accuracy on two-hop chains.",
    r"In \citet{zhong2023mquake}, the evaluation protocol is a disjunction.",
    r"Its main benchmark is built from Wikidata triples~\citep{zhong2023mquake}.",
]

USING = [
    # TALLRec: the CIKM blocking failure.
    r"Following the protocol of \citet{bao2023tallrec}, we report accuracy over all hops.",
    r"We adopt the split used by \citet{bao2023tallrec} for all ranking runs.",
    r"We use the same evaluation setup as \citet{other2020}.",
    r"The model is fine-tuned on the dataset of \citet{bao2023tallrec}.",
    r"Scores are computed exactly as described by \citet{other2020}.",
]

NEGATIVE = [
    r"Knowledge editing has attracted much attention~\cite{zhong2023mquake}.",
    r"Several works study this problem~\cite{a2020,b2021}.",
    r"This builds on a long line of work~\citep{other2020}.",
    r"Recent progress has been rapid~\cite{a2020}.",
    r"See \citet{other2020} for details.",
    r"Our approach differs from prior work~\cite{a2020}.",
    r"A survey is available~\cite{b2021}.",
]


@pytest.mark.parametrize("s", ATTRIBUTION)
def test_attribution_family_fires(s):
    sel = select(s)
    assert sel is not None, "not selected at all"
    assert sel.family == "attribution", f"got {sel.family}/{sel.pattern_id}"


@pytest.mark.parametrize("s", USING)
def test_using_family_fires(s):
    sel = select(s)
    assert sel is not None, "not selected at all"
    assert sel.family == "using", f"got {sel.family}/{sel.pattern_id}"


@pytest.mark.parametrize("s", NEGATIVE)
def test_background_citations_not_selected(s):
    assert select(s) is None, f"false positive: {select(s)}"


def test_sentence_without_citation_never_selected():
    assert select("We adopt the protocol of the earlier study.") is None


def test_claim_types():
    assert classify_claim_type(mask_cites(
        r"MQuAKE's native metric is conjunction~\cite{k}.")) == "metric"
    assert classify_claim_type(mask_cites(
        r"We follow the protocol of \citet{k}.")) == "protocol"
    assert classify_claim_type(mask_cites(
        r"We adopt the split of \citet{k}.")) == "split"
    assert classify_claim_type(mask_cites(
        r"CITEREF spans 13 yearly snapshots.")) == "number"


def test_number_claim_type_for_bare_counts():
    sel = select(r"ChroKnowBench~\cite{k} spans 13 yearly snapshots of facts.")
    assert sel.claim_type in ("number", "dataset")


def test_search_terms_drop_latex_and_stopwords():
    terms = search_terms(
        r"MQuAKE's native metric is conjunction over all edits~\cite{zhong2023mquake}.")
    low = [t.lower() for t in terms]
    assert "conjunction" in low and "metric" in low
    assert "cite" not in low and "zhong2023mquake" not in low
    assert "the" not in low


def test_search_terms_keep_numbers():
    assert "13" in search_terms(r"\citet{k} spans 13 yearly snapshots.")


# --- 2026-10-06: trailing citations and the finding family --------------------
# Citation-Integrity (deepcite/eval) measured selection recall of 0.0275 on dev:
# A2/A3 needed the citation BEFORE the verb or number, and nothing selected what a
# cited work FOUND. These are real sentences (biomedical ones from that corpus,
# CS ones from the user's own papers) that the old patterns missed.

TRAILING = [
    r"Registries reported a case fatality rate of 38\%, which may be as high as 70\% "
    r"in the Child-Pugh C category~\cite{k}.",
    r"MQuAKE evaluates whether edits propagate to multi-hop consequences~\citep{k}.",
]

FINDING = [
    r"\citet{k} show that rephrased samples evade $n$-gram and embedding decontamination.",
    r"\citet{k} find that RLVR raises pass@1 while narrowing the pass@$k$ boundary.",
    r"It has been shown that dropout reduces co-adaptation of hidden units~\cite{k}.",
    r"As reported in \citet{k}, the gains vanish under a fixed compute budget.",
    r"Previous studies found that low socioeconomic status predicts infection~\cite{k}.",
    # bare form, each with a specificity signal
    r"Majority-vote accuracy is bounded under dependence between classifiers~\citep{k}.",
    r"Under asymmetric loss the optimal decisions are cost-weighted threshold shifts "
    r"and accuracy is the wrong criterion~\citep{k}.",
    r"Patients with cirrhosis are at higher risk of severe COVID-19 and death~\cite{k}.",
]

NOT_A_FINDING = [
    # relational mention: names the work, says nothing it found (ROA-LLM ICML)
    r"The intuition connects to recent findings on model collapse~\cite{k} and "
    r"iterative transmission effects~\cite{j}.",
    # the number is the citing paper's own result, in an aside
    r"The multi-agent cascade (E3: 40\%$\to$75\% over four stages) parallels model "
    r"collapse in recursive self-training~\cite{k}.",
    # first person: the citing paper about itself
    r"We report 85\% accuracy, higher than prior work~\cite{k}.",
    r"Our method reduces latency by 30\% compared with~\cite{k}.",
    # a long list is background, not one checkable claim
    r"Many methods improve robustness in this setting~\cite{a,b,c,d,e}.",
    # pointer
    r"For a survey of methods that reduce hallucination, see~\cite{k}.",
    # declarative but unspecific
    r"Large language models have attracted wide interest~\cite{k}.",
    # "prompt injection" is not "the prompt in [X]" (missing \b, ROA-LLM ICML)
    r"Prompt injection~\cite{k,j} manipulates model behavior through adversarial inputs.",
]


@pytest.mark.parametrize("s", TRAILING)
def test_trailing_citation_is_matched(s):
    sel = select(s)
    assert sel is not None and sel.family == "attribution", sel


@pytest.mark.parametrize("s", FINDING)
def test_finding_family_fires(s):
    sel = select(s)
    # Any family will do ("as reported in X" is the vendored using pattern U3);
    # the point is that it reaches the worklist.
    assert sel is not None, "not selected at all"


@pytest.mark.parametrize("s", NOT_A_FINDING)
def test_not_a_checkable_finding(s):
    assert select(s) is None, f"false positive: {select(s)}"


def test_finding_claim_type_reaches_retrieval():
    # retrieve._TYPE_CUES["finding"] is dead unless selection can emit the type
    from deepcite.retrieve import _TYPE_CUES
    sel = select(r"\citet{k} show that rephrased samples evade decontamination.")
    assert sel.claim_type == "finding" and "finding" in _TYPE_CUES


def test_pdf_scanner_can_pass_masked_text():
    sel = select("", masked="CITEREF show that rephrased samples evade decontamination.")
    assert sel is not None and sel.family == "finding"
