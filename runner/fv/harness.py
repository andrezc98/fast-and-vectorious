import math
import time
from collections import defaultdict
from dataclasses import dataclass

from fv.config import K
from fv.stats import summary
from fv.vec import recall_at_k


@dataclass
class Row:
    store: str
    corpus: str
    model: str
    case: str
    rep: int
    qi: int
    latency_ms: float
    recall: float
    ok: bool
    error: str
    extra: str


def _once(store, case, vec, attr, k, docs_get):
    if case == "join":
        if hasattr(store, "query_join"):
            rows, extra = store.query_join(vec, k)
            if not rows:
                raise RuntimeError("join returned no rows")
            return [r[0] for r in rows], extra
        hits, extra = store.query(vec, k, None, True)
        doc_ids = [d for _, d in hits]
        if any(d is None for d in doc_ids):
            raise RuntimeError("join: a hit came back without doc_id")
        if len(docs_get(doc_ids)) < len(set(doc_ids)):
            raise RuntimeError("join: docs_get returned fewer docs than distinct doc_ids")
        return [h for h, _ in hits], extra
    hits, extra = store.query(vec, k, attr if case == "filter" else None, False)
    return [h for h, _ in hits], extra


def run_case(store, corpus, model, case, qvecs, qattrs, truth, reps, sink, docs_get, k=K, warmup=20):
    """One client, one query at a time: latency is per request, not throughput (said on stage)."""
    for i in range(min(warmup, len(qvecs))):
        try:
            _once(store, case, qvecs[i], qattrs[i], k, docs_get)
        except Exception:
            pass
    for rep in range(1, reps + 1):
        for qi, (vec, attr) in enumerate(zip(qvecs, qattrs)):
            t0 = time.perf_counter()
            try:
                ids, extra = _once(store, case, vec, attr, k, docs_get)
                ms = (time.perf_counter() - t0) * 1000
                tk = "plain" if case == "join" else case  # the join returns the same top-k as plain
                rec = recall_at_k(ids, truth[tk][qi], k) if tk in truth else float("nan")
                sink(Row(store.name, corpus, model, case, rep, qi, ms, rec, True, "", extra))
            except Exception as e:  # recorded, never silently fast
                sink(Row(store.name, corpus, model, case, rep, qi, (time.perf_counter() - t0) * 1000,
                         float("nan"), False, f"{type(e).__name__}: {e}"[:300], ""))


def summarize(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["store"], r["corpus"], r["model"], r["case"])].append(r)
    out = []
    for (store, corpus, model, case), rs in sorted(groups.items()):
        ok = [r for r in rs if r["ok"] == "True"]
        lat = summary([float(r["latency_ms"]) for r in ok])
        recalls = [float(r["recall"]) for r in ok if not math.isnan(float(r["recall"]))]
        out.append({"store": store, "corpus": corpus, "model": model, "case": case, "n_ok": len(ok),
                    "errors": len(rs) - len(ok), "p50_ms": lat["p50"], "p95_ms": lat["p95"],
                    "recall": sum(recalls) / len(recalls) if recalls else float("nan")})
    return out
