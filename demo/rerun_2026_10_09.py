"""2026-10-09 re-run on the runner (from ~/runner): warm-controlled grid, Aurora ef_search + EXPLAIN, cold start."""
import datetime as dt, json, os, random, subprocess, sys, time
import numpy as np
OUT = os.path.expanduser("~/results/2026-10-09"); os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "rerun.log"), "a")
def log(*a):
    line = f"{dt.datetime.utcnow().isoformat(timespec='seconds')}Z " + " ".join(map(str, a)); print(line, flush=True); LOG.write(line + "\n"); LOG.flush()
def fv(*args, env=None):
    t0 = time.time(); r = subprocess.run([os.path.expanduser("~/.local/bin/uv"), "run", "fv", *args], capture_output=True, text=True, env={**os.environ, **(env or {})})
    log("fv", *args, f"exit={r.returncode}", f"{time.time()-t0:.0f}s", (r.stderr.strip().splitlines() or [""])[-1][:200] if r.returncode else "")
    return r.returncode
phase = sys.argv[1] if len(sys.argv) > 1 else "all"

if phase in ("all", "grid"):
    runs = [(c, s, k) for c in ("so", "ley") for s in ("ddb", "s3v", "aoss", "aurora") for k in ("plain", "filter", "join")]
    random.Random(20261009).shuffle(runs)
    log("GRID order (seed 20261009):", runs)
    warmed = set()
    for c, s, k in runs:
        if (s, c) not in warmed:  # one discarded pass warms caches/capacity for that index
            fv("bench", "--store", s, "--corpus", c, "--model", "cohere", "--case", "plain", "--reps", "1", "--tag", "warmup")
            warmed.add((s, c))
        log("RUN start", s, c, k); fv("bench", "--store", s, "--corpus", c, "--model", "cohere", "--case", k, "--reps", "3"); log("RUN end", s, c, k)

if phase in ("all", "aurora", "ef"):
    for ef in ("100", "200"):
        for k in ("plain", "filter"):
            log("EF start", ef, k); fv("bench", "--store", "aurora", "--corpus", "so", "--model", "cohere", "--case", k, "--reps", "3", "--tag", f"ef{ef}", env={"FV_PG_EF_SEARCH": ef}); log("EF end", ef, k)

if phase in ("all", "aurora", "explain"):
    from fv.stores.aurora import _connect, query_sql
    from pgvector import Vector
    from pgvector.psycopg import register_vector
    import json as _j
    qv = np.load("data/so/emb/cohere/queries.npy"); qs = [_j.loads(l) for l in open("data/so/queries.jsonl")]
    c = _connect(); register_vector(c); c.execute("SET hnsw.iterative_scan = relaxed_order")
    plans = {}
    for i in (0, 1, 2):
        for filtered in (False, True):
            args = ((qs[i]["attr"],) if filtered else ()) + (Vector(qv[i]), 10)
            rows = c.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + query_sql("so", "cohere", filtered), args).fetchall()
            plans[f"q{i}-{'filter' if filtered else 'plain'}-attr={qs[i]['attr'] if filtered else ''}"] = [r[0] for r in rows]
    sel = c.execute("SELECT attr, count(*) FROM so_cohere_chunks GROUP BY attr ORDER BY 2 DESC").fetchall()
    json.dump({"plans": plans, "attr_counts": sel}, open(os.path.join(OUT, "aurora-explain.json"), "w"), indent=2, default=str)
    log("EXPLAIN saved", {k: next((l for l in v if "Scan" in l), v[0])[:120] for k, v in plans.items()})

if phase in ("all", "cold"):
    import boto3
    from fv.stores import get_store
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    def acu():
        now = dt.datetime.now(dt.timezone.utc)
        p = cw.get_metric_statistics(Namespace="AWS/RDS", MetricName="ServerlessDatabaseCapacity", Dimensions=[{"Name": "DBClusterIdentifier", "Value": "fv-aurora"}],
                                     StartTime=now - dt.timedelta(minutes=5), EndTime=now, Period=60, Statistics=["Maximum"])["Datapoints"]
        return max([x["Maximum"] for x in p], default=None)
    log("COLD idle start (no queries)")
    t_end = time.time() + 40 * 60
    time.sleep(15 * 60)
    while time.time() < t_end and acu() not in (0, 0.0):
        log("COLD waiting, aurora ACU max(5m) =", acu()); time.sleep(60)
    log("COLD aurora ACU max(5m) =", acu())
    qv = np.load("data/so/emb/cohere/queries.npy")
    res = {}
    for s in ("aurora", "aoss", "ddb", "s3v"):
        t0 = time.perf_counter(); st = get_store(s, "so", "cohere"); t_conn = time.perf_counter() - t0
        lat = []
        for i in range(3):
            t1 = time.perf_counter()
            try: st.query(qv[i], 10, None, False); ok = True
            except Exception as e: ok = f"{type(e).__name__}: {e}"[:200]
            lat.append((round((time.perf_counter() - t1) * 1000, 1), ok))
        res[s] = {"connect_s": round(t_conn, 2), "first_three_queries_ms": lat}
        log("COLD", s, res[s])
    json.dump({"collected_utc": dt.datetime.utcnow().isoformat() + "Z", "idle_min": 15, "aurora_acu_before": acu(), "stores": res},
              open(os.path.join(OUT, "cold-start.json"), "w"), indent=2, default=str)
log("PHASE DONE", phase)
