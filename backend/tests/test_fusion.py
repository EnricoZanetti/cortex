"""Reciprocal Rank Fusion."""

from __future__ import annotations

import uuid

from kb.retrieval.fusion import normalize_scores, reciprocal_rank_fusion

A, B, C, D = (uuid.uuid4() for _ in range(4))


def test_agreement_between_retrievers_beats_dominance_in_one() -> None:
    """The point of fusion.

    A is ranked #1 by the dense retriever but is absent from the keyword list; B and C
    appear in both. Both must outrank A, even though A leads a list outright -- that is
    what stops one retriever's confident mistake reaching the top of the results.
    """
    fused = reciprocal_rank_fusion({"dense": [A, B, C], "keyword": [C, B, D]}, k=60)
    ranking = [hit.chunk_id for hit in fused]
    assert set(ranking[:2]) == {B, C}
    assert ranking.index(B) < ranking.index(A)
    assert ranking.index(C) < ranking.index(A)


def test_provenance_is_reported() -> None:
    fused = reciprocal_rank_fusion({"dense": [A], "keyword": [B]}, k=60)
    by_id = {hit.chunk_id: hit for hit in fused}
    assert by_id[A].retrievers == ["semantic"]
    assert by_id[B].retrievers == ["keyword"]


def test_single_retriever_preserves_its_order() -> None:
    fused = reciprocal_rank_fusion({"keyword": [C, A, B]}, k=60)
    assert [hit.chunk_id for hit in fused] == [C, A, B]


def test_ordering_is_deterministic_for_tied_scores() -> None:
    first = reciprocal_rank_fusion({"dense": [A, B], "keyword": [B, A]}, k=60)
    second = reciprocal_rank_fusion({"dense": [A, B], "keyword": [B, A]}, k=60)
    assert [hit.chunk_id for hit in first] == [hit.chunk_id for hit in second]


def test_duplicate_ids_within_one_list_are_counted_once() -> None:
    fused = reciprocal_rank_fusion({"dense": [A, A, B]}, k=60)
    assert [hit.chunk_id for hit in fused] == [A, B]


def test_empty_input_yields_no_hits() -> None:
    assert reciprocal_rank_fusion({"dense": [], "keyword": []}, k=60) == []


def test_normalisation_puts_the_best_hit_at_one() -> None:
    fused = reciprocal_rank_fusion({"dense": [A, B, C], "keyword": [A, C, B]}, k=60)
    scored = normalize_scores(fused)
    assert scored[0][1] == 1.0
    assert all(0.0 <= relevance <= 1.0 for _, relevance in scored)
    # Scores must be non-increasing, matching the returned order.
    assert scored == sorted(scored, key=lambda pair: -pair[1])
