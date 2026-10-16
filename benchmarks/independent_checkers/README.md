# Two more reference checkers, run unmodified and offline

`../checkifexist_offline/` establishes that one third-party checker never
compares the year of a reference that arrives with a resolvable DOI. One
counterexample supports a narrow statement and cannot support prevalence. These
two are independent implementations by different authors, and they fail
differently — which is the point.

| | catches year chimeras (n=60, correct label HIGH) | flags legitimate preprint→publication refs (n=483, correct label LOW) |
|---|---:|---:|
| CheckIfExist, DOI route | **0 / 60** | 0 / 483 |
| CheckIfExist, free-text route (±2) | 18 / 18 resolved | hardens to a hard verdict at delta ≥ 3 |
| pvsundar (±2 tolerance) | **0 / 60** | 0 / 483 |
| Binara (exact match, no tolerance) | **60 / 60** | **353 / 483 = 73.1%** |

Nothing is in the middle. Two checkers report nothing; the one that reports
everything reports it on three quarters of legitimate references.

## Setup

Neither checker is vendored. One is MIT-licensed and the other ships no licence
file at all, and the whole point is that they run unmodified.

```bash
git clone https://github.com/pvsundar/bibliography-verification-tool vendor/bibliography-verification-tool
git -C vendor/bibliography-verification-tool checkout 2e060a56ab83824e6fbfa7c82a97df466d111649

git clone https://github.com/Binara-Sachin/doi-reference-validator vendor/doi-reference-validator
git -C vendor/doi-reference-validator checkout b791684815723b334c8143b79053a79c430cbf88
```

`PVSUNDAR_CLONE` / `BINARA_CLONE` are tried first, then `vendor/`, then
`.codex/`; the first path that actually contains the entry point wins, and a
path that does not exist is skipped rather than treated as an error. If none is
found the runner prints what it tried and exits 2.

Needs `python-docx`, `pandas`, `requests`, `rich`. Run from the repository root:

```bash
python benchmarks/independent_checkers/run_pvsundar.py
python benchmarks/independent_checkers/run_binara.py
```

Only the network entry points are rebound — `check_crossref`/`check_pubmed` for
the first, `requests.get` for the second. A socket guard blocks every non-local
socket and is **self-tested against a live URL before it is trusted**, so the
reported zero is measured and not asserted. Both runs report 0 attempts.

Fixtures: the 483 real pairs from `../data/preprint_hard_negatives_*.jsonl`
served from the frozen real Crossref responses in `../crossref_dates/cache/`,
and the 60 year-only chimeras from `../data/idchim_year_only.jsonl`, whose
registered records are a deterministic proxy built from their paired control
rows rather than live responses.

## pvsundar — the check runs and its result is thrown away

