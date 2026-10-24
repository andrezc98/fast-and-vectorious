import json

def load_prices(path: str) -> dict[str, float]:
    prices = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or cells[0] in ("item", "") or set(cells[0]) <= {"-"}:
                continue
            if cells[1] == "TODO":
                raise ValueError(f"price for {cells[0]} is still TODO in {path}")
            prices[cells[0]] = float(cells[1])
    return prices

GB = 2 ** 30
TB = 2 ** 40
DDB_MIN_BYTES = 1024  # 1 KB minimum per vector request (api-notes §1)


def ddb_search_bytes(extra: str) -> int:
    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("VectorSearchRequestBytes"), (int, float)):
                return int(o["VectorSearchRequestBytes"])
            for v in o.values():
                found = walk(v)
                if found:
                    return found
        return 0
    return walk(json.loads(extra)) if extra else 0


def ddb_per_1k(bytes_per_query: list[int], p: dict) -> float:
    billed = sum(max(b, DDB_MIN_BYTES) for b in bytes_per_query)
    return billed / GB * p["ddb_vector_search_gb"] * 1000 / len(bytes_per_query)


def s3v_per_1k(n_vectors: int, dim: int, meta_bytes: int, returned_bytes: int, p: dict) -> float:
    """S3 Vectors bills each query for the index size it processes, plus request and bytes returned
    (first-100K-vectors tier; api-notes §2)."""
    processed = n_vectors * (dim * 4 + meta_bytes) / TB * p["s3v_processed_tb_first_100k"]
    per_query = p["s3v_query_request"] + processed + returned_bytes / GB * p["s3v_returned_gb"]
    return per_query * 1000


def capacity_per_1k(unit_hours: float, queries: int, price_per_unit_hour: float) -> float:
    return unit_hours * price_per_unit_hour / queries * 1000


def at_rest_month(storage_bytes: int, price_gb_month: float) -> float:
    return round(storage_bytes / GB * price_gb_month, 10)
