import os
import random
import re
from defusedxml.ElementTree import iterparse

from fv.chunk import chunk_words, strip_html
from fv.io import write_jsonl

_TAGS = re.compile(r"[^<>|]+")


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rows(path):
    for _, el in iterparse(path, events=("end",)):
        if el.tag == "row":
            yield dict(el.attrib)
            el.clear()


def parse(posts_path: str, users_path: str):
    """Questions, answers and reputations; rows with bad ids or no body are skipped and counted
    (the 2025-06-30 dump carries deliberately corrupted rows, docs/api-notes.md §7)."""
    questions, answers, reputation, skipped = {}, [], {}, 0
    for r in _rows(posts_path):
        try:
            pid, kind, body = int(r["Id"]), r["PostTypeId"], r["Body"]
        except (KeyError, ValueError):
            skipped += 1
            continue
        if kind == "1":
            questions[pid] = {"title": r.get("Title", ""), "tags": _TAGS.findall(r.get("Tags", "")),
                              "score": _int(r.get("Score")) or 0, "owner": _int(r.get("OwnerUserId")),
                              "accepted": _int(r.get("AcceptedAnswerId")), "body": body}
        elif kind == "2":
            parent = _int(r.get("ParentId"))
            if parent is None:
                skipped += 1
                continue
            answers.append({"id": pid, "parent": parent, "body": body, "score": _int(r.get("Score")) or 0})
    for r in _rows(users_path):
        try:
            reputation[int(r["Id"])] = int(r["Reputation"])
        except (KeyError, ValueError):
            skipped += 1
    return questions, answers, reputation, skipped


def build(questions, answers, reputation, n_chunks, n_queries, n_bakeoff, pool_size, seed=7):
    rnd = random.Random(seed)
    usable = [a for a in answers if a["parent"] in questions and questions[a["parent"]]["tags"] and strip_html(a["body"])]
    rnd.shuffle(usable)
    chunks, seen, dropped = [], set(), 0
    for a in usable:
        q = questions[a["parent"]]
        for i, text in enumerate(chunk_words(strip_html(a["body"]))):
            if text in seen:  # exact duplicate text: keep the first, embedding twins only skews recall
                dropped += 1
                continue
            seen.add(text)
            chunks.append({"id": f"so#a{a['id']}-{i}", "text": text, "attr": q["tags"][0], "doc_id": f"so#q{a['parent']}"})
        if len(chunks) >= n_chunks:
            break
    chunks = chunks[:n_chunks]
    doc_ids = sorted({int(c["doc_id"][4:]) for c in chunks})
    docs = [{"doc_id": f"so#q{d}", "title": questions[d]["title"], "score": questions[d]["score"],
             "extra": f"reputation {reputation.get(questions[d]['owner'] or 0, 0)}"} for d in doc_ids]
    titled = [d for d in doc_ids if strip_html(questions[d]["title"])]
    queries = [{"qid": f"so#q{d}", "text": questions[d]["title"], "attr": questions[d]["tags"][0]}
               for d in rnd.sample(titled, min(n_queries, len(titled)))]

    by_id = {a["id"]: a for a in answers}
    with_accepted = [d for d, q in questions.items() if q["accepted"] in by_id and strip_html(q["title"])
                     and strip_html(by_id[q["accepted"]]["body"])]
    picked = rnd.sample(sorted(with_accepted), min(n_bakeoff, len(with_accepted)))
    bakeoff_queries = [{"qid": f"so#q{d}", "text": (questions[d]["title"] + "\n" + " ".join(strip_html(questions[d]["body"]).split()[:200])).strip(),
                        "target": f"so#a{questions[d]['accepted']}"} for d in picked]
    pool_ids = [q["accepted"] for q in (questions[d] for d in picked)]
    pool_set = set(pool_ids)
    others = [a["id"] for a in answers if a["id"] not in pool_set and strip_html(a["body"])]
    pool_ids += rnd.sample(others, max(0, min(pool_size - len(pool_ids), len(others))))
    bakeoff_pool = [{"id": f"so#a{i}", "text": " ".join(strip_html(by_id[i]["body"]).split()[:300])} for i in pool_ids]
    return {"chunks": chunks, "docs": docs, "queries": queries,
            "bakeoff_queries": bakeoff_queries, "bakeoff_pool": bakeoff_pool, "dropped_duplicates": dropped}


def prepare(dump_dir: str, out_dir: str, n_chunks: int = 100_000) -> dict:
    q, a, rep, skipped = parse(os.path.join(dump_dir, "Posts.xml"), os.path.join(dump_dir, "Users.xml"))
    out = build(q, a, rep, n_chunks=n_chunks, n_queries=1000, n_bakeoff=2000, pool_size=20_000)
    dropped = out.pop("dropped_duplicates")
    os.makedirs(out_dir, exist_ok=True)
    for name, rows in out.items():
        write_jsonl(os.path.join(out_dir, f"{name}.jsonl"), rows)
    return {"skipped": skipped, "dropped_duplicates": dropped, **{k: len(v) for k, v in out.items()}}
