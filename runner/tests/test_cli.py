import json
import os

import numpy as np

from fv import cli
from fv.vec import normalize


def test_bakeoff_scores_perfect_and_miss():
    pv = normalize(np.eye(3, dtype=np.float32))
    qv = pv[[0, 2]]
    s = cli.bakeoff_scores(qv, pv, ["a", "b", "c"], ["a", "b"], k=1)
    assert s == {"n": 2, "recall_at_10": 0.5, "mrr_at_10": 0.5}


def test_truth_writes_plain_and_filter(tmp_path):
    d = tmp_path / "so"
    (d / "emb" / "m").mkdir(parents=True)
    rows = [{"id": "c0", "text": "", "attr": "py", "doc_id": "d"}, {"id": "c1", "text": "", "attr": "js", "doc_id": "d"}]
    (d / "chunks.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (d / "queries.jsonl").write_text(json.dumps({"qid": "q", "text": "", "attr": "js"}))
    np.save(d / "emb" / "m" / "docs.npy", normalize(np.array([[1, 0], [0.8, 0.2]], dtype=np.float32)))
    np.save(d / "emb" / "m" / "queries.npy", normalize(np.array([[1, 0]], dtype=np.float32)))
    cli.main(["--data", str(tmp_path), "truth", "--corpus", "so", "--model", "m"])
    t = json.loads((d / "emb" / "m" / "truth.json").read_text())
    assert t == {"plain": [["c0", "c1"]], "filter": [["c1"]]}



def test_today_dir_anchored_to_repo_not_cwd(tmp_path, monkeypatch):
    results = tmp_path / "results"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(cli, "RESULTS", results)
    monkeypatch.chdir(elsewhere)
    d = cli._today_dir()
    assert os.path.isdir(d)
    assert os.path.commonpath([os.path.abspath(d), str(results)]) == str(results)
    assert list(elsewhere.iterdir()) == []


def test_load_limit_slices_rows_and_vectors(tmp_path, monkeypatch):
    d = tmp_path / "so"
    (d / "emb" / "m").mkdir(parents=True)
    rows = [{"id": f"c{i}", "text": "", "attr": "py", "doc_id": "d"} for i in range(5)]
    (d / "chunks.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (d / "queries.jsonl").write_text(json.dumps({"qid": "q", "text": "", "attr": "py"}))
    np.save(d / "emb" / "m" / "docs.npy", np.ones((5, 2), dtype=np.float32))
    np.save(d / "emb" / "m" / "queries.npy", np.ones((1, 2), dtype=np.float32))
    seen = {}

    class Stub:
        def load(self, r, v):
            seen["ids"], seen["n"] = [x["id"] for x in r], len(v)

    monkeypatch.setenv("FV_RUNNER", "1")
    monkeypatch.setattr("fv.stores.get_store", lambda *a: Stub())
    cli.main(["--data", str(tmp_path), "load", "--store", "s3v", "--corpus", "so", "--model", "m", "--limit", "2"])
    assert seen == {"ids": ["c0", "c1"], "n": 2}
    cli.main(["--data", str(tmp_path), "load", "--store", "s3v", "--corpus", "so", "--model", "m"])
    assert seen["n"] == 5


def _short_embedder(monkeypatch):
    import fv.embed as emb
    monkeypatch.setenv("FV_RUNNER", "1")
    monkeypatch.setattr(emb, "get_embedder", lambda m: None)
    monkeypatch.setattr(emb, "embed_all", lambda fn, texts, *a: np.ones((max(len(texts) - 1, 0), 2), dtype=np.float32))


def test_embed_row_count_mismatch_raises(tmp_path, monkeypatch):
    import pytest
    d = tmp_path / "so"
    d.mkdir()
    (d / "chunks.jsonl").write_text("\n".join(json.dumps({"id": str(i), "text": "t"}) for i in range(3)))
    (d / "queries.jsonl").write_text(json.dumps({"qid": "q", "text": "t"}))
    _short_embedder(monkeypatch)
    with pytest.raises(ValueError, match="3 inputs but 2"):
        cli.main(["--data", str(tmp_path), "embed", "--corpus", "so", "--model", "m"])


def test_bakeoff_row_count_mismatch_raises(tmp_path, monkeypatch):
    import pytest
    d = tmp_path / "so"
    d.mkdir()
    (d / "bakeoff_pool.jsonl").write_text("\n".join(json.dumps({"id": str(i), "text": "t"}) for i in range(3)))
    (d / "bakeoff_queries.jsonl").write_text(json.dumps({"qid": "q", "text": "t", "target": "0"}))
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    _short_embedder(monkeypatch)
    with pytest.raises(ValueError, match="bakeoff pool"):
        cli.main(["--data", str(tmp_path), "bakeoff", "--models", "m"])


def test_report_without_bench_files_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    cli.main(["report"])
    assert "no bench files in" in capsys.readouterr().out


def test_bench_writes_run_meta(tmp_path, monkeypatch):
    d = tmp_path / "so"
    (d / "emb" / "m").mkdir(parents=True)
    (d / "chunks.jsonl").write_text(json.dumps({"id": "c0", "text": "", "attr": "py", "doc_id": "d"}))
    (d / "queries.jsonl").write_text(json.dumps({"qid": "q", "text": "", "attr": "py"}))
    np.save(d / "emb" / "m" / "docs.npy", np.ones((1, 2), dtype=np.float32))
    np.save(d / "emb" / "m" / "queries.npy", np.ones((1, 2), dtype=np.float32))
    (d / "emb" / "m" / "truth.json").write_text(json.dumps({"plain": [["c0"]], "filter": [["c0"]]}))

    class Stub:
        name = "s3v"

        def query(self, *a):
            return [("c0", "d")], ""

    monkeypatch.setenv("FV_RUNNER", "1")
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    monkeypatch.setattr("fv.stores.get_store", lambda *a: Stub())
    cli.main(["--data", str(tmp_path), "bench", "--store", "s3v", "--corpus", "so", "--model", "m", "--case", "plain", "--reps", "1"])
    [meta] = list((tmp_path / "results").glob("*/run-meta-s3v-so-m-plain.json"))
    m = json.loads(meta.read_text())
    assert {"DIM", "K", "reps", "store", "corpus", "model", "case", "versions", "knobs"} <= set(m)
    assert set(m["versions"]) == {"boto3", "botocore", "opensearch-py", "psycopg", "pgvector", "numpy"}
    assert m["knobs"] == {"aoss_compression": "1x", "s3v_filter_mode": "ENHANCED", "pgvector_iterative_scan": "relaxed_order",
                          "pgvector_hnsw_m": 16, "pgvector_hnsw_ef_construction": 64, "pgvector_hnsw_ef_search": 40}
    assert m["DIM"] == 1024 and m["K"] == 10 and m["reps"] == 1


def test_bench_tag_and_ef_search_override(tmp_path, monkeypatch):
    # 2026-10-09 re-run: warm-up passes and ef_search sweeps must not overwrite the main files, and the
    # run meta must record the ef_search that was actually used.
    d = tmp_path / "so"
    (d / "emb" / "m").mkdir(parents=True)
    (d / "chunks.jsonl").write_text(json.dumps({"id": "c0", "text": "", "attr": "py", "doc_id": "d"}))
    (d / "queries.jsonl").write_text(json.dumps({"qid": "q", "text": "", "attr": "py"}))
    np.save(d / "emb" / "m" / "docs.npy", np.ones((1, 2), dtype=np.float32))
    np.save(d / "emb" / "m" / "queries.npy", np.ones((1, 2), dtype=np.float32))
    (d / "emb" / "m" / "truth.json").write_text(json.dumps({"plain": [["c0"]], "filter": [["c0"]]}))

    class Stub:
        name = "aurora"

        def query(self, *a):
            return [("c0", "d")], ""

    monkeypatch.setenv("FV_RUNNER", "1")
    monkeypatch.setenv("FV_PG_EF_SEARCH", "200")
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    monkeypatch.setattr("fv.stores.get_store", lambda *a: Stub())
    cli.main(["--data", str(tmp_path), "bench", "--store", "aurora", "--corpus", "so", "--model", "m", "--case", "plain",
              "--reps", "1", "--tag", "ef200"])
    [csvf] = list((tmp_path / "results").glob("*/bench-aurora-so-m-plain-ef200.csv"))
    [meta] = list((tmp_path / "results").glob("*/run-meta-aurora-so-m-plain-ef200.json"))
    assert json.loads(meta.read_text())["knobs"]["pgvector_hnsw_ef_search"] == 200


def test_aurora_store_sets_ef_search_from_env(monkeypatch):
    from fv.stores import aurora
    sent = []

    class Conn:
        def execute(self, sql, *a):
            sent.append(sql)
    monkeypatch.setattr(aurora, "_connect", lambda: Conn())
    monkeypatch.setattr(aurora, "register_vector", lambda c: None)
    monkeypatch.setenv("FV_PG_EF_SEARCH", "100")
    aurora.AuroraStore("so", "cohere")
    assert "SET hnsw.ef_search = 100" in sent
    monkeypatch.delenv("FV_PG_EF_SEARCH")
    sent.clear()
    aurora.AuroraStore("so", "cohere")
    assert not any("ef_search" in s for s in sent)  # default stays pgvector's 40
