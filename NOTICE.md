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
483 preprint-to-publication pairs in
`benchmarks/data/preprint_hard_negatives_*.jsonl`.
Public snapshot; records are reproduced only as the fields listed in
[`benchmarks/data/PREPRINT_HARD_NEGATIVES_PROVENANCE.md`](benchmarks/data/PREPRINT_HARD_NEGATIVES_PROVENANCE.md).

**Crossref REST API responses** — `benchmarks/crossref_dates/cache/` holds 483
frozen `/works/{doi}` responses so the date-field analysis replays without
network access. Crossref distributes its metadata under **CC0**. The cache was
collected serially at no more than one request per second with the User-Agent
`IntegriRef-research/1.0 (+https://github.com/GeoffreyWang1117/IntegriRef)` and
no contact email, i.e. deliberately not the polite pool. Four DOIs returned 404
and are stored as explicit `_fetch_error` envelopes, never as absent fields.

**References-Validation / CheckIfExist** (`zabbonat/References-Validation`) — a
third-party reference checker. It is **not vendored here** — it carries its own
upstream license and the harness's whole point is that it runs unmodified, so
`benchmarks/checkifexist_offline/` clones it at a pinned revision instead. See
that directory's README for the clone command.

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
