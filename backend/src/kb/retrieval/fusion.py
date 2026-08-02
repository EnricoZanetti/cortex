"""Reciprocal Rank Fusion.

Dense similarity scores (cosine, 0..1) and lexical scores (``ts_rank_cd``, unbounded and
corpus-dependent) are not comparable, so any weighted-sum blend needs calibration that
drifts as the corpus grows. RRF sidesteps this entirely: it only uses each result's
*rank* within its own list, so the two retrievers can keep their own score semantics.

    score(d) = sum over lists L of  1 / (k + rank_L(d))

``k`` (default 60, the value from the original Cormack et al. paper) damps the influence
of the very top ranks just enough that a document has to do well in *both* lists to beat
one that dominates a single list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FusedHit:
    """A chunk id with its fused score and per-retriever provenance."""

    chunk_id: uuid.UUID
    score: float
    dense_rank: int | None
    keyword_rank: int | None

    @property
    def retrievers(self) -> list[str]:
        """Which retrievers surfaced this chunk -- surfaced in tool output for debuggability."""
        found = []
        if self.dense_rank is not None:
            found.append("semantic")
        if self.keyword_rank is not None:
            found.append("keyword")
        return found


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[uuid.UUID]],
    *,
    k: int = 60,
) -> list[FusedHit]:
    """Fuse named ranked lists into one ranking, best first.

    ``ranked_lists`` maps a retriever name (``"dense"`` / ``"keyword"``) to its result
    ids in rank order. Ties are broken by best individual rank, then by id, so the
    ordering is fully deterministic -- which matters for reproducible tool output.
    """
    scores: dict[uuid.UUID, float] = {}
    dense_ranks: dict[uuid.UUID, int] = {}
    keyword_ranks: dict[uuid.UUID, int] = {}

    for name, ids in ranked_lists.items():
        target = dense_ranks if name == "dense" else keyword_ranks
        for rank, chunk_id in enumerate(ids, start=1):
            if chunk_id in target:
                continue
            target[chunk_id] = rank
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

    hits = [
        FusedHit(
            chunk_id=chunk_id,
            score=score,
            dense_rank=dense_ranks.get(chunk_id),
            keyword_rank=keyword_ranks.get(chunk_id),
        )
        for chunk_id, score in scores.items()
    ]
    hits.sort(
        key=lambda hit: (
            -hit.score,
            min(rank for rank in (hit.dense_rank, hit.keyword_rank) if rank is not None),
            str(hit.chunk_id),
        )
    )
    return hits


def normalize_scores(hits: list[FusedHit]) -> list[tuple[FusedHit, float]]:
    """Map raw RRF scores onto 0..1 relative to the best hit in this result set.

    Exposed to agents as ``relevance``: an absolute RRF value is meaningless to an LLM,
    whereas "1.0 is the best match here, 0.4 is noticeably weaker" supports the
    ``min_relevance`` parameter and lets the model judge whether to trust the result.
    """
    if not hits:
        return []
    best = hits[0].score or 1.0
    return [(hit, round(min(hit.score / best, 1.0), 4)) for hit in hits]
