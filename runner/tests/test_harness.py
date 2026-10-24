import math

import numpy as np

from fv.harness import Row, run_case, summarize


class FakeStore:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def query(self, vec, k, attr, need_doc):
        self.calls += 1
        if self.calls == 15:  # warm-up uses calls 1-10
            raise RuntimeError("ThrottlingException")
        return [("a", "d1"), ("b", "d2")], ""


def test_run_case_records_errors_and_continues():
    rows = []
    q = np.zeros((10, 2), dtype=np.float32)
    run_case(FakeStore(), "so", "m", "plain", q, ["x"] * 10, {"plain": [["a", "c"]] * 10}, 1, rows.append, None, warmup=20)
    assert len(rows) == 10
    bad = [r for r in rows if not r.ok]
    assert len(bad) == 1 and "Throttling" in bad[0].error
    assert all(r.recall == 0.5 for r in rows if r.ok)


def test_join_uses_docs_get_and_skips_recall():
    got = []
    rows = []
    run_case(FakeStore(), "so", "m", "join", np.zeros((1, 2), dtype=np.float32), ["x"], {}, 1, rows.append,
             lambda ids: got.append(ids) or [{}, {}], warmup=0)
    assert got == [["d1", "d2"]] and math.isnan(rows[0].recall)


def test_summarize_excludes_failed_from_latency():
    rows = [Row("s", "so", "m", "plain", 1, i, float(i), 1.0, True, "", "").__dict__ for i in range(1, 5)]
    rows.append(Row("s", "so", "m", "plain", 1, 9, 9999.0, float("nan"), False, "boom", "").__dict__)
    rows = [{k: str(v) for k, v in r.items()} for r in rows]  # as read back from CSV
    s = summarize(rows)[0]
    assert s["n_ok"] == 4 and s["errors"] == 1 and s["p50_ms"] == 2.5 and s["recall"] == 1.0


def _join(store, docs_get, truth):
    rows = []
    run_case(store, "so", "m", "join", np.zeros((1, 2), dtype=np.float32), ["x"], truth, 1, rows.append, docs_get, warmup=0)
    return rows[0]


def test_join_none_doc_id_is_an_error():
    class S(FakeStore):
        def query(self, *a):
            return [("a", "d1"), ("b", None)], ""
    r = _join(S(), lambda ids: [{}, {}], {})
    assert not r.ok and "doc_id" in r.error


def test_join_short_docs_get_is_an_error():
    r = _join(FakeStore(), lambda ids: [{}], {})
    assert not r.ok and "docs_get" in r.error


def test_join_recall_scored_against_plain_truth():
    r = _join(FakeStore(), lambda ids: [{}, {}], {"plain": [["a", "zzz"]]})
    assert r.ok and r.recall == 0.5


def test_aurora_join_with_no_rows_is_an_error():
    class A:
        name = "aurora"

        def query_join(self, vec, k):
            return [], ""
    r = _join(A(), None, {})
    assert not r.ok and "no rows" in r.error
