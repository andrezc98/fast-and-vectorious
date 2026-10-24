import numpy as np


def normalize(m) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return (m / n).astype(np.float32)


def truncate_normalize(m, dim: int) -> np.ndarray:
    """Matryoshka truncation: keep the first `dim` values, then back to unit length (api-notes §5, Qwen3)."""
    return normalize(np.asarray(m, dtype=np.float32)[:, :dim])


def exact_topk(q: np.ndarray, base: np.ndarray, k: int, allowed: np.ndarray | None = None) -> np.ndarray:
    """Brute-force cosine top-k on normalized vectors: the ground truth every store is compared against."""
    scores = base @ q
    k = min(k, len(scores))
    if allowed is not None:
        scores = np.where(allowed, scores, -np.inf)
        k = min(k, int(allowed.sum()))
    if k == 0:
        return np.array([], dtype=np.int64)
    idx = np.argpartition(-scores, k - 1)[:k]
    return idx[np.argsort(-scores[idx])]


def recall_at_k(found: list[str], truth: list[str], k: int) -> float:
    want = truth[:k]
    if not want:
        return float("nan")
    return len(set(found[:k]) & set(want)) / len(want)


def mrr(found: list[str], target: str, k: int) -> float:
    for rank, x in enumerate(found[:k], start=1):
        if x == target:
            return 1 / rank
    return 0.0
