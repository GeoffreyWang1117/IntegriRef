# What IntegriRef can actually claim, and the one thing it should

Written 2026-10-11, after an adversarial audit that removed most of the candidate
claims. Two independent paths — reading the paper's own Limitations, and a second
agent auditing the artifacts — converged on the same place, which is why this is a
narrowing rather than a retreat.

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
extensively and its free-text mode emits "Year Mismatch: you wrote X, actual is
Y" — the capability is there. But `resolveByDOI` computes title similarity, sets
confidence and populates `issues` with **no year comparison at all**, and
`_checkWithFallback` returns immediately once a DOI resolves, bypassing the
downstream validation that would have compared years. So a reference supplied with
a resolvable DOI never has its year checked — which is exactly the population an
ID-resolving chimera occupies. Verified by reading
[`SearchService.ts`](https://github.com/zabbonat/References-Validation/blob/main/src/services/SearchService.ts),
twice, independently.

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

## What the paper has to be

Not a methods paper. An **audited benchmark of valid-identifier chimeras plus a
system evaluation**, with the methodological observation above as its finding.
That shape fits IPM, and it is what the evidence supports.

## What is still missing before it can be written

1. **Real preprint-to-publication pairs as hard negatives.** This is the most
   important gap. The ±2-year tolerance exists precisely for legitimate
   preprint/publication date drift, so an FPR measured without such pairs does not
   test the policy's actual cost. `idchim_*` currently reports FPR 0/60 (year),
   1/60 (author), 0/60 (cross-domain) against *synthetic* controls only.
2. **A true offline execution of the independent tool**, not a source-path replay.
   Frozen Crossref fixtures plus a dependency-injected `fetch` would do it; the
   present result is a deterministic reading of its published code path, and the
   paper must say so in those words.
3. **More than one independent tool.** One counterexample motivates a narrow
   statement and cannot support prevalence.
4. **NatChim's sampling frame.** Irrecoverable: the mining script overwrote its own
   log (see `../benchmarks/data/NATCHIM_PROVENANCE.md`). Either re-mine with the
   now-timestamped script, or keep reporting the split without a frame.
5. **Separating year semantics.** Publication year, online-first year, issue year
   and arXiv version year are different things, and a numeric ±2 rule conflates
   them. The false-alarm cost of that conflation has not been measured.

## Honest summary

The system works and the benchmark is real. The novelty is small, specific, and
about auditing rather than detection. A paper that says that will survive review;
a paper that repeats the five-layer framing will not, and should not.
