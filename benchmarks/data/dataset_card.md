---
language:
  - en
license: cc-by-4.0
task_categories:
  - text-classification
  - zero-shot-classification
tags:
  - citation-verification
  - fact-verification
  - research-integrity
  - scientific-misconduct
  - NLI
  - graph-anomaly-detection
  - retraction
  - paper-mill-detection
  - tortured-phrases
  - statistical-checking
pretty_name: IntegriRef-Bench
size_categories:
  - n<1K
---

# IntegriRef-Bench

## Dataset Summary

**IntegriRef-Bench** is a multi-signal benchmark for evaluating citation integrity
verification systems.  It covers the full verification pipeline from low-level
metadata resolution (L0) through semantic intent classification (L1), claim–source
NLI alignment (L2), citation-graph anomaly detection (L3), and surface-level text
signals (tortured phrases, suspicious author emails, GRIM test failures,
statcheck errors).

The dataset is designed to evaluate systems that must decide whether a scientific
citation is:
- **Retracted** or otherwise problematic (fraud, fabrication, data misconduct)
- **Hallucinated** (entirely fabricated by an LLM)
- **A metadata chimera** (real title + wrong authors/year — subtle LLM error)
- **Misrepresenting** its source (citing sentence contradicts the cited abstract)
- **Anomalous at the graph level** (citation rings, excessive self-citation,
  temporal impossibilities, orphan clusters)

The benchmark draws on publicly documented real-world cases from the scientific
misconduct literature, making it suitable as a paper contribution.

## Splits at a Glance

| Split | Records | Description |
|---|---|---|
| `reference_verification` | 58 | Golden test set: retracted, real, hallucinated, chimera |
| `signal_unit_tests` | 86 | One test case per verifiable signal |
| `l1_intent` | 20 | Citing sentence ↔ abstract pairs for intent classification |
| `l2_nli` | 20 | Claim ↔ source-text pairs for NLI alignment |
| `graph_anomaly` | 30 | Citation-ring / self-citation / temporal / orphan cases |
| `retracted_papers` | 250 | Fetched retraction data from Crossref and PubMed |
| **Total** | **464** | |

## Supported Tasks and Leaderboards

- **Citation integrity classification** (`reference_verification`): given a
  bibliographic reference, predict whether it is retracted, legitimate, hallucinated,
  or a chimera.  Primary metric: macro-F1 over the four labels.
- **Citation intent classification** (`l1_intent`): given a citing sentence and the
  cited abstract, classify citation intent as *supporting*, *contrasting*, or
  *mentioning*.  Primary metric: macro-F1 over three intents.
- **NLI for scientific claims** (`l2_nli`): given a claim and source text, predict
  *entailment*, *contradiction*, or *neutral*.  Primary metric: macro-F1.
- **Graph anomaly detection** (`graph_anomaly`): given a description of a citation
  sub-network, predict which anomaly signals fire.  Primary metric: macro-F1 and
  AUC-PR per signal.
- **Signal unit tests** (`signal_unit_tests`): binary pass/fail per signal.
  Primary metric: signal-level precision and recall.

## Languages

English (`en`).  Domain vocabulary is academic/scientific English across
biomedical, computer science, social science, physics, and mathematics.

---

## Dataset Structure

### Data Fields

#### `reference_verification`

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier (e.g. `retracted_001`, `halluc_003`) |
| `split` | string | Always `reference_verification` |
| `category` | string | One of: `retracted`, `real`, `hallucinated`, `chimera` |
| `signal` | string | Primary signal tested (e.g. `retracted_citation`, `phantom_doi`) |
| `title` | string | Paper title |
| `authors` | string | JSON-serialised author list |
| `year` | string | Publication year |
| `doi` | string | DOI (may be empty for hallucinated entries) |
| `venue` | string | Journal or conference |
| `expected_retracted` | bool | Ground-truth retraction label |
| `expected_found` | bool | Whether the reference should resolve to a real paper |
| `expected_risk` | string | Risk level: `LOW`, `ELEVATED`, or `HIGH` |
| `subcategory` | string | More specific label (e.g. `fraud`, `fabrication`, `methodology`) |
| `note` | string | Free-text annotation explaining the case |

#### `signal_unit_tests`

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier (e.g. `tp_001`, `grim_003`) |
| `split` | string | Always `signal_unit_tests` |
| `category` | string | Signal group (e.g. `tortured_phrases`, `grim_test`) |
| `signal` | string | Exact signal name as fired by the verifier |
| `input_text` | string | Primary text input (paper excerpt, stat string, or serialised authors) |
| `expected_fired` | bool | Whether the signal should fire |
| `note` | string | Annotation |
| `extra` | string | JSON-serialised signal-specific fields (tortured phrase, GRIM details, etc.) |

