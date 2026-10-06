"""The cache-file contract: normalization, hashing, identifiers, exit codes.

This module is the single source of truth for everything the self-review reader
also has to compute. It is deliberately dependency-free (stdlib only) so both
sides can implement it without pulling the registry stack.

Cross-validated against the hand-written fixture at
``~/Tools/self-review/tests/fixtures/citations/`` on 2026-10-05: 5 test vectors,
7 records' context hashes, record ids and byte offsets all reproduce bit-for-bit.

Spec: docs/DEEPCITE_SPEC_v1.1.md §5.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

FORMAT_VERSION = 1
TOOL_NAME = "integriref-deepcite"
TOOL_VERSION = "0.2.0"

# Exit codes (§4). 1 stays reserved for unexpected failures.
EXIT_OK = 0
EXIT_NO_BIB = 2
EXIT_BIBGUARD_TOO_OLD = 3
EXIT_ALL_RECORDS_FAILED = 4
EXIT_QUOTE_REJECTED = 5

MIN_BIBGUARD = (0, 5, 0)

# Covers natbib (\citet, \citep, \citealp, \citeauthor, \citeyear) and biblatex
# (\parencite, \textcite, \autocite) via the [a-zA-Z]* tail, plus capitalized
# forms. Two optional arguments because natbib allows \citep[e.g.,][]{key}.
#
# Consequence worth stating: a \citet <-> \citep edit does NOT change the hash,
# so reformatting citations never invalidates a cached record. That is intended.
CITE_RE = re.compile(r"\\[Cc]ite[a-zA-Z]*\*?(\[[^\]]*\]){0,2}\{[^}]*\}")

# Captures the keys so a citing sentence can be attributed to bib entries.
CITE_KEYS_RE = re.compile(r"\\[Cc]ite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}")


def strip_comments(text: str) -> str:
    """Remove each unescaped ``%`` and the rest of its line, keeping the newline.

    "Unescaped" means preceded by an even number of consecutive backslashes, so
    ``\\%`` is a literal percent (kept) while ``\\\\%`` is a line break followed
    by a comment (stripped). The newline survives, so line numbers never shift.

    Known limitations, agreed with the reader side and not treated as defects:
      * A ``%`` inside ``\\verb|%|`` or a verbatim environment is a literal and
        is wrongly stripped here. Citing sentences live in prose, so we accept it.
      * TeX treats an end-of-line ``%`` as a line continuation that swallows the
        newline, so ``met%\\nric`` renders as "metric" while this yields
        "met ric". Both sides agree, so it is not drift.
    """
    out = []
    for line in text.split("\n"):
        i, cut = 0, None
        while i < len(line):
            if line[i] == "\\":
                i += 2          # skip the escaped character, whatever it is
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def norm(s: str) -> str:
    """The seven normalization steps of §5, in order.

    Note step 3 deletes the whole \\cite{...} call but does not unescape ``\\%``
    into ``%`` -- the normalized text keeps the backslash. Both implementations
    agree on this; do not "fix" it, the hash depends on it.
    """
    s = unicodedata.normalize("NFKC", s)   # 1
    s = strip_comments(s)                  # 2 (defensive; callers strip earlier)
    s = CITE_RE.sub("", s)                 # 3
    s = s.replace("~", " ")                # 4
    s = re.sub(r"\s+", " ", s)             # 5
    s = s.strip()                          # 6
    return s.lower()                       # 7


def context_sha256(sentence: str) -> str:
    return hashlib.sha256(norm(sentence).encode("utf-8")).hexdigest()


def record_id(bib_key: str, cited_id: str | None, citing_file: str,
              ctx_sha: str) -> str:
    """16 hex chars of sha256 over the four fields joined by '|'.

    ``cited_id`` of None contributes the empty string. The citing file path is
    included because the same sentence citing the same key can appear in two
    files, and those are separate occurrences.
    """
    raw = f"{bib_key}|{cited_id or ''}|{citing_file}|{ctx_sha}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def file_sha256(raw_bytes: bytes) -> str:
    """Hash of the file's RAW bytes, before comments are stripped.

    Raw bytes so the value stays checkable with plain ``sha256sum``. A
    comment-only edit therefore marks the file changed, which costs the reader
    one offset retry plus at worst a substring search -- never a false STALE.
    """
    return hashlib.sha256(raw_bytes).hexdigest()


def byte_span(stripped_text: str, char_start: int, char_end: int) -> tuple[int, int]:
    """Convert a character span in the comment-stripped text to UTF-8 byte offsets.

    The cache stores BYTE offsets (fields ``byte_start``/``byte_end``). Readers
    must slice ``stripped.encode("utf-8")[a:b]`` and must NOT slice the str:
    on a line containing non-ASCII the two differ silently.
    """
    return (len(stripped_text[:char_start].encode("utf-8")),
            len(stripped_text[:char_end].encode("utf-8")))


def slice_bytes(stripped_text: str, byte_start: int, byte_end: int) -> str:
    return stripped_text.encode("utf-8")[byte_start:byte_end].decode("utf-8", "replace")


def parse_version(s: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


# --- Staleness classification (§5) -----------------------------------------
#
# Shipped here, in the contract module, because both sides must agree on it.
# The READER decides staleness; the writer never sets a flag it cannot know.

FRESH = "FRESH"    # sentence is byte-identical at its stored offset
MOVED = "MOVED"    # sentence found elsewhere in the file; record still valid
STALE = "STALE"    # sentence no longer present; record must not be trusted
UNSCANNED = "UNSCANNED"   # citing file absent from files_sha256


def classify_staleness(stored_file_sha: str | None, current_raw: bytes,
                       stored_ctx_sha: str, byte_start: int, byte_end: int,
                       stored_sentence: str) -> str:
    """Decide a record's freshness from the current file alone.

    Order matters and is cheapest-first: if the file is untouched the record is
    FRESH without any search; if it changed, retry the stored offset before
    falling back to a substring scan of the whole file.
    """
    if stored_file_sha is None:
        return UNSCANNED
    stripped = strip_comments(current_raw.decode("utf-8", "replace"))
    if file_sha256(current_raw) == stored_file_sha:
        return FRESH
    # File changed elsewhere -- the sentence may still sit at its old offset.
    if context_sha256(slice_bytes(stripped, byte_start, byte_end)) == stored_ctx_sha:
        return FRESH
    # Last resort: has it simply moved?
    if norm(stored_sentence) and norm(stored_sentence) in norm(stripped):
        return MOVED
    return STALE
