"""Does the search find the article a question was written from? (Ley questions carry their source chunk.)

Store recall compares a store against exact search; this measures whether exact search itself returns the
right law, which is what the audience cares about.
"""
import numpy as np

from fv.vec import exact_topk


def article_of(chunk_id: str) -> str:
    """'ley#<norm>-<article>-<piece>' -> 'ley#<norm>-<article>' (long articles are split into pieces)."""
    return chunk_id.rsplit("-", 1)[0]


def source_hits(qv: np.ndarray, dv: np.ndarray, ids: list[str], sources: list[str], ks=(1, 3, 10)) -> dict:
    kmax = max(ks)
    hits = {f"{u}@{k}": 0 for u in ("chunk", "article") for k in ks}
    rr = 0.0
    for q, src in zip(qv, sources):
        top = [ids[i] for i in exact_topk(q, dv, kmax)]
        rank = top.index(src) + 1 if src in top else None
        rr += 1 / rank if rank else 0.0
        arts = [article_of(t) for t in top]
        for k in ks:
            hits[f"chunk@{k}"] += src in top[:k]
            hits[f"article@{k}"] += article_of(src) in arts[:k]
    n = len(sources)
    return {"n": n, **{k: v / n for k, v in hits.items()}, "mrr_chunk": rr / n}
