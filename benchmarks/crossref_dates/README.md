# Crossref date-field decomposition

## Collection and caching

- Enriched offline fixture: [`preprint_hard_negatives_crossref_20261015_215800.jsonl`](preprint_hard_negatives_crossref_20261015_215800.jsonl), 483 records, built from the corrected T10 fixture and the response cache.
- Cache directory: [`crossref_cache/`](crossref_cache/). Each DOI has one percent-escaped `<doi>.json` file. Successful files contain the raw Crossref JSON response; failed requests contain an explicit `_fetch_error` envelope with status/body, DOI, URL, User-Agent, and attempt count. In the enriched fixture, each requested date field is marked `present`, `field_absent`, `malformed`, or `fetch_failed`; an unsuccessful lookup is never represented as a missing field.
- Fetcher: [`fetch_crossref.py`](fetch_crossref.py). It reads the cache first, performs serial requests with a 1.05-second minimum start interval, sends exactly `IntegriRef-research/1.0 (+https://github.com/GeoffreyWang1117/IntegriRef)` as User-Agent with no email, and retries transport errors/429/5xx with exponential backoff. There were no 429/5xx or transport failures in this run, so the retry branch was not exercised.

| Run | Successful responses | Failed lookups | Cache hits | Network requests | Retries |
|---|---:|---:|---:|---:|---:|
| Initial fetch, 483 distinct DOI | 479 | 4 | 0 | 483 | 0 |
| Cache-only replay | 479 | 4 | 483 | 0 | 0 |

The four failures are Crossref HTTP 404 (`Resource not found`) for `10.1285/i15900932v22n2p53`, `10.20382/jocg.v11i1a25`, `10.20382/jocg.v7i1a6`, and `10.2140/rmj.2021.51.213`. They remain unknown for every date field. A 404 is not interpreted as proof that the DOI or the work is invalid; it only means this request did not obtain a Crossref work record.

## Measurement rule

All 483 citations are labelled legitimate preprint-to-publication pairs (`expected_risk=LOW`). For each observed Crossref date-part year, compare its absolute distance from the printed arXiv-ID year. The audited threshold tolerates differences up to 2 years; a gap greater than 2 is therefore counted as a false positive on this legitimate-pair sample. Rates below are complete-case rates over successful responses with that specific field and a parseable year. Missing fields and failed lookups stay outside the denominator and are counted separately.

The `journal-ref` publication year is available for all 483 and gives 283/483 flagged (**58.6%**): delta 1–2: 0/200; delta 3: 100/100; delta 4: 97/97; delta 5: 86/86. This is the offline T10 baseline, not a Crossref date-part.

## Crossref date-field results

| Year field compared with arXiv-ID year | Year available | Gap >2 (false positives) | FPR among available | Successful response, field absent | Fetch failed / unknown |
|---|---:|---:|---:|---:|---:|
| `published-print` (print/issue publication date) | 385 | 226 | **58.7%** | 94 | 4 |
| `published-online` (online-first date) | 306 | 164 | **53.6%** | 173 | 4 |
| `issued` (Crossref issued date) | 479 | 255 | **53.2%** | 0 | 4 |
| `posted` | 0 | 0 | **Not estimable** | 479 | 4 |

The delta-specific complete-case rates show why a single tolerance number obscures which registered date is used:

| Delta | `published-print` | `published-online` | `issued` |
|---:|---:|---:|---:|
| 1 | 1/80 (1.3%) | 4/62 (6.5%) | 1/100 (1.0%) |
| 2 | 2/79 (2.5%) | 2/62 (3.2%) | 1/99 (1.0%) |
| 3 | 80/82 (97.6%) | 39/60 (65.0%) | 76/99 (76.8%) |
| 4 | 72/72 (100%) | 61/64 (95.3%) | 94/97 (96.9%) |
| 5 | 71/72 (98.6%) | 58/58 (100%) | 83/84 (98.8%) |

