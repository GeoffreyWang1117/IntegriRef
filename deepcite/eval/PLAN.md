# Validating deepcite, and the data to do it with

Status 2026-10-06: Part A has run and produced the first numbers. They localize the
weakness precisely enough to change what gets built next, so they are reported here
before anything is rebuilt.

## Why this exists

The spec (`docs/DEEPCITE_SPEC_v1.1.md` §10.1) flagged that everything rests on
selection quality and that the `attribution` family was new and unvalidated, with
no precision/recall numbers. deepcite is also intended to carry the journal version
of IntegriRef, so these numbers are paper numbers, not just engineering checks.

## The data landscape, assessed rather than assumed

| Corpus | Gives | Verdict for deepcite |
|---|---|---|
| **Citation-Integrity** (Sarol et al., *Bioinformatics* 2024; MIT; 3,063 instances, 100 biomedical papers) | citing sentence + cited doc + per-sentence gold evidence + error taxonomy (ACCURATE / NOT_SUBSTANTIATE / CONTRADICT / OVERSIMPLIFY / MISQUOTE / INDIRECT / ETIQUETTE) | **The only external labelled corpus for this task.** Used for Part A. Abstracts only, biomedical only |
| **unarXive 2024** (HF `saier/unarXive_citrec`, `ines-besrour/unarxive_2024`) | 1.9M structured arXiv **full texts**, 134M in-text citation markers, 65M linked to OpenAlex, LaTeX math preserved | **The right substrate for Part C.** Same shape as deepcite's fetcher. No accuracy labels, so it is a mining ground, not an evaluation set |
| **S2ORC** | 81M papers, 8.1M full texts, linked citation spans, multi-domain | Alternative mining ground; broader domains, heavier |
| **MultiCite** (12,653 expert-annotated contexts, ACL papers) | multi-sentence, multi-label citation **contexts** | Useful for the context-window question: deepcite currently selects on one sentence, and MultiCite exists because that is often wrong |
| SciCite / ACL-ARC / SciFact | already consumed by L1/L2 | Training data, not evaluation for this |

What no public corpus supplies: labelled cases where the error is only visible in the
**full text or a released artifact**. That is deepcite's whole premise, and Part C has
to build it.

## Part A — selection and retrieval on Citation-Integrity (done)

`python -m deepcite.eval.citation_integrity --split dev` →
`benchmarks/results/deepcite_citint_dev.json`

What the corpus can and cannot settle, measured:
- Abstracts only. Evidence-bearing docs have a median of 5 sentences and 52.9% have
  ≤5, where a top-5 retrieval cannot miss. The harness reports the >5 subset
  separately and that is the number to quote.
- Its hardest subtask — pick the cited doc from a ~79-doc pool — is not deepcite's:
  deepcite gets a bib key and resolves it through bibguard, so the harness supplies
  the gold document.
- Its 0.59 micro-F1 baseline is **not** comparable: deepcite deliberately emits no
  accuracy label. Only retrieval recall compares, and loosely (their retriever was
  BM25 + MonoT5 over top-20).

### Results, dev split, patterns `6a73406f…`

| Stage | Number |
|---|---|
| Retrieval recall@5, all | **0.759** (274/361) |
| Retrieval recall@5, discriminative (>5-sentence docs) | **0.806** (137/170) |
| Median rank of first gold evidence hit | **1** |
| Selection recall on checkable claims | **0.0275** (7/255) |
| Selection recall on erroneous claims | **0.0109** (1/92) |
| Selection fire rate on no-evidence claims | 0.0164 (1/61) |

**Retrieval works. Selection does not generalize at all.** The ranker finds the gold
evidence sentence at rank 1 in the median case; the selector never gets it that far,
because it fires on 1% of the citations that are actually wrong.

### Why, with the sentences

The misses are empirical **findings** attributed to the cited work:

> "Patients with cirrhosis are at risk of SARS-CoV-2 infection due to innate and
> humoral immune dysfunction [CITE]" — NOT_SUBSTANTIATE
> "registries … reported a case fatality rate of 38%, which may be as high as 70%
> in the Child-Pugh C category [CITE]" — MISQUOTE
> "PWLD and particularly those with cirrhosis seem to be in higher risk for severe
> COVID-19 course and death [CITE]" — CONTRADICT

Both existing families are about **apparatus**: `using` = we adopted their
protocol/metric/split; `attribution` = their protocol/metric/split has property X.
Neither covers *their study found Y*, which dominates this corpus and probably most
literature outside methods sections. The four motivating failures in the spec were all
apparatus claims, so the families were fitted to a narrow slice of the claim space
without that being visible.

Two separable defects:

1. **A missing family: findings.** "X found/showed/reported/demonstrated/observed
   that …", and the far commoner bare form "⟨finding⟩ [CITE]" with no reporting verb
   at all. The bare form is the hard case: almost any declarative sentence ending in a
   citation is a finding claim, so a naive pattern here will fire on everything and
   the worklist becomes useless. This needs a precision guard, probably "the sentence
   asserts something specific enough to check" — a number, a named entity, a
   comparative, or a causal verb — not merely "is declarative".
