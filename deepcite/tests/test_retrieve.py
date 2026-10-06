"""Retrieval tests: what may and may not become a passage.

The comment case is the important one. A retrieved LaTeX comment is text the cited
authors deleted; offering it as evidence can support a claim the paper no longer
makes, which is the exact failure deepcite exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from deepcite import retrieve as V


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_commented_out_text_never_becomes_a_passage(tmp_path):
    src = _write(tmp_path, "main.tex", "\n".join([
        "\\section{Results}",
        "% The variance of experiential gradients dominates the batch term.",
        "We report multi-hop accuracy as the primary evaluation metric here.",
        "Each run is repeated five times and we report the mean and spread.",
    ]))
    got = V.rank(["variance", "experiential", "gradients"], [src], tmp_path,
                 claim_type="metric")
    blob = " ".join(p.text.lower() for p in got) + " ".join(p.raw for p in got)
    assert "experiential" not in blob, "a LaTeX comment was surfaced as evidence"


def test_live_text_on_the_same_line_as_a_comment_survives(tmp_path):
    src = _write(tmp_path, "main.tex", "\n".join([
        "\\section{Setup}",
        "We evaluate on the disjunction over paraphrases. % TODO cite the appendix",
        "The split follows the leave-one-out protocol of the original release.",
        "Numbers are averaged over five seeds for every configuration reported.",
    ]))
    got = V.rank(["disjunction", "paraphrases"], [src], tmp_path,
                 claim_type="metric")
    assert got, "the live half of a partially-commented line was dropped"
    assert "todo" not in " ".join(p.text.lower() for p in got)


def test_escaped_percent_is_not_treated_as_a_comment(tmp_path):
    """A results line reading 95\\% accuracy must stay intact -- the number is
    usually the whole claim."""
    src = _write(tmp_path, "main.tex", "\n".join([
        "\\section{Results}",
        "The model reaches 95\\% accuracy on two-hop chains in our evaluation.",
        "Baselines reach between 60 and 70 percent under the same conditions.",
        "We attribute the gap to the retrieval stage rather than the reader.",
    ]))
    got = V.rank(["accuracy", "two-hop", "chains"], [src], tmp_path,
                 claim_type="metric")
    assert got
    assert "95" in " ".join(p.text for p in got)


def test_line_numbers_still_point_into_the_real_file(tmp_path):
    """Comment stripping keeps newlines, so a locator stays usable."""
    lines = ["\\section{Intro}", "% dropped claim about variance", "",
             "The protocol samples one negative per positive in every fold.",
             "Filler to make the window long enough to be scored at all here."]
    src = _write(tmp_path, "main.tex", "\n".join(lines))
    got = V.rank(["protocol", "negative", "positive", "fold"], [src], tmp_path,
                 claim_type="protocol")
    assert got
    p = got[0]
    assert 1 <= p.line_start <= len(lines)
    real = src.read_text(encoding="utf-8").split("\n")
    window = " ".join(real[p.line_start - 1:p.line_end])
    assert "protocol" in window.lower(), \
        f"locator {p.line_start}-{p.line_end} does not contain the passage"


def test_rank_units_scores_the_same_way_as_rank(tmp_path):
    """The two entry points must stay comparable; eval reports mix them."""
    sentences = ["We report multi-hop accuracy as the primary metric.",
                 "The dataset contains three paraphrases per question.",
                 "Training uses a batch size of thirty-two throughout."]
    src = _write(tmp_path, "main.tex", "\n".join(sentences))
    units = [("main.tex", i + 1, i + 1, s) for i, s in enumerate(sentences)]
    a = V.rank(["multi-hop", "accuracy", "metric"], [src], tmp_path,
               claim_type="metric")
    b = V.rank_units(["multi-hop", "accuracy", "metric"], units,
                     claim_type="metric")
    assert a and b
    assert "accuracy" in a[0].text.lower() and "accuracy" in b[0].text.lower()


def test_passage_budget_is_enforced_in_the_tool(tmp_path):
    body = "\n".join(f"Section {i} reports accuracy for configuration {i} here."
                     for i in range(200))
    src = _write(tmp_path, "main.tex", body)
    got = V.rank(["accuracy", "configuration", "reports"], [src], tmp_path)
    assert len(got) <= V.MAX_PASSAGES
    for p in got:
        assert len(p.text.split()) <= V.MAX_WORDS + 1
