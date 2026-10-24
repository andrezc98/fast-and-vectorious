import time
from urllib.parse import urlparse

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection, helpers

from fv.config import DIM, REGION, env


def index_body() -> dict:
    return {"settings": {"index": {"knn": True}},
            "mappings": {"properties": {
                "v": {"type": "knn_vector", "dimension": DIM, "space_type": "cosinesimil", "compression_level": "1x"},
                "attr": {"type": "keyword"}, "doc_id": {"type": "keyword"}}}}


def knn_body(vec, k, attr, need_doc) -> dict:
    knn = {"vector": [float(x) for x in vec], "k": k}
    if attr is not None:
        knn["filter"] = {"term": {"attr": attr}}
    return {"size": k, "_source": ["doc_id"] if need_doc else False, "query": {"knn": {"v": knn}}}


def bulk_actions(index, rows, vecs):
    for row, v in zip(rows, vecs, strict=True):
        yield {"_index": index, "_id": row["id"], "_source": {"v": [float(x) for x in v], "attr": row["attr"], "doc_id": row["doc_id"]}}


class AossStore:
    def __init__(self, corpus: str, model: str):
        self.name, self.index = "aoss", f"{corpus}-{model}"
        host = urlparse(env("AOSS_ENDPOINT")).hostname
        auth = AWSV4SignerAuth(boto3.Session().get_credentials(), REGION, "aoss")
        self.c = OpenSearch(hosts=[{"host": host, "port": 443}], http_auth=auth, use_ssl=True, verify_certs=True,
                            connection_class=RequestsHttpConnection, pool_maxsize=20, timeout=120)

    def create_index(self):
        if self.c.indices.exists(index=self.index):
            return
        self.c.indices.create(index=self.index, body=index_body())

    def load(self, rows, vecs):
        helpers.bulk(self.c, bulk_actions(self.index, rows, vecs), chunk_size=200, max_retries=5)

    def wait_count(self, n, timeout_s=1800):
        deadline = time.time() + timeout_s
        while self.c.count(index=self.index)["count"] < n:
            if time.time() > deadline:
                raise TimeoutError(f"{self.index} below {n} docs")
            time.sleep(15)

    def query(self, vec, k, attr, need_doc):
        r = self.c.search(index=self.index, body=knn_body(vec, k, attr, need_doc))
        return [(h["_id"], (h.get("_source") or {}).get("doc_id")) for h in r["hits"]["hits"]], ""
