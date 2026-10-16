# What IntegriRef can actually claim, and the one thing it should

Written 2026-10-11, after an adversarial audit that removed most of the candidate
claims. Two independent paths — reading the paper's own Limitations, and a second
agent auditing the artifacts — converged on the same place, which is why this is a
narrowing rather than a retreat.

Updated 2026-10-16: prerequisites 1 and 2 are met and 5 is half met. The
CheckIfExist finding is now an executed result rather than a reading of its
source, and the ±2-year policy's false-alarm cost has a number. See "What is
still missing" for what that leaves.

## Claims that do not survive

**"Existing tools only check whether a DOI resolves."** False. Dunford et al.
(2024) document comparing a resolved DOI's registered metadata against the printed
reference to surface chimeras, and CheckIfExist cascades three registries. The
abstract has been corrected and Dunford is now cited; it had been missing
entirely, which in a paper about citation integrity is the most expensive error
available.

**"A five-layer framework."** The paper's own Limitations dismantle it: L1 is
triage-only at 11% FPR on real contexts, L2 fires on 34–66% of *correct*
citations, and L3 alone drives clean-bibliography FPR from 0.9% to 73.7%. What
works is L0 multi-registry existence checking fused by L4. Four reviewers said the
same thing; the layer framing should stop being a contribution.

**"We discovered that fields must be checked after an identifier resolves."**
Dunford says it first.

**"Year-only chimeras are a blind spot in citation-integrity systems."** Too
strong, and the evidence cuts both ways — see below.

## The claim that does survive

> A field-level check being *correct* does not mean it *runs*, or *survives*, for
> the records that need it. Two independent codebases skip the same comparison by
> two different mechanisms, and unit-level testing of the check cannot see either.

Evidence, from two systems with different owners:

**CheckIfExist (third party).** Its `checkReference` path compares years
extensively and emits a graded verdict — "Year Mismatch: expected X, found Y"
when metadata is supplied, "...you wrote X, actual is Y" when it is parsed from
free text, and softer notes inside ±2 years — so the capability is there. But `resolveByDOI` computes title similarity, sets
confidence and populates `issues` with **no year comparison at all**, and
`_checkWithFallback` returns immediately once a DOI resolves, bypassing the
downstream validation that would have compared years. So a reference supplied with
a resolvable DOI never has its year checked — which is exactly the population an
ID-resolving chimera occupies.

**This is now executed, not inferred.** The upstream repository is run unmodified
at revision `e4605651` with an injected `fetch`, against 603 frozen records (60
year-only chimeras, their 60 paired real controls, and the 483 T10 pairs). Every
case is routed through `/works/{doi}`; no other route is ever requested. Sending
the *same* 603 records down the free-text path, where the year comparison lives,
separates routing from capability:

| | DOI path | free-text path |
|---|---|---|
| 60 year-only chimeras | 60/60 accepted, confidence 100, **zero issues** | 18 resolved → **18/18 `Year Mismatch`**; the other 42 rejected outright |
| 60 paired real controls | 60/60 accepted, zero issues | 46 resolved, **0 false alarms** |

