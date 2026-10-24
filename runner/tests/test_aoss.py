import numpy as np

from fv.stores import aoss


def test_index_body_is_uncompressed_cosine():
    f = aoss.index_body()["mappings"]["properties"]["v"]
    assert f == {"type": "knn_vector", "dimension": 1024, "space_type": "cosinesimil", "compression_level": "1x"}


def test_knn_body_filter_and_source():
    b = aoss.knn_body(np.array([0.5], dtype=np.float32), 10, "python", need_doc=True)
    assert b["size"] == 10 and b["_source"] == ["doc_id"]
    assert b["query"]["knn"]["v"] == {"vector": [0.5], "k": 10, "filter": {"term": {"attr": "python"}}}
    assert aoss.knn_body(np.array([0.5], dtype=np.float32), 10, None, need_doc=False)["_source"] is False


def test_bulk_actions_use_chunk_id():
    a = list(aoss.bulk_actions("so-x", [{"id": "c0", "attr": "py", "doc_id": "d"}], np.ones((1, 1), dtype=np.float32)))
    assert a == [{"_index": "so-x", "_id": "c0", "_source": {"v": [1.0], "attr": "py", "doc_id": "d"}}]


class _Indices:
    def __init__(self, exists):
        self._exists, self.created = exists, []

    def exists(self, index):
        return self._exists

    def create(self, index, body):
        self.created.append(index)


def _store(exists):
    s = object.__new__(aoss.AossStore)
    s.index, s.c = "so-m", type("C", (), {"indices": _Indices(exists)})()
    return s


def test_create_index_skips_when_exists():
    s = _store(True)
    s.create_index()
    assert s.c.indices.created == []
    s = _store(False)
    s.create_index()
    assert s.c.indices.created == ["so-m"]
