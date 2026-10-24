"""Load the lab's saved Ley vectors into the event's S3 Vectors, OpenSearch and Aurora (no Bedrock cost).

Run with AWS_PROFILE=fv-lab after `terraform apply` and build_corpora.py in this folder:
  uv run --project ../../runner python load.py --corpus ley    (then --corpus so)
Each corpus goes to its own index: "<corpus>-cohere" (S3 Vectors, OpenSearch) and table "<corpus>_chunks" (Aurora).
Reuses the lab runner's request shapes (runner/fv/stores/*.py). Aurora goes through the Data API, the same path
the site uses, so no network route into the VPC is needed.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

import boto3
import numpy as np
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection, helpers

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "runner"))
from fv.io import read_jsonl  # noqa: E402
from fv.stores.aoss import bulk_actions, index_body  # noqa: E402
from fv.stores.s3v import put_batches  # noqa: E402

REGION = "us-east-1"
EVENT = os.path.expanduser("~/fv-data/event")


def tf_outputs() -> dict:
    out = json.loads(subprocess.run(["terraform", "-chdir=" + HERE, "output", "-json"], capture_output=True, text=True,
                                    check=True).stdout)
    return out["store_env"]["value"] | {"ADMIN_SECRET": out["admin_secret_arn"]["value"]}


def load_s3v(env, corpus, rows, vecs):
    c = boto3.client("s3vectors", region_name=REGION)
    for batch in put_batches(rows, vecs):
        c.put_vectors(vectorBucketName=env["VECTOR_BUCKET"], indexName=f"{corpus}-cohere", vectors=batch)


def load_aoss(env, corpus, rows, vecs):
    index = f"{corpus}-cohere"
    auth = AWSV4SignerAuth(boto3.Session().get_credentials(), REGION, "aoss")
    c = OpenSearch(hosts=[{"host": urlparse(env["AOSS_ENDPOINT"]).hostname, "port": 443}], http_auth=auth, use_ssl=True,
                   verify_certs=True, connection_class=RequestsHttpConnection, timeout=120)
    for _ in range(12):  # data policy takes 30-60 s to propagate after apply
        try:
            if not c.indices.exists(index=index):
                c.indices.create(index=index, body=index_body())
            break
        except Exception as e:  # noqa: BLE001
            print("aoss not ready:", e, flush=True)
            time.sleep(10)
    helpers.bulk(c, bulk_actions(index, rows, vecs), chunk_size=200, max_retries=5)


def data_api(env):
    rds = boto3.client("rds-data", region_name=REGION)

    def run(sql, params=None, batch=None):
        kw = {"resourceArn": env["AURORA_ARN"], "secretArn": env["ADMIN_SECRET"], "database": "postgres", "sql": sql}
        for attempt in range(20):  # a paused cluster answers DatabaseResumingException for ~15 s
            try:
                if batch is not None:
                    return rds.batch_execute_statement(**kw, parameterSets=batch)
                return rds.execute_statement(**kw, **({"parameters": params} if params else {}))
            except rds.exceptions.DatabaseResumingException:
                time.sleep(5)
        raise TimeoutError("Aurora did not resume")
    return run


def load_aurora(env, corpus, rows, vecs):
    table = f"{corpus}_chunks"
    run = data_api(env)
    reader = json.loads(boto3.client("secretsmanager", region_name=REGION)
                        .get_secret_value(SecretId=env["AURORA_SECRET"])["SecretString"])
    assert reader["password"].isalnum()  # goes into DDL as a literal (DDL takes no parameters)
    for sql in ["CREATE EXTENSION IF NOT EXISTS vector",
                f"CREATE TABLE IF NOT EXISTS {table} (id text PRIMARY KEY, doc_id text, v vector(1024))",
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS attr text",  # giveaway "filtro" round
                f"CREATE TABLE IF NOT EXISTS {corpus}_docs (doc_id text PRIMARY KEY, title text)",  # "contexto" round JOIN
                f"TRUNCATE {corpus}_docs",
                f"TRUNCATE {table}"]:
        run(sql)
    ins = f"INSERT INTO {table} (id, doc_id, v, attr) VALUES (:id, :doc, CAST(:v AS vector), :attr)"
    sets = [[{"name": "id", "value": {"stringValue": r["id"]}}, {"name": "doc", "value": {"stringValue": r["doc_id"]}},
             {"name": "attr", "value": {"stringValue": r["attr"]}},
             {"name": "v", "value": {"stringValue": json.dumps([round(float(x), 6) for x in v])}}]
            for r, v in zip(rows, vecs, strict=True)]
    for i in range(0, len(sets), 50):  # ~12 KB per vector; stays well under the Data API request size
        run(ins, batch=sets[i:i + 50])
    docs = {r["doc_id"]: r["title"] for r in rows}
    dsets = [[{"name": "d", "value": {"stringValue": d}}, {"name": "t", "value": {"stringValue": t}}] for d, t in docs.items()]
    for i in range(0, len(dsets), 200):
        run(f"INSERT INTO {corpus}_docs VALUES (:d, :t)", batch=dsets[i:i + 200])
    for sql in [f"CREATE INDEX IF NOT EXISTS {table}_attr ON {table} (attr)",
                f"CREATE INDEX IF NOT EXISTS {table}_hnsw ON {table} USING hnsw (v vector_cosine_ops) WITH (m = 16, ef_construction = 64)",
                f"ANALYZE {table}",
                f"DO $$BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{reader['username']}') THEN "
                f"CREATE ROLE {reader['username']} LOGIN; END IF; END$$",
                f"ALTER ROLE {reader['username']} PASSWORD '{reader['password']}'",
                f"GRANT SELECT ON {table}, {corpus}_docs TO {reader['username']}"]:
        run(sql)
    print("aurora rows:", run(f"SELECT count(*) FROM {table}")["records"][0][0]["longValue"], flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, choices=["ley", "so"])
    ap.add_argument("--only", choices=["s3v", "aoss", "aurora"])
    a = ap.parse_args(argv)
    rows = read_jsonl(os.path.join(EVENT, a.corpus, "chunks.jsonl"))
    vecs = np.load(os.path.join(EVENT, a.corpus, "docs.npy"))
    assert len(rows) == len(vecs), (len(rows), len(vecs))
    env = tf_outputs()
    for name, fn in [("s3v", load_s3v), ("aoss", load_aoss), ("aurora", load_aurora)]:
        if a.only in (None, name):
            t = time.time()
            fn(env, a.corpus, rows, vecs)
            print(json.dumps({"store": name, "corpus": a.corpus, "items": len(rows), "s": round(time.time() - t, 1)}), flush=True)


if __name__ == "__main__":
    main()
