"""Fill the search table with Ley Chile articles and their Cohere Embed v4 vectors.

Student mode (default): fetches the 11 norms from BCN (one polite request each, cached), chunks them and embeds
them with Bedrock (one-time cost ~USD 0.31). Event mode: --vectors reuses the lab's saved vectors (no Bedrock cost).
Reuses the lab runner's tested code. Run through deploy.sh, or:
  uv run --project ../runner python load.py --table <name> [--region us-east-1]
"""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import boto3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "runner"))
from fv.corpus import ley  # noqa: E402
from fv.io import read_jsonl  # noqa: E402

BCN = "https://www.bcn.cl/leychile/navegar?idNorma={}"


def items(chunks, docs, vecs):
    titles = {d["doc_id"]: d["title"] for d in docs or []}
    for c, v in zip(chunks, vecs, strict=True):
        # event corpora (event-stores/build_corpora.py) carry their own title and link; Ley-only rows get BCN's
        url = c.get("url") or BCN.format(c["doc_id"].split("#n", 1)[1])
        yield {"id": {"S": c["id"]}, "attr": {"S": c["attr"]}, "doc_id": {"S": c["doc_id"]},
               "title": {"S": c.get("title") or titles[c["doc_id"]]},
               "text": {"S": c["text"]}, "url": {"S": url},
               "v": {"L": [{"N": repr(float(x))} for x in v]}}


def write(table: str, region: str, rows: list) -> None:
    ddb = boto3.client("dynamodb", region_name=region)

    def batch(part):
        req = {table: [{"PutRequest": {"Item": it}} for it in part]}
        while req:
            req = ddb.batch_write_item(RequestItems=req).get("UnprocessedItems") or None

    parts = [rows[i:i + 25] for i in range(0, len(rows), 25)]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(batch, parts))


def embed_credentials(profile: str) -> dict:
    """Short-lived keys from the AWS CLI (it refreshes SSO sessions that boto3 sometimes cannot)."""
    out = subprocess.run(["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
                         capture_output=True, text=True, check=True).stdout
    c = json.loads(out)
    return {"aws_access_key_id": c["AccessKeyId"], "aws_secret_access_key": c["SecretAccessKey"],
            "aws_session_token": c.get("SessionToken")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--work", default=os.path.join(HERE, ".data"))
    ap.add_argument("--vectors", help="event mode: saved docs.npy (with --chunks and --docs from the lab)")
    ap.add_argument("--chunks")
    ap.add_argument("--docs")
    ap.add_argument("--docs-table", help="event mode: also write doc_id -> title here")
    ap.add_argument("--embed-profile", help="embed under another AWS profile (e.g. an account with Bedrock quota)")
    a = ap.parse_args(argv)
    if a.vectors:
        chunks, docs, vecs = read_jsonl(a.chunks), (read_jsonl(a.docs) if a.docs else []), np.load(a.vectors)
    else:
        out = os.path.join(a.work, "ley")
        stats = ley.prepare(os.path.join(HERE, "..", "data-sources", "ley-norms.tsv"), os.path.join(a.work, "raw"), out)
        print("corpus:", stats, flush=True)
        chunks, docs = read_jsonl(os.path.join(out, "chunks.jsonl")), read_jsonl(os.path.join(out, "docs.jsonl"))
        os.environ.setdefault("AWS_REGION", a.region)
        from fv.embed import embed_all, get_embedder
        if a.embed_profile:  # embedder builds its clients from the default session; writes below keep this process's own credentials
            boto3.setup_default_session(**embed_credentials(a.embed_profile), region_name=a.region)
        vecs = embed_all(get_embedder("cohere"), [c["text"] for c in chunks], "doc", "ley", os.path.join(a.work, "emb"))
        boto3.setup_default_session(region_name=a.region)
    rows = list(items(chunks, docs, vecs))
    write(a.table, a.region, rows)
    if a.docs_table:  # event mode: doc_id -> title, the giveaway's "contexto" round reads it
        seen = {r["doc_id"]["S"]: r["title"]["S"] for r in rows}
        write(a.docs_table, a.region, [{"doc_id": {"S": d}, "title": {"S": t}} for d, t in seen.items()])
    print(json.dumps({"table": a.table, "items": len(rows)}))


if __name__ == "__main__":
    main()
