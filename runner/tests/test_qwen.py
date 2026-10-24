import numpy as np

from fv.embed import qwen


def test_query_text_follows_model_card_format():
    assert qwen.query_text("¿qué es?", "so") == "Instruct: " + qwen.INSTRUCTIONS["so"] + "\nQuery:¿qué es?"


def test_embedder_prefixes_queries_only_and_truncates(monkeypatch):
    sent = []

    def fake_invoke(texts):
        sent.append(texts)
        return [[3.0, 4.0] + [0.0] * 4094 for _ in texts]

    e = qwen.QwenEmbedder.__new__(qwen.QwenEmbedder)
    monkeypatch.setattr(e, "_invoke", fake_invoke, raising=False)
    out = e(["doc a"], "doc", "so")
    assert sent[-1] == ["doc a"]
    assert out.shape == (1, 1024) and np.isclose(np.linalg.norm(out[0]), 1.0)
    e(["q"], "query", "ley")
    assert sent[-1][0].startswith("Instruct: ")


from botocore.exceptions import ClientError  # noqa: E402

DELETES = ["delete_endpoint", "delete_endpoint_config", "delete_model"]


def _err(code, msg):
    return ClientError({"Error": {"Code": code, "Message": msg}}, "Op")


class FakeSM:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}  # op name -> exception to raise

    def _do(self, op, **kw):
        if op.startswith("describe"):  # status reads are not part of the delete order
            return {}
        self.calls.append(op)
        if op in self.fail:
            raise self.fail[op]
        return {}

    def __getattr__(self, op):
        return lambda **kw: self._do(op, **kw)

    def get_waiter(self, name):
        return type("W", (), {"wait": lambda _self, **kw: self._do("wait")})()


def _env(monkeypatch):
    monkeypatch.setenv("FV_RUNNER", "1")
    monkeypatch.setenv("FV_SAGEMAKER_ROLE_ARN", "arn:test")
    monkeypatch.setenv("FV_TEI_IMAGE", "img:test")


def test_endpoint_down_deletes_all_three_in_order(monkeypatch):
    _env(monkeypatch)
    fake = FakeSM()
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    qwen.endpoint_down()
    assert fake.calls == DELETES


def test_endpoint_down_tolerates_not_found_and_continues(monkeypatch):
    _env(monkeypatch)
    fake = FakeSM({"delete_endpoint": _err("ValidationException", "Could not find endpoint: fv-qwen.")})
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    qwen.endpoint_down()
    assert fake.calls == DELETES


def test_endpoint_down_raises_other_error_after_all_deletes(monkeypatch):
    _env(monkeypatch)
    fake = FakeSM({"delete_endpoint": _err("ThrottlingException", "Rate exceeded")})
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    try:
        qwen.endpoint_down()
    except ClientError as e:
        assert e.response["Error"]["Code"] == "ThrottlingException"
    else:
        raise AssertionError("expected ThrottlingException to be raised")
    assert fake.calls == DELETES


def test_endpoint_up_cleans_up_and_reraises_on_create_failure(monkeypatch):
    _env(monkeypatch)
    fake = FakeSM({"create_endpoint": _err("ThrottlingException", "Rate exceeded")})
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    try:
        qwen.endpoint_up()
    except ClientError as e:
        assert e.response["Error"]["Code"] == "ThrottlingException"
    else:
        raise AssertionError("expected ClientError to be re-raised")
    assert fake.calls == ["create_model", "create_endpoint_config", "create_endpoint"] + DELETES


def test_endpoint_down_waits_while_creating(monkeypatch):
    # 2026-10-08: DeleteEndpoint on a Creating endpoint fails ("Cannot update in-progress endpoint").
    _env(monkeypatch)
    states = iter(["Creating", "Creating", "InService"])
    fake = FakeSM()
    fake.describe_endpoint = lambda **kw: (fake.calls.append("describe"), {"EndpointStatus": next(states)})[1]
    sleeps = []
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    monkeypatch.setattr(qwen.time, "sleep", sleeps.append)
    qwen.endpoint_down()
    assert fake.calls == ["describe", "describe", "describe"] + DELETES and len(sleeps) == 2


