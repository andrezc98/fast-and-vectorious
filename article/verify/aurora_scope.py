"""Follow-up to aurora_force.py, run on a fresh Aurora cluster:
A) the planner settings scoped with SET LOCAL inside a transaction, for javascript only
B) every filtered query (all tags) with the forced settings on the whole session, against an unforced baseline
"""
import csv, os, statistics, sys
sys.argv = ["guide_check.py", "none"]
import guide_check as g

FORCED = ["SET {}enable_bitmapscan = off", "SET {}enable_seqscan = off",
          "SET {}plan_cache_mode = force_custom_plan", "SET {}hnsw.ef_search = {ef}"]


def summary(path, label):
    rs = [r for r in csv.DictReader(open(path)) if r["ok"] == "True"]
    print(label, len(rs), "recall", round(statistics.mean(float(r["recall"]) for r in rs), 3),
          "p50", round(statistics.median(float(r["latency_ms"]) for r in rs), 1), flush=True)


g.load_aurora()
conn = g.pg()
conn.execute("SET maintenance_work_mem = '1GB'")
conn.execute("CREATE INDEX IF NOT EXISTS so_chunks_js_hnsw ON so_chunks USING hnsw "
             "(v vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE attr = 'javascript'")
conn.execute("ANALYZE so_chunks")
conn.execute("SET hnsw.iterative_scan = relaxed_order")
truth = g.truth_filter_cached()
js = [qi for qi, t in enumerate(g.query_tags) if t == "javascript"]


# A) SET LOCAL around each hot-tag query; the connection itself keeps default settings
def scoped(ef):
    def query(vec, tag):
        with conn.transaction():
            for s in FORCED:
                conn.execute(s.format("LOCAL ", ef=ef), prepare=False)  # SET cannot be a prepared statement
            return [row[0] for row in conn.execute(
                "SELECT id FROM so_chunks WHERE attr = %s ORDER BY v <=> %s LIMIT 10",
                (tag, g.Vector(vec))).fetchall()]
    return query


for ef in (40, 100):
    path = os.path.join(g.OUT, f"guide-aurora-setlocal-ef{ef}.csv")
    g.bench({f"aurora_setlocal_ef{ef}": scoped(ef)}, truth, path, qis=js)
    summary(path, f"SET LOCAL ef{ef} javascript")
print("session after SET LOCAL runs:", conn.execute("SHOW enable_bitmapscan").fetchone()[0],
      conn.execute("SHOW plan_cache_mode").fetchone()[0], flush=True)

# B) all 1,000 filtered queries: default settings, then forced settings on the session
query_aurora = g.make_query_aurora(conn)
path = os.path.join(g.OUT, "guide-aurora-all-default.csv")
g.bench({"aurora_all_default": query_aurora}, truth, path)
g.group(path)
for s in FORCED:
    conn.execute(s.format("", ef=100), prepare=False)
plans = {t: g.explain(conn, t, g.query_tags.index(t)) for t in ("asp.net", "python", "javascript")}
open(os.path.join(g.OUT, "guide-aurora-all-forced-plans.txt"), "w").write(
    "\n\n".join(f"== {t}\n" + "\n".join(l for l in p.splitlines() if "Order By" not in l and "Sort Key" not in l)
                for t, p in plans.items()))
path = os.path.join(g.OUT, "guide-aurora-all-forced-ef100.csv")
g.bench({"aurora_all_forced_ef100": query_aurora}, truth, path)
g.group(path)
