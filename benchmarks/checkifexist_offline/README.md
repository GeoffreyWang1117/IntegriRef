# Offline execution of CheckIfExist

A third-party reference checker, run unmodified against frozen fixtures, to
separate **what a checker can do** from **what it does on the path a given
reference actually takes**.

The result this produces: on the DOI path every one of 60 year-only chimeras is
accepted at confidence 100 with no issue raised; on the free-text path not one
is silently accepted. Same tool, same inputs, same frozen responses.

## Setup

The upstream checker is **not vendored here** — it is third-party code under its
own licence, and the point of this harness is that it runs unmodified.

```bash
git clone https://github.com/zabbonat/References-Validation vendor/References-Validation
git -C vendor/References-Validation checkout e4605651751f53579e57a0e14159c2f93b3f5e31
```

`CHECKIFEXIST_CLONE=/path/to/clone` is tried first, then `vendor/`, then
`.codex/`; the first one that actually contains `src/services/SearchService.ts`
wins, and a path that does not exist is skipped rather than treated as an error.
If none is found the runner prints the list it tried and exits 2.

Requires Node with `--experimental-strip-types` (developed on Node 26). `ts_extension_loader.mjs` exists only so Node can resolve the upstream
TypeScript's extensionless local imports and its JSON import — it changes no
upstream behaviour.

## Running

Both runners are invoked from the repository root and print JSON to stdout:

```bash
L=benchmarks/checkifexist_offline/ts_extension_loader.mjs
node --no-warnings --experimental-strip-types --experimental-loader ./$L \
     benchmarks/checkifexist_offline/run_doi_path.mjs
node --no-warnings --experimental-strip-types --experimental-loader ./$L \
     benchmarks/checkifexist_offline/run_freetext_control.mjs
```

Fixtures: `benchmarks/data/idchim_year_only.jsonl` (60 year-only chimeras plus
their 60 paired real controls) and the newest
`benchmarks/data/preprint_hard_negatives_*.jsonl` (483 real
preprint-to-publication pairs; see that directory's
`PREPRINT_HARD_NEGATIVES_PROVENANCE.md`).

`globalThis.fetch` is replaced by a shim that serves `api.crossref.org` from
those fixtures and **records every other route it is asked for**. Both runs
report zero other routes, which is how "offline" is established rather than
asserted.

## `run_doi_path.mjs` — what the DOI route does

603 cases, 603 `/works/{doi}` lookups, zero other routes.

| Cohort | N | `exists=true` | year issue | confidence 100 |
|---|---:|---:|---:|---:|
| Year-only chimeras | 60 | 60 | **0** | 60 |
| Paired real controls | 60 | 60 | 0 | 60 |
| Real preprint→publication pairs | 483 | 483 | **0** | 483 |

For every chimera the registered year was exactly 7 years from the printed one
and nothing was raised.

Why, in the upstream source at the pinned revision: `resolveByDOI`
(`src/services/SearchService.ts:1062`) computes `resultYear` but its only
`issues.push` calls are a retraction flag and two title-similarity notes — there
is no year comparison on this path. `_checkWithFallback` returns as soon as the
DOI resolves (`:1199–1201`), so the year-aware matching further down is never
reached. A reference supplied with a resolvable DOI never has its year checked,
which is exactly the population an ID-resolving chimera occupies.

## `run_freetext_control.mjs` — the positive control

**A zero that cannot fail is not evidence.** The table above is also what a
broken harness prints. This runner sends the *same* 603 fixtures down
`checkReference`, the path where upstream's year comparison lives, so the DOI
zero can be read as a routing consequence rather than an absent capability.

| Cohort | resolved | verdict |
|---|---:|---|
| Year-only chimeras | 18/60 | **18/18 `Year Mismatch`**; the other 42 rejected outright — 0 silently accepted |
| Paired real controls | 46/60 | **0 false alarms** |
| Real pairs, delta 1 | 93 | soft note, "likely online-first vs print date" |
| Real pairs, delta 2 | 95 | soft note |
| Real pairs, delta 3 | 95 | hard `Year Mismatch` |
| Real pairs, delta 4 | 94 | hard `Year Mismatch` |
| Real pairs, delta 5 | 83 | hard `Year Mismatch` |

So the ±2-year boundary is exactly where the verdict hardens, on records whose
correct label is LOW. That is a measured false-alarm cost for a ±2 tolerance
rule — on a third party's rule, not our own.

It also classifies issues against **all eight** of upstream's year-message
templates. The first version of this measurement matched `/year.*mismatch/i`,
which hits only two of the eight: it was dormant on the DOI path, which emits no
year message at all, and would have silently reported zero for delta 1–2 above,
where 188 notices actually fire. `naive_substring_regex_would_flag` is kept in
the output so the gap stays visible.

## Reading these numbers

- **Denominator is `resolved`, not N.** The free-text path rejects matches on its
  own terms; counting those as "not flagged" would understate it. Of 603 queries
  only 3 miss the fixture's title key (LaTeX-heavy titles that upstream's
  `cleanQuery` rewrites) — the rest of the non-resolutions are upstream's own
  decisions.
- **The hard-negative sample is balanced across delta 1–5 by construction**, so
  its delta distribution is not a natural prevalence. Report per delta, never
  pooled.
- **These are deterministic fixtures built from our own records, not live
  Crossref.** This establishes the routing behaviour of the published
  implementation, not its behaviour across the full variety of real registry
  responses.
