import json
import os
import re

import boto3
import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from fv.config import DIM, REGION, env


def _ident(s):
    if not re.fullmatch(r"[a-z0-9]+", str(s)):
        raise ValueError(f"unsafe SQL identifier part: {s!r}")
    return s


def _t(corpus, model):
    return f"{_ident(corpus)}_{_ident(model)}_chunks"


def schema_sql(corpus, model) -> list[str]:
    return ["CREATE EXTENSION IF NOT EXISTS vector",
            "ALTER EXTENSION vector UPDATE TO '0.8.2'",
            f"CREATE TABLE IF NOT EXISTS {_ident(corpus)}_docs (doc_id text PRIMARY KEY, title text, score int, extra text)",
            f"CREATE TABLE IF NOT EXISTS {_t(corpus, model)} (id text PRIMARY KEY, doc_id text, attr text, v vector({DIM}))"]


def index_sql(corpus, model) -> list[str]:
    t = _t(corpus, model)
    return ["SET maintenance_work_mem = '1GB'", "SET max_parallel_maintenance_workers = 3",
            f"CREATE INDEX IF NOT EXISTS {t}_hnsw ON {t} USING hnsw (v vector_cosine_ops) WITH (m = 16, ef_construction = 64)",
            f"CREATE INDEX IF NOT EXISTS {t}_attr ON {t} (attr)", f"ANALYZE {t}"]


def query_sql(corpus, model, filtered: bool) -> str:
    where = " WHERE attr = %s" if filtered else ""
    return f"SELECT id, doc_id FROM {_t(corpus, model)}{where} ORDER BY v <=> %s LIMIT %s"


def join_sql(corpus, model) -> str:
    return (f"SELECT c.id, d.title, d.score, d.extra FROM (SELECT id, doc_id, v <=> %s AS dist FROM {_t(corpus, model)} "
            f"ORDER BY dist LIMIT %s) c JOIN {_ident(corpus)}_docs d USING (doc_id) ORDER BY c.dist")


def _connect():
    secret = json.loads(boto3.client("secretsmanager", region_name=REGION)
                        .get_secret_value(SecretId=env("AURORA_SECRET_ARN"))["SecretString"])
    return psycopg.connect(host=env("AURORA_HOST"), dbname="postgres", user=secret["username"], password=secret["password"],
                           sslmode="require", connect_timeout=30, autocommit=True)  # resume from 0 ACU takes ~15 s


class AuroraStore:
    def __init__(self, corpus: str, model: str):
        self.name, self.corpus, self.model = "aurora", corpus, model
        self.conn = _connect()
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.conn)
        self.conn.execute("SET hnsw.iterative_scan = relaxed_order")  # filtered queries keep scanning until k rows
        if os.environ.get("FV_PG_EF_SEARCH"):  # recall/latency sweep; unset = pgvector default 40
            self.conn.execute(f"SET hnsw.ef_search = {int(os.environ['FV_PG_EF_SEARCH'])}")

    def setup(self):
        for sql in schema_sql(self.corpus, self.model):
            self.conn.execute(sql)

    def load(self, rows, vecs):
        self.conn.execute(f"TRUNCATE {_t(self.corpus, self.model)}")  # a rerun reloads cleanly
        with self.conn.cursor() as cur, cur.copy(
                f"COPY {_t(self.corpus, self.model)} (id, doc_id, attr, v) FROM STDIN WITH (FORMAT BINARY)") as cp:
            cp.set_types(["text", "text", "text", "vector"])
            for row, v in zip(rows, vecs, strict=True):
                cp.write_row((row["id"], row["doc_id"], row["attr"], Vector(v)))  # plain lists would dump as arrays

    def build_index(self):
        for sql in index_sql(self.corpus, self.model):
            self.conn.execute(sql)

    def load_docs(self, rows):
        with self.conn.cursor() as cur:
            cur.executemany(f"INSERT INTO {_ident(self.corpus)}_docs VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                            [(r["doc_id"], r["title"], int(r["score"]), r["extra"]) for r in rows])

    def query(self, vec, k, attr, need_doc):
        args = ((attr,) if attr is not None else ()) + (Vector(vec), k)
        rows = self.conn.execute(query_sql(self.corpus, self.model, attr is not None), args).fetchall()
        return [(r[0], r[1] if need_doc else None) for r in rows], ""

    def query_join(self, vec, k):
        return self.conn.execute(join_sql(self.corpus, self.model), (Vector(vec), k)).fetchall(), ""
