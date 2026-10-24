import pytest
from pytest import approx

from fv.cost import GB, TB, at_rest_month, capacity_per_1k, ddb_per_1k, ddb_search_bytes, load_prices, s3v_per_1k


def test_load_prices(tmp_path):
    p = tmp_path / "prices.md"
    p.write_text("| item | usd | source |\n|---|---|---|\n| s3v_query_request | 0.0000025 | x |\n")
    assert load_prices(str(p)) == {"s3v_query_request": 2.5e-06}


def test_load_prices_refuses_todo(tmp_path):
    p = tmp_path / "prices.md"
    p.write_text("| item | usd | source |\n|---|---|---|\n| aoss_ocu_hour | TODO | |\n")
    with pytest.raises(ValueError, match="aoss_ocu_hour"):
        load_prices(str(p))


def test_ddb_bytes_from_consumed_capacity():
    assert ddb_search_bytes('{"TableName": "t", "VectorSearchRequestBytes": 524288}') == 524288
    assert ddb_search_bytes("") == 0


def test_ddb_per_1k_applies_1kb_minimum():
    p = {"ddb_vector_search_gb": 0.002}
    assert ddb_per_1k([10] * 1000, p) == approx(1000 * 1024 / GB * 0.002)


def test_s3v_per_1k():
    p = {"s3v_query_request": 2.5e-06, "s3v_processed_tb_first_100k": 0.004, "s3v_returned_gb": 0.01}
    per_query = 2.5e-06 + 100_000 * (1024 * 4 + 100) / TB * 0.004 + 400 / GB * 0.01
    assert abs(s3v_per_1k(100_000, 1024, 100, 400, p) - 1000 * per_query) < 1e-12


def test_capacity_and_rest():
    assert capacity_per_1k(2.0, 4000, 0.24) == approx(0.12)
    assert at_rest_month(GB * 3, 0.06) == approx(0.18)


def test_ddb_bytes_nested_and_by_name():
    assert ddb_search_bytes('{"Table": {"VectorSearchRequestBytes": 2048}}') == 2048
    both = '{"VectorWriteRequestBytes": 99, "Table": {"VectorSearchRequestBytes": 2048}}'
    assert ddb_search_bytes(both) == 2048
    assert ddb_search_bytes('{"VectorWriteRequestBytes": 99}') == 0
