# NatChim provenance, and one discrepancy I cannot close

The paper evaluates **NatChim = `natural_chimeras_v1.jsonl`** (33 chimeras + 60
matched controls = 93 rows). `natchim_eval_20260804_183008.json` names that file
and reports 33/33 at ≥HIGH, 32 CRITICAL, 0/60 FPR, 18/18 on year cases.

## Two mining runs, and which is which

| File | Rows | What it is |
|---|---|---|
| `natural_chimeras.jsonl` | 120 (60 chimeras + 60 controls) | the **unfiltered** run |
| `natural_chimeras_v1.jsonl` | 93 (33 + 60) | **what the paper uses**; a strict subset of the above |
| `natural_chimeras_full120_meta.json` | — | metadata for the **120-row run**, not for NatChim |

Every one of the 93 ids appears in the 120, so v1 is the 120-row run after a
stricter filter. The paper's Limitations describe that filter's effect: "the split
stays small because precision demanded discarding far more candidates than it
kept."

The metadata file was published in `4d759dc` under the name
`natural_chimeras_meta.json`, which read as NatChim's provenance. It is not; it
describes the superset. Renamed so that it cannot be mistaken for the evaluated
split's lineage again.

## The discrepancy

`natural_chimeras_full120_meta.json` records **1,341 citing works scanned**,
60 chimeras, breakdown 19 year-only / 40 author-only / 1 both.

The paper says NatChim came from **845 published bibliographies**, yielding 33
with breakdown **17 year-only / 15 author-only / 1 both**.

The 33-row breakdown is internally consistent (17+15+1 = 33, and 17+1 = 18 year
cases, matching the evaluation's 18/18). But **the v1 run's own log is not in the
repository**, so the 845 figure cannot be checked against anything here, and the
only published mining metadata states a different scan size for a different run.

I am recording this rather than resolving it. Either the v1 log turns up and 845
is confirmed, or the paper's provenance sentence has to be rewritten to describe
what can actually be shown. Until then, treat 845 as unverified. The 33 cases and
their evaluation are reproducible; the sampling frame they were drawn from is not.

## Why this matters beyond bookkeeping

An exploratory 33-case set cannot support a prevalence claim, and the cases were
selected *for* the phenomenon, so no confidence interval computed on them repairs
that selection. The 60 matched controls bound false alarms for this selection
procedure only. Anyone citing NatChim should read it as a demonstration that the
failure class occurs in real bibliographies, not as an estimate of how often.
