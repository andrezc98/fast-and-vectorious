import fv.genq as genq
from fv.genq import clean, generate


def test_clean_one_question():
    assert clean('1. "¿Quién puede ser elegido diputado?"\nExtra') == "¿Quién puede ser elegido diputado?"
    assert clean("Qué plazo tiene el empleador") == "¿Qué plazo tiene el empleador?"


def test_clean_empty_returns_empty():
    assert clean("") == ""
    assert clean("   \n ") == ""
    assert clean('""') == ""


class _FakeRuntime:
    """Good question for most chunks; one raises, one returns empty text, one has no content."""

    def converse(self, modelId, messages, inferenceConfig):
        prompt = messages[0]["content"][0]["text"]
        cid = prompt.split("[")[1].split("]")[0]  # chunk text carries its id, e.g. "[c1]"
        if cid == "c2":
            raise Exception("ThrottlingException")
        if cid == "c3":
            return {"output": {"message": {"content": [{"text": ""}]}}}
        if cid == "c4":
            return {"output": {"message": {"content": []}}}
        return {"output": {"message": {"content": [{"text": f"qué dice el artículo {cid}"}]}}}


def test_generate_drops_failed_and_empty_and_keeps_qids_contiguous(monkeypatch):
    monkeypatch.setattr(genq.boto3, "client", lambda *a, **k: _FakeRuntime())
    chunks = [{"id": f"c{i}", "attr": f"attr{i}", "text": f"Artículo [c{i}] de la ley."} for i in range(1, 6)]
    rows = generate(chunks, 5, "fake-model")
    assert sorted(r["qid"] for r in rows) == ["ley#q0", "ley#q1"]  # contiguous from 0, after filtering
    assert sorted(r["source"] for r in rows) == ["c1", "c5"]  # failed c2, empty c3, no-content c4 dropped
    for r in rows:
        cid = r["source"]
        assert r["attr"] == f"attr{cid[1:]}"
        assert r["text"] == f"¿Qué dice el artículo {cid}?"


def test_generate_raises_when_nothing_kept(monkeypatch):
    import pytest

    class Bad:
        def converse(self, **k):
            raise Exception("boom")
    monkeypatch.setattr(genq.boto3, "client", lambda *a, **k: Bad())
    with pytest.raises(RuntimeError, match="0 questions.*boom"):  # first error surfaces, not just the count
        generate([{"id": "c1", "attr": "a", "text": "x"}], 1, "fake-model")


def test_generate_sends_no_temperature(monkeypatch):
    # Claude Haiku 5.5 rejects `temperature` ("deprecated for this model"), seen 2026-10-08.
    seen = []

    class Rec:
        def converse(self, modelId, messages, inferenceConfig):
            seen.append(inferenceConfig)
            return {"output": {"message": {"content": [{"text": "¿ok?"}]}}}
    monkeypatch.setattr(genq.boto3, "client", lambda *a, **k: Rec())
    generate([{"id": "c1", "attr": "a", "text": "x"}], 1, "fake-model")
    assert seen and all("temperature" not in c for c in seen)


def test_generate_reads_text_after_a_reasoning_block(monkeypatch):
    class Thinks:
        def converse(self, modelId, messages, inferenceConfig):
            return {"output": {"message": {"content": [{"reasoningContent": {"reasoningText": {"text": ""}}},
                                                       {"text": "¿Cuándo termina la existencia legal?"}]}}}
    monkeypatch.setattr(genq.boto3, "client", lambda *a, **k: Thinks())
    rows = generate([{"id": "c1", "attr": "a", "text": "x"}], 1, "fake-model")
    assert rows[0]["text"] == "¿Cuándo termina la existencia legal?"
