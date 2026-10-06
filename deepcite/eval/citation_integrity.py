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

So the numbers this produces bound the selection and retrieval stages. They do not
measure the end-to-end feature.

Usage:
    python -m deepcite.eval.citation_integrity --split dev [--download]
"""

from __future__ import annotations

import argparse
import json
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


def evaluate(split: str, top_k: int = V.MAX_PASSAGES) -> dict:
    rows, corpus = load(split)

    sel_fired_with_ev = sel_total_with_ev = 0
    sel_fired_no_ev = sel_total_no_ev = 0
    by_label_fired: Counter = Counter()
    by_label_total: Counter = Counter()

    hit = total = 0
    hit_hard = total_hard = 0
    ranks: list[int] = []

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
            found = {p.line_start for p in got}
            ok = bool(found & g["sentences"])
            total += 1
            hit += ok
            if len(units) > TRIVIAL_DOC_SENTENCES:
                total_hard += 1
                hit_hard += ok
            if ok:
                for i, p in enumerate(got, 1):
                    if p.line_start in g["sentences"]:
                        ranks.append(i)
                        break

    return {
        "split": split,
        "claims": len(rows),
        "top_k": top_k,
        # Numbers move when the patterns move. Recording the hash is what makes a
        # before/after comparison meaningful instead of anecdotal.
        "selection_regex_sha256": S.patterns_sha256(),
        "tool_version": __import__("deepcite").__version__,
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
            f"recall@{top_k}": _rate(hit, total),
            "hit/total": f"{hit}/{total}",
            f"recall@{top_k}_discriminative": _rate(hit_hard, total_hard),
            "hit/total_discriminative": f"{hit_hard}/{total_hard}",
            "note": (f"'discriminative' = cited documents with more than "
                     f"{TRIVIAL_DOC_SENTENCES} sentences, where a top-{top_k} "
                     f"retrieval can actually miss"),
            "median_rank_of_first_gold_hit": (st.median(ranks) if ranks else None),
        },
    }


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
