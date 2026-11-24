"""Offline tables for the Builder Center article. No AWS calls.

Reads the committed bench CSVs (results/2026-10-09) plus the per-query filter sizes in
article/query-selectivity-<corpus>.csv, and prints the tables the article quotes.

    python3 article/analysis.py            # print tables
    python3 article/analysis.py --export   # also rebuild query-selectivity CSVs from the local corpus
"""
import collections
import csv
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "results", "2026-10-09")
STORES = {"aurora": "Aurora pgvector", "ddb": "DynamoDB", "aoss": "OpenSearch Serverless", "s3v": "S3 Vectors"}
CORPUS_SIZE = {"so": 100_000, "ley": 8_851}
BUCKETS = [(0, 0.001, "< 0.1%"), (0.001, 0.01, "0.1-1%"), (0.01, 0.05, "1-5%"), (0.05, 1.01, ">= 5%")]


def p(vals, q):  # percentile by rounded index into the sorted list, same rule for every store
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def rows(tag):
    with open(os.path.join(RES, f"bench-{tag}.csv")) as f:
        return list(csv.DictReader(f))


def baseline_table():
    print("## Baseline (3 reps x 1,000 queries per cell; warm-up and ef sweeps excluded)\n")
    print("| corpus | case | store | ok | errors | p50 ms | p95 ms | recall@10 |")
    print("|---|---|---|---|---|---|---|---|")
    for corpus in ("so", "ley"):
        for case in ("plain", "filter", "join"):
            for s in STORES:
                rs = rows(f"{s}-{corpus}-cohere-{case}")
                ok = [r for r in rs if r["ok"] == "True"]
                lat = [float(r["latency_ms"]) for r in ok]
                rec = st.mean(float(r["recall"]) for r in ok)
                print(f"| {corpus} | {case} | {STORES[s]} | {len(ok)} | {len(rs) - len(ok)} | "
                      f"{st.median(lat):.2f} | {p(lat, .95):.2f} | {rec:.4f} |")
    print("\n## Aurora ef_search sweep (so)\n")
    print("| case | ef_search | p50 ms | p95 ms | recall@10 |")
    print("|---|---|---|---|---|")
    for case in ("plain", "filter"):
        for ef, tag in ((40, ""), (100, "-ef100"), (200, "-ef200")):
            rs = rows(f"aurora-so-cohere-{case}{tag}")
            lat = [float(r["latency_ms"]) for r in rs]
            print(f"| {case} | {ef} | {st.median(lat):.2f} | {p(lat, .95):.2f} | "
                  f"{st.mean(float(r['recall']) for r in rs):.4f} |")


def selectivity(corpus):
    with open(os.path.join(HERE, f"query-selectivity-{corpus}.csv")) as f:
        return {int(r["qi"]): int(r["matching_rows"]) for r in csv.DictReader(f)}


def selectivity_table(corpus):
    sel, n = selectivity(corpus), CORPUS_SIZE[corpus]
    print(f"\n## Filtered queries by share of the index that matches the filter ({corpus})\n")
    counts = collections.Counter(next(b[2] for b in BUCKETS if b[0] <= m / n < b[1]) for m in sel.values())
    print("| store | " + " | ".join(f"{b[2]} ({counts.get(b[2], 0)} q)" for b in BUCKETS) + " |")
    print("|---|" + "---|" * len(BUCKETS))
    out = {}
    for s in STORES:
        agg = collections.defaultdict(lambda: ([], []))
        for r in rows(f"{s}-{corpus}-cohere-filter"):
            share = sel[int(r["qi"])] / n
            b = next(b[2] for b in BUCKETS if b[0] <= share < b[1])
            agg[b][0].append(float(r["latency_ms"]))
            agg[b][1].append(float(r["recall"]))
        cells = []
        for b in BUCKETS:
            if b[2] in agg:
                lat, rec = agg[b[2]]
                cells.append(f"{st.mean(rec):.3f} / {st.median(lat):.1f} ms")
                out[(s, b[2])] = (st.mean(rec), st.median(lat), len(lat) // 3)
            else:
                cells.append("-")
        print(f"| {STORES[s]} | " + " | ".join(cells) + " |")
    print("\n(cell = mean recall@10 / p50 latency)")
    return out


def detail_tables():
    sel = {}
    with open(os.path.join(HERE, "query-selectivity-so.csv")) as f:
        for r in csv.DictReader(f):
            sel[int(r["qi"])] = (r["attr"], int(r["matching_rows"]))
    print("\n## Two tags (so, filter): mean recall@10 / p50 over every request for that tag\n")
    print("| store | asp.net | javascript | rare tags (< 0.1%) with >= 10 matching rows |")
    print("|---|---|---|---|")
    for s in STORES:
        g = collections.defaultdict(lambda: ([], []))
        for r in rows(f"{s}-so-cohere-filter"):
            attr, n = sel[int(r["qi"])]
            for key in (attr, "rare10" if 10 <= n < 100 else None):
                if key:
                    g[key][0].append(float(r["recall"]))
                    g[key][1].append(float(r["latency_ms"]))
        cell = lambda k: f"{st.mean(g[k][0]):.3f} / {st.median(g[k][1]):.1f} ms ({len(g[k][0])} req)"
        print(f"| {STORES[s]} | {cell('asp.net')} | {cell('javascript')} | {st.mean(g['rare10'][0]):.3f} |")


def export(corpus):
    base = os.path.expanduser(f"~/fv-data/out/{corpus}")
    with open(os.path.join(base, "chunks.jsonl")) as f:
        cnt = collections.Counter(json.loads(line)["attr"] for line in f)
    with open(os.path.join(base, "queries.jsonl")) as f:
        qs = [json.loads(line) for line in f]
    with open(os.path.join(HERE, f"query-selectivity-{corpus}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qi", "qid", "attr", "matching_rows"])
        for i, q in enumerate(qs):
            w.writerow([i, q["qid"], q["attr"], cnt[q["attr"]]])


if __name__ == "__main__":
    if "--export" in sys.argv:
        for c in CORPUS_SIZE:
            export(c)
    baseline_table()
    for c in CORPUS_SIZE:
        selectivity_table(c)
    detail_tables()
