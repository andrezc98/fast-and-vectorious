import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path

import numpy as np

from fv.config import K, require_sandbox
from fv.io import read_jsonl
from fv.vec import exact_topk, mrr, recall_at_k


RESULTS = Path(__file__).resolve().parents[2] / "results"  # repo root, not cwd


def _today_dir() -> str:
    d = os.path.join(RESULTS, dt.date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    return d


def bakeoff_scores(qv, pv, pool_ids, targets, k=K) -> dict:
    rec, rr = [], []
    for q, target in zip(qv, targets):
        found = [pool_ids[i] for i in exact_topk(q, pv, k)]
        rec.append(recall_at_k(found, [target], k))
        rr.append(mrr(found, target, k))
    return {"n": len(targets), "recall_at_10": float(np.mean(rec)), "mrr_at_10": float(np.mean(rr))}


def cmd_so_prepare(a):
    from fv.corpus import so
    print(so.prepare(a.dump, os.path.join(a.data, "so")))


def cmd_ley_prepare(a):
    from fv.corpus import ley
    print(ley.prepare(a.norms, a.cache, os.path.join(a.data, "ley")))


def cmd_ley_questions(a):
    require_sandbox()
    from fv.genq import generate
    from fv.io import write_jsonl
    chunks = read_jsonl(os.path.join(a.data, "ley", "chunks.jsonl"))
    write_jsonl(os.path.join(a.data, "ley", "queries.jsonl"), generate(chunks, a.n, a.model_id))


def _check_rows(name, rows, matrix):
    if len(matrix) != len(rows):
        raise ValueError(f"{name}: {len(rows)} inputs but {len(matrix)} embeddings")


def cmd_bakeoff(a):
    require_sandbox()
    from fv.embed import embed_all, get_embedder
    qs = read_jsonl(os.path.join(a.data, "so", "bakeoff_queries.jsonl"))
    pool = read_jsonl(os.path.join(a.data, "so", "bakeoff_pool.jsonl"))
    path = os.path.join(_today_dir(), "bakeoff.csv")
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "n", "recall_at_10", "mrr_at_10"])
        if f.tell() == 0:
            w.writeheader()
        for m in a.models.split(","):
            fn, base = get_embedder(m), os.path.join(a.data, "so", "bakeoff", m)
            pv = embed_all(fn, [p["text"] for p in pool], "doc", "so", os.path.join(base, "pool"))
            qv = embed_all(fn, [q["text"] for q in qs], "query", "so", os.path.join(base, "queries"))
            _check_rows("bakeoff pool", pool, pv)
            _check_rows("bakeoff queries", qs, qv)
            row = {"model": m, **bakeoff_scores(qv, pv, [p["id"] for p in pool], [q["target"] for q in qs])}
            w.writerow(row)
            f.flush()
            print(row)


def cmd_embed(a):
    require_sandbox()
    from fv.embed import embed_all, get_embedder
    fn, base = get_embedder(a.model), os.path.join(a.data, a.corpus, "emb", a.model)
    for name, purpose, rows in (("docs", "doc", read_jsonl(os.path.join(a.data, a.corpus, "chunks.jsonl"))),
                                ("queries", "query", read_jsonl(os.path.join(a.data, a.corpus, "queries.jsonl")))):
        m = embed_all(fn, [r["text"] for r in rows], purpose, a.corpus, os.path.join(base, name))
        _check_rows(name, rows, m)
        np.save(os.path.join(base, f"{name}.npy"), m)
        print(name, m.shape)


def cmd_truth(a):
    base = os.path.join(a.data, a.corpus)
    chunks = read_jsonl(os.path.join(base, "chunks.jsonl"))
    queries = read_jsonl(os.path.join(base, "queries.jsonl"))
    dv = np.load(os.path.join(base, "emb", a.model, "docs.npy"))
    qv = np.load(os.path.join(base, "emb", a.model, "queries.npy"))
    ids, attrs = [c["id"] for c in chunks], np.array([c["attr"] for c in chunks])
    out = {"plain": [[ids[i] for i in exact_topk(q, dv, K)] for q in qv],
           "filter": [[ids[i] for i in exact_topk(q, dv, K, attrs == qr["attr"])] for q, qr in zip(qv, queries)]}
    with open(os.path.join(base, "emb", a.model, "truth.json"), "w") as f:
        json.dump(out, f)


