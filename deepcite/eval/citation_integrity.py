"""Evaluate deepcite's selection and retrieval against the Citation-Integrity corpus.

Citation-Integrity (Sarol et al., *Bioinformatics* 40(7), 2024, MIT licence,
github.com/ScienceNLP-Lab/Citation-Integrity) is the only external, expert-labelled
corpus for the task deepcite performs: does a citing sentence describe the cited
work correctly. 3,063 citation instances from 100 highly-cited biomedical papers,
39.2% of them carrying an accuracy error, with per-sentence gold evidence in the
cited document.

**What this corpus can and cannot validate.** Measured here, not assumed:

  * It is **abstracts only**. Evidence-bearing documents have a median of 5
    sentences and 52.9% have 5 or fewer, so a top-5 retrieval hits those
    trivially. Only the >5-sentence cases (47.1% on dev) discriminate, and this
    harness reports those separately. deepcite's own premise -- read the primary
    full text, never a summary -- is therefore **not** tested here. That claim
    needs its own data; see PLAN.md.
  * Its hardest subtask, picking the cited document out of a ~79-document
    candidate pool, is **not deepcite's problem**: deepcite is handed a bib key
    and resolves it through bibguard, so the harness supplies the gold document.
  * Its label set is finer than deepcite's output (ACCURATE, NOT_SUBSTANTIATE,
    CONTRADICT, OVERSIMPLIFY, MISQUOTE, INDIRECT, ETIQUETTE). deepcite emits no
    accuracy label at all, so the published 0.59 micro-F1 is **not** a baseline
    we can beat or lose to. Only retrieval recall is comparable, and even that
    loosely: their retriever ran BM25 + MonoT5 over the top 20.

  * Its gold evidence is a *selected* set of sentences. Nothing in the annotation
    protocol marks the remaining sentences as irrelevant, so a returned non-gold
    sentence is **not annotated as evidence** -- it is not established to be
    useless. Precision figures below therefore bound the annotated fraction of a
    worklist from below, and a blinded audit of high-ranked non-gold passages is
    what would turn that bound into an estimate.

So the numbers this produces bound the selection and retrieval stages. They do not
measure the end-to-end feature.

**Reading the precision numbers.** With a fixed top-5 and a median of 1-2 gold
sentences per pair, the arithmetic ceiling on evidence precision@5 is 0.2-0.4. A
precision of 0.38 against a ceiling of 0.43 is a near-saturated list, not a poor
one, so every precision figure is reported next to the ceiling the gold labels
allow. The ranking objective here is recall -- missing the only useful passage
costs a reviewer more than one extra paragraph of reading -- with precision
reported as the workload it implies rather than as a second objective.

Usage:
    python -m deepcite.eval.citation_integrity --split dev [--download]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics as st
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .. import retrieve as V
from .. import select as S

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "benchmarks" / "data" / "citation_integrity"
RAW = ("https://raw.githubusercontent.com/ScienceNLP-Lab/Citation-Integrity/main/"
       "Data/multivers-format/")
FILES = {"train": "claims-train.jsonl", "dev": "claims-dev.jsonl",
         "test": "claims-test.jsonl", "corpus": "corpus.jsonl"}

# The corpus anonymises citation markers; deepcite's selection requires a real
# \cite call, so markers are rewritten rather than stripped. Rewriting (not
# deleting) matters: "CITEREF reports 95%" and "reports 95%" select differently.
_MARKERS = [
    re.compile(r"\[?<\|multi_cit\|>\]?"),
    re.compile(r"<CITATION_MARKER>.*?</CITATION_MARKER>", re.S),
    re.compile(r"\[?<\|cit\|>\]?"),
]
ERROR_LABELS = {"NOT_SUBSTANTIATE", "CONTRADICT", "OVERSIMPLIFY", "MISQUOTE",
                "INDIRECT", "ETIQUETTE"}
TRIVIAL_DOC_SENTENCES = 5      # at or below this, a top-5 retrieval cannot miss


def download() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for name in FILES.values():
        dest = DATA / name
        if dest.exists():
            continue
        print(f"  fetching {name} …")
        urllib.request.urlretrieve(RAW + name, dest)


def unmask(claim: str, key: str = "cited") -> str:
    """Put a \\cite call back where the corpus removed one."""
    out = claim
    for rx in _MARKERS:
        out = rx.sub(f"\\\\cite{{{key}}}", out)
    if "\\cite{" not in out:
        # No marker survived the corpus's preprocessing; append one so the
        # sentence is still a citing sentence rather than a bare assertion.
        out = out.rstrip()
        out = (out[:-1] if out.endswith(".") else out) + f"~\\cite{{{key}}}."
    return out


@dataclass
class Row:
    claim_id: int
    claim: str          # with a \cite call restored
    gold: dict          # {doc_id: {"sentences": set[int], "labels": set[str]}}
    has_evidence: bool


def load(split: str) -> tuple[list[Row], dict[int, dict]]:
    corpus = {}
    with open(DATA / FILES["corpus"], encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            corpus[int(d["doc_id"])] = d
    rows = []
    with open(DATA / FILES[split], encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            gold: dict[int, dict] = defaultdict(
                lambda: {"sentences": set(), "labels": set()})
            for doc, items in (d.get("evidence") or {}).items():
                for it in items:
                    gold[int(doc)]["sentences"].update(it.get("sentences") or [])
                    gold[int(doc)]["labels"].add(it.get("label"))
            rows.append(Row(claim_id=d["id"], claim=unmask(d["claim"]),
                            gold=dict(gold), has_evidence=bool(gold)))
    return rows, corpus


def doc_units(doc: dict) -> list[tuple[str, int, int, str]]:
    """One retrieval unit per abstract sentence, line numbers = sentence index."""
    return [(f"doc{doc['doc_id']}", i, i, s)
            for i, s in enumerate(doc.get("abstract") or [])]


def _dcg(flags) -> float:
    """Binary-relevance DCG over a ranked list of hit/miss flags."""
    return sum(1.0 / math.log2(i + 1) for i, ok in enumerate(flags, 1) if ok)


class _Retrieval:
    """Every retrieval metric for one subset of (claim, cited document) pairs.

    All of k=1..top_k come from a single ranking. ``retrieve._score``
    deduplicates *before* truncating, so the first k of its output is exactly
    what a top-k run would have returned; re-ranking per k would be slower and
    could not differ. (If that order ever changes, this becomes wrong, which is
    why it is written down here rather than left to the reader.)
    """

    def __init__(self, top_k: int) -> None:
        self.k = top_k
        n = top_k + 1
        self.inter = [0] * n       # |R_k intersect G|, summed over pairs
        self.shown = [0] * n       # |R_k|, summed
        self.hit = [0] * n         # pairs with at least one gold in R_k
        self.ceiling = [0] * n     # sum of min(|R_k|, |G|): the most |R_k ^ G| can be
        self.gold = self.pairs = self.empty = self.collisions = 0
        self.rr: list[float] = []
        self.ndcg: list[float] = []
        self.macro_p: list[float] = []
        self.macro_r: list[float] = []
        self.returned: list[int] = []
        self.first_rank: list[int] = []

    def add(self, got: list, gold: set[int]) -> None:
        idx = [p.line_start for p in got]
        if len(set(idx)) != len(idx):
            # Should be unreachable: units are one sentence each and _dedupe
            # drops passages overlapping a higher-ranked one in the same file.
            # Counted rather than assumed -- an unnoticed duplicate would
            # inflate precision by scoring one sentence twice.
            self.collisions += 1
            seen: set[int] = set()
            uniq = []
            for p in got:
                if p.line_start not in seen:
                    seen.add(p.line_start)
                    uniq.append(p)
            got = uniq
        flags = [p.line_start in gold for p in got]

        self.pairs += 1
        self.gold += len(gold)
        self.returned.append(len(got))
        if not got:
            self.empty += 1
        for k in range(1, self.k + 1):
            f = flags[:k]
            self.inter[k] += sum(f)
            self.shown[k] += len(f)
            self.hit[k] += bool(any(f))
            self.ceiling[k] += min(len(f), len(gold))

        # Macro precision is undefined with nothing returned. Averaging over the
        # pairs where it is defined, and publishing that denominator, is the only
        # honest option: substituting 0 punishes an empty list twice and
        # substituting 1 rewards it.
        if got:
            self.macro_p.append(sum(flags) / len(flags))
        self.macro_r.append(sum(flags) / len(gold))
        rank = next((i for i, ok in enumerate(flags, 1) if ok), 0)
        self.rr.append(1.0 / rank if rank else 0.0)
        if rank:
            self.first_rank.append(rank)
        ideal = _dcg([True] * min(self.k, len(gold)))
        self.ndcg.append(_dcg(flags) / ideal if ideal else 0.0)

    def report(self) -> dict:
        k = self.k
        out = {
            f"hit@{k}": _rate(self.hit[k], self.pairs),
            "hit/pairs": f"{self.hit[k]}/{self.pairs}",
            f"evidence_recall@{k}_micro": _rate(self.inter[k], self.gold),
            f"evidence_recall@{k}_macro": _mean(self.macro_r),
            f"evidence_precision@{k}_micro": _rate(self.inter[k], self.shown[k]),
            f"evidence_precision@{k}_macro": _mean(self.macro_p),
            f"evidence_precision@{k}_ceiling": _rate(self.ceiling[k],
                                                     self.shown[k]),
            f"mrr@{k}": _mean(self.rr),
            f"ndcg@{k}": _mean(self.ndcg),
            "gold_sentences": self.gold,
            "passages_shown": self.shown[k],
            "passages_per_pair_mean": (round(st.mean(self.returned), 3)
                                       if self.returned else None),
            "passages_per_pair_median": (st.median(self.returned)
                                         if self.returned else None),
            "pairs_with_no_result": f"{self.empty}/{self.pairs}",
            "coverage": _rate(self.pairs - self.empty, self.pairs),
            "macro_precision_defined_on": f"{len(self.macro_p)}/{self.pairs}",
            "median_rank_of_first_gold_hit": (st.median(self.first_rank)
                                              if self.first_rank else None),
            "duplicate_index_pairs": self.collisions,
            # The whole curve, so a single k cannot be chosen after the fact.
            "by_k": {
                str(j): {
                    "hit": _rate(self.hit[j], self.pairs),
                    "evidence_recall_micro": _rate(self.inter[j], self.gold),
                    "evidence_precision_micro": _rate(self.inter[j],
                                                      self.shown[j]),
                    "evidence_precision_ceiling": _rate(self.ceiling[j],
                                                        self.shown[j]),
                }
                for j in range(1, k + 1)
            },
        }
        # Kept so results predating the rename stay comparable: this figure was
        # published as "recall@5" when it is the fraction of pairs with at least
        # one gold sentence retrieved, i.e. hit@5. Same number, wrong name.
        out[f"recall@{k}_deprecated_alias_of_hit"] = out[f"hit@{k}"]
        return out


def evaluate(split: str, top_k: int = V.MAX_PASSAGES) -> dict:
    rows, corpus = load(split)

    sel_fired_with_ev = sel_total_with_ev = 0
    sel_fired_no_ev = sel_total_no_ev = 0
    by_label_fired: Counter = Counter()
    by_label_total: Counter = Counter()

    allp = _Retrieval(top_k)
    hard = _Retrieval(top_k)

    for r in rows:
        sel = S.select(r.claim)
        if r.has_evidence:
            sel_total_with_ev += 1
            sel_fired_with_ev += bool(sel)
            for g in r.gold.values():
                for lab in g["labels"]:
                    by_label_total[lab] += 1
                    by_label_fired[lab] += bool(sel)
        else:
            sel_total_no_ev += 1
            sel_fired_no_ev += bool(sel)

        # Retrieval is scored on every evidence-bearing claim, independently of
        # whether selection fired: conflating the two would hide which stage loses
        # a case.
        terms = S.search_terms(r.claim)
        ct = sel.claim_type if sel else "other"
        for doc_id, g in r.gold.items():
            doc = corpus.get(doc_id)
            if not doc or not g["sentences"]:
                continue
            units = doc_units(doc)
            if not units:
                continue
            got = V.rank_units(terms, units, top_k=top_k, claim_type=ct)
            allp.add(got, g["sentences"])
            if len(units) > TRIVIAL_DOC_SENTENCES:
                hard.add(got, g["sentences"])

    return {
        "split": split,
        "claims": len(rows),
        "top_k": top_k,
        # Numbers move when the patterns move. Recording the hash is what makes a
        # before/after comparison meaningful instead of anecdotal.
        "selection_regex_sha256": S.patterns_sha256(),
        "tool_version": __import__("deepcite").__version__,
        # Every knob that can move a number, recorded in the result rather than
        # encoded in a filename. Three reported retrieval figures have already
        # changed under a constant patterns hash, because the hash pins selection
        # only; a reader could not tell those runs apart.
        "retrieval_config": {
            "min_unit_words": V.MIN_UNIT_WORDS,
            "min_window_words": V.MIN_WINDOW_WORDS,
            "max_passages": V.MAX_PASSAGES,
            "max_words": V.MAX_WORDS,
            "search_terms_limit": S.search_terms.__defaults__[0],
        },
        "selection": {
            "recall_on_checkable": _rate(sel_fired_with_ev, sel_total_with_ev),
            "fired/with_evidence": f"{sel_fired_with_ev}/{sel_total_with_ev}",
            "fire_rate_on_no_evidence": _rate(sel_fired_no_ev, sel_total_no_ev),
            "fired/no_evidence": f"{sel_fired_no_ev}/{sel_total_no_ev}",
            "by_label": {lab: f"{by_label_fired[lab]}/{by_label_total[lab]}"
                         for lab in sorted(by_label_total)},
            "recall_on_erroneous": _rate(
                sum(by_label_fired[l] for l in ERROR_LABELS),
                sum(by_label_total[l] for l in ERROR_LABELS)),
        },
        "retrieval": {
            "note": (
                f"'hit@{top_k}' is the fraction of pairs with at least one gold "
                f"sentence in the top {top_k}; it was published as 'recall@{top_k}', "
                f"which was the wrong name for it. 'evidence_recall' is the real "
                f"recall over gold sentences. 'discriminative' = cited documents "
                f"with more than {TRIVIAL_DOC_SENTENCES} sentences, where a "
                f"top-{top_k} retrieval can actually miss, and is the figure to "
                f"read. Precision is bounded by 'evidence_precision@{top_k}_ceiling' "
                f"= sum min(|R_k|,|G|) / sum |R_k|, not by 1.0. A non-gold passage "
                f"is not annotated as evidence; it is not shown to be irrelevant."),
            "all": allp.report(),
            "discriminative": hard.report(),
        },
    }


def _mean(xs: list[float]) -> float | None:
    return round(st.mean(xs), 4) if xs else None


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m deepcite.eval.citation_integrity")
    ap.add_argument("--split", default="dev", choices=("train", "dev", "test"))
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--top-k", type=int, default=V.MAX_PASSAGES)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.download:
        download()
    missing = [n for n in (FILES[a.split], FILES["corpus"])
               if not (DATA / n).exists()]
    if missing:
        print(f"missing {missing} under {DATA}; re-run with --download")
        return 2
    res = evaluate(a.split, top_k=a.top_k)
    text = json.dumps(res, indent=1, ensure_ascii=False)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