So not one chimera is silently accepted on the free-text path, and every one is
silently accepted on the DOI path. Same tool, same input, same frozen responses —
the outcome is decided by which route the reference takes. The code reading
([`SearchService.ts`](https://github.com/zabbonat/References-Validation/blob/main/src/services/SearchService.ts):1062–1145)
explains it: `resolveByDOI` computes `resultYear` but its only `issues.push`
calls are a retraction flag and two title-similarity notes.

The limit to state in the paper, in these words: this is execution against
deterministic fixtures built from our own records, not against live Crossref, so
it establishes the routing behaviour of the published implementation and not its
behaviour on the full variety of real registry responses.

**IntegriRef's own earlier releases (not independent, and the nuance matters).**
bibguard v0.2.0–v0.4.0 raise a year `FAIL` on all 60 cases, then a confirmed
title+author postprocessor downgrades every one to `WARN`. So the discrepancy was
never *omitted* — it surfaced, at a lower severity. Counting any warning as a
detection gives 60/60; counting only hard failures gives 0/60.

**That second finding is why "blind spot" is the wrong word.** In our tool the
verdict was softened; in theirs it was skipped. Neither is an absent capability.
The common structure is that both failures live *downstream of a correct check* —
in a severity rule and in a routing decision — and both are invisible to anyone
testing the comparator in isolation. That is a statement about how to audit such
pipelines, and it is worth a careful paragraph rather than a headline.

### A third instance, found in our own instrument

The same structure appeared once more, in the apparatus built to measure it. The
first offline run classified CheckIfExist's verdicts by matching its `issues`
strings against `/year.*mismatch/i`, and reported zero year issues across all 603
cases. The number was correct, but it was not yet *evidence*: upstream emits
eight distinct year messages and that pattern matches only two of them, so a
broken detector and a genuinely silent code path produce the same zero. The flaw
was dormant on the DOI path, which emits no year message at all — and would have
silently reported zero on the free-text path, where 188 year notices actually
fire.

What makes the DOI-path zero mean something is the positive control: the same
fixtures, the same frozen responses, routed down the path where the comparison
lives, where the detector does fire. A measurement that cannot fail is not a
measurement, and this one could not fail until a case that *should* trip it was
run through it.

This is worth a sentence in the paper rather than a section. It is not a result
about CheckIfExist; it is the same failure the paper is about — a correct check
that does not run on the path that matters — reappearing one level up, in the
code written to study it. That it caught the authors too is the most honest
available argument that the pattern is worth naming.

## What the paper has to be

Not a methods paper. An **audited benchmark of valid-identifier chimeras plus a
system evaluation**, with the methodological observation above as its finding.
That shape fits IPM, and it is what the evidence supports.

## What is still missing before it can be written

1. ~~**Real preprint-to-publication pairs as hard negatives.**~~ **Done.** 483
   real pairs mined from the `gfissore/arxiv-abstracts-2021` snapshot (1,999,486
   records scanned; seed 20261013; stratified at most 20 per delta × category
   cell), with every screening stage and its retention published in
   `benchmarks/data/PREPRINT_HARD_NEGATIVES_PROVENANCE.md`. Printed year is the arXiv-ID year, publication year is
   parsed from `journal-ref`. Verified independently: a separately written parser
   reproduces all 483 ID years and publication years with zero disagreements, and
   the report's 20-row manual audit matches the fixture row for row, in order.
   **The sample is deliberately balanced across delta 1–5, so its delta
   distribution is not a natural prevalence — report per delta, never pooled.**
2. ~~**A true offline execution of the independent tool**, not a source-path
   replay.~~ **Done**, as described above: upstream unmodified at `e4605651`,
   dependency-injected `fetch`, 603 records, zero network routes other than
   `/works/{doi}`. The sentence the paper owes its reader is now about fixtures
   rather than about source reading — see the limit stated above.
3. **More than one independent tool.** Still open, and now the binding
   constraint. One counterexample motivates a narrow statement and cannot support
   prevalence.
4. **NatChim's sampling frame.** Irrecoverable: the mining script overwrote its own
   log (see `../benchmarks/data/NATCHIM_PROVENANCE.md`). Either re-mine with the
   now-timestamped script, or keep reporting the split without a frame.
5. **Separating year semantics.** Half done. Publication year, online-first year,
   issue year and arXiv version year are different things, and a numeric ±2 rule
   conflates them. **The false-alarm cost of that conflation is now measured on a
   third party.** Running the 483 legitimate pairs down CheckIfExist's free-text
   path, the ±2 boundary is exactly where its verdict hardens:

   | delta | resolved | verdict |
   |---:|---:|---|
   | 1 | 93/93 | soft note, "likely online-first vs print date" |
   | 2 | 95/95 | soft note |
   | 3 | 95/95 | hard `Year Mismatch` |
   | 4 | 94/94 | hard `Year Mismatch` |
   | 5 | 83/83 | hard `Year Mismatch` |

   Every record that resolved drew a year notice — 460/460 — and all 272 resolved
   records at delta 3–5 drew the hard one, on records whose correct label is LOW.
   Measuring this on someone else's tolerance rule rather than our own is what
   makes it worth reporting. What remains is the *semantic* half: the fixture's
   publication year comes from `journal-ref`, not from registry date-parts, so it
   cannot yet separate online-first from issue year. T12 supplies cached Crossref
   date-parts; until then `crossref_dates` is null in all 483 rows, and a
   throttled response must be recorded as not fetched, never as a field absent.

## Honest summary

The system works and the benchmark is real. The novelty is small, specific, and
about auditing rather than detection. A paper that says that will survive review;
a paper that repeats the five-layer framing will not, and should not.
