# Licensing and third-party notices

## This repository

**Code** is MIT licensed — see [LICENSE](LICENSE).

**IntegriRef-Bench data** under `benchmarks/data/` is **CC-BY-4.0**, as declared
in [`benchmarks/data/dataset_card.md`](benchmarks/data/dataset_card.md).
The two licenses are deliberately different: the data card is the authority for
the data, and LICENSE is the authority for the code.

## Third-party data this repository does *not* redistribute

Two evaluation splits are **fixed-seed selections over third-party corpora**, not
copies of them. They ship as the script that rebuilds them
(`benchmarks/exp_borderline_expanded.py`), so running it requires obtaining the
upstream data under its own terms:

| Split | Built over | Obtain from |
|---|---|---|
| `Borderline-L1-v2` (289) | ACL-ARC | its own distributors, under its own terms |
| `Borderline-L2-v2` (209) | SciFact dev | its own distributors, under its own terms |

## Third-party corpora used in evaluation

**Citation-Integrity** (Sarol et al., *Bioinformatics* 40(7), 2024; MIT) — 3,063
instances over 100 biomedical papers, used by `deepcite/eval/` as the external
labelled corpus for retrieval and selection measurement. Not redistributed here.

**arXiv metadata snapshot** `gfissore/arxiv-abstracts-2021` — the source of the
483 preprint-to-publication pairs in `.codex/preprint_hard_negatives_*.jsonl`.
Public snapshot; records are reproduced only as the fields listed in
`.codex/REPORT_T10.md`.

**References-Validation / CheckIfExist** (`zabbonat/References-Validation`) — a
third-party reference checker. A clone is kept under `.codex/` for offline
execution at a pinned revision and is **run unmodified**; no upstream source or
decision logic is changed. It carries its own upstream license.

## Dependency licensing worth knowing about

`pymupdf` is **dual-licensed AGPL-3.0 or commercial**. This repository's own code
is not a derivative of it: `adapters/pdf.py` imports it inside a
`try/except ImportError` that returns `[]` when it is absent, so PDF extraction
degrades rather than breaks. But `requirements.txt` currently lists it as a
non-optional line, so a plain `pip install -r requirements.txt` places AGPL code
in the environment. Anyone redistributing a combined work, or offering it over a
network, should consider that before installing the default set.

`bibtexparser` is LGPLv3-or-BSD; the remaining core dependencies are Apache-2.0,
MIT or BSD.
