# deepcite

Does a paper's *description* of a cited work match that work's own text?

Every existing IntegriRef layer (L0–L4) checks metadata: whether the DOI exists,
whether author/year/venue agree. None of them checks the thing that actually got
through review — a sentence that describes a cited work incorrectly. deepcite is
the advanced mode of `/ref-check` that does.

It produces a **worklist, not a verdict**. There is no SUPPORTED label: the tool
selects citations that make a checkable claim, fetches the cited work's primary
artifact, retrieves candidate passages, and stops. A human — or an agent via
`annotate`, whose every quote is verified mechanically — does the judging.

Full design: [`../docs/DEEPCITE_SPEC_v1.1.md`](../docs/DEEPCITE_SPEC_v1.1.md).

## Why

Four real failures, none of them a metadata error:

| Case | What went wrong |
|---|---|
| ROA-LLM | Wrote that MQuAKE's "native metric is conjunction". MQuAKE's own text defines multi-hop accuracy as correct if *any* of three paraphrases is answered — a disjunction. Conjunction is only its reference metric. |
| CIKM | The one blocking item in the real reviews: a protocol credited to the wrong paper. The internal panel missed it completely. |
| crystleLLM | 8 citations described the cited work incorrectly. |
| ChroKnowBench | A *secondary summary* nearly made the paper change a correct "13 snapshots" into a wrong 14. The released file has 13 yearly fields. |

The last one is why the tool reads the primary artifact and why a quote must
resolve against it. A summary is not evidence.

## Usage

```bash
# Build the worklist. Deterministic; never calls an LLM.
python -m deepcite run --paper paper/nlpcc2026 --bib paper/refs.bib --main main.tex

# For a claim whose evidence lives in a released data file, not a paper:
python -m deepcite run --paper . --bib refs.bib \
       --artifact park2025chroknowledge=~/data/chroknowbench/temporal.jsonl

# Attach an opinion. Every quote must appear in the cached artifact or the whole
# opinion is rejected (exit 5).
python -m deepcite annotate --paper . --record a1b2c3d4e5f6a7b8 --opinion-file op.json
```

Exit codes: `0` ran (including nothing-in-scope, which still writes a cache so a
reader can tell that from "never ran") · `2` bib missing or unparseable · `3`
bibguard older than 0.5.0 — it fails loudly and never falls back to resolving ids
itself · `4` every record failed · `5` `annotate` rejected an opinion · `1`
reserved for unexpected failures.

Output goes to `<paper>/.cache/ref_check_deep.json`. Fetched e-prints are cached
separately under `${XDG_CACHE_HOME:-~/.cache}/integriref/deepcite/eprints/`,
keyed by versioned arXiv id, shared across papers because artifacts are immutable
per version.

## Design constraints worth knowing

**No coupling to the pipeline.** deepcite never writes a risk tier and never
feeds the Bayesian fusion. Table 5's empirical likelihood ratios are calibrated
on the published 8,817-case pool and the camera-ready is locked at 13 fragile
pages; a new signal flowing into tiers would invalidate published numbers.

**NLI may rank, never label.** On SciFact-matched pairs L2 scored FPR 0.68 for
{contradicted ∪ unsupported}; contradicted-only was recall 0.77 / FPR 0.36. Worse,
wrapping a claim in a citation template flips supported → unsupported — and here
the claim is unavoidably a citing sentence. So `--rank nli` reorders passages and
nothing else; the default lexical ranker is deterministic and needs no torch.

**Selection patterns live only here.** The `using` family is *vendored* from
`semantic/intent_classifier.py:121-142` rather than imported: that package's
`__init__` drags `core.registry` and `requests`, `IntentClassifier.__init__`
eagerly loads torch models that are on disk, and its public 3-class output merges
USING into SUPPORTING — discarding the exact signal selection needs. The
`attribution` family is new; it catches the MQuAKE and ChroKnowBench shapes,
which contain no first-person usage verb and which the L1 cues cannot see.

**Coverage is 3 of the 4 motivating cases automatically.** ChroKnowBench's fact
lives in a released data file, not in the paper, so it needs `--artifact`.

## Contract

`<paper>/.cache/ref_check_deep.json` is a contract with the self-review reader,
which only ever reads it. `contract.py` is the single source of truth for
normalization, hashing, identifiers and the three-way staleness decision, and is
stdlib-only so both sides can implement it.

Staleness is decided by the **reader**, never flagged by the writer, which cannot
know what will be edited after it runs. Offsets are **UTF-8 bytes**
(`byte_start`/`byte_end`) — slice `stripped.encode("utf-8")[a:b]`, never the
`str`, or non-ASCII lines silently produce the wrong slice.

## Tests

```bash
python -m pytest deepcite/tests/ -q                       # 48 offline tests
DEEPCITE_NETWORK=1 python -m pytest deepcite/tests/test_acceptance.py -v
```

`test_contract.py` reproduces a fixture hand-written by the reader side in a
separate repo: 5 test vectors, 7 records' hashes, record ids, byte offsets and the
FRESH/MOVED/STALE classification. If it fails, the two implementations have
drifted and that must be settled before either side writes more code.
