"""Selection: which citing sentences make a checkable claim about the cited work.

Three families:

  ``using``        first-person adoption of something belonging to the cited work
                   ("we follow the protocol of X") -- catches the TALLRec shape.
  ``attribution``  an assertion about the cited work's own properties
                   ("X's native metric is conjunction", "X spans 13 snapshots")
                   -- catches the MQuAKE and ChroKnowBench shapes. NEW, P0.
  ``finding``      what the cited work found ("X showed that ...", "it has been
                   reported that ... [X]", and the bare "<specific finding> [X]").
                   Added 2026-10-06 after Citation-Integrity (deepcite/eval) measured
                   selection recall of 0.0275 on checkable claims: both earlier
                   families are about apparatus, and most citing sentences outside
                   methods sections assert a result.

Also 2026-10-06: A2 and A3 required the citation BEFORE the verb or number, while
real citations trail their clause ("registries reported a case fatality rate of
38% [X]"). A2T/A3T match the trailing position, refusing first-person clauses
("we report 85% ... [X]" is about the citing paper).

The ``using`` patterns are VENDORED (not imported) from
``semantic/intent_classifier.py:121-142``. Three reasons they are copied:
  1. ``semantic/__init__.py`` eagerly imports ``abstract_fetcher``/
     ``semantic_pipeline``, which drag ``core.registry`` and ``requests``;
     Python runs the package ``__init__`` even for a submodule import.
  2. ``IntentClassifier.__init__`` defaults to loading torch models that are
     present on disk, so merely constructing it is expensive.
  3. Its public 3-class output merges USING into SUPPORTING, which discards the
     exact signal selection depends on.
The cache key includes a hash of the patterns here, so a change to them
invalidates affected records -- see ``selection_regex_sha256``.

Deliberately NOT vendored from the L1 list: cues like ``see X for details`` and
``available at|released by``. They are tuned for intent classification, not for
"asserts something checkable", and they would flood the worklist with pointers.

Spec: docs/DEEPCITE_SPEC_v1.1.md §3.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .contract import CITE_KEYS_RE, CITE_RE

# A citation is masked to this token before matching, so a pattern can anchor on
# where the citation sits in the sentence ("CITEREF reports", "Bench~CITEREF spans").
CITE_TOKEN = "CITEREF"

# Nouns that make a claim checkable against the cited work's own text.
_ARTIFACT = (r"(?:protocol|procedure|setup|set-?up|setting|configuration|config"
             r"|metric|measure|score|evaluation|eval|split|splits|partition"
             r"|dataset|data\s?set|benchmark|corpus|subset|preprocessing"
             r"|hyper-?parameters?|implementation|architecture|objective|loss"
             r"|prompt|template|baseline|pipeline|criterion|criteria)")

_CLAIM_TYPE_WORDS = [
    ("protocol", r"protocol|procedure|pipeline"),
    ("metric", r"metric|measure|score|evaluation|eval|criterion|criteria"),
    ("split", r"split|splits|partition|fold|subset"),
    ("dataset", r"dataset|data\s?set|benchmark|corpus"),
    ("setup", r"setup|set-?up|setting|configuration|config|hyper-?parameters?"
               r"|architecture|objective|loss|prompt|template|implementation"),
]

# --- using family (vendored subset, re-scoped to checkable claims) ----------
_USING: list[tuple[str, str]] = [
    ("U1", rf"(?:follow|following|adopt|adopting|reuse|reusing|replicat\w+|use|using)"
           rf"\s+(?:the\s+)?(?:same\s+)?{_ARTIFACT}\s+(?:of|in|from|used\s+by|described)\b"),
    ("U2", rf"we\s+(?:use|employ|apply|utilize|adopt|leverage|implement|reuse|follow)"
           rf"\b[^.]{{0,60}}?{_ARTIFACT}"),
    ("U3", r"(?:as|exactly as|precisely as)\s+(?:described|defined|proposed|introduced"
           r"|reported|outlined|specified|implemented)\s+(?:by|in)"),
    ("U4", rf"(?:adapted|derived|borrowed|taken|modeled|modelled)\s+(?:from|after)"
           rf"\b[^.]{{0,40}}?(?:{CITE_TOKEN}|{_ARTIFACT})"),
    ("U5", r"(?:pre-?trained|fine-?tuned|trained|evaluated|benchmarked)\s+"
           r"(?:on|from|with|against|following)\b"),
    # 2026-10-06: U1/U5 (and A5 below) end in a \b. Without it "prompt injection
    # [X]" read as "prompt in [X]" -- hidden until LaTeX ties became spaces.
    # NB: there is deliberately no bare "the <artifact> of CITEREF" pattern here.
    # Without a usage verb that phrasing is an ASSERTION about the cited work
    # ("the metric of X is precision at 5"), which belongs to the attribution
    # family (A5), not to using. "we use / fine-tuned on the <artifact> of X" is
    # already covered by U2 and U5.
    ("U6", rf"(?:was|were|is|are)\s+(?:applied|used|employed|adopted|implemented"
           rf"|performed|conducted|computed|calculated|estimated|measured)"
           rf"\b[^.]{{0,40}}?(?:following|as\s+in|per)\b"),
]

# --- attribution family (NEW, P0) ------------------------------------------
# Assertions about the cited work itself. These catch the failures the L1 cues
# cannot see, because there is no first-person usage verb anywhere in them.
_ATTRIBUTION: list[tuple[str, str]] = [
    # "MQuAKE's native metric is conjunction" / "its main metric is ..."
    ("A1", rf"(?:\w+|{CITE_TOKEN})\s*(?:'s|s')\s+(?:\w+\s+){{0,3}}?{_ARTIFACT}\b"),
    # "X uses / reports / contains / defines / introduces ..."
    ("A2", rf"{CITE_TOKEN}\s*(?:\w+\s+){{0,2}}?(?:uses|use|used|reports|report|reported"
           rf"|contains|contain|includes|include|provides|provide|defines|define"
           rf"|introduces|introduce|adopts|adopt|evaluates|evaluate|measures|measure"
           rf"|spans|span|covers|cover|consists|comprises|releases|release)\b"),
    # "... spans 13 yearly snapshots ..." where the subject carries the citation
    ("A3", rf"{CITE_TOKEN}[^.]{{0,80}}?\b\d+(?:\.\d+)?\s*(?:\\%|%|\w+)"),
    # "in X, the metric is ..." / "in X the setup is ..."
    ("A4", rf"\bin\s+{CITE_TOKEN}\s*,?\s*(?:the\s+)?(?:\w+\s+){{0,3}}?{_ARTIFACT}\b"),
    # "the metric of X is ..." / "the split used by X is ..."
    ("A5", rf"(?:the\s+)?{_ARTIFACT}\s+(?:of|in|used\s+by|reported\s+by|proposed\s+by)\b"
           rf"\s*(?:\w+\s*)?{CITE_TOKEN}"),
    # "the native / main / primary / default metric IS ..." -- the verb is
    # required: "Primary metric: NDCG@10~\cite{k}" merely attributes a metric's
    # origin and asserts nothing about the cited work, so it must not fire.
    ("A6", rf"(?:native|main|primary|default|original|official|canonical|standard)"
           rf"\s+(?:\w+\s+){{0,2}}?{_ARTIFACT}\s+(?:is|are|was|were|uses|used"
           rf"|remains|becomes|consists|comprises)\b"),
]

# --- trailing-citation variants of A2/A3 (positional fix) -------------------
_ATTR_VERBS = (r"uses|used|reports|reported|contains|includes|provides|defines"
               r"|introduces|introduced|adopts|evaluates|evaluated|measures|spans"
               r"|covers|consists|comprises|releases|released|achieves|achieved"
               r"|attains|obtains|obtained|reaches|reached|scores|scored")
# Verbs that state what a cited work, system or theorem DOES. Measured 2026-10-06 on
# 67 hand-labelled citing sentences from four of the user's CS papers: with only
# _ATTR_VERBS, "AutoGPT's plugin system [X] allows third-party extensions to execute
# arbitrary code", "empirical Bernstein [X] gives ... the one-sided bound" and "[X]
# ask whether a test set is large enough" were all missed.
_MECH_VERBS = (_ATTR_VERBS + r"|allows|allow|enables|executes|execute|persists|persist|stores"
               r"|states|state|gives|give|yields|yield|implies|guarantees|asks|ask|assumes"
               r"|requires|caps|limits|restricts|formalizes|formalises|formalize|frames|models"
               r"|estimates|computes|bounds|inverts|corrects|elicits|elicit|probes|characterises"
               r"|characterizes|characterise|characterize|optimises|optimizes|audits|edits|edit"
               r"|discretises|discretizes|calls|names|terms|predicts|selects|samples|filters"
               r"|retrieves|ranks|scores|penalizes|penalises|normalizes|normalises|standardizes")
# Words that are as often nouns as verbs ("language models have", "the bounds are")
# may follow a citation ("[X] bounds the error") but must not be matched anywhere in
# a sentence, which the trailing pattern A10 would do.
_NOUNISH = {"models", "samples", "scores", "names", "calls", "terms", "states", "state",
            "bounds", "ranks", "filters", "limits", "caps", "estimates", "frames",
            "requires", "edits", "edit", "audits", "probes", "give", "yield", "ask",
            "allow", "execute", "persist", "formalize", "characterise", "characterize",
            "elicit", "use", "used", "report", "reported", "scored", "include"}
_MECH_VERBS_STRICT = "|".join(v for v in _MECH_VERBS.split("|") if v not in _NOUNISH)
_ATTRIBUTION_TRAILING: list[tuple[str, str]] = [
    # "MQuAKE evaluates whether edits propagate ... [X]" / "registries reported ... [X]"
    ("A2T", rf"\b(?:{_ATTR_VERBS})\b[^.;]{{0,160}}?{CITE_TOKEN}"),
    # "... a case fatality rate of 38% [X]" -- a number in the clause the citation closes
    ("A3T", rf"(?<![\w.\-])\d+(?:\.\d+)?\s*(?:\\%|%|×|\\times|x\b|-?fold|times"
            rf"|percent|points?|pp\b)[^.;]{{0,100}}?{CITE_TOKEN}"),
    # "AutoGPT's plugin system [X] allows ..." / "\citet{x} ask whether ..."
    ("A7", rf"{CITE_TOKEN}\s*(?:\w+\s+){{0,2}}?(?:{_MECH_VERBS})\b"),
    # "prompt injection [X] is bounded by ..." / "[X] is built from the same problems"
    # Participles that say what the work IS, not what the citing paper did with it
    # ("ESC [X] ... are faithfully replayed" is the citing paper's own usage).
    ("A8", rf"{CITE_TOKEN}\s*(?:is|are|was|were)\s+(?:\w+ly\s+)?(?:bounded|built|bound|known"
           rf"|designed|defined|limited|restricted|derived|constructed|drawn|taken|based"
           rf"|trained|composed|collected|annotated|labell?ed|curated|proposed|introduced"
           rf"|formulated|parameteri[sz]ed|optimi[sz]ed|computed|capped)\b"),
    # naming: "... is the naive Bayes combiner, also called ..., of the literature [X]"
    ("A9", rf"\b(?:is|are|known\s+as|called|termed|named|dubbed)\s+(?:the\s+|an?\s+)?"
           rf"(?:[\w-]+\s+){{0,5}}?(?:combiner|estimator|bound|inequality|algorithm|rule|test"
           rf"|theorem|lemma|property|criterion|metric|loss|objective|procedure|model|method"
           rf"|framework|benchmark|dataset|protocol|estimand|statistic|scheme|heuristic)\b"
           rf"[^.;]{{0,90}}?{CITE_TOKEN}"),
    # trailing mechanism verb: "... selective retention optimises utility under a budget [X]"
    ("A10", rf"\b(?:{_MECH_VERBS_STRICT})\b[^.;]{{0,120}}?{CITE_TOKEN}"),
]

# --- finding family (NEW 2026-10-06) ----------------------------------------
_FIND_VERBS = (r"show|shows|showed|shown|demonstrate|demonstrates|demonstrated|find"
               r"|finds|found|observe|observes|observed|report|reports|reported|reveal"
               r"|reveals|revealed|prove|proves|proved|proven|establish|establishes"
               r"|established|confirm|confirms|confirmed|suggest|suggests|suggested"
               r"|indicate|indicates|indicated|conclude|concludes|concluded|argue"
               r"|argues|argued|note|notes|noted|identify|identifies|identified"
               r"|document|documents|documented|highlight|highlights|highlighted")
_FINDING: list[tuple[str, str]] = [
    # "\citet{x} show that ..." / "X~\cite{x} also found ..."
    ("F1", rf"{CITE_TOKEN}\s*(?:\(\s*\d{{4}}\w?\s*\)\s*)?(?:\w+\s+){{0,3}}?(?:{_FIND_VERBS})\b"),
    # "as shown in [X]" / "was reported by [X]"
    ("F2", rf"\b(?:as|was|were|been|is|are)\s+(?:\w+\s+){{0,2}}?(?:shown|demonstrated|reported"
           rf"|observed|found|proven|proved|established|noted|confirmed|documented"
           rf"|suggested|argued|described|discussed)\s+(?:in|by)\s+(?:\w+\s+){{0,3}}?{CITE_TOKEN}"),
    # "it has been shown that ... [X]"
    ("F3", rf"\b(?:has|have|had)\s+(?:\w+\s+)?been\s+(?:\w+\s+)?(?:shown|demonstrated|reported"
           rf"|observed|found|proven|proved|established|documented|confirmed|suggested)"
           rf"\b[^.]{{0,200}}?{CITE_TOKEN}"),
    # "previous studies found that ... [X]"
    ("F4", rf"\b(?:prior|previous|recent|earlier|existing|several|many|some|other|these"
           rf"|such|one|two|three)\s+(?:\w+\s+)?(?:work|works|studies|study|research|reports"
           rf"|analyses|analysis|experiments|authors|papers|trials|surveys|evaluations)\s+"
           rf"(?:\w+\s+){{0,2}}?(?:{_FIND_VERBS})\b[^.]{{0,200}}?{CITE_TOKEN}"),
    # "Kim et al. [X] found that" / "they report that ... [X]"
    ("F5", rf"(?:et\s+al\.?|\bthey|\bthe\s+authors)\s*(?:{CITE_TOKEN}\s*)?(?:\w+\s+){{0,2}}?"
           rf"(?:{_FIND_VERBS})\b[^.]{{0,200}}?{CITE_TOKEN}|(?:et\s+al\.?|\bthey)\s*{CITE_TOKEN}"
           rf"\s*(?:\w+\s+){{0,2}}?(?:{_FIND_VERBS})\b"),
]

# The bare form "<finding> [X]" has no reporting verb. Almost any declarative
# sentence ending in a citation has that shape, so it fires only when the claim is
# specific enough to check (a guard the IntegriRef eval asked for): a quantity, a
# comparison, a causal/effect relation, or a universal/scope word -- and never when
# the citing paper is talking about itself or pointing at a long list of works.
_SPECIFIC: list[tuple[str, str]] = [
    ("quantity", r"(?<![\w.\-])\d+(?:\.\d+)?\s*(?:\\%|%|×|\\times|x\b|-?fold|times|percent"
                 r"|points?|pp\b|ms\b|s\b|hours?|days?|gb\b|tb\b|[kmb]\b)|\bp\s*[<=>]\s*0?\.\d"),
    ("comparison", r"\b(?:more|less|fewer|higher|lower|greater|smaller|larger|better|worse"
                   r"|faster|slower|outperform\w*|underperform\w*|exceed\w*|surpass\w*"
                   r"|superior|inferior|than|twice|half)\b"),
    ("effect", r"\b(?:causes?|caused|causing|causal\w*|lead(?:s|ing)?\s+to|led\s+to|result(?:s|ed)?"
               r"\s+in|due\s+to|because|increase[sd]?|decrease[sd]?|reduce[sd]?|reduction"
               r"|improve[sd]?|improvement|degrade[sd]?|degradation|enable[sd]?|prevent[sd]?"
               r"|induce[sd]?|trigger(?:s|ed)?|drives?|drove|harms?|hurts?|boosts?|mitigate[sd]?"
               r"|associated\s+with|correlat(?:es|ed|ing)\s+with|linked\s+to|predicts?|predictive|risk\w*"
               r"|vulnerab\w+|robust\w*|fail(?:s|ed|ure)?|collapse[sd]?|converge[sd]?|diverge[sd]?)\b"),
    ("scope", r"\b(?:most|majority|all|none|only|never|always|consistently|significantly"
              r"|substantially|dramatically|rarely|frequently|universally|cannot|impossible"
              r"|no\s+longer|first|state[\s-]of[\s-]the[\s-]art|sota|optimal|unbiased"
              r"|equivalent|identical|independent\s+of)\b"),
]
# First person is a NEGATIVE cue here and a POSITIVE one in retrieve._TYPE_CUES,
# on purpose: this reads the CITING sentence ("we" = the citing paper about
# itself), retrieval reads the CITED work ("we show" = its authors' own finding).
# Do not make the two consistent.
_FIRST_PERSON = re.compile(r"\b(?:we|our|ours|us|this\s+(?:paper|work|study|article|section))\b",
                           re.IGNORECASE)
_POINTER = re.compile(r"\b(?:see|cf\.?|e\.g\.?|i\.e\.?|for\s+(?:a\s+)?(?:review|survey|overview"
                      r"|details|example|instance)|such\s+as|including|surveyed\s+in|reviewed\s+in)"
                      rf"\s*,?\s*(?:\w+\s+){{0,2}}?{CITE_TOKEN}"
                      r"|^\s*(?:for\s+(?:a|an|more|further)?\s*(?:survey|review|overview|details"
                      r"|discussion|background|introduction|treatment)|see\s+also)\b", re.IGNORECASE)
# "The intuition connects to recent findings on model collapse [X]" names the cited
# work as related, it does not say what the work found (observed on ROA-LLM ICML).
_RELATIONAL = re.compile(r"\b(?:relates?\s+to|related\s+to|connects?\s+to|parallels|paralleling"
                         r"|reminiscent\s+of|inspired\s+by|analogous\s+to|akin\s+to|builds?\s+on"
                         r"|built\s+on|draws?\s+on|in\s+the\s+spirit\s+of|in\s+line\s+with"
                         r"|similar(?:ly)?\s+to|echoes|mirrors)\b", re.IGNORECASE)
# A parenthetical that does not hold the citation is usually the citing paper's own
# aside or result: "(E3: 40%->75% over four stages) parallels model collapse [X]".
_PAREN = re.compile(r"\([^()]*\)")
MAX_KEYS_BARE = 3        # "many works do X [a,b,c,d,e]" is background, not one checkable claim

# A quotation beside a citation is the most checkable claim there is: the words
# must appear in the cited work. Replay-V (2026-10-06) quoted a cited paper as
# ``drastically reduces inference compute without degrading'' where the paper says
# "drastically reduce inference compute without degrading---and in some cases even
# improving---final model performance"; a hand check found it, deepcite selected
# nothing in that paragraph.
_QUOTE = re.compile(r"``(.+?)''|\\enquote\*?\{([^{}]+)\}|\u201c([^\u201d]+)\u201d"
                    r"|(?<![\w\\])\"([^\"]{10,})\"", re.DOTALL)
MIN_QUOTE_WORDS = 4      # shorter is a term or a scare quote, not a quotation

_C_USING = [(pid, re.compile(p, re.IGNORECASE)) for pid, p in _USING]
_C_ATTR = [(pid, re.compile(p, re.IGNORECASE)) for pid, p in _ATTRIBUTION]
_C_ATTR_T = [(pid, re.compile(p, re.IGNORECASE)) for pid, p in _ATTRIBUTION_TRAILING]
_C_FIND = [(pid, re.compile(p, re.IGNORECASE)) for pid, p in _FINDING]
_C_SPEC = [(name, re.compile(p, re.IGNORECASE)) for name, p in _SPECIFIC]
_C_TYPE = [(name, re.compile(p, re.IGNORECASE)) for name, p in _CLAIM_TYPE_WORDS]
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\s*(?:\\%|%)|\b\d+\s+\w+")


def patterns_sha256() -> str:
    """Hash over the sorted pattern sources, for ``selection_regex_sha256``.

    Changing a pattern changes which citations are in scope, so it has to
    invalidate cached records independently of the tool version.
    """
    src = (sorted(p for _, p in _USING) + sorted(p for _, p in _ATTRIBUTION)
           + sorted(p for _, p in _ATTRIBUTION_TRAILING) + sorted(p for _, p in _FINDING)
           + sorted(p for _, p in _SPECIFIC)
           + [_FIRST_PERSON.pattern, _POINTER.pattern, _RELATIONAL.pattern,
              _PAREN.pattern, str(MAX_KEYS_BARE), _QUOTE.pattern, str(MIN_QUOTE_WORDS)])
    return hashlib.sha256("\x00".join(src).encode("utf-8")).hexdigest()


def mask_cites(text: str) -> str:
    return CITE_RE.sub(CITE_TOKEN, text)


@dataclass(frozen=True)
class Selection:
    family: str        # "using" | "attribution" | "finding"
    pattern_id: str
    claim_type: str    # protocol|metric|setup|dataset|split|number|finding|other


def classify_claim_type(masked: str) -> str:
    for name, rx in _C_TYPE:
        if rx.search(masked):
            return name
    if _NUMBER.search(masked):
        return "number"
    return "other"


def quoted_spans(sentence: str) -> list[str]:
    """Quotations of at least MIN_QUOTE_WORDS words in a citing sentence."""
    out = []
    for m in _QUOTE.finditer(sentence):
        q = next(g for g in m.groups() if g is not None)
        if len(re.findall(r"[A-Za-z0-9]+", q)) >= MIN_QUOTE_WORDS:
            out.append(re.sub(r"\s+", " ", q).strip())
    return out


def _clause_before(masked: str, pos: int) -> str:
    """Text from the start of the clause containing ``pos`` up to ``pos``."""
    cut = max(masked.rfind(";", 0, pos), masked.rfind(":", 0, pos))
    return masked[cut + 1:pos]


def _third_person(masked: str, m: re.Match) -> bool:
    """The matched assertion is not the citing paper talking about itself."""
    return not _FIRST_PERSON.search(_clause_before(masked, m.start()) + m.group(0))


def select(sentence: str, masked: str | None = None,
           max_keys_per_marker: int | None = None) -> Selection | None:
    """Return a Selection if the sentence makes a checkable claim, else None.

    ``using`` is tried first so a sentence that both adopts and describes is
    recorded under the stronger, more specific signal; then attribution, then
    finding. ``masked`` lets a caller that already replaced its own citation
    markers (the PDF scanner) pass the masked text directly.
    """
    if masked is None:
        masked = mask_cites(sentence)
        keys = [len([k for k in m.group(1).split(",") if k.strip()])
                for m in CITE_KEYS_RE.finditer(sentence)]
        max_keys_per_marker = max(keys) if keys else max_keys_per_marker
    if CITE_TOKEN not in masked:
        return None
    # "in~\\cite{x}": LaTeX's tie is a space for every pattern here. Without this
    # "as shown in~CITEREF" and "the metric of~CITEREF" never matched (2026-10-06).
    masked = re.sub(r"[\s~]+", " ", masked)
    for pid, rx in _C_USING:
        if rx.search(masked):
            return Selection("using", pid, classify_claim_type(masked))
    for pid, rx in _C_ATTR:
        text = masked
        if pid == "A3":
            # 2026-10-06, held-out CS sample: 7 of 13 false selections were A3 firing on
            # the citing paper's own numbers ("MovieLens-25M [X]: 10,000-user sample",
            # "Applying IPS [X] to our Beauty data ... 10x lower").
            text = _drop_asides(masked)
            m = rx.search(text)
            if not m or not _third_person(text, m) or _FIRST_PERSON.search(text[m.start():]):
                continue
        if rx.search(text):
            ct = classify_claim_type(masked)
            # A3 exists to catch numbers attributed to the cited work; if no
            # artifact noun is present, the claim type is the number itself.
            if pid == "A3" and ct == "other":
                ct = "number"
            return Selection("attribution", pid, ct)
    long_list = max_keys_per_marker is not None and max_keys_per_marker > MAX_KEYS_BARE
    for pid, rx in _C_ATTR_T:
        if long_list and pid in ("A2T", "A7", "A10"):
            continue        # "Modern frameworks [7 keys] manage state ..." is background
        text = _drop_asides(masked) if pid == "A3T" else masked
        m = rx.search(text)
        if (m and _third_person(text, m) and not _POINTER.search(text)
                and not (pid == "A3T" and _RELATIONAL.search(text))):
            ct = classify_claim_type(masked)
            if pid == "A3T" and ct == "other":
                ct = "number"
            return Selection("attribution", pid, ct)
    for pid, rx in _C_FIND:
        m = rx.search(masked)
        if m and (pid == "F1" or _third_person(masked, m)):
            ct = classify_claim_type(masked)
            return Selection("finding", pid, "finding" if ct == "other" else ct)
    if quoted_spans(sentence or masked):
        return Selection("attribution", "AQ", "quote")
    return _bare_finding(masked, max_keys_per_marker)


def _drop_asides(masked: str) -> str:
    """Remove parentheticals that do not contain a citation."""
    prev = None
    while prev != masked:
        prev = masked
        masked = _PAREN.sub(lambda m: m.group(0) if CITE_TOKEN in m.group(0) else " ",
                            masked)
    return masked


def _bare_finding(masked: str, max_keys_per_marker: int | None) -> Selection | None:
    """``<specific finding> [X]``: only with a specificity signal, see _SPECIFIC."""
    if _FIRST_PERSON.search(masked) or _POINTER.search(masked) or _RELATIONAL.search(masked):
        return None
    masked = _drop_asides(masked)
    if max_keys_per_marker is not None and max_keys_per_marker > MAX_KEYS_BARE:
        return None
    # The claim is the clause the citation closes, not the whole sentence: in
    # "X is widely used [a], and it fails on Y [b]" only the second clause is b's.
    pos = masked.rfind(CITE_TOKEN)
    clause = _clause_before(masked, pos)
    if len(clause.split()) < 5:
        return None
    for name, rx in _C_SPEC:
        if rx.search(clause):
            ct = classify_claim_type(masked)
            return Selection("finding", f"FB:{name}",
                             "finding" if ct in ("other", "number") and name != "quantity"
                             else ("number" if ct == "other" else ct))
    return None


def search_terms(sentence: str, limit: int = 8) -> list[str]:
    """Content words from the claim, used to retrieve passages and to record
    what was looked for when nothing is found (CLAIM_ABSENT_FROM_ARTIFACT)."""
    masked = mask_cites(sentence).replace(CITE_TOKEN, " ")
    masked = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{([^}]*)\})?",
                    lambda m: m.group(1) or " ", masked)
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|\d+(?:\.\d+)?", masked)
    stop = {"the", "and", "for", "with", "that", "this", "from", "are", "was",
            "were", "which", "their", "its", "our", "all", "use", "used", "using",
            "we", "report", "reports", "follow", "following", "adopt", "same",
            "also", "than", "then", "they", "them", "has", "have", "had", "been",
            "but", "not", "can", "may", "more", "most", "other", "such", "both",
            "over", "under", "into", "across", "within", "between", "while",
            "when", "where", "each", "every", "any", "one", "two", "via", "per",
            "only", "same", "well", "much", "many", "very", "upon", "about"}
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw in stop or lw in seen:
            continue
        seen.add(lw)
        out.append(w)
        if len(out) >= limit:
            break
    return out
