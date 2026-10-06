"""Selection precision/recall on hand-labelled citing sentences.

Citation-Integrity (citation_integrity.py) cannot measure selection PRECISION: its
"no evidence" claims are claims whose support is missing from the cited ABSTRACT,
which is what deepcite is for, not sentences that make no checkable claim. This
harness takes sentences labelled "checkable" (the sentence attributes something
specific to the cited work -- a finding, number, protocol, mechanism, definition --
that reading that work could confirm or refute) or "not" (background, pointer,
the citing paper's own usage), and reports how the current patterns separate them.

The label files stay OUTSIDE the repository when the sentences come from unpublished
papers; only the aggregate numbers and the patterns hash are recorded.

Usage:
    python -m deepcite.eval.labeled_sentences --items I.json --labels L.json [--out R.json]
items:  [{"id": ..., "raw": "<citing sentence, LaTeX>", ...}, ...]
labels: {"<id>": "checkable" | "not", ...}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import select as S


def evaluate(items: list[dict], labels: dict[str, str]) -> dict:
    pos = {k for k, v in labels.items() if v == "checkable"}
    rows = [i for i in items if i["id"] in labels]
    fired = {i["id"]: S.select(i["raw"]) for i in rows}
    tp = sum(1 for i in rows if i["id"] in pos and fired[i["id"]])
    fp = sum(1 for i in rows if i["id"] not in pos and fired[i["id"]])
    n_pos = sum(1 for i in rows if i["id"] in pos)
    n_neg = len(rows) - n_pos
    by_pattern: dict[str, list[int]] = {}
    for i in rows:
        s = fired[i["id"]]
        if s:
            b = by_pattern.setdefault(s.pattern_id.split(":")[0], [0, 0])
            b[0 if i["id"] in pos else 1] += 1
    return {
        "selection_regex_sha256": S.patterns_sha256(),
        "tool_version": __import__("deepcite").__version__,
        "sentences": len(rows), "checkable": n_pos, "not_checkable": n_neg,
        "selected": tp + fp,
        "recall": round(tp / n_pos, 4) if n_pos else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "false_fire_rate": round(fp / n_neg, 4) if n_neg else None,
        "by_pattern_true_false": {k: v for k, v in sorted(by_pattern.items())},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m deepcite.eval.labeled_sentences")
    ap.add_argument("--items", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = evaluate(json.loads(Path(a.items).read_text(encoding="utf-8")),
                   json.loads(Path(a.labels).read_text(encoding="utf-8")))
    text = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
