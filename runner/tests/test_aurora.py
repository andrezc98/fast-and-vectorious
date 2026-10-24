import pytest

from fv.stores import aurora


def test_schema_and_index():
    s = " ".join(aurora.schema_sql("so", "cohere"))
    assert "CREATE EXTENSION IF NOT EXISTS vector" in s and "vector(1024)" in s and "so_docs" in s
    i = " ".join(aurora.index_sql("so", "cohere"))
    assert "USING hnsw (v vector_cosine_ops)" in i and "(attr)" in i


def test_query_sql():
    assert aurora.query_sql("so", "cohere", False) == "SELECT id, doc_id FROM so_cohere_chunks ORDER BY v <=> %s LIMIT %s"
    assert "WHERE attr = %s" in aurora.query_sql("so", "cohere", True)


def test_join_sql_joins_docs_once():
    j = aurora.join_sql("ley", "nova")
    assert "JOIN ley_docs d USING (doc_id)" in j and j.count("LIMIT %s") == 1


@pytest.mark.parametrize("corpus,model", [("so; DROP TABLE x", "cohere"), ("so", "co-here"), ("so", ""), ("SO", "nova")])
def test_table_names_reject_unsafe_strings(corpus, model):
    with pytest.raises(ValueError):
        aurora.schema_sql(corpus, model)


def test_load_truncates_before_copy():
    log = []

    class Cp:
        def __enter__(self):
            log.append("COPY")
            return self

        def __exit__(self, *a):
            pass

        def set_types(self, t):
            pass

        def write_row(self, r):
            pass

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def copy(self, sql):
            return Cp()

    class Conn:
        def execute(self, sql, *a):
            log.append(sql)

        def cursor(self):
            return Cur()

    s = object.__new__(aurora.AuroraStore)
    s.corpus, s.model, s.conn = "so", "cohere", Conn()
    import numpy as np
    s.load([{"id": "c", "doc_id": "d", "attr": "a"}], np.ones((1, 2), dtype=np.float32))
    assert log == ["TRUNCATE so_cohere_chunks", "COPY"]


def test_index_build_memory_fits_low_acu():
    # 2026-10-08: '4GB' failed on the SO load: "could not resize shared memory segment ... to 4291896512 bytes"
    # (parallel HNSW builds put maintenance_work_mem in shared memory; Serverless v2 was near min ACU).
    from fv.stores.aurora import index_sql
    sets = [s for s in index_sql("so", "cohere") if s.startswith("SET maintenance_work_mem")]
    assert sets == ["SET maintenance_work_mem = '1GB'"]  # 100k x 1024 float32 = ~410 MB: graph still fits
