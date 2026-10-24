import numpy as np
import pytest

from fv.embed import embed_all
from fv.embed.bedrock import cohere_body, cohere_parse, nova_body, nova_parse


def test_cohere_body_purpose_and_dim():
    b = cohere_body(["a", "b"], "query")
    assert b == {"texts": ["a", "b"], "input_type": "search_query", "embedding_types": ["float"],
                 "output_dimension": 1024, "truncate": "RIGHT"}
    assert cohere_body(["a"], "doc")["input_type"] == "search_document"


def test_cohere_parse_both_shapes():
    assert cohere_parse({"embeddings": [[1.0, 2.0]]}) == [[1.0, 2.0]]
    assert cohere_parse({"embeddings": {"float": [[1.0, 2.0]]}}) == [[1.0, 2.0]]


def test_nova_body_and_parse():
    b = nova_body("hola", "doc")
    assert b["singleEmbeddingParams"]["embeddingPurpose"] == "GENERIC_INDEX"
    assert b["singleEmbeddingParams"]["embeddingDimension"] == 1024
    assert nova_body("hola", "query")["singleEmbeddingParams"]["embeddingPurpose"] == "GENERIC_RETRIEVAL"
    assert nova_parse({"embeddings": [{"embedding": [0.5]}]}) == [0.5]


def test_embed_all_resumes_and_keeps_order(tmp_path):
    calls = []

    def fake(texts, purpose, corpus):
        calls.append(list(texts))
        return np.array([[float(t), 1.0] for t in texts], dtype=np.float32)

    texts = [str(i) for i in range(5)]
    np.save(tmp_path / "shard_00000.npy", np.array([[9.0, 9.0], [9.0, 9.0]], dtype=np.float32))  # pretend shard 0 finished
    out = embed_all(fake, texts, "doc", "so", str(tmp_path), shard=2)
    assert calls == [["2", "3"], ["4"]]
    assert out.shape == (5, 2) and out[0][0] == 9.0 and out[4][0] == 4.0


def test_crash_mid_write_leaves_no_partial_shard(tmp_path):
    import os

    def good(texts, purpose, corpus):
        return np.array([[float(t), 1.0] for t in texts], dtype=np.float32)

    def crash(texts, purpose, corpus):
        # unpicklable object array: np.save starts writing the file, then pickling fails mid-write
        return np.array([lambda: 0], dtype=object)

    texts = [str(i) for i in range(5)]
    calls = []

    def first(texts_, purpose, corpus):
        calls.append(list(texts_))
        return good(texts_, purpose, corpus) if len(calls) == 1 else crash(texts_, purpose, corpus)

    with pytest.raises(Exception):
        embed_all(first, texts, "doc", "so", str(tmp_path), shard=2)
    assert os.path.exists(tmp_path / "shard_00000.npy")
    assert not os.path.exists(tmp_path / "shard_00001.npy")

    calls2 = []

    def again(texts_, purpose, corpus):
        calls2.append(list(texts_))
        return good(texts_, purpose, corpus)

    out = embed_all(again, texts, "doc", "so", str(tmp_path), shard=2)
    assert calls2 == [["2", "3"], ["4"]]
    assert out.shape == (5, 2) and [row[0] for row in out] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_invoke_waits_out_throttling_instead_of_crashing(monkeypatch):
    # 2026-10-08: Cohere v4 throttled ("Too many tokens") past botocore's 12 retries and killed the bake-off.
    import io
    import json

    from botocore.exceptions import ClientError

    from fv.embed import bedrock

    calls, sleeps = [], []

    class Client:
        def invoke_model(self, modelId, body):
            calls.append(modelId)
            if len(calls) < 3:
                raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many tokens"}}, "InvokeModel")
            return {"body": io.BytesIO(json.dumps({"ok": 1}).encode())}
    monkeypatch.setattr(bedrock.boto3, "client", lambda *a, **k: Client())
    monkeypatch.setattr(bedrock.time, "sleep", sleeps.append)
    e = bedrock.BedrockEmbedder("cohere")
    assert e._invoke({}) == {"ok": 1} and len(calls) == 3 and len(sleeps) == 2
    assert calls[0] == "us.cohere.embed-v4:0"  # cross-region profile: 300k tokens/min vs 150k in-region


def test_invoke_reraises_other_errors(monkeypatch):
    from botocore.exceptions import ClientError

    from fv.embed import bedrock

    class Client:
        def invoke_model(self, modelId, body):
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "bad"}}, "InvokeModel")
    monkeypatch.setattr(bedrock.boto3, "client", lambda *a, **k: Client())
    monkeypatch.setattr(bedrock.time, "sleep", lambda s: None)
    with pytest.raises(ClientError):
        bedrock.BedrockEmbedder("cohere")._invoke({})
