import math


def percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "p50": float("nan"), "p95": float("nan")}
    return {"n": len(values), "p50": percentile(values, 50), "p95": round(percentile(values, 95), 6)}