def cmd_qwen(a):
    require_sandbox()
    from fv.embed import qwen
    if a.action == "up":
        qwen.endpoint_up()
    else:
        qwen.endpoint_down()


def _emb(a):
    base = os.path.join(a.data, a.corpus)
    return (read_jsonl(os.path.join(base, "chunks.jsonl")), np.load(os.path.join(base, "emb", a.model, "docs.npy")),
            read_jsonl(os.path.join(base, "queries.jsonl")), np.load(os.path.join(base, "emb", a.model, "queries.npy")))


def cmd_relevance(a):
    """Source article in the top-k by exact search (no AWS): does the search find the right law?"""
    from fv.relevance import source_hits
    rows, dv, queries, qv = _emb(a)
    r = source_hits(qv, dv, [c["id"] for c in rows], [q["source"] for q in queries])
    path = os.path.join(_today_dir(), f"relevance-{a.corpus}-{a.model}.json")
    with open(path, "w") as f:
        json.dump(r, f, indent=2)
    print(r)


def cmd_load(a):
    require_sandbox()
    from fv.stores import get_store
    rows, dv, _, qv = _emb(a)
    if a.limit:
        rows, dv = rows[:a.limit], dv[:a.limit]
    st = get_store(a.store, a.corpus, a.model)
    if a.store == "ddb":
        st.create()
    if a.store == "aoss":
        st.create_index()
    if a.store == "aurora":
        st.setup()
        st.load_docs(read_jsonl(os.path.join(a.data, a.corpus, "docs.jsonl")))
    st.load(rows, dv)
    if a.store == "aurora":
        st.build_index()
    if a.store == "ddb":
        st.wait_searchable(qv[0])
    if a.store == "aoss":
        st.wait_count(len(rows))
    print("loaded", a.store, a.corpus, a.model, len(rows))


def cmd_docs_load(a):
    require_sandbox()
    from fv.stores.dynamo import docs_load
    docs_load(read_jsonl(os.path.join(a.data, a.corpus, "docs.jsonl")))


def _tag(a) -> str:
    """Optional file suffix so warm-up passes and parameter sweeps never overwrite the main run."""
    return f"-{a.tag}" if getattr(a, "tag", "") else ""