2. **Positional brittleness, independent of the above.** `A2` requires `CITEREF`
   *before* the reporting verb and `A3` requires `CITEREF` *before* the number. Real
   citations trail their clause: "reported a case fatality rate of 38% [CITE]" should
   have matched A2 and did not. Markers must be matched in both positions. This is
   cheap and will raise recall on the apparatus families too, including in CS prose.

### What these numbers do and do not license

They do **not** say deepcite fails — the CIKM and MQuAKE acceptance cases still work,
and retrieval is solid. They say the selector's **coverage** was characterized on four
hand-picked apparatus failures and does not extend to findings claims. For the journal
version this is reportable as a scoped claim plus a measured limitation, which is
stronger than an unmeasured one.

## Part B — context window (not started)

deepcite selects on a single sentence. MultiCite exists because citation contexts are
routinely multi-sentence: the claim and its citation often sit in different sentences.
Measure how many Citation-Integrity and MultiCite contexts are unreachable from the
citing sentence alone, and if the loss is large, widen selection to ±1 sentence — which
changes `context_sha256` and so is a cache-format decision, not just a tuning knob.

## Part C — the full-text claim (started 2026-10-06; the one that matters)

Harness: `deepcite/eval/mine_fulltext_cases.py`, two sources.

**The unarXive route is blocked today, not abandoned.** `saier/unarXive_citrec`
gives 2.5M (citing paragraph, cited OpenAlex id) pairs, but turning a label into an
abstract and an arXiv id needs OpenAlex, whose unauthenticated budget is **per-IP
and per-day** and is exhausted on this machine (`$0 remaining; resets at midnight
UTC`). Resumes with an API key or tomorrow.

**The arXiv-only route works and is cheaper than the original design.** The title
is already in the `.bib`, so no id-resolution service is needed, and one arXiv API
query returns the versioned id *and* the abstract together — the original step 3
costs no extra request. Only the e-print fetch is rate-limited.

### A methodology error, found by reading the first candidates

The first filter kept a pair when the abstract scored ≤2.0 and the full text scored
≥4.0. **That comparison is unsound.** BM25's idf depends on `n` and `df` within the
corpus being ranked, so a score over a 2-sentence abstract and a score over a
400-sentence body are not on the same scale, and the body wins for being bigger.
The observed gap (0.947 vs 11.342) was mostly corpus size.

Fixed: abstract sentences and body sentences are ranked **together in one call**,
one idf space, and the decision is about **ordering** — a pair is kept only when no
abstract sentence appears in the top 3 and the top passage is from the body. The
old threshold survives only as a pre-filter that avoids fetching an e-print when
the abstract obviously already answers the claim; it never decides what is kept.

### What the first run showed about selection, not about full text

ROA-LLM, 37 bib entries → 3 candidates, 13 rejected (9 cited works not on arXiv,
3 answered by the abstract, 1 not answered anywhere), 58 s. Reading the three:

* Two came from the **same background-list sentence** ("AgentDojo~\cite{},
  InjecAgent~\cite{}, and …"), one candidate per cited work. Selection fired on a
  list, which asserts nothing about any single entry. Now rejected by a
  `MAX_CITES_IN_SENTENCE = 2` guard.
* The third, "The ReAct paradigm established agents combining reasoning with tool
  use", retrieved ReAct's future-work sentence from its introduction
  (`iclr2023/text/intro.tex:34` — live text, not a comment; an earlier note here
  said otherwise and was wrong). High BM25, no evidential bearing on the claim,
  and the claim itself is a vague characterization rather than something
  checkable. It survives every mechanical guard, which is the point: the filter
  raises the hit rate and cannot replace a human.

So the full-text filter does **not** compensate for permissive selection: a high
body score only means the terms occur somewhere. This is the same bottleneck Part A
found from the other direction, which is worth stating in the paper as one finding
rather than two.

### Original design notes

No public corpus labels errors that are only visible in the full text, so build one:

1. Mine candidates from **unarXive 2024**: it has arXiv full texts with in-text
   citation markers linked to OpenAlex, i.e. (citing sentence, cited paper full text)
   pairs at scale, in deepcite's native format.
2. Filter to apparatus and findings claims that are **not answerable from the cited
   abstract** — the discriminating condition, and mechanically checkable: run
   retrieval against the abstract and against the full text and keep the pairs where
   only the full text contains a candidate passage.
3. Hand-label a few hundred. This mirrors the NatChim methodology already in the
   paper: mine aggressively, confirm against a second source, keep few, and publish
   the rejects so the filter is auditable.
4. Seed it with the known cases: MQuAKE's metric (body, not abstract), TALLRec's
   protocol attribution, and ChroKnowBench's snapshot count (a released data file, not
   the paper — the one case no full-text corpus can cover).

Only Part C can support the sentence "reading the primary artifact catches errors that
abstract-level checking misses". Until it exists, that claim is motivated but not
evidenced.

## Reproducing

```bash
python -m deepcite.eval.citation_integrity --split dev --download
python -m deepcite.eval.citation_integrity --split test --out results.json
```

Every result records `selection_regex_sha256`. The selection patterns are expected to
change — a "finding" family is the obvious next step — and a number without the hash
that produced it is not attributable to anything.
