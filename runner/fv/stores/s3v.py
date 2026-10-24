import boto3
from botocore.config import Config

from fv.config import REGION, env


def index_name(corpus: str, model: str) -> str:
    return f"{corpus}-{model}"


def put_batches(rows, vecs, size=500):  # 500 vectors / 20 MiB per PutVectors (api-notes §2)
    batch = []
    for row, v in zip(rows, vecs, strict=True):
        batch.append({"key": row["id"], "data": {"float32": [float(x) for x in v]},
                      "metadata": {"attr": row["attr"], "doc_id": row["doc_id"]}})
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def query_request(bucket, index, vec, k, attr, need_doc) -> dict:
    r = {"vectorBucketName": bucket, "indexName": index, "topK": k,
         "queryVector": {"float32": [float(x) for x in vec]}, "returnMetadata": need_doc, "returnDistance": False}
    if attr is not None:
        r |= {"filter": {"attr": {"$eq": attr}}, "queryMode": "ENHANCED"}  # prefilter, fair vs SQL WHERE
    return r


class S3VectorsStore:
    def __init__(self, corpus: str, model: str):
        self.name, self.bucket, self.index = "s3v", env("VECTOR_BUCKET"), index_name(corpus, model)
        self.c = boto3.client("s3vectors", region_name=REGION,
                              config=Config(retries={"mode": "adaptive", "max_attempts": 10}, max_pool_connections=32))

    def load(self, rows, vecs):
        for batch in put_batches(rows, vecs):
            self.c.put_vectors(vectorBucketName=self.bucket, indexName=self.index, vectors=batch)

    def query(self, vec, k, attr, need_doc):
        r = self.c.query_vectors(**query_request(self.bucket, self.index, vec, k, attr, need_doc))
        return [(v["key"], (v.get("metadata") or {}).get("doc_id")) for v in r["vectors"]], ""