def _write_run_meta(csv_path, a):
    """Everything a reader needs to reproduce or question a run: versions and the fairness knobs."""
    from importlib.metadata import version

    from fv.config import DIM
    meta = {"DIM": DIM, "K": K, "reps": a.reps, "store": a.store, "corpus": a.corpus, "model": a.model, "case": a.case,
            "versions": {p: version(p) for p in ("boto3", "botocore", "opensearch-py", "psycopg", "pgvector", "numpy")},
            "knobs": {"aoss_compression": "1x", "s3v_filter_mode": "ENHANCED", "pgvector_iterative_scan": "relaxed_order",
                      "pgvector_hnsw_m": 16, "pgvector_hnsw_ef_construction": 64, "pgvector_hnsw_ef_search": int(os.environ.get("FV_PG_EF_SEARCH", 40))}}  # 40 = pgvector default
    meta_path = os.path.join(os.path.dirname(csv_path), f"run-meta-{a.store}-{a.corpus}-{a.model}-{a.case}{_tag(a)}.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def cmd_bench(a):
    require_sandbox()
    from fv.harness import run_case
    from fv.results import RowSink
    from fv.stores import get_store
    from fv.stores.dynamo import docs_get
    _, _, queries, qv = _emb(a)
    with open(os.path.join(a.data, a.corpus, "emb", a.model, "truth.json")) as f:
        truth = json.load(f)
    st = get_store(a.store, a.corpus, a.model)
    path = os.path.join(_today_dir(), f"bench-{a.store}-{a.corpus}-{a.model}-{a.case}{_tag(a)}.csv")
    _write_run_meta(path, a)
    try:
        with RowSink(path) as sink:
            run_case(st, a.corpus, a.model, a.case, qv, [q["attr"] for q in queries], truth, a.reps, sink, docs_get)
    finally:
        if hasattr(st, "close"):
            st.close()
    print(path)


def cmd_report(a):
    import glob

    from fv.harness import summarize
    from fv.results import read_rows
    d = _today_dir() if not a.date else os.path.join(RESULTS, a.date)
    files = sorted(glob.glob(os.path.join(d, "bench-*.csv")))
    if not files:
        print(f"no bench files in {d}")
        return
    rows = [r for p in files for r in read_rows(p)]
    out = summarize(rows)
    with open(os.path.join(d, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(r)


def cmd_ddb_delete(a):
    require_sandbox()
    import boto3

    from fv.config import REGION
    c = boto3.client("dynamodb", region_name=REGION)
    for t in c.list_tables()["TableNames"]:
        if t.startswith("fv-vec-"):
            c.delete_table(TableName=t)
            print("deleted", t)


def cmd_capacity(a):
    require_sandbox()
    import boto3

    from fv.config import REGION
    dims = [dict(zip(("Name", "Value"), d.split("=", 1))) for d in a.dims.split(",")]
    start, end = dt.datetime.fromisoformat(a.start), dt.datetime.fromisoformat(a.end)
    r = boto3.client("cloudwatch", region_name=REGION).get_metric_statistics(
        Namespace=a.namespace, MetricName=a.metric, Dimensions=dims, StartTime=start, EndTime=end,
        Period=60, Statistics=["Average"])
    pts = [p["Average"] for p in r["Datapoints"]]
    avg = sum(pts) / len(pts) if pts else 0.0
    print({"points": len(pts), "avg_units": avg, "unit_hours": avg * (end - start).total_seconds() / 3600})


def format_hit(i, title, text, width=100) -> str:
    body = " ".join(text.split())
    return f"{i}. {title}\n   {body[:width]}{'…' if len(body) > width else ''}"


def cmd_ask(a):
    """Live build: one question against one Ley index, top 3 with the law's name."""
    require_sandbox()
    from fv.embed import get_embedder
    from fv.stores import get_store
    chunks = {c["id"]: c for c in read_jsonl(os.path.join(a.data, "ley", "chunks.jsonl"))}
    docs = {d["doc_id"]: d for d in read_jsonl(os.path.join(a.data, "ley", "docs.jsonl"))}
    vec = get_embedder(a.model)([a.question], "query", "ley")[0]
    hits, _ = get_store(a.store, "ley", a.model).query(vec, 3, None, False)
    for i, (cid, _) in enumerate(hits, 1):
        print(format_hit(i, docs[chunks[cid]["doc_id"]]["title"], chunks[cid]["text"]))


def _add(sub, name, fn, *args):
    p = sub.add_parser(name)
    for flags, kw in args:
        p.add_argument(*flags, **kw)
    p.set_defaults(fn=fn)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fv")
    ap.add_argument("--data", default="data")
    sub = ap.add_subparsers(required=True)
    corpus = (("--corpus",), {"required": True, "choices": ["so", "ley"]})
    model = (("--model",), {"required": True})
    _add(sub, "so-prepare", cmd_so_prepare, (("--dump",), {"required": True}))
    _add(sub, "ley-prepare", cmd_ley_prepare, (("--norms",), {"required": True}), (("--cache",), {"required": True}))
    _add(sub, "bakeoff", cmd_bakeoff, (("--models",), {"default": "cohere,nova,qwen"}))
    _add(sub, "embed", cmd_embed, corpus, model)
    _add(sub, "truth", cmd_truth, corpus, model)
    _add(sub, "qwen", cmd_qwen, (("action",), {"choices": ["up", "down"]}))
    _add(sub, "ley-questions", cmd_ley_questions, (("--model-id",), {"required": True}), (("--n",), {"type": int, "default": 1000}))
    store = (("--store",), {"required": True, "choices": ["ddb", "s3v", "aoss", "aurora"]})
    _add(sub, "load", cmd_load, store, corpus, model, (("--limit",), {"type": int, "default": 0}))
    _add(sub, "ask", cmd_ask, (("question",), {}), store, model)
    _add(sub, "relevance", cmd_relevance, corpus, model)
    _add(sub, "docs-load", cmd_docs_load, corpus)
    _add(sub, "bench", cmd_bench, store, corpus, model, (("--case",), {"required": True, "choices": ["plain", "filter", "join"]}),
         (("--reps",), {"type": int, "default": 3}), (("--tag",), {"default": ""}))
    _add(sub, "report", cmd_report, (("--date",), {"default": ""}))
    _add(sub, "ddb-delete", cmd_ddb_delete, (("--all",), {"action": "store_true", "required": True}))
    _add(sub, "capacity", cmd_capacity, (("--namespace",), {"required": True}), (("--metric",), {"required": True}),
         (("--dims",), {"required": True}), (("--start",), {"required": True}), (("--end",), {"required": True}))
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    a.fn(a)
