"""Human-readable views of the cache: the worklist report and the judging packet.

The JSON cache is the contract; these are views of it and are regenerated, never
read back. Order is the order a reader should work in: an opinion that found a
mismatch first, then claims whose terms the cited work never mentions, then
candidate evidence nobody has judged yet.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import cache as K
from . import contract as C
from . import fetch as F
from .retrieve import display_text

_STATUS_NOTE = {
    K.CLAIM_ABSENT_FROM_ARTIFACT: "the cited work's text never mentions what the sentence "
                                  "attributes to it -- read these first",
    K.CANDIDATE_EVIDENCE: "passages found; nobody has compared them with the sentence yet",
    K.SOURCE_UNAVAILABLE: "a lookup or download source refused (rate limit, timeout); "
                          "re-run later -- not evidence about the citation",
    K.UNRESOLVED: "no copy of the cited work found; supply one with --artifact KEY=PATH_OR_URL",
    K.NO_FULLTEXT: "the cited work has no retrievable full text",
    K.WRONG_ARTIFACT_KIND: "the artifact has no searchable text (e.g. a scanned PDF)",
}
_CITE = re.compile(r"\\[Cc]ite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}")


def _math(m: re.Match) -> str:
    """Inline math as readable text: $95\\%$ -> 95%, $4.27$--$5.15\\%$ stays numeric."""
    s = m.group(1).replace("\\%", "%").replace("{,}", ",").replace("\\times", "×")
    s = re.sub(r"\\(?:text|mathrm|mathit|mathbf|operatorname)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+", " ", s)
    return re.sub(r"[{}^_]", "", s).strip()


def _sentence(s: str) -> str:
    s = _CITE.sub(lambda m: "[" + ", ".join(k.strip() for k in m.group(1).split(",")) + "]", s)
    s = re.sub(r"\$([^$]{0,60})\$", _math, s)
    return display_text(s)


def _link(cited_id: str | None) -> str:
    if not cited_id:
        return ""
    if cited_id.startswith("arXiv:"):
        return f"[{cited_id}](https://arxiv.org/abs/{cited_id[6:]})"
    if cited_id.startswith("doi:"):
        return f"[{cited_id}](https://doi.org/{cited_id[4:]})"
    if cited_id.startswith("openreview:"):
        return f"[{cited_id}](https://openreview.net/forum?id={cited_id[11:]})"
    return cited_id


def _bucket(r: dict) -> tuple[int, str]:
    a = (r.get("llm_opinion") or {}).get("assessment")
    if a == "mismatch":
        return 0, "Judged: mismatch"
    if any(not q["found"] for q in r.get("quotes_checked") or []) and a != "no_mismatch_found":
        return 0, "Quotation not found verbatim in the cited work"
    if r["status"] == K.CLAIM_ABSENT_FROM_ARTIFACT and not r.get("llm_opinion"):
        return 1, K.CLAIM_ABSENT_FROM_ARTIFACT
    if r["status"] == K.CANDIDATE_EVIDENCE and not r.get("llm_opinion"):
        return 2, K.CANDIDATE_EVIDENCE
    if a == "unclear" or (r.get("llm_opinion") and not a):
        return 3, "Judged: unclear"
    if a == "no_mismatch_found":
        return 4, "Judged: no mismatch found in the quoted text"
    return 5, f"Not checked: {r['status']}"


def _entry(r: dict, lines: list[str], full: bool) -> None:
    c = r.get("citing") or {}
    sel = r.get("selection") or {}
    title = r.get("cited_title") or ""
    lines.append(f"### `{r['bib_key']}` {('-- ' + title) if title else ''}")
    meta = [x for x in (_link(r.get("cited_id")),
                        (f"p. {c['page']}" if c.get("page") else f"`{c.get('file')}:{c.get('line')}`"),
                        f"{sel.get('family')}/{sel.get('pattern_id')}, {sel.get('claim_type')}",
                        f"record `{r['record_id']}`") if x]
    lines.append(" · ".join(meta) + "\n")
    lines.append(f"> {_sentence(c.get('sentence', ''))}\n")
    if r.get("error"):
        lines.append(f"- {r['error']}")
    for q in r.get("quotes_checked") or []:
        where = f"found at `{q['file']}:{q['line']}`" if q["found"] else "**NOT FOUND verbatim**"
        lines.append(f"- quotation \"{q['text']}\": {where}")
    op = r.get("llm_opinion")
    if op:
        lines.append(f"- **opinion ({op.get('assessment', 'no assessment')})**: {op.get('text', '')}")
        for q in op.get("quotes") or []:
            lines.append(f"  - \"{q['text']}\" (`{q['file']}:{q['line']}`, verified)")
        if op.get("searched"):
            lines.append(f"  - searched for: {', '.join(op['searched'])} (no passage quoted)")
    if full and r["status"] in (K.CANDIDATE_EVIDENCE, K.CLAIM_ABSENT_FROM_ARTIFACT):
        lines.append(f"- searched for: {', '.join(r.get('search_terms') or [])}")
        for i, ps in enumerate(r.get("passages") or [], 1):
            lines.append(f"  {i}. `{ps['file']}:{ps['line_start']}-{ps['line_end']}` {ps['text']}")
    lines.append("")


def render(data: dict) -> str:
    recs = data.get("records") or []
    paper = data.get("paper") or {}
    lines = [f"# deepcite worklist -- {Path(paper.get('root', '')).name or 'paper'}\n",
             f"{len(recs)} record(s) · {data.get('tool')} {data.get('tool_version')} · "
             f"patterns `{(data.get('selection_regex_sha256') or '')[:8]}` · "
             f"{data.get('generated_at')}\n",
             "A record is one citing sentence that makes a checkable claim about one cited "
             "work. Nothing here is a verdict: there is no SUPPORTED. An opinion is shown "
             "only with quotes that were found verbatim in the cited work.\n"]
    if not recs:
        lines.append("No citing sentence made a checkable claim (protocol, metric, dataset, "
                     "split, number, or finding attributed to a cited work).")
        return "\n".join(lines) + "\n"
    counts: dict[str, int] = {}
    for r in recs:
        counts[_bucket(r)[1]] = counts.get(_bucket(r)[1], 0) + 1
    lines.append("| Bucket | Records |\n|---|---|")
    for name in sorted(counts, key=lambda n: min(_bucket(r)[0] for r in recs
                                                  if _bucket(r)[1] == n)):
        lines.append(f"| {name} | {counts[name]} |")
    lines.append("")
    for order in range(6):
        group = [r for r in recs if _bucket(r)[0] == order]
        if not group:
            continue
        name = _bucket(group[0])[1]
        lines.append(f"## {name} ({len(group)})\n")
        note = _STATUS_NOTE.get(name)
        if order == 5:
            for st in sorted({r["status"] for r in group}):
                lines.append(f"- **{st}**: {_STATUS_NOTE.get(st, '')}")
            lines.append("")
        elif note:
            lines.append(f"_{note}_\n")
        for r in sorted(group, key=lambda r: (r["citing"]["file"], r["citing"]["line"])):
            _entry(r, lines, full=order < 5)
    return "\n".join(lines) + "\n"


def _context(data: dict, rec: dict, width: int = 400) -> str:
    """The text around a citing sentence, for a judge who needs the paragraph."""
    paper = data.get("paper") or {}
    c = rec.get("citing") or {}
    src = Path(paper.get("body_text") or Path(paper.get("root", "")) / c.get("file", ""))
    try:
        raw = src.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""
    text = C.strip_comments(raw) if src.suffix == ".tex" else raw
    b = text.encode("utf-8")
    a0, a1 = c.get("byte_start", 0), c.get("byte_end", 0)
    before = b[max(0, a0 - width):a0].decode("utf-8", "ignore")
    after = b[a1:a1 + width // 2].decode("utf-8", "ignore")
    return _sentence(before) + " **>>** " + _sentence(c.get("sentence", "")) + " **<<** " + _sentence(after)


PACKET_HEAD = """# deepcite judging packet