class _UpSM(FakeSM):
    """describe_endpoint walks a scripted list of states; Failed carries a FailureReason."""

    def __init__(self, states, reason="Unable to provision requested ML compute capacity."):
        super().__init__()
        self.states, self.reason = iter(states), reason

    def describe_endpoint(self, **kw):
        st = next(self.states, "Gone")
        if st == "Gone":
            raise _err("ValidationException", 'Could not find endpoint "fv-qwen".')
        return {"EndpointStatus": st, **({"FailureReason": self.reason} if st == "Failed" else {})}


def test_endpoint_up_returns_when_in_service(monkeypatch):
    _env(monkeypatch)
    fake = _UpSM(["Creating", "Creating", "InService"])
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    monkeypatch.setattr(qwen.time, "sleep", lambda s: None)
    qwen.endpoint_up()
    assert fake.calls == ["create_model", "create_endpoint_config", "create_endpoint"]  # no teardown


def test_endpoint_up_reports_failure_reason_then_tears_down(monkeypatch):
    # 2026-10-08: the reason was lost because cleanup deleted the endpoint before anyone read it.
    import pytest
    _env(monkeypatch)
    fake = _UpSM(["Creating", "Failed", "Failed"])
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    monkeypatch.setattr(qwen.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="Unable to provision"):
        qwen.endpoint_up()
    assert fake.calls[-3:] == DELETES


def test_endpoint_down_waits_while_deleting(monkeypatch):
    # 2026-10-08: a second `qwen down` hit "Cannot update in-progress endpoint" while the first delete ran.
    _env(monkeypatch)
    fake = _UpSM(["Deleting", "Deleting"])  # then "Gone"
    sleeps = []
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    monkeypatch.setattr(qwen.time, "sleep", sleeps.append)
    qwen.endpoint_down()  # returns once the endpoint is gone; the deletes then tolerate not-found
    assert len(sleeps) == 2 and fake.calls == DELETES


def test_endpoint_config_uses_instance_pools_with_fallback(monkeypatch):
    # 2026-10-08: g6e.xlarge and g6.2xlarge both hit InsufficientInstanceCapacity; pools fall back on ICE.
    _env(monkeypatch)
    seen = {}
    fake = _UpSM(["InService"])
    fake.create_endpoint_config = lambda **kw: (fake.calls.append("create_endpoint_config"), seen.update(kw))
    monkeypatch.setattr(qwen, "_sm", lambda: fake)
    monkeypatch.setattr(qwen.time, "sleep", lambda s: None)
    qwen.endpoint_up()
    v = seen["ProductionVariants"][0]
    assert "InstanceType" not in v and v["VariantInstanceProvisionTimeoutInSeconds"] <= 3600
    pools = sorted(v["InstancePools"], key=lambda p: p["Priority"])
    assert [p["Priority"] for p in pools] == list(range(1, len(pools) + 1)) and 2 <= len(pools) <= 5
    assert pools[0]["InstanceType"] == "ml.g6.2xlarge"


def test_qwen_region_override(monkeypatch):
    # 2026-10-08: us-east-1 had no GPU capacity on four instance types; the endpoint can live elsewhere.
    regions = []
    monkeypatch.setattr(qwen.boto3, "client", lambda svc, region_name=None, **k: regions.append((svc, region_name)))
    monkeypatch.delenv("FV_QWEN_REGION", raising=False)
    qwen._sm()
    monkeypatch.setenv("FV_QWEN_REGION", "us-west-2")
    qwen._sm()
    qwen.QwenEmbedder()
    assert regions == [("sagemaker", "us-east-1"), ("sagemaker", "us-west-2"), ("sagemaker-runtime", "us-west-2")]
