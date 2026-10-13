# Which result file backs which reported number

Every headline number in the paper is reproduced by one of these files. Values
below were re-checked against the files in this directory, not copied from the
manuscript.

| Reported | File | Where in the file |
|---|---|---|
| Year-only chimeras: 0/60 → **60/60 at CRITICAL**, FPR **0/60** | `yearfix_revalidate_20260804_163734.json` | `A_year_only.metrics` — `n=120` (60 chimeras + 60 matched controls), `recall_critical 60/60`, `fpr_high 0/60`, `chimera_detected` fires on all 60 |
| Real-control FPR under the year fix: **4.0%** (rv-1926), **3.7%** (ret-6391) | `yearfix_revalidate_20260804_163734.json` | control sections |
| NatChim: **33/33** ≥HIGH, **32** CRITICAL, **0/60** FPR, year cases **18/18** | `natchim_eval_20260804_183008.json` | `summary` — note `recall_by_reason` sums to 34 because the one case that is both year- and author-wrong counts under both |
| L1 trained detector on matched pool: **P 0.91 / R 1.00 / F1 0.95 / FPR 1.2%** | `l1_matched_prf_20260713_134305.json` | `matched_prf` — `tp 10, fp 1, fn 0, tn 84`. The table's `N=95` is the evaluation pool: 10 contrast positives + 85 matched controls. The 1.2% FPR denominator is the 85 negatives (1/85 = 1.18%) |
| L1 heuristic on matched controls: **0/85** contrast false positives | `matched_controls_20260713_134318.json` | `metrics.l1_contrast_fire_fpr` |
| L2 over-firing on correct citations | `matched_controls_20260713_134318.json` | `metrics.l2_signal_fire_fpr` — 82/85 |
| ACL-ARC test F1 **0.458**, borderline contrast 10/10 at 0 FP | `l1_contrast_model_20260713_133929.json` | `acl_arc_test`, `borderline_l1` |
| Borderline-L1-v2 (289) / L2-v2 (209) | `borderline_expanded_20260804_170536.json` | rebuild the splits with `benchmarks/exp_borderline_expanded.py` |
| Table 5 empirical likelihood ratios | `fusion_20260318_102715.json` | per-case tiers; LRs recomputed offline over the 8,817-case pool with Laplace 0.5 |
| L4 tier flips | `l4_tierflips_20260713_132415.json` | |
| L3 on non-biomedical domains | `l3_nonbio_20260804_172432.json` | input split `../data/borderline_l3_nonbio.jsonl` |

## Superseded runs kept on purpose

`l1_matched_prf_20260713_134139.json` is **not** the reported result. It ran 86
seconds before `…134305.json`, with the same detector name and threshold, and
reports FPR 0.2235 (19 false positives) instead of 0.0118 (1). The difference is
the SciBERT 3-class bug described in the paper's L1 discussion: the earlier run
read a contrast score off a 3-class head, the later one uses the dedicated binary
detector. The losing run is kept so the correction is visible in the record
rather than quietly absent — if only the better number shipped, there would be no
way to tell selection from repair.

The three `borderline_l1_2026 05 24*` files and `borderline_l2_20260524*` are the
retired 40-case hand-built splits that Borderline-L1/L2-v2 replace. They are not
used for any reported number.

## Reproducing

Scripts live one directory up. Experiments that query live registries will not
reproduce byte-identically, because Crossref, OpenAlex, S2 and PubMed records
change; tier counts are stable, individual posteriors drift. Experiments over
fixed splits (`exp_yearfix_revalidate.py`, `exp_natchim_eval.py`) are
deterministic given the same registry responses.

## Scale runs (added 2026-10-11)

An audit by a second agent found that every headline scale number was
**unverifiable from this repository**. The outputs were not missing -- they were
sitting untracked in `paper/gem2026/bench_results/`, which is not where anyone
auditing the benchmark would look. They are now published under
`benchmarks/results/scale/` and every number below was recomputed from them.

| Reported | File | Recomputation |
|---|---|---|
| rv-1926: hallucination recall **99.9%** | `scale/bench_reference_verification_20260524_215031.json` | `metrics.halluc_recall_elev` = 1214/1215. The ≥HIGH operating point is 1211/1215 = 99.7%, also in the file |
| rv-1926: chimera **100%**, retraction **100%** | same | 339/339 and 23/23 at ≥ELEVATED |
| rv-1926: FPR **4.6%**, accuracy **99.1%** | same | 16/349 and 1909/1926 |
| ret-6391: retraction recall **98.3%**, 5,393/5,488 | `scale/bench_retracted_papers_20260524_202842.json` | `metrics.retract_recall_elev` |
| ret-6391: accuracy **97.9%** | same | 6258/6391 |
| ret-6391: FPR **4.2%**, "38 FP = 18+20" | same | 38 of 903 `real_control` rows reach ≥ELEVATED; 38/903 = 4.2%. The 18 and 20 are the `chimera_detected` and `no_id_match` fire counts on those controls, not two FP subsets — the audit could not tell from the paper alone |
| Crossref-only ablation | `scale/*_crossref_only_*.json` | same shape, single registry |