#### `l1_intent`

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier (e.g. `l1_pair_001`) |
| `split` | string | Always `l1_intent` |
| `category` | string | Intent category: `supporting`, `contrasting`, `mentioning`, `misrepresents` |
| `signal` | string | Always `citation_misrepresents_source` when `expected_misrepresents` is true |
| `citing_sentence` | string | The sentence in the citing paper containing the citation |
| `citation_key` | string | In-text citation key |
| `cited_doi` | string | DOI of the cited paper |
| `cited_title` | string | Title of the cited paper |
| `cited_abstract` | string | Abstract excerpt of the cited paper |
| `expected_intent` | string | Ground-truth intent label |
| `expected_misrepresents` | bool | True when the citing sentence contradicts the cited paper |
| `note` | string | Annotation |

#### `l2_nli`

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier (e.g. `l2_pair_001`) |
| `split` | string | Always `l2_nli` |
| `category` | string | NLI relation: `entailment`, `contradiction`, `neutral` |
| `signal` | string | `claim_contradicted` when contradiction, `claim_unsupported` when neutral, otherwise `none` |
| `claim` | string | The claim made in the citing paper |
| `source_text` | string | Excerpt from the cited paper that should verify the claim |
| `expected_relation` | string | Ground-truth NLI label |
| `domain` | string | Scientific domain (e.g. `cs`, `biomedical`, `psychology`) |
| `note` | string | Annotation |

#### `graph_anomaly`

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier (e.g. `ring_001`, `ta_002`) |
| `split` | string | Always `graph_anomaly` |
| `category` | string | Anomaly type: `citation_ring`, `self_citation`, `temporal_anomaly`, `orphan_cluster` |
| `signal` | string | Signal name: `citation_ring_detected`, `excessive_self_citation`, `temporal_anomaly`, `orphan_cluster` |
| `description` | string | Natural-language description of the case |
| `dois` | string | JSON-serialised list of DOIs involved |
| `expected_anomaly` | bool | Whether the anomaly signal should fire |
| `note` | string | Annotation with real-world provenance |
| `extra` | string | JSON-serialised numeric features (ring size, self-citation rate, etc.) |

#### `retracted_papers`

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier derived from source and DOI/PMID |
| `split` | string | Always `retracted_papers` |
| `category` | string | `retracted` or `real_control` |
| `signal` | string | Always `retracted_citation` |
| `source` | string | Data origin: `crossref_retracted`, `pubmed_retracted`, `crossref_real_*` |
| `doi` | string | DOI (may be empty) |
| `pmid` | string | PubMed ID (may be empty) |
| `title` | string | Paper title |
| `authors` | string | JSON-serialised author list |
| `year` | string | Publication year |
| `venue` | string | Journal or conference |
| `expected_retracted` | bool | Ground-truth retraction label |
| `expected_risk` | string | Risk level: `LOW` or `HIGH` |

---

## Dataset Creation

### Source Data

#### Initial Data Collection and Normalization

The `reference_verification` golden test set was hand-curated from the scientific
misconduct literature by the IntegriRef authors.  All retracted entries reference
real documented retractions with DOIs confirmed via Crossref and/or Retraction Watch.

The `signal_unit_tests` cases were authored to reflect confirmed real-world detection
patterns as documented in the primary literature (see References below).

The `l1_intent` and `l2_nli` pairs use real published abstracts (first ~300
characters) for the cited papers; claiming sentences were authored to span the full
range of intent and NLI labels, including deliberately misrepresenting cases.

The `graph_anomaly` split encodes documented real cases: the Brazilian citation
stacking scandal (2009–2013), the Ji-Huan He IJNSNS ring (2007–2011), the IOP
paper-mill mass retraction (2022), and the Hindawi special-issue paper-mill
clusters (2022–2023).

The `retracted_papers` split was fetched programmatically:
- Crossref retracted works using the `update-policy:PoliciesUpdates/retract` filter
- PubMed retracted publications via the ESearch/EFetch API (`retracted publication[pt]`)
- Real-paper controls: five top-cited works per domain (deep learning, genomics,
  clinical trials, NLP) fetched from Crossref

#### Who are the source language producers?

All benchmark text originates from peer-reviewed scientific publications.
Case annotations were written by the IntegriRef authors.

### Annotations

#### Annotation Process

Ground-truth labels were assigned by the dataset authors based on:
- Confirmed retractions: verified via Crossref retraction notices and Retraction Watch
  database entries
- Hallucinated references: constructed by prompting large language models (GPT-4,
  Claude) and verifying non-existence of all DOIs and title+author combos
- Signal-level labels: cross-validated against published descriptions in the
  primary source papers (Cabanac & Labbé 2021, Brown & Heathers 2017, etc.)

#### Who are the annotators?

IntegriRef research team.  No crowdsourcing was used.

---

## Considerations for Using the Data

### Social Impact of Dataset

