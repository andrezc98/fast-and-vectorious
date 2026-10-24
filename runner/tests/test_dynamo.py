import numpy as np

from fv.stores import dynamo


def test_create_table_request_on_demand_with_cosine_index():
    r = dynamo.create_table_request("fv-vec-so-cohere")
    assert r["BillingMode"] == "PAY_PER_REQUEST"
    idx = r["VectorIndexes"][0]
    assert idx["Dimensions"] == 1024 and idx["DistanceFunction"] == "COSINE"
    assert idx["SearchSchema"] == [{"AttributeName": "attr", "SearchSchemaElementType": "INLINE_FILTER"}]
    assert {"AttributeName": "attr", "AttributeType": "S"} in r["AttributeDefinitions"]


def test_search_request_filter_is_equality():
    v = np.array([0.5, 0.25], dtype=np.float32)
    r = dynamo.search_request("t", v, 10, "python", need_doc=True)
    assert r["SearchVector"] == [{"N": "0.5"}, {"N": "0.25"}]
    assert r["TopK"] == 10 and r["SearchConditionExpression"] == "#a = :a"
    assert r["ExpressionAttributeValues"] == {":a": {"S": "python"}}
    assert r["ProjectionExpression"] == "id, doc_id"
    plain = dynamo.search_request("t", v, 10, None, need_doc=False)
    assert "SearchConditionExpression" not in plain and plain["ProjectionExpression"] == "id"


def test_item_shape():
    item = dynamo.item({"id": "so#a1-0", "attr": "python", "doc_id": "so#q1"}, np.array([0.5], dtype=np.float32))
    assert item == {"id": {"S": "so#a1-0"}, "attr": {"S": "python"}, "doc_id": {"S": "so#q1"}, "v": {"L": [{"N": "0.5"}]}}


# --- retry / readiness behavior, stub client (no AWS) ---
import pytest
from botocore.exceptions import ClientError


class _Exc:
    ClientError = ClientError


class Stub:
    exceptions = _Exc

    def __init__(self, **calls):
        self.calls = {k: list(v) for k, v in calls.items()}

    def __getattr__(self, name):
        def f(**kw):
            r = self.calls[name].pop(0) if len(self.calls[name]) > 1 else self.calls[name][0]
            if isinstance(r, Exception):
                raise r
            return r
        return f


def _err(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


@pytest.fixture(autouse=True)
def _nosleep(monkeypatch):
    monkeypatch.setattr(dynamo.time, "sleep", lambda s: None)


def _store(stub):
    s = dynamo.DynamoStore.__new__(dynamo.DynamoStore)
    s.table, s.c = "t", stub
    return s


def test_docs_get_retries_unprocessed_keys(monkeypatch):
    monkeypatch.setenv("FV_DOCS_TABLE", "d")
    stub = Stub(batch_get_item=[{"Responses": {"d": [{"doc_id": {"S": "a"}}]}, "UnprocessedKeys": {"d": {"Keys": [{}]}}},
                                {"Responses": {"d": [{"doc_id": {"S": "b"}}]}}])
    monkeypatch.setattr(dynamo, "_client", lambda: stub)
    assert [i["doc_id"]["S"] for i in dynamo.docs_get(["a", "b"])] == ["a", "b"]


def test_docs_get_raises_when_keys_never_drain(monkeypatch):
    monkeypatch.setenv("FV_DOCS_TABLE", "d")
    stub = Stub(batch_get_item=[{"Responses": {}, "UnprocessedKeys": {"d": {"Keys": [{}]}}}])
    monkeypatch.setattr(dynamo, "_client", lambda: stub)
    with pytest.raises(RuntimeError):
        dynamo.docs_get(["a"])


def test_write_raises_after_cap():
    stub = Stub(batch_write_item=[{"UnprocessedItems": {"t": [{}]}}])
    with pytest.raises(RuntimeError):
        _store(stub)._write([{}])


def _desc(status, backfilling):
    return {"Table": {"VectorIndexes": [{"IndexName": "vec", "IndexStatus": status, "Backfilling": backfilling}]}}


def test_wait_searchable_polls_until_active_and_probe_ok():
    stub = Stub(describe_table=[_desc("CREATING", True), _desc("ACTIVE", True), _desc("ACTIVE", False)],
                search_vectors=[_err("ValidationException"), {"SearchResults": [{"Item": {"id": {"S": "x"}}}]}])
    hits, _ = _store(stub).wait_searchable(np.zeros(2, dtype=np.float32))
    assert hits == [("x", None)] and stub.calls["describe_table"] == [_desc("ACTIVE", False)]


def test_wait_searchable_reraises_other_errors():
    stub = Stub(describe_table=[_desc("ACTIVE", False)], search_vectors=[_err("AccessDeniedException")])
    with pytest.raises(ClientError):
        _store(stub).wait_searchable(np.zeros(2, dtype=np.float32))


def _store(client):
    s = object.__new__(dynamo.DynamoStore)
    s.name, s.table, s.c = "ddb", "t", client
    return s


class _Waiter:
    def __init__(self, waited):
        self.waited = waited

    def wait(self, **kw):
        self.waited.append(kw)


class _Client:
    def __init__(self, code=None):
        self.code, self.waited = code, []

    def create_table(self, **kw):
        if self.code:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "CreateTable")

    def get_waiter(self, name):
        return _Waiter(self.waited)


def test_create_tolerates_existing_table_and_still_waits():
    c = _Client("ResourceInUseException")
    _store(c).create()
    assert c.waited == [{"TableName": "t"}]


def test_create_reraises_other_errors():
    import pytest
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):
        _store(_Client("AccessDeniedException")).create()


def test_load_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        _store(_Client()).load([{"id": "c", "attr": "a", "doc_id": "d"}] * 3, np.ones((2, 2), dtype=np.float32))