Each row of `results[]` carries `posterior`, `signals_fired`, `actual_risk`,
`actual_found`, `actual_retracted` and latency, so the metrics can be recomputed
from the per-case data rather than read off a summary.

## Which file holds the authoritative golden-58 labels

**`benchmarks/data/golden_test_set.json`** -- 28 retracted, 10 real, 15
hallucinated, 5 chimera, matching Table 1.

`fusion_20260318_102715.json` and `comparison_20260318_100615.json` carry
**23/18/14/3** in their `category` fields. That is the *retired reclassification*
the paper describes at `main.tex:162`: "an earlier reclassification of 8 cases as
``real'' inflated our recall and is no longer reported". The delta reconciles
exactly -- real −8, retracted +5, hallucinated +1, chimera +2 -- and the paper
reports metrics under the original labels, recomputed offline from those files'
per-case tiers.

An audit read the 23/18/14/3 composition as contradicting Table 1. It does not,
but nothing in this directory said so, which is the same failure in a different
place: the data was right and the signposting was missing.

## Baselines and L3, published 2026-10-11

Found by the same audit, in the same place as the scale runs: untracked under
`paper/gem2026/bench_results/`.

| Reported | File | Recomputation |
|---|---|---|
| L3 clean bibliographies: 19 bibs, 114 refs, FPR **0.9% → 73.7%**, 8,381 events, **11/19** critical | `scale/bench_borderline_l3_clean_20260526_132740.json` | `metrics` carries all of them: `n_clean_bibs 19`, `ref_level_fpr_by_config` `1/114 = 0.9%` and `84/114 = 73.7%`, `bib_fpr_crit_by_config` `11/19 = 57.9%`, `anomaly_type_counts_clean.reciprocal_citation 8381` |
| Table 1 LLM rows: **0–95%** recall, **0–30%** FPR | `baselines/llm_baseline_*.json` (9 models) | per-case `ground_truth` / `prediction`. Recomputing the 20 hallucinated+chimera positives against the real negatives reproduces that range |

**One caveat on the baseline files, stated because it would otherwise look like a
discrepancy.** Four of the nine (claude, gemini_pro, gemini_flash, gpt4o) score
against **15** real negatives rather than the 10 in Table 1's caption, and encode
`prediction` differently from the five newer runs. So the published *range*
reproduces, but an exact per-model row needs each file's own encoding. The
CheckIfExist row has no published file at all.

## Still not published

These remain unverifiable from the repository, and saying so is better than
leaving a reader to discover it: the **CheckIfExist** row of Table 1 and the
Clopper-Pearson inputs for every row; the fold assignment and feature matrix
behind **AUC .989 vs .992** and the 0-FPR recall .03 → .77; the **500-reference
local microbenchmark** logs behind the throughput figures; and the manifest of the
**845 bibliographies** walked for NatChim — see
`../data/NATCHIM_PROVENANCE.md`, where the only published mining metadata states a
different scan size for a different run.

Resolved since first writing this list: the scale runs, the L3 clean-bibliography
run and the LLM baseline verdicts, all above. The calibration figures are
recomputable from the scale per-case posteriors.

## deepcite retrieval metrics (added 2026-10-13)

`deepcite_citint_{dev,test}_20261013_125450.json`. Read the `discriminative`
block, not `all`: half the cited documents have 5 or fewer sentences, where a
top-5 retrieval cannot miss.

| Reported | Where in the file |
|---|---|
| hit@5 dev **0.8765** (149/170), test **0.8378** (284/339) | `retrieval.discriminative.hit@5` |
| evidence recall@5 micro **0.7068 / 0.6984** | same block |
| evidence precision@5 micro **0.3001 / 0.3015** | same block |
| precision ceiling the gold labels allow **0.3900 / 0.4041** | `…evidence_precision@5_ceiling` |
| MRR@5 **0.6961 / 0.6443**, nDCG@5 **0.6689 / 0.6064** | same block |
| pairs returning nothing **4/170, 7/339** (full set: 28/361, 59/770) | `pairs_with_no_result`, `coverage` |

**Precision has to be read against the ceiling in the row below it.** Five slots
and a median of one or two gold sentences cap precision@5 at ~0.39-0.40, so 0.30
is 77% of what is attainable. Treating it as a weak ranker would be a
misreading; the headroom is at k=1 (precision 0.5904 against a ceiling of 1.0),
which the top-5 figure hides. See `../../deepcite/eval/PLAN.md`, final section.

What was published as `recall@5` (0.806 / 0.726 in earlier files) is **hit@5** --
the share of pairs with at least one gold sentence retrieved, not recall over
gold sentences. The old key is kept as `recall@5_deprecated_alias_of_hit` so the
earlier `_ab0e1801`, `_minunit4` and `_r3` files stay comparable. The number never
changed; the name was wrong.

These files are timestamped rather than labelled with the knobs that moved
(`_minunit4`, `_r3`): the knobs are now a `retrieval_config` block inside the
result, because a filename cannot be read back by whatever recomputes the metric.
