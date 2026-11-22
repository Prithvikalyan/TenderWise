from __future__ import annotations

import math
from typing import Iterable


def ranking_metrics(retrieved: list[str], relevant: Iterable[str], k: int = 5) -> dict[str, float]:
    """Calculate binary relevance Recall@k, Precision@k, MRR and nDCG@k."""
    if k < 1:
        raise ValueError("k must be at least 1")
    relevant_set = set(relevant)
    if not relevant_set:
        return {f"recall@{k}": 0.0, f"precision@{k}": 0.0, "mrr": 0.0, f"ndcg@{k}": 0.0}
    top = retrieved[:k]
    hits = [1 if item in relevant_set else 0 for item in top]
    hit_count = sum(hits)
    first_relevant = next((index for index, hit in enumerate(hits, start=1) if hit), None)
    mrr = 1.0 / first_relevant if first_relevant else 0.0
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, start=1))
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return {
        f"recall@{k}": hit_count / len(relevant_set),
        f"precision@{k}": hit_count / k,
        "mrr": mrr,
        f"ndcg@{k}": dcg / idcg if idcg else 0.0,
    }
