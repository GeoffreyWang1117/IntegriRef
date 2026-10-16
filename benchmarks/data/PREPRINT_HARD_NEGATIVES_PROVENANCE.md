# Preprint-to-publication hard negatives — provenance

## Artifact and construction

- Current fixture: [`preprint_hard_negatives_20261015_164124.jsonl`](preprint_hard_negatives_20261015_164124.jsonl), 483 JSONL records.
- Builder: [`build_preprint_hard_negatives.py`](../build_preprint_hard_negatives.py); source snapshot: `gfissore/arxiv-abstracts-2021`, downloaded from the public Hugging Face `arxiv-abstracts.jsonl.gz` file to `/tmp/arxiv-abstracts-2021.jsonl.gz` (897 MiB compressed). The gzip was read as a stream; no uncompressed copy was created.
- Random seed: `20261013`. Reservoir samples are stratified by parsed delta and mapped category. There is no hard-coded output path; the timestamped artifact name is made at runtime.
- Label: `expected_risk=LOW`. Printed title/authors/venue/DOI are taken from the snapshot; printed year is the arXiv-ID year. Registered title/authors/venue/DOI and year are the same snapshot record, with publication year parsed from `journal-ref`.
- The source contains no version submission dates and no raw Crossref date-parts. `crossref_dates` is therefore explicitly null in all four fields in this fixture. The ID year has month-level precision at best; only the year is used. The publication year is a parsed journal-ref year, not an independently verified Crossref year.

## Stream scan and sequential screening

The file contained 1,999,486 JSONL records. Counts below are sequential: each later stage is evaluated only on records surviving the earlier stages. Percentages use the immediately preceding stage as denominator.

| Stage | Kept | Removed at this stage | Rule |
|---|---:|---:|---|
| JSON decoded | 1,999,486 | 0 | Invalid JSON counted separately; none encountered |
| arXiv ID year parsed | 1,999,486 | 0 | New-style `YYMM.NNNN[N]` and legacy `archive/YYMMNNN` IDs; two-digit year pivot 91–99 → 1991–1999, 00–90 → 2000–2090 |
| Nonempty `journal-ref` | 748,731 | 1,250,755 | Missing journal-ref excluded |
| Exactly one distinct four-digit year in journal-ref | 735,193 | 13,538 | 3,375 with no year; 10,163 with multiple distinct years excluded; no guessing |
| Publication year ≥ arXiv-ID year | 689,155 | 46,038 | Earlier journal-ref year treated as invalid and discarded |
| Delta in 1–5 | 321,124 | 368,031 | Other deltas excluded |
| Exactly one DOI | 284,996 | 36,128 | 34,730 missing DOI; 398 records with multiple DOI strings excluded |
| Mapped primary category | 284,325 | 671 | 10,034 additional records without one of the five requested broad groups were excluded |
| Deterministic stratified sample | 483 | 283,842 eligible but unsampled | Up to 20 per delta × category cell; sparse cells contribute all available records |

The category mapping is explicit in `benchmarks/build_preprint_hard_negatives.py`: physics includes hep/astro-ph/cond-mat/gr-qc/nucl/physics/quant-ph/nlin families; cs includes `cs.*` and `eess.*`; math includes `math.*`, `math-ph`, and `solv-int`; stat includes `stat.*`, `econ.*`, and `q-fin.*`; q-bio includes `q-bio.*`. This broad grouping is for sampling coverage, not a claim that those subfields are interchangeable.

Two implementation defects in the initial builder were corrected before this fixture was finalized. Version removal is now anchored to a terminal `v` plus digits, so `solv-int/9801001` is not truncated at its internal `v`; math-ph and the other listed category families are no longer silently omitted. The corrected scan has zero unparsed IDs; the initial `arxiv_id_unparsed` count of 844 was a misleading counter caused by the unanchored split. The previous 476-row fixture is superseded by this corrected 483-row fixture. The builder also rejects a record containing multiple DOI strings rather than passing an ambiguous DOI to the checker.

## Sample composition

| Delta (publication year − preprint-ID year) | physics | cs | math | stat | q-bio | Total |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 20 | 20 | 20 | 20 | 100 |
| 2 | 20 | 20 | 20 | 20 | 20 | 100 |
| 3 | 20 | 20 | 20 | 20 | 20 | 100 |
| 4 | 20 | 20 | 20 | 20 | 17 | 97 |
| 5 | 20 | 20 | 20 | 20 | 6 | 86 |
| **Total** | **100** | **100** | **100** | **100** | **83** | **483** |