The `posted` field has no year in any of the 479 successful Crossref responses. Its FPR is therefore **unknown/not estimable**, not 0%. Four other records had no successful response and remain unknown. The arXiv-ID year is the printed year/source-side year, not an independently observed submission timestamp: the snapshot's `versions` list has no version dates. Comparing the ID year with itself would be a tautological 0/483 and is not a meaningful false-positive measurement.

## Interpretation

Using Crossref `published-print`, `published-online`, or `issued` gives different denominators and different measured FPRs. Among available records, the measured rate ranges from 53.2% to 58.7%; at delta 1–2, the fields still produce 1.0%–6.5% false positives in complete cases, while most delta 3–5 pairs are flagged. This supports reporting a date-field-specific, sample-specific cost of a year rule. It does not support a single pooled FPR for all citations: 94 print dates and 173 online dates are absent in successful responses, four DOI lookups failed, and the snapshot only samples records with unambiguous journal-ref years and exactly one DOI.

The enriched fixture preserves the fetched date-parts and explicit field status per row. To reproduce the offline analysis, run `python3 benchmarks/crossref_dates/analyze_date_fields.py`; it reads only the frozen fixture and cache and performs no network requests. Re-running `python3 benchmarks/crossref_dates/fetch_crossref.py` reports 483 cache hits and zero network requests. Both were re-run under a socket guard that raises on any non-local connection, so "offline" is demonstrated rather than asserted.


## Are these three rates comparable with each other?

**Not as printed above.** Each complete-case rate is computed over a different
set of rows: `published-print` is absent from 94 successful responses and
`published-online` from 173. Comparing 58.7% with 53.6% compares two
subpopulations, not two fields.

`analyze_field_comparability.py` restricts to the 212 records carrying all
three, so the difference is attributable to the field:

| | complete-case (different rows) | paired (same 212 rows) |
|---|---:|---:|
| `published-print` | 58.7% | **61.3%** |
| `published-online` | 53.6% | **52.8%** |
| `issued` | 53.2% | **50.5%** |

Paired by delta, the field choice turns out to matter in exactly one place:

| delta | `published-print` | `published-online` | `issued` |
|---:|---:|---:|---:|
| 1 | 0/42 | 4/42 | 0/42 |
| 2 | 2/42 | 1/42 | 0/42 |
| 3 | **43/43** | **24/43** | **24/43** |
| 4 | 39/39 | 37/39 | 37/39 |
| 5 | 46/46 | 46/46 | 46/46 |

At delta 3 the print date flags every pair and the online date flags a little
over half. That is the expected shape: online-first typically precedes print by
about a year, so a 3-year preprint-to-print gap is often a 2-year
preprint-to-online gap and falls inside the tolerance. At delta 1–2 there is
almost nothing to separate, and by delta 4–5 the gap defeats every field.

## `published-online` is not only an online-first date

Among the 212 records carrying both, the online date is **earlier** than print
for 76, the **same year** for 126, and **later** for 10 — by up to 9 years.
Eight of those ten have an online year of 2011–2013, which is the signature of
retro-digitisation: the date the publisher's platform received a scan of an
older article, not the date the article first appeared online.

Five of them produce a false positive that the print date would not raise:

| case | delta | arXiv ID year | print | online |
|---|---:|---:|---:|---:|
| T10-0001 | 1 | 2004 | 2005 | 2012 |
| T10-0003 | 1 | 2003 | 2004 | 2011 |
| T10-0005 | 1 | 2004 | 2005 | 2012 |
| T10-0010 | 1 | 2008 | 2008 | 2012 |
| T10-0168 | 2 | 2010 | 2012 | 2013 |

That accounts for all four of the delta-1 false positives in the online column
and one of the two at delta 2. So the online column's low-delta errors are not
a date-semantics effect at all; they are a data-quality artefact of one field
carrying two different events under one name.

**This is the part that matters for a tolerance rule.** Separating publication
year from online-first year is necessary but not sufficient, because one of the
fields is itself not a single thing. A rule cannot be fixed only by choosing a
better field.
