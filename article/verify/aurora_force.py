"""Follow-up to guide_check.py aurora: does a partial HNSW index help once the planner must use an index?"""
import os, statistics, sys, time
sys.argv = ["guide_check.py", "none"]
import guide_check as g
from pgvector import Vector

conn = g.pg()
query_aurora = g.make_query_aurora(conn)
truth = g.truth_filter_cached()
js = [qi for qi, t in enumerate(g.query_tags) if t == "javascript"]
conn.execute("SET maintenance_work_mem = '1GB'")
conn.execute("CREATE INDEX IF NOT EXISTS so_chunks_js_hnsw ON so_chunks USING hnsw "
             "(v vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE attr = 'javascript'")
conn.execute("ANALYZE so_chunks")
conn.execute("SET enable_bitmapscan = off")
conn.execute("SET enable_seqscan = off")
MODE = os.environ.get("FV_PLAN_MODE", "auto")
assert MODE in ("auto", "force_custom_plan", "force_generic_plan"), MODE  # goes into SET as text
conn.execute(f"SET plan_cache_mode = {MODE}")
EF = int(os.environ.get("FV_PG_EF_SEARCH", 40))
conn.execute(f"SET hnsw.ef_search = {EF}")
MODE = f"{MODE}-ef{EF}"  # generic plans with $1 cannot match a partial index predicate
plan = g.explain(conn, "javascript", js[0])
print(plan, flush=True)
path = os.path.join(g.OUT, f"guide-aurora-partial_hnsw_forced-{MODE}.csv")
g.bench({"aurora_partial_hnsw_forced": query_aurora}, truth, path, qis=js)
import csv
rs = [r for r in csv.DictReader(open(path)) if r["ok"] == "True"]
print("forced", MODE, "javascript", len(rs), "recall", round(statistics.mean(float(r["recall"]) for r in rs), 3),
      "p50", round(statistics.median(float(r["latency_ms"]) for r in rs), 1), flush=True)
open(os.path.join(g.OUT, f"guide-aurora-partial_hnsw_forced-{MODE}-plan.txt"), "w").write(plan)
