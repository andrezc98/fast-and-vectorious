"""Runs the article's build guide as written, on the Stack Overflow corpus, from the lab runner.

    python guide_check.py load     # Step 3 for all four stores, plus the DynamoDB HASH table
    python guide_check.py bench    # Steps 4-6, filtered, 3 reps; adds the HASH table
    python guide_check.py aurora   # Step 7 follow-ups: partial HNSW index and planner settings

Expects DATA/so/{chunks,queries}.jsonl and DATA/so/emb/cohere/{docs,queries}.npy, and the FV_*
variables the runner's user_data exports. Writes CSVs and plans to results/<today>/.
"""
import collections, csv, datetime as dt, json, math, os, statistics, sys, time

import boto3
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runner"))
from fv.config import require_sandbox  # noqa: E402

require_sandbox()  # repo rule: refuse anything but the lab profile or the lab runner
if os.environ.get("FV_RUNNER") != "1":
    sys.exit("run this on the lab runner")  # it also needs the runner's corpus and FV_* outputs

DATA = os.environ.get("FV_DATA_DIR", os.path.expanduser("~/data"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results", dt.date.today().isoformat())
os.makedirs(OUT, exist_ok=True)


def read_jsonl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- "What you need": the arrays and names ---
chunks = read_jsonl(f"{DATA}/so/chunks.jsonl")
queries = read_jsonl(f"{DATA}/so/queries.jsonl")
ids = [c["id"] for c in chunks]
attrs = np.array([c["attr"] for c in chunks])
vecs = np.load(f"{DATA}/so/emb/cohere/docs.npy")
query_vecs = np.load(f"{DATA}/so/emb/cohere/queries.npy")
query_tags = [q["attr"] for q in queries]
assert len(ids) == len(vecs) and len(query_tags) == len(query_vecs)

table = "fv-vec-so-cohere"
hash_table = "fv-vec-so-cohere-hash"
bucket = os.environ["FV_VECTOR_BUCKET"]
s3v_index = "so-cohere"
host = os.environ["FV_AOSS_ENDPOINT"].removeprefix("https://")
aoss_index = "so-cohere"

# --- Step 1 ---
rows_per_value = collections.Counter(attrs)
matching = [rows_per_value[tag] for tag in query_tags]


def bucket_of(n, total=len(attrs)):
    share = n / total
    return "<0.1%" if share < 0.001 else "0.1-1%" if share < 0.01 else "1-5%" if share < 0.05 else ">=5%"


# --- Step 2 ---
def exact_topk(q, base, k, allowed=None):
    scores = base @ q
    k = min(k, len(scores))
    if allowed is not None:
        scores = np.where(allowed, scores, -np.inf)
        k = min(k, int(allowed.sum()))
    if k == 0:
        return np.array([], dtype=np.int64)
    idx = np.argpartition(-scores, k - 1)[:k]
    return idx[np.argsort(-scores[idx])]


def recall_at_k(found, truth, k):
    want = truth[:k]
    if not want:
        return float("nan")
    return len(set(found[:k]) & set(want)) / len(want)


def truth_filter_cached():
    p = f"{DATA}/so/emb/cohere/truth-article.json"
    if os.path.exists(p):
        return json.load(open(p))
    t = [[ids[i] for i in exact_topk(q, vecs, 10, attrs == tag)] for q, tag in zip(query_vecs, query_tags)]
    json.dump(t, open(p, "w"))
    return t


# --- Step 3 clients ---
import psycopg
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection, helpers
from pgvector import Vector
from pgvector.psycopg import register_vector

ddb = boto3.client("dynamodb", region_name="us-east-1")
s3v = boto3.client("s3vectors", region_name="us-east-1")
auth = AWSV4SignerAuth(boto3.Session().get_credentials(), "us-east-1", "aoss")
os_client = OpenSearch(hosts=[{"host": host, "port": 443}], http_auth=auth,
                       use_ssl=True, connection_class=RequestsHttpConnection, timeout=120)


def pg():
    secret = json.loads(boto3.client("secretsmanager", region_name="us-east-1")
                        .get_secret_value(SecretId=os.environ["FV_AURORA_SECRET_ARN"])["SecretString"])
    conn = psycopg.connect(host=os.environ["FV_AURORA_HOST"], dbname="postgres", user=secret["username"],
                           password=secret["password"], sslmode="require", connect_timeout=30, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def create_ddb(name, element_type):
    try:
        ddb.describe_table(TableName=name)
        return  # rerun: table exists, load_ddb overwrites the same items
    except ddb.exceptions.ResourceNotFoundException:
        pass
    ddb.create_table(
        TableName=name, BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"},
                              {"AttributeName": "attr", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        VectorIndexes=[{
            "IndexName": "vec", "VectorAttribute": {"AttributeName": "v"},
            "Dimensions": 1024, "DistanceFunction": "COSINE",
            "Projection": {"ProjectionType": "ALL"},
            "SearchSchema": [{"AttributeName": "attr", "SearchSchemaElementType": element_type}],
        }],
    )
    ddb.get_waiter("table_exists").wait(TableName=name)


def load_ddb(name):
    def put(batch):
        pending, delay = {name: batch}, 0.05
        while pending:
            pending = ddb.batch_write_item(RequestItems=pending).get("UnprocessedItems")
            if pending:
                time.sleep(delay)
                delay = min(delay * 2, 2)

    batch = []
    for id_, attr, v in zip(ids, attrs, vecs):
        batch.append({"PutRequest": {"Item": {
            "id": {"S": id_}, "attr": {"S": str(attr)},
            "v": {"L": [{"N": repr(float(x))} for x in v]}}}})
        if len(batch) == 25:
            put(batch)
            batch = []
    if batch:
        put(batch)


def wait_for_index(name):
    while True:
        idx = ddb.describe_table(TableName=name)["Table"].get("VectorIndexes", [])
        if idx and all(i["IndexStatus"] == "ACTIVE" and not i.get("Backfilling") for i in idx):
            return
        time.sleep(30)


def load_s3v():
    for i in range(0, len(ids), 500):
        s3v.put_vectors(vectorBucketName=bucket, indexName=s3v_index, vectors=[
            {"key": id_, "data": {"float32": v.tolist()}, "metadata": {"attr": str(attr)}}
            for id_, attr, v in zip(ids[i:i + 500], attrs[i:i + 500], vecs[i:i + 500])])


def load_aoss():
    if not os_client.indices.exists(index=aoss_index):
        os_client.indices.create(index=aoss_index, body={
            "settings": {"index": {"knn": True}},
            "mappings": {"properties": {
                "v": {"type": "knn_vector", "dimension": 1024,
                      "space_type": "cosinesimil", "compression_level": "1x"},
                "attr": {"type": "keyword"}}}})
    helpers.bulk(os_client, ({"_index": aoss_index, "_id": id_,
                              "_source": {"v": v.tolist(), "attr": str(attr)}}
                             for id_, attr, v in zip(ids, attrs, vecs)),
                 chunk_size=200, max_retries=5)
    while os_client.count(index=aoss_index)["count"] < len(ids):
        time.sleep(15)


def load_aurora():
    conn = pg()
    conn.execute("ALTER EXTENSION vector UPDATE TO '0.8.2'")
    conn.execute("DROP TABLE IF EXISTS so_chunks")
    conn.execute("CREATE TABLE so_chunks (id text PRIMARY KEY, attr text, v vector(1024))")
    with conn.cursor() as cur, cur.copy("COPY so_chunks (id, attr, v) FROM STDIN WITH (FORMAT BINARY)") as cp:
        cp.set_types(["text", "text", "vector"])
        for id_, attr, v in zip(ids, attrs, vecs):
            cp.write_row((id_, str(attr), Vector(v)))
    conn.execute("SET maintenance_work_mem = '1GB'")
    conn.execute("CREATE INDEX ON so_chunks USING hnsw (v vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
    conn.execute("CREATE INDEX ON so_chunks (attr)")
    conn.execute("ANALYZE so_chunks")
    print("pgvector", conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0])


def timed(label, fn):
    t0 = time.time()
    fn()
    print(f"{label}: {time.time() - t0:.0f} s", flush=True)


def cmd_load():
    for name, kind in ((table, "INLINE_FILTER"), (hash_table, "HASH")):
        timed(f"ddb create {name}", lambda: create_ddb(name, kind))
        timed(f"ddb load {name}", lambda: load_ddb(name))
    timed("s3v load", load_s3v)
    timed("aoss load", load_aoss)
    timed("aurora load + index", load_aurora)
    for name in (table, hash_table):
        timed(f"ddb index ready {name}", lambda: wait_for_index(name))


# --- Step 4 ---
def query_ddb_on(name):
    def q(vec, tag):
        r = ddb.search_vectors(
            TableName=name, IndexName="vec", TopK=10,
            SearchVector=[{"N": repr(float(x))} for x in vec],
            SearchConditionExpression="#a = :a",
            ExpressionAttributeNames={"#a": "attr"},
            ExpressionAttributeValues={":a": {"S": tag}},
            ProjectionExpression="id")
        return [h["Item"]["id"]["S"] for h in r["SearchResults"]]
    return q


def query_s3v(vec, tag):
    r = s3v.query_vectors(
        vectorBucketName=bucket, indexName=s3v_index, topK=10,
        queryVector={"float32": [float(x) for x in vec]},
        filter={"attr": {"$eq": tag}}, queryMode="ENHANCED")
    return [v["key"] for v in r["vectors"]]


def query_aoss(vec, tag):
    r = os_client.search(index=aoss_index, body={
        "size": 10, "_source": False,
        "query": {"knn": {"v": {"vector": [float(x) for x in vec], "k": 10,
                                "filter": {"term": {"attr": tag}}}}}})
    return [h["_id"] for h in r["hits"]["hits"]]


def make_query_aurora(conn):
    conn.execute("SET hnsw.iterative_scan = relaxed_order")

    def query_aurora(vec, tag):
        return [row[0] for row in conn.execute(
            "SELECT id FROM so_chunks WHERE attr = %s ORDER BY v <=> %s LIMIT 10",
            (tag, Vector(vec))).fetchall()]
    return query_aurora


def smoke(stores):
    by_id = dict(zip(ids, attrs))
    for name, query in stores.items():
        for attempt in range(20):  # DynamoDB can raise ValidationException just after ACTIVE
            try:
                got = query(query_vecs[0], "javascript")
                break
            except Exception as e:
                print(name, "smoke retry:", type(e).__name__, e, flush=True)
                time.sleep(15)
        assert len(got) == 10 and all(by_id[i] == "javascript" for i in got), (name, got)
        print(name, "smoke ok", flush=True)


# --- Step 5 ---
def bench(stores, truth_filter, path, qis=None, reps=3):
    qis = range(len(query_vecs)) if qis is None else qis
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["store", "rep", "qi", "latency_ms", "recall", "ok", "error"])
        for name, query in stores.items():
            for vec, tag in list(zip(query_vecs, query_tags))[:20]:
                try:
                    query(vec, tag)
                except Exception as e:
                    print(name, "warm-up failed:", e)
            for rep in range(1, reps + 1):
                for qi in qis:
                    vec, tag = query_vecs[qi], query_tags[qi]
                    t0 = time.perf_counter()
                    try:
                        got = query(vec, tag)
                        ms = (time.perf_counter() - t0) * 1000
                        writer.writerow([name, rep, qi, ms, recall_at_k(got, truth_filter[qi], 10), True, ""])
                    except Exception as e:
                        ms = (time.perf_counter() - t0) * 1000
                        writer.writerow([name, rep, qi, ms, "", False, f"{type(e).__name__}: {e}"])
            print(name, "benched", flush=True)


# --- Step 6 ---
def group(path):
    groups = collections.defaultdict(lambda: ([], []))
    errors = collections.Counter()
    for r in csv.DictReader(open(path)):
        if r["ok"] != "True":
            errors[r["store"]] += 1
            continue
        if math.isnan(float(r["recall"])):
            continue
        recalls, latencies = groups[(r["store"], bucket_of(matching[int(r["qi"])]))]
        recalls.append(float(r["recall"]))
        latencies.append(float(r["latency_ms"]))
    for (store, b), (recalls, latencies) in sorted(groups.items()):
        print(store, b, len(recalls), round(statistics.mean(recalls), 3), round(statistics.median(latencies), 1))
    print("errors:", dict(errors))


def cmd_bench():
    truth_filter = truth_filter_cached()
    conn = pg()
    stores = {"ddb": query_ddb_on(table), "ddb_hash": query_ddb_on(hash_table), "s3v": query_s3v,
              "aoss": query_aoss, "aurora": make_query_aurora(conn)}
    smoke(stores)
    path = os.path.join(OUT, "guide-filter-requests.csv")
    bench(stores, truth_filter, path)
    group(path)


# --- Step 7 follow-ups ---
def explain(conn, tag, qi):
    plan = conn.execute(
        "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM so_chunks WHERE attr = %s ORDER BY v <=> %s LIMIT 10",
        (tag, Vector(query_vecs[qi]))).fetchall()
    return "\n".join(row[0] for row in plan)


def cmd_aurora():
    truth_filter = truth_filter_cached()
    js = [qi for qi, t in enumerate(query_tags) if t == "javascript"]
    narrow = next(qi for qi, t in enumerate(query_tags) if t == "asp.net")
    conn = pg()
    query_aurora = make_query_aurora(conn)
    plans = {}
    variants = [("baseline", []),
                ("partial_hnsw", ["CREATE INDEX IF NOT EXISTS so_chunks_js_hnsw ON so_chunks USING hnsw "
                                  "(v vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE attr = 'javascript'",
                                  "ANALYZE so_chunks"]),
                ("full_hnsw_no_bitmap", ["DROP INDEX IF EXISTS so_chunks_js_hnsw", "SET enable_bitmapscan = off"])]
    for name, setup in variants:
        conn.execute("SET maintenance_work_mem = '1GB'")
        for sql in setup:
            t0 = time.time()
            conn.execute(sql)
            print(name, sql[:60], f"{time.time() - t0:.0f} s", flush=True)
        plans[name] = {"javascript": explain(conn, "javascript", js[0]), "asp.net": explain(conn, "asp.net", narrow)}
        print(plans[name]["javascript"], flush=True)
        bench({f"aurora_{name}": query_aurora}, truth_filter,
              os.path.join(OUT, f"guide-aurora-{name}.csv"), qis=js + [narrow])
        rs = list(csv.DictReader(open(os.path.join(OUT, f"guide-aurora-{name}.csv"))))
        jsr = [r for r in rs if r["ok"] == "True" and query_tags[int(r["qi"])] == "javascript"]
        print(name, "javascript", len(jsr), "recall", round(statistics.mean(float(r["recall"]) for r in jsr), 3),
              "p50", round(statistics.median(float(r["latency_ms"]) for r in jsr), 1), flush=True)
    conn.execute("RESET enable_bitmapscan")
    json.dump(plans, open(os.path.join(OUT, "guide-aurora-plans.json"), "w"), indent=2)


COMMANDS = {"load": cmd_load, "bench": cmd_bench, "aurora": cmd_aurora}
if __name__ == "__main__" and sys.argv[1:2] != ["none"]:
    if sys.argv[1:2] == [] or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: python guide_check.py {{{'|'.join(COMMANDS)}}}")
    COMMANDS[sys.argv[1]]()
