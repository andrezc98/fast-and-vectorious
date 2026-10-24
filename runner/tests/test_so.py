from fv.corpus import so

POSTS = """<?xml version="1.0" encoding="utf-8"?>
<posts>
  <row Id="1" PostTypeId="1" AcceptedAnswerId="2" Title="Error en pandas" Tags="|python|pandas|" Score="5" OwnerUserId="10" Body="&lt;p&gt;como arreglo&lt;/p&gt;" />
  <row Id="2" PostTypeId="2" ParentId="1" Score="3" Body="&lt;p&gt;usa reset_index&lt;/p&gt;" />
  <row Id="3" PostTypeId="2" ParentId="1" Score="0" Body="&lt;p&gt;otra cosa&lt;/p&gt;" />
  <row Id="x9" PostTypeId="2" ParentId="1" Body="poisoned id" />
  <row Id="4" PostTypeId="2" ParentId="999" Body="&lt;p&gt;orphan&lt;/p&gt;" />
  <row Id="5" PostTypeId="1" Title="Sin tags" Score="1" Body="" />
  <row Id="6" PostTypeId="2" ParentId="1" Score="1" />
  <row Id="7" PostTypeId="2" ParentId="²" Score="1" Body="&lt;p&gt;superscript parent&lt;/p&gt;" />
  <row Id="8" PostTypeId="1" Tags="|sql|" Title="Score roto" Score="abc" OwnerUserId="zz" Body="&lt;p&gt;consulta&lt;/p&gt;" />
  <row Id="9" PostTypeId="2" ParentId="8" Score="2" Body="&lt;p&gt;respuesta sql&lt;/p&gt;" />
</posts>"""

USERS = """<?xml version="1.0" encoding="utf-8"?>
<users><row Id="10" Reputation="1234" /><row Id="bad" Reputation="x" /></users>"""


def write(tmp_path):
    (tmp_path / "Posts.xml").write_text(POSTS, encoding="utf-8")
    (tmp_path / "Users.xml").write_text(USERS, encoding="utf-8")
    return str(tmp_path / "Posts.xml"), str(tmp_path / "Users.xml")


def test_parse_skips_bad_rows_and_reads_both_tag_formats(tmp_path):
    q, a, rep, skipped = so.parse(*write(tmp_path))
    assert set(q) == {1, 5, 8}
    assert q[1]["tags"] == ["python", "pandas"]
    assert q[8]["score"] == 0 and q[8]["owner"] is None  # bad Score -> 0, bad OwnerUserId -> None, row kept
    assert [x["id"] for x in a] == [2, 3, 4, 9]
    assert rep == {10: 1234}
    assert skipped == 4  # x9 bad id, Id=6 no body, Id=7 superscript ParentId, users "bad"


def test_build_shapes(tmp_path):
    q, a, rep, _ = so.parse(*write(tmp_path))
    out = so.build(q, a, rep, n_chunks=10, n_queries=1, n_bakeoff=1, pool_size=2, seed=1)
    ids = {c["id"] for c in out["chunks"]}
    assert ids == {"so#a2-0", "so#a3-0", "so#a9-0"}  # orphan answer 4 dropped
    assert all(c["attr"] == "python" and c["doc_id"] == "so#q1" for c in out["chunks"] if c["id"] != "so#a9-0")
    assert [c for c in out["chunks"] if c["id"] == "so#a9-0"][0]["doc_id"] == "so#q8"
    assert out["docs"] == [
        {"doc_id": "so#q1", "title": "Error en pandas", "score": 5, "extra": "reputation 1234"},
        {"doc_id": "so#q8", "title": "Score roto", "score": 0, "extra": "reputation 0"},  # bad owner -> reputation 0
    ]
    assert len(out["queries"]) == 1 and out["queries"][0]["qid"] in {"so#q1", "so#q8"}
    assert out["bakeoff_queries"][0]["target"] == "so#a2"
    pool = {p["id"] for p in out["bakeoff_pool"]}
    assert "so#a2" in pool and len(pool) == 2  # accepted answer always in the pool, plus one random other


def _q(title="T", accepted=None, tags=("python",)):
    return {"title": title, "tags": list(tags), "score": 1, "owner": None, "accepted": accepted, "body": "pregunta"}


def _a(i, parent, body):
    return {"id": i, "parent": parent, "body": body, "score": 1}


def test_parse_superscript_accepted_id_is_none_and_build_does_not_crash(tmp_path):
    (tmp_path / "Posts.xml").write_text(
        '<posts><row Id="1" PostTypeId="1" AcceptedAnswerId="\u00b2" Title="t" Tags="|a|" Body="b" />'
        '<row Id="2" PostTypeId="2" ParentId="1" Body="resp" /></posts>', encoding="utf-8")
    (tmp_path / "Users.xml").write_text("<users/>")
    q, a, rep, _ = so.parse(str(tmp_path / "Posts.xml"), str(tmp_path / "Users.xml"))
    assert q[1]["accepted"] is None
    out = so.build(q, a, rep, n_chunks=5, n_queries=1, n_bakeoff=5, pool_size=5, seed=1)
    assert out["bakeoff_queries"] == []


def test_empty_body_answer_never_in_chunks_or_pool():
    qs = {1: _q(accepted=2), 5: _q(accepted=None)}
    ans = [_a(2, 1, "<p></p>"), _a(3, 1, "texto bueno"), _a(4, 5, " <br/> ")]
    out = so.build(qs, ans, {}, n_chunks=10, n_queries=2, n_bakeoff=5, pool_size=5, seed=1)
    assert {c["id"] for c in out["chunks"]} == {"so#a3-0"}
    assert out["bakeoff_queries"] == []  # its accepted answer is empty
    assert {p["id"] for p in out["bakeoff_pool"]} <= {"so#a3"}


def test_empty_title_question_never_a_query():
    qs = {1: _q(title="  "), 2: _q(title="hola", accepted=11), 3: _q(title="", accepted=12)}
    ans = [_a(10, 1, "uno"), _a(11, 2, "dos"), _a(12, 3, "tres")]
    out = so.build(qs, ans, {}, n_chunks=10, n_queries=10, n_bakeoff=10, pool_size=5, seed=1)
    assert [q["qid"] for q in out["queries"]] == ["so#q2"]
    assert [q["qid"] for q in out["bakeoff_queries"]] == ["so#q2"]


def test_duplicate_chunk_texts_are_dropped_and_counted():
    qs = {1: _q(), 2: _q()}
    ans = [_a(10, 1, "mismo texto"), _a(11, 2, "mismo texto"), _a(12, 2, "otro")]
    out = so.build(qs, ans, {}, n_chunks=10, n_queries=1, n_bakeoff=0, pool_size=0, seed=1)
    assert sorted(c["text"] for c in out["chunks"]) == ["mismo texto", "otro"]
    assert out["dropped_duplicates"] == 1
