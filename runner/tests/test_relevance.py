import numpy as np

from fv.relevance import article_of, source_hits
from fv.vec import normalize


def test_article_of_drops_the_chunk_index():
    assert article_of("ley#22740-484-0") == "ley#22740-484"
    assert article_of("ley#22740-484-12") == "ley#22740-484"


def test_source_hits_counts_chunk_and_article_at_each_k():
    ids = ["n-1-0", "n-1-1", "n-2-0", "n-3-0"]
    dv = normalize(np.array([[1, 0], [0.9, 0.1], [0, 1], [0.7, 0.7]], dtype=np.float32))
    qv = normalize(np.array([[0.9, 0.1], [0, 1]], dtype=np.float32))
    # q0's source is n-1-0: nearest is n-1-1 (same article), n-1-0 second; q1's source n-3-0 is second after n-2-0
    r = source_hits(qv, dv, ids, ["n-1-0", "n-3-0"], ks=(1, 3))
    assert r["chunk@1"] == 0.0 and r["chunk@3"] == 1.0
    assert r["article@1"] == 0.5 and r["article@3"] == 1.0
    assert r["n"] == 2 and r["mrr_chunk"] == (1 / 2 + 1 / 2) / 2


def test_cli_relevance_writes_json(tmp_path, monkeypatch):
    import json

    from fv import cli
    d = tmp_path / "ley"
    (d / "emb" / "m").mkdir(parents=True)
    (d / "chunks.jsonl").write_text("\n".join(json.dumps({"id": i, "text": "", "attr": "a", "doc_id": "d"}) for i in ("n-1-0", "n-2-0")))
    (d / "queries.jsonl").write_text(json.dumps({"qid": "q", "text": "", "attr": "a", "source": "n-2-0"}))
    np.save(d / "emb" / "m" / "docs.npy", normalize(np.array([[1, 0], [0, 1]], dtype=np.float32)))
    np.save(d / "emb" / "m" / "queries.npy", normalize(np.array([[0, 1]], dtype=np.float32)))
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    cli.main(["--data", str(tmp_path), "relevance", "--corpus", "ley", "--model", "m"])
    [f] = list((tmp_path / "results").glob("*/relevance-ley-m.json"))
    assert json.loads(f.read_text())["chunk@1"] == 1.0
