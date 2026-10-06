"""Build matched normal-citation controls for L1/L2 evaluation (Rebuttal Exp 1).

Reviewers noted Borderline-L1/L2 contain only misrepresentation positives,
so L1/L2 FPR / precision / F1 cannot be assessed. This script constructs
correct-citation controls from SciFact dev claims labelled SUPPORT: the
claim is a verified-correct statement about the cited abstract.

Each control case gets:
  citing_sentence — the claim wrapped in a neutral citing template using the
      resolved first-author surname and year (matching the borderline cases'
      citing style)
  cited_paper     — DOI / authors / year / venue resolved via Crossref title
      search (top hit, normalized title similarity >= 0.85)
  cited_abstract  — the SciFact corpus abstract (so L2 runs without fetching)
  ground_truth_risk = "low", expected_misrepresentation = false

Notes:
  - SciFact dev was used for model selection (epoch/threshold) of the L2
    NLI checkpoint; we flag this in the dataset card. Train claims are
    never used.
  - Claims whose paper cannot be confidently resolved are skipped.

Usage:
    python -m benchmarks.build_matched_controls [--limit 124] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
SCIFACT = REPO / "benchmarks/data/scifact"
OUT_DEFAULT = REPO / "benchmarks/data/matched_controls_l1l2.json"
MAILTO = "zhaohui.geoffrey.wang@gmail.com"

TEMPLATES = [
    "{author} et al. ({year}) showed that {claim} [1].",
    "As reported by {author} et al. ({year}), {claim} [1].",
    "{author} et al. ({year}) found that {claim} [1].",
    "Prior work demonstrated that {claim} [1].",
    "Consistent with {author} et al. ({year}), {claim} [1].",
]


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def resolve_crossref(title: str, session: requests.Session) -> dict | None:
    try:
        r = session.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": 1, "mailto": MAILTO},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json()["message"]["items"]
        if not items:
            return None
        it = items[0]
        got = norm_title((it.get("title") or [""])[0])
        want = norm_title(title)
        if SequenceMatcher(None, got, want).ratio() < 0.85:
            return None
        authors = []
        for a in it.get("author", [])[:6]:
            fam = a.get("family", "")
            giv = a.get("given", "")
            if fam:
                authors.append(f"{fam}, {giv[:1]}." if giv else fam)
        year = ""
        for k in ("published-print", "published-online", "issued"):
            parts = (it.get(k) or {}).get("date-parts", [[None]])
            if parts and parts[0] and parts[0][0]:
                year = str(parts[0][0])
                break
        return {
            "doi": it.get("DOI", ""),
            "title": (it.get("title") or [""])[0],
            "authors": authors,
            "year": year,
            "venue": (it.get("container-title") or [""])[0],
        }
    except Exception:
        return None


def lowercase_first(s: str) -> str:
    return s[0].lower() + s[1:] if s and s[0].isupper() and not s[:2].isupper() else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=124)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    corpus = {}
    for line in open(SCIFACT / "corpus.jsonl"):
        d = json.loads(line)
        corpus[d["doc_id"]] = d

    support = []
    for line in open(SCIFACT / "claims_dev.jsonl"):
        d = json.loads(line)
        for doc_id, evs in (d.get("evidence") or {}).items():
            if any(e["label"] == "SUPPORT" for e in evs):
                support.append((d["id"], d["claim"], int(doc_id)))
                break

    print(f"{len(support)} SUPPORT claims in SciFact dev")
    session = requests.Session()
    cases, skipped = [], 0
    for i, (cid, claim, doc_id) in enumerate(support):
        if len(cases) >= args.limit:
            break
        doc = corpus.get(doc_id)
        if not doc:
            skipped += 1
            continue
        meta = resolve_crossref(doc["title"], session)
        time.sleep(0.15)
        if not meta or not meta["authors"] or not meta["year"]:
            skipped += 1
            continue
        surname = meta["authors"][0].split(",")[0]
        tpl = TEMPLATES[len(cases) % len(TEMPLATES)]
        sentence = tpl.format(author=surname, year=meta["year"],
                              claim=lowercase_first(claim.rstrip(".")))
        cases.append({
            "id": f"control_{cid}",
            "sub_pattern": "correct_support",
            "target_layer": "control",
            "citing_sentence": sentence,
            "citation_key": "1",
            "cited_paper": meta,
            "cited_abstract": " ".join(doc["abstract"]),
            "scifact_claim_id": cid,
            "scifact_doc_id": doc_id,
            "expected_intent": "supporting",
            "expected_misrepresentation": False,
            "expected_nli_label": "entailment",
            "ground_truth_risk": "low",
        })
        if (i + 1) % 20 == 0:
            print(f"  {i+1} processed, {len(cases)} kept, {skipped} skipped")

    out = {
        "_description": "Matched normal-citation controls (correct SUPPORT "
                        "citations from SciFact dev) for L1/L2 FPR evaluation.",
        "_created": datetime.now().strftime("%Y-%m-%d"),
        "_notes": [
            "SciFact dev was used for L2 model selection; train claims never used.",
            "Metadata resolved via Crossref title search, similarity >= 0.85.",
            "Citing templates rotate over 5 neutral supporting phrasings.",
        ],
        "controls": cases,
    }
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"kept {len(cases)}, skipped {skipped} -> {args.out}")


if __name__ == "__main__":
    main()