For each record below, decide whether the cited work's OWN text says what the citing
sentence attributes to it. The passages are candidates ranked by term overlap, not
evidence: open the artifact files and search them yourself, especially for
CLAIM_ABSENT_FROM_ARTIFACT records, where the terms were not found at all.

Write one JSON object per line to `opinions.jsonl`:

    {"record": "<record id>", "assessment": "mismatch|no_mismatch_found|unclear",
     "text": "<what the cited work says vs. what the sentence says, 1-3 sentences>",
     "quotes": [{"text": "<copied verbatim from an artifact file, at least 12 characters>"}]}

- Quotes are checked mechanically against the artifact files (whitespace and LaTeX
  commands are ignored, words are not). One quote that is not there rejects the opinion.
- `mismatch`: the quoted text contradicts the sentence, or the sentence attributes
  something (a number, protocol, metric, finding) the work does not say.
- `no_mismatch_found`: the quoted text says what the sentence says. Not "supported":
  it means the quotes do not contradict it.
- `unclear`: may omit quotes, but then must give `"searched": ["term", ...]`.

Then run: `deepcite annotate --paper <paper dir> --opinions opinions.jsonl`
"""


def packet(data: dict, art_cache: Path, include_judged: bool = False) -> str:
    recs = [r for r in data.get("records") or []
            if r["status"] in (K.CANDIDATE_EVIDENCE, K.CLAIM_ABSENT_FROM_ARTIFACT)
            and (include_judged or not r.get("llm_opinion"))]
    recs.sort(key=lambda r: (r["status"] != K.CLAIM_ABSENT_FROM_ARTIFACT,
                             r["citing"]["file"], r["citing"]["line"]))
    lines = [PACKET_HEAD, f"{len(recs)} record(s) to judge.\n"]
    for r in recs:
        lines.append(f"## record `{r['record_id']}` -- `{r['bib_key']}` ({r['status']})\n")
        if r.get("cited_title"):
            lines.append(f"- cited work: {r['cited_title']} {_link(r.get('cited_id'))}")
        sel = r.get("selection") or {}
        lines.append(f"- claim type: {sel.get('claim_type')} ({sel.get('family')}/{sel.get('pattern_id')})")
        lines.append(f"- citing sentence (`{r['citing']['file']}:{r['citing']['line']}`):\n\n"
                     f"  > {_sentence(r['citing']['sentence'])}\n")
        ctx = _context(data, r)
        if ctx:
            lines.append(f"- in context: {ctx}\n")
        got = F.local_files(r.get("artifact") or {}, r.get("cited_id"), art_cache)
        if got:
            files, root = got
            lines.append(f"- artifact files (search these; root `{root}`):")
            for f in files[:12]:
                try:
                    size = f.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"  - `{f}` ({size // 1024} KB)")
            if len(files) > 12:
                lines.append(f"  - ... {len(files) - 12} more under the root")
        lines.append(f"- searched for: {', '.join(r.get('search_terms') or [])}")
        for i, ps in enumerate(r.get("passages") or [], 1):
            lines.append(f"  {i}. `{ps['file']}:{ps['line_start']}-{ps['line_end']}` {ps['text']}")
        lines.append("")
    return "\n".join(lines) + "\n"
