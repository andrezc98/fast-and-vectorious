import math

import numpy as np

from fv.vec import exact_topk, mrr, normalize, recall_at_k, truncate_normalize


def test_normalize_unit_rows_and_zero_row():
    m = normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert m.dtype == np.float32
    assert np.allclose(m[0], [0.6, 0.8]) and np.allclose(m[1], [0, 0])


def test_truncate_normalize_cuts_then_renormalizes():
    m = truncate_normalize(np.array([[3.0, 4.0, 12.0]]), 2)
    assert m.shape == (1, 2) and np.allclose(np.linalg.norm(m[0]), 1.0)


def test_exact_topk_orders_by_cosine():
    base = normalize(np.array([[1, 0], [0.9, 0.1], [0, 1], [-1, 0]], dtype=np.float32))
    q = normalize(np.array([[1, 0]], dtype=np.float32))[0]
    assert list(exact_topk(q, base, 2)) == [0, 1]


def test_exact_topk_respects_filter_with_fewer_than_k_allowed():
    base = normalize(np.eye(4, dtype=np.float32))
    allowed = np.array([False, False, True, False])
    assert list(exact_topk(base[0], base, 10, allowed)) == [2]


def test_recall_uses_truth_size_when_smaller_than_k():
    assert recall_at_k(["a", "x"], ["a"], 10) == 1.0
    assert recall_at_k(["a", "b", "c"], ["a", "b", "z", "y"], 4) == 0.5
    assert math.isnan(recall_at_k(["a"], [], 10))


def test_mrr():
    assert mrr(["x", "t", "y"], "t", 10) == 0.5
    assert mrr(["x"], "t", 10) == 0.0
