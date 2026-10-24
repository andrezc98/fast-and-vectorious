"""Build the two event corpora with the same number of chunks, each chunk carrying its title and source link.

ley: the 70 norms in data-sources/ley-norms-event.tsv. The first 8,851 chunks (the 11 lab norms) reuse the lab's
     vectors; only the new ones are embedded (Cohere Embed v4, ~USD 0.12). Run with AWS_PROFILE=fv-lab.
so:  Stack Overflow en español, whole questions drawn at random (seed 20261024, the talk date) from the lab's 100k
     chunks until the count matches Ley. Lab vectors, no Bedrock cost.
  uv run --project ../../runner python build_corpora.py
Writes ~/fv-data/event/<corpus>/{chunks.jsonl,docs.npy}.
"""
import json
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "runner"))
from fv.corpus import ley  # noqa: E402
from fv.io import read_jsonl, write_jsonl  # noqa: E402

DATA = os.path.expanduser("~/fv-data")
OUT = os.path.join(DATA, "event")
BCN = "https://www.bcn.cl/leychile/navegar?idNorma={}"


def save(corpus, rows, vecs):
    assert len(rows) == len(vecs)
    os.makedirs(os.path.join(OUT, corpus), exist_ok=True)
    write_jsonl(os.path.join(OUT, corpus, "chunks.jsonl"), rows)
    np.save(os.path.join(OUT, corpus, "docs.npy"), np.asarray(vecs, dtype=np.float32))
    print(json.dumps({"corpus": corpus, "chunks": len(rows)}), flush=True)


def build_ley():
    out = os.path.join(DATA, "out", "ley-event")
    ley.prepare(os.path.join(HERE, "..", "..", "data-sources", "ley-norms-event.tsv"), os.path.join(DATA, "ley", "raw"), out)
    chunks, docs = read_jsonl(os.path.join(out, "chunks.jsonl")), read_jsonl(os.path.join(out, "docs.jsonl"))
    old_chunks, old = read_jsonl(os.path.join(DATA, "out", "ley", "chunks.jsonl")), np.load(os.path.join(DATA, "emb", "ley", "cohere", "docs.npy"))
    assert chunks[:len(old_chunks)] == old_chunks, "lab norms changed: their vectors no longer line up"
    from fv.embed import embed_all, get_embedder
    os.environ.setdefault("AWS_REGION", "us-east-1")
    new = embed_all(get_embedder("cohere"), [c["text"] for c in chunks[len(old):]], "doc", "ley-event",
                    os.path.join(OUT, "ley-emb"))  # sharded: a rerun resumes instead of paying twice
    titles = {d["doc_id"]: d["title"] for d in docs}
    rows = [c | {"title": titles[c["doc_id"]], "url": BCN.format(c["doc_id"].split("#n", 1)[1])} for c in chunks]
    save("ley", rows, np.concatenate([old, np.asarray(new)]))
    return len(rows)


def build_so(n):
    chunks = read_jsonl(os.path.join(DATA, "out", "so", "chunks.jsonl"))
    vecs = np.load(os.path.join(DATA, "emb", "so", "cohere", "docs.npy"))
    titles = {d["doc_id"]: d["title"] for d in read_jsonl(os.path.join(DATA, "out", "so", "docs.jsonl"))}
    by_q = {}
    for i, c in enumerate(chunks):
        by_q.setdefault(c["doc_id"], []).append(i)
    qs = sorted(by_q)
    random.Random(20261024).shuffle(qs)
    pick = []
    for q in qs:
        if len(pick) >= n:
            break
        pick += by_q[q]
    pick = sorted(pick[:n])
    rows = [chunks[i] | {"title": titles[chunks[i]["doc_id"]],
                         "url": "https://es.stackoverflow.com/a/" + chunks[i]["id"].split("#a", 1)[1].split("-", 1)[0]}
            for i in pick]
    save("so", rows, vecs[pick])


if __name__ == "__main__":
    build_so(build_ley())