This one is a released, citable tool: MIT, ORCID, archived at
[10.5281/zenodo.17622390](https://doi.org/10.5281/zenodo.17622390).

Its DOI route *does* reach a year comparison — Codex's static review of that was
right, and exact to the line. `check_crossref` (`:260`) has one call site
(`:416`), so the DOI branch and the title-search branch return into the same
post-fetch block, and the year is compared at `:465–482` against
`ALLOW_YEAR_DIFFERENCE = 2` (`:59`), appending `YEAR_MISMATCH_{n}yrs` at `:480`.

It appends it to a local list that is then discarded. `result['Issues_Detected']`
is initialised to `[]` at `:390`; at `:513` the falsy check rebinds
`issues = []` and at `:516` the other branch rebinds it from the result dict —
**both branches drop the list that held the year label** — and `:538` writes the
rebound list out. A year mismatch costs 25 points of match score and nothing
else.

| delta | n | VERIFIED | NEEDS_REVIEW | median score | carries a YEAR_MISMATCH label |
|---|---:|---:|---:|---:|---:|
| 1 | 100 | 95 | 5 | 90 | 0 |
| 2 | 100 | 90 | 10 | 90 | 0 |
| 3 | 100 | 84 | 16 | 75 | 0 |
| 4 | 97 | 87 | 10 | 75 | 0 |
| 5 | 86 | 75 | 11 | 75 | 0 |
| **chimeras (delta 7)** | **60** | **57** | 3 | 75 | **0** |

Not one `NEEDS_REVIEW` above is a year detection: they are
`LOW_MATCH_CONFIDENCE` (46), `NOT_FOUND_IN_DATABASES` (5, the unresolvable DOIs)
and `TITLE_NOT_EXTRACTED` (4, our APA reconstruction failing, a harness
artefact). 57 of the 60 chimeras come back `VERIFIED` with
`Issues_Detected = None`.

**The positive control, because a zero that cannot fail is not evidence.**
`NO_DOI_FOUND` is appended at `:530`, *after* the rebind, and it appears in the
output; the year label is appended at `:480`, *before* it, and it does not.

| case | printed | registered | score | status | `Issues_Detected` |
|---|---:|---:|---:|---|---|
| year off by 7 | 2015 | 2008 | 75 | VERIFIED | `None` |
| no DOI supplied | 2015 | 2015 | 100 | VERIFIED | `NO_DOI_FOUND` |
| DOI unresolvable | 2015 | — | 0 | NEEDS_REVIEW | `NOT_FOUND_IN_DATABASES` |
| wrong title and author | 2015 | 2015 | 25 | NEEDS_REVIEW | `LOW_MATCH_CONFIDENCE` |
| in press, year off by 7 | 2099 | 2008 | 75 | VERIFIED | `In press or future publication` |

The last row exercises the other branch of `:513`: `Issues_Detected` is already
non-empty, and the year label is still gone.

**Its own report asks the reader to triage on a label it can never emit.** The
summary log writes `If YEAR_MISMATCH ≤ 2 years AND title similarity > 0.75:
Likely OK` (`:644`), and lists per-reference detail only for rows whose status is
`NEEDS_REVIEW` (`:627`), printing `None - all references verified!` otherwise. A
bibliography whose only defect is a wrong year produces exactly that line.

This is the same failure as CheckIfExist's arriving by a different road. There
the check is never reached; here it runs, and its answer is dropped on the floor.

## Binara — compares the year, with no tolerance at all

Advertised as letting you "instantly spot incorrect years", and it does: `:77`
tests whether the Crossref `issued` year appears in the reference text, `:81`
does the same for the first author's surname, and `:95–101` requires all of them
for a clean row. It catches **60 of 60** chimeras.

The cost is on the other side. Run as written it flags only 67/483 legitimate
pairs — but that number is an artefact of the fixture, not a property of the
tool: **all 483 journal-ref venue strings contain the registered year**, so the
substring test passes on text that has nothing to do with the printed year. With
those digits masked (`mask_years_outside_printed_slot`, a control, not the
realistic input), the rate is:

| delta | n | year flagged |
|---|---:|---:|
| 1 | 100 | 70 |
| 2 | 100 | 72 |
| 3 | 100 | 79 |
| 4 | 97 | 69 |
| 5 | 86 | 63 |
| **total** | **483** | **353 = 73.1%** |

**Flat across delta**, as a rule with no tolerance must be: it is not measuring
how far apart the years are, only that they differ. The ~27% it lets through are
the records where Crossref `issued` already carries the preprint-era year, which
is the same `issued` behaviour measured in `../crossref_dates/`.

That it flags the right 60/60 and also 353 legitimate references is not a
checker a user would keep on.

## Reading these numbers

- **The hard-negative sample is balanced across delta 1–5 by construction**, so
  its delta distribution is not a natural prevalence. Report per delta, never
  pooled.
- **The reference strings are reconstructed APA lines**, built from each record's
  stored author/title/venue/DOI. Real bibliographies vary; 4 rows defeat
  pvsundar's title extraction and 53 of 483 trip its book heuristic (a venue
  containing "Press", "Springer" and so on), which changes its title thresholds
  but not its year logic.
- **PubMed is deliberately unanswered** in the pvsundar run. It is consulted only
  for journal articles and only affects rows whose DOI did not resolve, i.e. the
  5 `NOT_FOUND_IN_DATABASES`.
- **The chimera registered records are a deterministic proxy**, built from the
  paired real control rows. The 483 real pairs are served from real, frozen
  Crossref responses; the 60 chimeras are not.
- **Static review is not execution.** The earlier static pass on these two
  candidates got the first one exactly right and the second one backwards — it
  reported that Binara does not compare the year, when every revision of that
  file has, and cited a function and line numbers that exist in no revision of
  it. That is why these are run.
