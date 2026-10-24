import json
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
import numpy as np
from botocore.config import Config
from botocore.exceptions import ClientError

from fv.config import DIM, REGION
from fv.vec import normalize

# Cohere via the US cross-region profile: 300k tokens/min vs 150k in-region (Service Quotas, 2026-10-08)
MODEL_IDS = {"cohere": "us.cohere.embed-v4:0", "nova": "amazon.nova-2-multimodal-embeddings-v1:0"}
COHERE_BATCH = 96  # max texts per Cohere v4 call


def cohere_body(texts, purpose):
    return {"texts": list(texts), "input_type": "search_document" if purpose == "doc" else "search_query",
            "embedding_types": ["float"], "output_dimension": DIM, "truncate": "RIGHT"}


def cohere_parse(body):
    emb = body["embeddings"]
    return emb["float"] if isinstance(emb, dict) else emb  # both response shapes exist (api-notes §5)


def nova_body(text, purpose):
    return {"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": {
        "embeddingPurpose": "GENERIC_INDEX" if purpose == "doc" else "GENERIC_RETRIEVAL",
        "embeddingDimension": DIM, "text": {"truncationMode": "END", "value": text}}}


def nova_parse(body):
    return body["embeddings"][0]["embedding"]


class BedrockEmbedder:
    def __init__(self, model: str, workers: int | None = None):
        # Cohere sends 96 texts per call (~30k tokens): 4 in flight stays near the token quota
        self.model, self.workers = model, workers or (4 if model == "cohere" else 16)
        self.client = boto3.client("bedrock-runtime", region_name=REGION, config=Config(
            retries={"mode": "adaptive", "max_attempts": 12}, max_pool_connections=self.workers * 2))

    def _invoke(self, body, waits: int = 40):
        # botocore's adaptive retries give up under a token-quota storm; wait it out instead
        for n in range(waits + 1):
            try:
                r = self.client.invoke_model(modelId=MODEL_IDS[self.model], body=json.dumps(body))
                return json.loads(r["body"].read())
            except ClientError as e:
                if e.response["Error"]["Code"] != "ThrottlingException" or n == waits:
                    raise
                time.sleep(min(60, 5 * (n + 1)))

    def _call(self, batch, purpose):
        if self.model == "cohere":
            return cohere_parse(self._invoke(cohere_body(batch, purpose)))
        return [nova_parse(self._invoke(nova_body(t, purpose))) for t in batch]

    def __call__(self, texts, purpose, corpus):
        size = COHERE_BATCH if self.model == "cohere" else 8
        batches = [texts[i:i + size] for i in range(0, len(texts), size)]
        with ThreadPoolExecutor(self.workers) as ex:
            parts = list(ex.map(lambda b: self._call(b, purpose), batches))
        return normalize(np.array([v for p in parts for v in p], dtype=np.float32))
