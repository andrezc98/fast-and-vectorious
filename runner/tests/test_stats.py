import math

from fv.stats import percentile, summary


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 95) == 5


def test_summary_empty_is_nan():
    s = summary([])
    assert s["n"] == 0 and math.isnan(s["p50"]) and math.isnan(s["p95"])


def test_summary():
    assert summary([1.0, 2.0, 3.0]) == {"n": 3, "p50": 2.0, "p95": 2.9}
