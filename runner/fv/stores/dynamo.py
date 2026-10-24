import functools
import json
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from fv.config import DIM, REGION, env

INDEX = "vec"
_CFG = Config(retries={"mode": "adaptive", "max_attempts": 10}, max_pool_connections=32)


def table_name(corpus: str, model: str) -> str:
    return f"fv-vec-{corpus}-{model}"


def create_table_request(name: str) -> dict:
    # Terraform has no vector index yet (provider issue #49350), so the runner owns these tables;
    # the destroy workflow deletes every fv-vec-* table first. Fields: docs/api-notes.md §1.
    return {"TableName": name, "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"},
                                     {"AttributeName": "attr", "AttributeType": "S"}],
            "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "Tags": [{"Key": "project", "Value": "fast-and-vectorious"}],
            "VectorIndexes": [{"IndexName": INDEX, "VectorAttribute": {"AttributeName": "v"}, "Dimensions": DIM,
                               "DistanceFunction": "COSINE", "Projection": {"ProjectionType": "ALL"},
                               "SearchSchema": [{"AttributeName": "attr", "SearchSchemaElementType": "INLINE_FILTER"}]}]}


def item(row: dict, vec) -> dict:
    return {"id": {"S": row["id"]}, "attr": {"S": row["attr"]}, "doc_id": {"S": row["doc_id"]},
            "v": {"L": [{"N": repr(float(x))} for x in vec]}}


def search_request(name, vec, k, attr, need_doc) -> dict:
    r = {"TableName": name, "IndexName": INDEX, "SearchVector": [{"N": repr(float(x))} for x in vec], "TopK": k,
         "ProjectionExpression": "id, doc_id" if need_doc else "id", "ReturnConsumedCapacity": "TOTAL"}
    if attr is not None:
        r |= {"SearchConditionExpression": "#a = :a", "ExpressionAttributeNames": {"#a": "attr"},
              "ExpressionAttributeValues": {":a": {"S": attr}}}
    return r


@functools.cache
def _client():
    return boto3.client("dynamodb", region_name=REGION, config=_CFG)


class DynamoStore:
    def __init__(self, corpus: str, model: str):
        self.name, self.table, self.c = "ddb", table_name(corpus, model), _client()

    def create(self):
        try:
            self.c.create_table(**create_table_request(self.table))
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceInUseException":  # table exists: a rerun
                raise
        self.c.get_waiter("table_exists").wait(TableName=self.table)

    def delete(self):
        self.c.delete_table(TableName=self.table)

    def load(self, rows, vecs):
        batch = []
        for row, v in zip(rows, vecs, strict=True):
            batch.append({"PutRequest": {"Item": item(row, v)}})
            if len(batch) == 25:
                self._write(batch)
                batch = []
        if batch:
            self._write(batch)

    def _write(self, batch):
        pending, delay = {self.table: batch}, 0.05
        for _ in range(10):
            pending = self.c.batch_write_item(RequestItems=pending).get("UnprocessedItems")
            if not pending:
                return
            time.sleep(delay)
            delay = min(delay * 2, 2)
        raise RuntimeError(f"{self.table}: UnprocessedItems did not drain after 10 attempts")

    def _index_ready(self) -> bool:
        idx = self.c.describe_table(TableName=self.table)["Table"].get("VectorIndexes", [])
        return bool(idx) and all(i["IndexStatus"] == "ACTIVE" and not i.get("Backfilling") for i in idx)

    def wait_searchable(self, probe, timeout_s=3600):
        """Wait for the index to be ACTIVE and done backfilling, then probe; SearchVectors can still raise
        ValidationException briefly after ACTIVE (api-notes §1)."""
        deadline = time.time() + timeout_s
        while True:
            try:
                if self._index_ready():
                    return self.query(probe, 1, None, False)
            except self.c.exceptions.ClientError as e:
                if e.response["Error"]["Code"] != "ValidationException":
                    raise
            if time.time() > deadline:
                raise TimeoutError(f"{self.table}: vector index not searchable after {timeout_s}s")
            time.sleep(30)

    def query(self, vec, k, attr, need_doc):
        r = self.c.search_vectors(**search_request(self.table, vec, k, attr, need_doc))
        hits = [(h["Item"]["id"]["S"], h["Item"].get("doc_id", {}).get("S")) for h in r["SearchResults"]]
        return hits, json.dumps(r.get("ConsumedCapacity", {}), default=str)


def docs_load(rows: list[dict]) -> None:
    t = boto3.resource("dynamodb", region_name=REGION, config=_CFG).Table(env("DOCS_TABLE"))
    with t.batch_writer() as w:
        for r in rows:
            w.put_item(Item={"doc_id": r["doc_id"], "title": r["title"], "score": int(r["score"]), "extra": r["extra"]})


def docs_get(doc_ids: list[str]) -> list[dict]:
    keys = [{"doc_id": {"S": d}} for d in dict.fromkeys(d for d in doc_ids if d)]
    if not keys:
        return []
    table, c = env("DOCS_TABLE"), _client()
    pending, out, delay = {table: {"Keys": keys}}, [], 0.05
    for _ in range(8):
        r = c.batch_get_item(RequestItems=pending)
        out += r["Responses"].get(table, [])
        pending = r.get("UnprocessedKeys")
        if not pending:
            return out
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"{table}: UnprocessedKeys did not drain after 8 attempts")
