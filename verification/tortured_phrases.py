"""Tortured phrases detector for paper mill and paraphrasing tool detection.

Detects "tortured phrases" — awkward synonym substitutions produced by
paraphrasing tools and paper mills to evade plagiarism detection. These
substitutions replace standard technical terms with unusual synonyms that
no domain expert would naturally use.

Based on:
  - Cabanac, Labbé & Magazinov (2021) "Tortured phrases: A dubious
    writing style emerging in science"
  - Cabanac & Labbé (2021) "Prevalence of nonsensical algorithmically
    generated papers in the scientific literature"
  - Else (2021) "Tortured phrases reveal high-tech plagiarism in
    network of scientific papers" (Nature)
  - Problematic Paper Screener (https://www.irit.fr/~Guillaume.Cabanac/
    problematic-paper-screener)

The detector maintains a dictionary of known tortured phrase → standard
term mappings. When scanning text, it compiles case-insensitive regex
patterns and reports matches with character offsets and surrounding context.

Scoring: the presence of even one tortured phrase in an academic text is
highly suspicious, since these phrases are never used by legitimate authors.
Multiple matches strongly indicate machine paraphrasing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Rust accelerator (optional) ──────────────────────────────────────────
try:
    from integriref_core import RustTorturedPhraseDetector as _RustDetector
    _HAS_RUST = True
except ImportError:
    _RustDetector = None  # type: ignore
    _HAS_RUST = False

try:
    import ahocorasick
    _HAS_AHO = True
except ImportError:
    _HAS_AHO = False


# ---------------------------------------------------------------------------
# Tortured phrase dictionary
# Keys = tortured (paraphrased) form, values = correct standard term.
# Organised by domain for maintainability.
# ---------------------------------------------------------------------------

# --- Machine Learning & AI ---
_ML_AI = {
    "profound learning": "deep learning",
    "profound studying": "deep learning",
    "neural system": "neural network",
    "brain network": "neural network",
    "brain system": "neural network",
    "machine studying": "machine learning",
    "apparatus learning": "machine learning",
    "artificial consciousness": "artificial intelligence",
    "sham system": "fake network",
    "arbitrary forest": "random forest",
    "random woodland": "random forest",
    "bolster vector machine": "support vector machine",
    "support vector apparatus": "support vector machine",
    "regular language preparing": "natural language processing",
    "characteristic language handling": "natural language processing",
    "convolution brain system": "convolutional neural network",
    "convolutional brain system": "convolutional neural network",
    "repetitive brain system": "recurrent neural network",
    "intermittent brain system": "recurrent neural network",
    "slope descent": "gradient descent",
    "inclination descent": "gradient descent",
    "back spread": "backpropagation",
    "rear proliferation": "backpropagation",
    "back proliferation": "backpropagation",
    "highlight extraction": "feature extraction",
    "include extraction": "feature extraction",
    "enormous data": "big data",
    "huge data": "big data",
    "square chain": "blockchain",
    "block chain": "blockchain",
    "support learning": "reinforcement learning",
    "fortification learning": "reinforcement learning",
    "exchange learning": "transfer learning",
    "generative antagonistic": "generative adversarial",
    "consideration instrument": "attention mechanism",
    "consideration component": "attention mechanism",
    "conclusion analysis": "sentiment analysis",
    "supposition mining": "opinion mining",
    "named substance acknowledgment": "named entity recognition",
    "named element acknowledgment": "named entity recognition",
    "information chart": "knowledge graph",
    "knowledge chart": "knowledge graph",
    "united learning": "federated learning",
    "government learning": "federated learning",
    "cluster standardization": "batch normalization",
    "bunch standardization": "batch normalization",
    "hyper boundary": "hyperparameter",
    "super parameter": "hyperparameter",
    "misfortune function": "loss function",
    "deficit function": "loss function",
    "initiation function": "activation function",
    "enactment function": "activation function",
    "gathering layer": "pooling layer",
    "pooling layer": "pooling layer",
    "encoder translator": "encoder-decoder",
    "word to vector": "word2vec",
    "long haul short-term memory": "long short-term memory",
    "long haul memory": "long short-term memory",
    "head segment analysis": "principal component analysis",
    "key part examination": "principal component analysis",
    "head part examination": "principal component analysis",
    "stochastic slope descent": "stochastic gradient descent",
    "cross approval": "cross-validation",
    "cross endorsement": "cross-validation",
    "information expansion": "data augmentation",
    "information increase": "data augmentation",
    "picture division": "image segmentation",
    "picture portioning": "image segmentation",
    "protest identification": "object detection",
    "article identification": "object detection",
    "semantic division": "semantic segmentation",
    "self-governing driving": "autonomous driving",
    "independent driving": "autonomous driving",
    "ill-disposed assault": "adversarial attack",
    "ill-disposed assault": "adversarial attack",
    "antagonistic assault": "adversarial attack",
    "zero-energy learning": "zero-shot learning",
    "zero-vitality learning": "zero-shot learning",
    "dim learning": "deep learning",
    "counterfeit neural system": "artificial neural network",
    "manufactured neural system": "artificial neural network",
    "coarse pursuit": "grid search",
    "framework pursuit": "grid search",
    "precision review": "accuracy evaluation",
    "disarray framework": "confusion matrix",
    "disarray grid": "confusion matrix",
    "perplexity grid": "confusion matrix",
    "uneven woodland": "random forest",
    "choice tree": "decision tree",
    "choice woodland": "decision tree",
    "credulous Bayes": "naive Bayes",
    "guileless Bayes": "naive Bayes",
    "closest neighbor": "nearest neighbor",
    "K closest neighbor": "K nearest neighbor",
    "K-closest neighbor": "K-nearest neighbor",
    "dimensionality decrease": "dimensionality reduction",
    "peculiarity identification": "anomaly detection",
    "irregularity location": "anomaly detection",
    "notion extraction": "feature extraction",
    "picture acknowledgment": "image recognition",
    "picture grouping": "image classification",
    "discourse acknowledgment": "speech recognition",
    "discourse combination": "speech synthesis",
    "voice acknowledgment": "voice recognition",
    "characteristic language comprehension": "natural language understanding",
    "characteristic language age": "natural language generation",
}

# --- Statistics & Mathematics ---
_STATS = {
    "direct relapse": "linear regression",
    "straight relapse": "linear regression",
    "calculated relapse": "logistic regression",
    "strategic relapse": "logistic regression",
    "invalid theory": "null hypothesis",
    "invalid speculation": "null hypothesis",
    "void speculation": "null hypothesis",
    "p esteem": "p-value",
    "p worth": "p-value",
    "certainty interim": "confidence interval",
    "certainty stretch": "confidence interval",
    "certainty time span": "confidence interval",
    "standard abnormality": "standard deviation",
    "standard deviation": "standard deviation",  # keep for mapping completeness
    "relapse investigation": "regression analysis",
    "relapse examination": "regression analysis",
    "bogus positive": "false positive",
    "misleading positive": "false positive",
    "bogus negative": "false negative",
    "misleading negative": "false negative",
    "Monte Carlos": "Monte Carlo",
    "monto carlo": "Monte Carlo",
    "chi squared": "chi-squared",
    "Gaussian blend model": "Gaussian mixture model",
    "Gaussian blend": "Gaussian mixture",
    "irregular variable": "random variable",
    "stochastic variable": "random variable",
    "back likelihood": "posterior probability",
    "earlier likelihood": "prior probability",
    "greatest probability": "maximum likelihood",
    "greatest probability estimation": "maximum likelihood estimation",
    "most extreme probability": "maximum likelihood",
    "smallest amount squares": "least squares",
    "minimum squares": "least squares",
    "ordinary minimum squares": "ordinary least squares",
    "connection coefficient": "correlation coefficient",
    "relationship coefficient": "correlation coefficient",
    "fluctuation investigation": "variance analysis",
    "difference examination": "analysis of variance",
    "investigation of fluctuation": "analysis of variance",
    "time arrangement investigation": "time series analysis",
    "factual essentialness": "statistical significance",
    "factual importance": "statistical significance",
    "measurable essentialness": "statistical significance",
    "type I blunder": "type I error",
    "type II blunder": "type II error",
}

# --- Medical & Clinical ---
_MEDICAL = {
    "bosom malignancy": "breast cancer",
    "bosom growth": "breast cancer",
    "bosom disease": "breast cancer",
    "prostatic disease": "prostate cancer",
    "prostate malignancy": "prostate cancer",
    "deliberate audit": "systematic review",
    "methodical audit": "systematic review",
    "precise audit": "systematic review",
    "meta examination": "meta-analysis",
    "meta investigation": "meta-analysis",
    "randomised preliminary": "randomized controlled trial",
    "randomised controlled preliminary": "randomized controlled trial",
    "fake treatment": "placebo",
    "fake treatment impact": "placebo effect",
    "twofold visually impaired": "double-blind",
    "twofold dazzle": "double-blind",
    "case control": "case-control",
    "partner study": "cohort study",
    "associate investigation": "cohort study",
    "associate study": "cohort study",
    "hazard proportion": "hazard ratio",
    "risk proportion": "hazard ratio",
    "chances proportion": "odds ratio",
    "chances ratio": "odds ratio",
    "mortality proportion": "mortality rate",
    "mortality rate": "mortality rate",
    "clinical preliminary": "clinical trial",
    "clinical preliminary investigation": "clinical trial study",
    "medication obstruction": "drug resistance",
    "anti-microbial obstruction": "antimicrobial resistance",
    "anti-toxin obstruction": "antibiotic resistance",
    "cardiovascular illness": "cardiovascular disease",
    "coronary illness": "heart disease",
    "emotional well-being": "mental health",
    "psychological wellness": "mental health",
    "well-being data innovation": "health information technology",
    "medical services": "healthcare",
    "wellbeing administrations": "health services",
    "quality articulation": "gene expression",
    "quality expression": "gene expression",
    "protein overlay": "protein folding",
    "protein collapsing": "protein folding",
    "genomic sequencing": "genome sequencing",
    "cell demise": "cell death",
    "modified cell demise": "programmed cell death",
    "cell expansion": "cell proliferation",
}

# --- Computer Science (non-ML) ---
_CS = {
    "distributed computing": "cloud computing",
    "distributed figuring": "cloud computing",
    "Web of Things": "Internet of Things",
    "Web of things": "Internet of Things",
    "edge figuring": "edge computing",
    "haze figuring": "fog computing",
    "programming characterized organizing": "software-defined networking",
    "programming characterized": "software-defined",
    "digital assault": "cyber attack",
    "digital security": "cybersecurity",
    "digital protection": "cybersecurity",
    "figure multifaceted nature": "computational complexity",
    "computational multifaceted nature": "computational complexity",
    "time multifaceted nature": "time complexity",
    "space multifaceted nature": "space complexity",
    "information base": "database",
    "social information base": "relational database",
    "disseminated framework": "distributed system",
    "dispersed framework": "distributed system",
    "parallel figuring": "parallel computing",
    "quantum figuring": "quantum computing",
    "quantum PC": "quantum computer",
    "programming building": "software engineering",
    "programming designing": "software engineering",
    "deft programming advancement": "agile software development",
    "ceaseless combination": "continuous integration",
    "persistent combination": "continuous integration",
    "rendition control": "version control",
    "form control": "version control",
    "open source programming": "open source software",
    "working framework": "operating system",
    "working system": "operating system",
    "figure hub": "compute node",
    "system geography": "network topology",
    "arrange geography": "network topology",
    "steering convention": "routing protocol",
    "directing convention": "routing protocol",
    "parcel exchanging": "packet switching",
    "transmission control convention": "transmission control protocol",
    "area name framework": "domain name system",
}

# --- Physics & Engineering ---
_PHYSICS = {
    "vitality effectiveness": "energy efficiency",
    "sustainable power vitality": "renewable energy",
    "sun oriented vitality": "solar energy",
    "sun oriented board": "solar panel",
    "wind vitality": "wind energy",
    "warm vitality": "thermal energy",
    "warm conductivity": "thermal conductivity",
    "limited component investigation": "finite element analysis",
    "limited component technique": "finite element method",
    "computational liquid elements": "computational fluid dynamics",
    "liquid elements": "fluid dynamics",
    "motion examination": "motion analysis",
    "flag preparing": "signal processing",
    "flag to commotion proportion": "signal-to-noise ratio",
    "sign to clamor proportion": "signal-to-noise ratio",
    "recurrence reaction": "frequency response",
    "Fourier change": "Fourier transform",
    "wavelet change": "wavelet transform",
}

# --- General academic ---
_GENERAL = {
    "writing audit": "literature review",
    "writing survey": "literature review",
    "logical audit": "systematic review",
    "subjective examination": "qualitative analysis",
    "quantitative investigation": "quantitative analysis",
    "blended techniques": "mixed methods",
    "subjective exploration": "qualitative research",
    "explore plan": "research design",
    "information assortment": "data collection",
    "information gathering": "data collection",
    "center gathering": "focus group",
    "center group": "focus group",
    "poll overview": "questionnaire survey",
    "overview survey": "survey questionnaire",
    "reaction rate": "response rate",
    "test estimate": "sample size",
    "test size": "sample size",
    "comfort inspecting": "convenience sampling",
    "purposive inspecting": "purposive sampling",
    "snowball inspecting": "snowball sampling",
    "substance investigation": "content analysis",
    "topical investigation": "thematic analysis",
    "grounded hypothesis": "grounded theory",
    "contextual investigation": "case study",
    "contextual analysis": "case study",
    "partner examined": "peer reviewed",
    "peer investigated": "peer reviewed",
    "reference list": "bibliography",
    "abstract diary": "peer-reviewed journal",
    "diary article": "journal article",
}


def _build_phrase_dict() -> dict[str, str]:
    """Merge all domain dictionaries into one lookup, lowercased."""
    merged: dict[str, str] = {}
    for domain_dict in (_ML_AI, _STATS, _MEDICAL, _CS, _PHYSICS, _GENERAL):
        for tortured, standard in domain_dict.items():
            key = tortured.lower()
            # Skip identity mappings
            if key != standard.lower():
                merged[key] = standard
    return merged


# Singleton dictionary built at import time
TORTURED_PHRASES: dict[str, str] = _build_phrase_dict()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TorturedPhraseMatch:
    """A single tortured phrase occurrence in text."""
    tortured_phrase: str   # the tortured phrase as found in the text
    standard_term: str     # the correct standard term
    position: int          # character offset in text
    context: str           # surrounding text snippet (~80 chars)


@dataclass
class TorturedPhraseReport:
    """Results of scanning a text for tortured phrases."""
    text_length: int
    match_count: int         # total match occurrences (incl. duplicates)
    unique_phrases: int      # number of distinct tortured phrases found
    tortured_score: float    # 0-100
    matches: list[TorturedPhraseMatch] = field(default_factory=list)
    is_suspicious: bool = False

    def summary(self) -> dict:
        """Return a JSON-serialisable summary."""
        return {
            "text_length": self.text_length,
            "match_count": self.match_count,
            "unique_phrases": self.unique_phrases,
            "tortured_score": round(self.tortured_score, 1),
            "is_suspicious": self.is_suspicious,
            "matches": [
                {
                    "tortured": m.tortured_phrase,
                    "standard": m.standard_term,
                    "position": m.position,
                    "context": m.context,
                }
                for m in self.matches
            ],
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class TorturedPhraseDetector:
    """Scan text for known tortured phrases.

    On construction the detector compiles all known tortured phrases into
    a set of case-insensitive regex patterns (with word boundaries) for
    efficient scanning.

    Usage::

        detector = TorturedPhraseDetector()
        report = detector.scan(paper_fulltext)
        if report.is_suspicious:
            print(f"Found {report.match_count} tortured phrases!")
    """

    # Context window: how many chars to show around a match
    _CONTEXT_WINDOW = 40

    def __init__(self, extra_phrases: dict[str, str] | None = None):
        """Build matching structures from the phrase dictionary.

        Backend priority: Rust Aho-Corasick → Python pyahocorasick → regex.

        Args:
            extra_phrases: Optional additional tortured→standard mappings
                to merge with the built-in dictionary.
        """
        self._phrases: dict[str, str] = dict(TORTURED_PHRASES)
        if extra_phrases:
            for tortured, standard in extra_phrases.items():
                key = tortured.lower()
                if key != standard.lower():
                    self._phrases[key] = standard

        # Priority 1: Rust Aho-Corasick (fastest — compiled, single-pass)
        self._rust_detector = None
        if _HAS_RUST:
            try:
                self._rust_detector = _RustDetector(self._phrases)
            except Exception:
                self._rust_detector = None

        # Priority 2: Python Aho-Corasick automaton
        self._automaton = None
        if self._rust_detector is None and _HAS_AHO:
            self._automaton = ahocorasick.Automaton()
            for tortured, standard in self._phrases.items():
                self._automaton.add_word(tortured, (tortured, standard))
            self._automaton.make_automaton()

        # Priority 3: regex fallback
        self._patterns: list[tuple[re.Pattern, str, str]] = []
        if self._rust_detector is None and not _HAS_AHO:
            sorted_phrases = sorted(self._phrases.items(),
                                    key=lambda kv: len(kv[0]), reverse=True)
            for tortured, standard in sorted_phrases:
                try:
                    pattern = re.compile(
                        r"\b" + re.escape(tortured) + r"\b",
                        re.IGNORECASE,
                    )
                    self._patterns.append((pattern, tortured, standard))
                except re.error:
                    continue

    def scan(self, text: str) -> TorturedPhraseReport:
        """Scan text for tortured phrases.

        Backend priority: Rust → Python Aho-Corasick → regex.

        Args:
            text: Full text to scan (paper body, abstract, etc.)

        Returns:
            TorturedPhraseReport with matches, score, and suspicion flag.
        """
        if not text:
            return TorturedPhraseReport(
                text_length=0,
                match_count=0,
                unique_phrases=0,
                tortured_score=0.0,
                matches=[],
                is_suspicious=False,
            )

        # Rust fast path — returns dict, convert to TorturedPhraseReport
        if self._rust_detector is not None:
            return self._from_rust_result(self._rust_detector.scan(text))

        if self._automaton is not None:
            matches = self._scan_aho(text)
        else:
            matches = self._scan_regex(text)

        # Sort by position
        matches.sort(key=lambda m: m.position)

        unique = len({m.tortured_phrase.lower() for m in matches})
        score = self._compute_score(len(matches), unique, len(text))

        return TorturedPhraseReport(
            text_length=len(text),
            match_count=len(matches),
            unique_phrases=unique,
            tortured_score=score,
            matches=matches,
            is_suspicious=score >= 40.0,
        )

    # -- Internal scan strategies -----------------------------------------

    def _scan_aho(self, text: str) -> list[TorturedPhraseMatch]:
        """Single-pass scan using Aho-Corasick automaton."""
        text_lower = text.lower()
        text_len = len(text_lower)
        matches: list[TorturedPhraseMatch] = []
        seen_positions: set[tuple[int, int]] = set()

        # Collect raw hits; automaton.iter yields (end_index, value)
        raw_hits: list[tuple[int, int, str, str]] = []
        for end_idx, (tortured, standard) in self._automaton.iter(text_lower):
            start = end_idx - len(tortured) + 1
            end = end_idx + 1

            # Word-boundary check: character before start and after end
            # must not be alphanumeric or underscore (mimics \b).
            if start > 0 and (text_lower[start - 1].isalnum() or text_lower[start - 1] == "_"):
                continue
            if end < text_len and (text_lower[end].isalnum() or text_lower[end] == "_"):
                continue

            raw_hits.append((start, end, tortured, standard))

        # Sort longest-first so longer phrases take priority on overlap
        raw_hits.sort(key=lambda h: -(h[1] - h[0]))

        for start, end, tortured, standard in raw_hits:
            # Overlap check against already-accepted spans
            overlap = False
            for s, e in seen_positions:
                if start < e and end > s:
                    overlap = True
                    break
            if overlap:
                continue

            seen_positions.add((start, end))
            matches.append(self._make_match(text, start, end, standard))

        return matches

    def _scan_regex(self, text: str) -> list[TorturedPhraseMatch]:
        """Sequential regex scan (fallback when ahocorasick unavailable)."""
        matches: list[TorturedPhraseMatch] = []
        seen_positions: set[tuple[int, int]] = set()

        for pattern, tortured, standard in self._patterns:
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()

                # Skip if this span overlaps with an already-matched
                # longer phrase.
                overlap = False
                for s, e in seen_positions:
                    if start < e and end > s:
                        overlap = True
                        break
                if overlap:
                    continue

                seen_positions.add((start, end))
                matches.append(self._make_match(text, start, end, standard))

        return matches

    def _make_match(self, text: str, start: int, end: int,
                    standard: str) -> TorturedPhraseMatch:
        """Build a TorturedPhraseMatch with context snippet."""
        ctx_start = max(0, start - self._CONTEXT_WINDOW)
        ctx_end = min(len(text), end + self._CONTEXT_WINDOW)
        context = text[ctx_start:ctx_end].replace("\n", " ").strip()
        if ctx_start > 0:
            context = "..." + context
        if ctx_end < len(text):
            context = context + "..."

        return TorturedPhraseMatch(
            tortured_phrase=text[start:end],
            standard_term=standard,
            position=start,
            context=context,
        )

    def scan_abstract(self, abstract: str) -> TorturedPhraseReport:
        """Scan an abstract for tortured phrases (stricter: 1.2x boost)."""
        if not abstract:
            return TorturedPhraseReport(
                text_length=0, match_count=0, unique_phrases=0,
                tortured_score=0.0, matches=[], is_suspicious=False,
            )

        # Rust fast path
        if self._rust_detector is not None:
            return self._from_rust_result(self._rust_detector.scan_abstract(abstract))

        report = self.scan(abstract)

        # Boost score for abstracts (shorter text → each match matters more)
        if report.match_count > 0:
            boosted = min(100.0, report.tortured_score * 1.2)
            report.tortured_score = boosted
            report.is_suspicious = boosted >= 40.0

        return report

    @staticmethod
    def _from_rust_result(d: dict) -> TorturedPhraseReport:
        """Convert Rust detector dict output to TorturedPhraseReport."""
        matches = [
            TorturedPhraseMatch(
                tortured_phrase=m["tortured"],
                standard_term=m["standard"],
                position=m["position"],
                context=m["context"],
            )
            for m in d.get("matches", [])
        ]
        return TorturedPhraseReport(
            text_length=d["text_length"],
            match_count=d["match_count"],
            unique_phrases=d["unique_phrases"],
            tortured_score=d["tortured_score"],
            matches=matches,
            is_suspicious=d["is_suspicious"],
        )

    @staticmethod
    def _compute_score(match_count: int, unique_count: int,
                       text_length: int) -> float:
        """Compute tortured phrase score (0-100).

        Scoring rationale: even one tortured phrase in an academic paper
        is extremely unusual and warrants investigation. The score ramps
        up quickly:

            0 matches → 0
            1 match   → 30
            2 matches → 55
            3 matches → 70
            4 matches → 80
            5 matches → 90
            6+ matches → 92-100

        Unique phrase count provides a secondary boost: if all matches
        are distinct phrases, the text is more suspicious than if the
        same phrase repeats.
        """
        if match_count == 0:
            return 0.0

        # Base score from match count
        base_scores = {1: 30.0, 2: 55.0, 3: 70.0, 4: 80.0, 5: 90.0}
        if match_count in base_scores:
            base = base_scores[match_count]
        elif match_count > 5:
            # Asymptotic approach to 100
            base = 90.0 + min(10.0, (match_count - 5) * 2.0)
        else:
            base = 0.0

        # Diversity bonus: if all matches are unique phrases, boost slightly
        if match_count > 1 and unique_count > 1:
            diversity = unique_count / match_count
            base = min(100.0, base + diversity * 3.0)

        return min(100.0, base)

    @property
    def phrase_count(self) -> int:
        """Number of tortured phrases in the dictionary."""
        return len(self._phrases)
