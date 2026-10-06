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