Every delta has at least 20 records, including 86 in delta 5. The missing 3 and 14 q-bio entries in the delta-4 and delta-5 cells reflect only 17 and 6 eligible q-bio records found in the full snapshot after the stated filters. These cells are not padded with other categories.

## Twenty-row manual audit

The following are the first 20 sampled records, with the exact `journal-ref` value carried by the source record and both parsed years. The fixture itself carries the same values for all 483 rows.

| # | arXiv ID | `journal-ref` (source text) | ID year | Parsed publication year |
|---:|---|---|---:|---:|
| 1 | `gr-qc/0409052` | `Int.J.Mod.Phys. D14 (2005) 1413-1429` | 2004 | 2005 |
| 2 | `1508.02729` | `ACS Photonics 3 (5), 875 (2016)` | 2015 | 2016 |
| 3 | `hep-th/0311156` | `Mod.Phys.Lett. A19 (2004) 1701-1710` | 2003 | 2004 |
| 4 | `hep-ph/0511087` | `Phys.Lett. B633 (2006) 497-506` | 2005 | 2006 |
| 5 | `astro-ph/0411130` | `Int.J.Mod.Phys.A20:3147-3150,2005` | 2004 | 2005 |
| 6 | `1311.4773` | `Journal of Optics 16, 015702-7 (2014)` | 2013 | 2014 |
| 7 | `1312.0225` | `Eur. Phys. J. C (2014) 74:2743` | 2013 | 2014 |
| 8 | `1609.01767` | `Phys. Rev. B 95, 125109 (2017)` | 2016 | 2017 |
| 9 | `cond-mat/0306294` | `Phys. Rev. Lett. 92, 137202 (2004)` | 2003 | 2004 |
| 10 | `0808.2575` | `Int.J.Mod.Phys.D17:2367-2372,2009` | 2008 | 2009 |
| 11 | `1108.3028` | `Eur. Phys. Lett. 100, 37001 (2012)` | 2011 | 2012 |
| 12 | `0912.3525` | `Mon.Not.Roy.Astron.Soc.406:1007-1013,2010` | 2009 | 2010 |
| 13 | `hep-ex/0106042` | `Phys.Lett. B524 (2002) 245-251` | 2001 | 2002 |
| 14 | `1811.08555` | `Optics Letters 44, 2224-2227 (2019)` | 2018 | 2019 |
| 15 | `hep-ph/9705222` | `J.Phys.G24:49-65,1998` | 1997 | 1998 |
| 16 | `2008.10851` | `Science of the Total Environment, Volume 789, 2021, 147739` | 2020 | 2021 |
| 17 | `1309.7933` | `Phys. Rev. Lett. 112, 040501 (2014)` | 2013 | 2014 |
| 18 | `1803.06530` | `Quantum Inf. Process. 18, 328 (2019)` | 2018 | 2019 |
| 19 | `0909.3300` | `JCAP 1001:031,2010` | 2009 | 2010 |
| 20 | `1712.01608` | `JHEP 01 (2018) 078` | 2017 | 2018 |

The year parser rejects a journal-ref with zero years or more than one distinct four-digit year; it does not choose the “most plausible” candidate. The selected fixture therefore had no multi-year journal-ref. A separate manual check reported 0 selected rows where the venue contained multiple candidate years, and checked the volume/year-looking IMRN and CJTCS cases against those journals' year-numbered volumes; it found no incorrect selected year. These spot checks support parsing plausibility but do not independently confirm every DOI record against its publisher.

## Can this support an FPR claim?

It supports a measured **sample-specific** false-positive rate for legitimate preprint-to-publication year drift, once a checker is run against the frozen records. It does not by itself justify a population-wide FPR claim: records must have a DOI, one unambiguous journal-ref year, a supported category, and a 1–5 year gap; category coverage is uneven in the tail; the publication year is inferred from arXiv's journal-ref rather than independently verified registry dates; and no submission dates exist in this snapshot. Use the counts by delta, not one pooled rate, and describe the frame and limits alongside any result. T12 adds cached Crossref date-parts where obtainable; an unavailable/limited Crossref response remains unknown, not evidence that a date field is absent.