This dataset is intended to support the development of automated tools for detecting
scientific misconduct, retracted citations, and citation manipulation.  Improved
detection aids journal editors, systematic reviewers, and researchers who need to
verify the integrity of sources cited in scientific literature.

### Discussion of Biases

- **Geographic bias**: the well-documented misconduct cases over-represent certain
  regions and fields (biomedical, social psychology, Chinese/Brazilian paper mills).
  This reflects the availability of public misconduct records rather than an
  accurate estimate of regional base rates.
- **Temporal bias**: high-profile retraction scandals from 2004–2024 are
  disproportionately represented; more recent or smaller retractions are likely
  under-sampled.
- **English-language bias**: all annotations and most paper abstracts are in English,
  even when the original misconduct occurred in journals published in other languages.
- **False negative risk**: the `real_papers` control set was selected to include
  canonical, highly-cited papers unlikely to be retracted; ordinary published papers
  with lower citation counts are not represented.

### Other Known Limitations

- The `retracted_papers` split was fetched at a point in time; retraction notices
  may be updated or new retractions issued after the dataset freeze.
- GRIM and statcheck cases use analytically computed ground truth; real-world
  use requires access to the full statistical context of a paper.
- Graph anomaly cases encode structured numeric features; detectors that require
  raw citation network adjacency matrices must reconstruct these from the `dois`
  field.
- The benchmark does not currently cover non-English tortured-phrase patterns or
  image duplication signals (Western-blot manipulation, figure recycling).

---

## Additional Information

### Dataset Curators

IntegriRef research team.

### Licensing Information

Creative Commons Attribution 4.0 International (CC BY 4.0).
Source paper abstracts are reproduced in abbreviated form for research use only
and remain the intellectual property of their respective publishers.

### Citation Information

If you use IntegriRef-Bench in your research, please cite:

```bibtex
@dataset{integriref_bench_2026,
  title        = {{IntegriRef-Bench}: A Multi-Signal Benchmark for Citation Integrity Verification},
  author       = {{IntegriRef Research Team}},
  year         = {2026},
  publisher    = {HuggingFace Datasets},
  url          = {https://huggingface.co/datasets/integriref/IntegriRef-Bench},
  note         = {Multi-signal benchmark covering retracted, hallucinated, chimera,
                  and misrepresenting citations, with unit tests for 18 Bayesian
                  signals and graph anomaly cases drawn from documented real-world
                  misconduct.}
}
```

### References

- Cabanac, G. & Labbé, C. (2021). Prevalence of nonsensical algorithmically
  generated papers in Web of Science and Scopus. *Scientometrics*, 127, 379–396.
  https://doi.org/10.1007/s11192-021-04188-3

- Brown, N. J. L. & Heathers, J. A. J. (2017). The GRIM test: A simple technique
  detects numerous anomalies in the reporting of results in psychology.
  *Social Psychological and Personality Science*, 8(4), 363–369.
  https://doi.org/10.1177/1948550616673876

- Nuijten, M. B., Hartgerink, C. H. J., van Assen, M. A. L. M., Epskamp, S. &
  Wicherts, J. M. (2016). The prevalence of statistical reporting errors in
  psychology (1985–2013). *Behavior Research Methods*, 48, 1205–1226.
  https://doi.org/10.3758/s13428-015-0664-2

- Fabbri, A., Santos, A. I., Urra, O., & Díaz-Faes, A. A. (2020). Brazilian
  citation cartel identified. *Scientometrics*, 125, 979–990.
  https://doi.org/10.1007/s11192-020-03672-6

- Schiermeier, Q. (2015). Self-citation cartel stripped of impact factors.
  *Nature News*. https://doi.org/10.1038/nature.2015.18766

- Van Noorden, R. (2013). Brazilian citation scheme outed. *Nature*, 500, 510.
  https://doi.org/10.1038/500510a

- Arnold, D. N. & Fowler, K. K. (2011). Nefarious numbers. *Notices of the AMS*,
  58(3), 434–437.

- Kojaku, S., Livan, G. & Masuda, N. (2021). Detecting anomalous citation groups
  in journal networks. *Scientific Reports*, 11, 14524.
  https://doi.org/10.1038/s41598-021-93572-3

- Biagioli, M. & Lippman, A. (Eds.) (2020). *Gaming the Metrics: Misconduct and
  Manipulation in Academic Research*. MIT Press.

- Wadden, D., Lin, S., Lo, K., Wang, L. L., van Zuylen, M., Cohan, A. &
  Hajishirzi, H. (2020). Fact or Fiction: Verifying Scientific Claims.
  *EMNLP 2020*. https://doi.org/10.18653/v1/2020.emnlp-main.609

- Cohan, A., Ammar, W., van Zuylen, M. & Cady, F. (2019). Structural Scaffolds for
  Citation Intent Classification in Scientific Publications. *NAACL 2019*.
  https://doi.org/10.18653/v1/N19-1361
