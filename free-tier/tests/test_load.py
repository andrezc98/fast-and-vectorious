import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import load  # noqa: E402


def test_items_carry_title_link_and_vector():
    chunks = [{"id": "ley#207436-67-0", "attr": "codigo-trabajo", "doc_id": "ley#n207436", "text": "Art. 67.- ..."}]
    docs = [{"doc_id": "ley#n207436", "title": "Código del Trabajo"}]
    [it] = list(load.items(chunks, docs, [[0.5, 0.25]]))
    assert it["title"]["S"] == "Código del Trabajo"
    assert it["url"]["S"] == "https://www.bcn.cl/leychile/navegar?idNorma=207436"
    assert it["v"]["L"] == [{"N": "0.5"}, {"N": "0.25"}] and it["id"]["S"] == "ley#207436-67-0"


def test_items_refuse_mismatched_lengths():
    with pytest.raises(ValueError):
        list(load.items([{"id": "a", "attr": "x", "doc_id": "ley#n1", "text": ""}], [{"doc_id": "ley#n1", "title": "T"}], []))
