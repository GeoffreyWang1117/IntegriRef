"""Hand-computed checks on the retrieval metric accumulator.

Every number in this file was worked out by hand before the code ran. The
arithmetic is small but easy to get subtly wrong -- the ideal DCG denominator,
what bounds the precision ceiling, and which pairs a macro average is defined
over -- and a wrong metric is worse than a missing one, because it still looks
like a measurement.
"""

from __future__ import annotations

import math

from deepcite.eval.citation_integrity import _Retrieval, _dcg
from deepcite.retrieve import Passage


def _got(indices):
    return [Passage(file="doc1", line_start=i, line_end=i, text=f"s{i}",
                    rank_score=1.0 / (n + 1), raw=f"s{i}")
            for n, i in enumerate(indices)]


def test_dcg_is_binary_and_log2_discounted():
    assert _dcg([]) == 0.0
    assert _dcg([True]) == 1.0                       # 1/log2(2)
    assert _dcg([False, True]) == 1 / math.log2(3)
    assert _dcg([True, False, True]) == 1 + 1 / math.log2(4)


def test_two_pairs_one_of_them_empty():
    acc = _Retrieval(5)
    # Pair A: gold {1,3}, returned [1,0,3,4,2] -> flags T F T F F
    acc.add(_got([1, 0, 3, 4, 2]), {1, 3})
    # Pair B: gold {7}, returned nothing at all
    acc.add(_got([]), {7})
    r = acc.report()

    assert r["hit@5"] == 0.5 and r["hit/pairs"] == "1/2"
    assert r["gold_sentences"] == 3 and r["passages_shown"] == 5
    assert r["evidence_recall@5_micro"] == 0.6667          # 2/3
    assert r["evidence_recall@5_macro"] == 0.5             # mean(1.0, 0.0)
    assert r["evidence_precision@5_micro"] == 0.4          # 2/5
    # Defined on the one pair that returned something, and says so.
    assert r["evidence_precision@5_macro"] == 0.4
    assert r["macro_precision_defined_on"] == "1/2"
    # Ceiling is min(|R_k|,|G|) summed: min(5,2) + min(0,1) = 2, over 5 shown.
    assert r["evidence_precision@5_ceiling"] == 0.4
    assert r["mrr@5"] == 0.5                               # mean(1/1, 0)
    # ideal DCG uses min(k,|G|) hits: pair A ideal = 1 + 1/log2(3)
    ndcg_a = (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    assert r["ndcg@5"] == round((ndcg_a + 0.0) / 2, 4)
    assert r["pairs_with_no_result"] == "1/2" and r["coverage"] == 0.5
    assert r["median_rank_of_first_gold_hit"] == 1
    assert r["duplicate_index_pairs"] == 0


def test_empty_return_does_not_inflate_precision():
    """A pair that returns nothing must not be averaged in as precision 1.0,
    nor as 0.0 -- it has no precision, and the denominator is published."""
    acc = _Retrieval(5)
    acc.add(_got([4]), {4})           # perfect, 1 shown 1 gold
    acc.add(_got([]), {9})            # nothing shown
    r = acc.report()
    assert r["evidence_precision@5_macro"] == 1.0
    assert r["macro_precision_defined_on"] == "1/2"
    assert r["evidence_precision@5_micro"] == 1.0          # 1/1, not 1/2
    assert r["evidence_recall@5_macro"] == 0.5             # recall IS defined: 0


def test_ceiling_is_bounded_by_what_was_shown_not_only_by_k():
    """With one gold sentence and five slots the ceiling is 1/5; with two
    returned passages it is 1/2. k alone is the wrong bound."""
    acc = _Retrieval(5)
    acc.add(_got([0, 1, 2, 3, 4]), {2})
    assert acc.report()["evidence_precision@5_ceiling"] == 0.2
    acc2 = _Retrieval(5)
    acc2.add(_got([0, 1]), {1})
    assert acc2.report()["evidence_precision@5_ceiling"] == 0.5


def test_by_k_curve_is_a_prefix_of_one_ranking():
    acc = _Retrieval(5)
    acc.add(_got([1, 0, 3, 4, 2]), {1, 3})     # flags T F T F F
    by_k = acc.report()["by_k"]
    assert by_k["1"]["evidence_precision_micro"] == 1.0        # 1/1
    assert by_k["2"]["evidence_precision_micro"] == 0.5        # 1/2
    assert by_k["3"]["evidence_precision_micro"] == 0.6667     # 2/3
    assert by_k["5"]["evidence_precision_micro"] == 0.4        # 2/5
    assert by_k["1"]["evidence_recall_micro"] == 0.5           # 1 of 2 gold
    assert by_k["3"]["evidence_recall_micro"] == 1.0
    assert by_k["1"]["hit"] == 1.0 and by_k["2"]["hit"] == 1.0
    # Ceiling tightens as k grows past the gold count: min(1,2) then min(3,2).
    assert by_k["1"]["evidence_precision_ceiling"] == 1.0
    assert by_k["3"]["evidence_precision_ceiling"] == 0.6667


def test_duplicate_indices_are_counted_and_scored_once():
    """_dedupe should make this unreachable. If it ever happens, one sentence
    scored twice would inflate precision, so it is counted, not assumed away."""
    acc = _Retrieval(5)
    acc.add(_got([2, 2, 5]), {2})
    r = acc.report()
    assert r["duplicate_index_pairs"] == 1
    assert r["passages_shown"] == 2                        # deduped to [2, 5]
    assert r["evidence_precision@5_micro"] == 0.5          # 1/2, not 2/3
    assert r["evidence_recall@5_micro"] == 1.0
