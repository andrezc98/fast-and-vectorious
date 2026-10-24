import numpy as np

from fv.stores import s3v


def test_put_batches_size_and_shape():
    rows = [{"id": f"c{i}", "attr": "py", "doc_id": "d"} for i in range(3)]
    vecs = np.ones((3, 2), dtype=np.float32)
    batches = list(s3v.put_batches(rows, vecs, size=2))
    assert [len(b) for b in batches] == [2, 1]
    assert batches[0][0] == {"key": "c0", "data": {"float32": [1.0, 1.0]}, "metadata": {"attr": "py", "doc_id": "d"}}


def test_query_request_filter_uses_enhanced_mode():
    v = np.array([0.5], dtype=np.float32)
    r = s3v.query_request("b", "so-cohere", v, 10, "python", need_doc=False)
    assert r["filter"] == {"attr": {"$eq": "python"}} and r["queryMode"] == "ENHANCED"
    assert r["returnMetadata"] is False and r["topK"] == 10
    plain = s3v.query_request("b", "so-cohere", v, 10, None, need_doc=True)
    assert "filter" not in plain and "queryMode" not in plain and plain["returnMetadata"] is True


def test_put_batches_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        list(s3v.put_batches([{"id": "c", "attr": "a", "doc_id": "d"}] * 3, np.ones((2, 2), dtype=np.float32)))
