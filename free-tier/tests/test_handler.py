import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import handler as h  # noqa: E402


def test_clean_question_trims_and_caps():
    assert h.clean_question("  ¿vacaciones?  ") == "¿vacaciones?"
    assert len(h.clean_question("a" * 900)) == h.MAX_CHARS
    assert h.clean_question("   ") == "" and h.clean_question(None) == ""


def test_code_ok_only_when_required():
    assert h.code_ok(None, "") and h.code_ok("x", "")  # no event code configured -> open
    assert h.code_ok("scd", "scd") and not h.code_ok("nope", "scd") and not h.code_ok(None, "scd")


class FakeDdb:
    def __init__(self, used=0):
        self.used, self.calls = used, []

    def update_item(self, **kw):
        self.calls.append(kw)
        cap = int(kw["ExpressionAttributeValues"][":cap"]["N"])
        if self.used >= cap:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")
        self.used += 1
        return {}


def test_take_quota_is_atomic_and_stops_at_cap():
    d = FakeDdb(used=1)
    assert h.take_quota(d, "t", 2) is True
    assert h.take_quota(d, "t", 2) is False  # cap reached: refused before Bedrock
    kw = d.calls[0]
    assert kw["UpdateExpression"].startswith("ADD") and "attribute_not_exists" in kw["ConditionExpression"]


def test_requests_mirror_the_lab():
    b = h.cohere_body("hola")
    assert b == {"texts": ["hola"], "input_type": "search_query", "embedding_types": ["float"], "output_dimension": 1024, "truncate": "RIGHT"}
    r = h.search_request("t", [0.5, 0.25], 3)
    assert r["IndexName"] == "vecf" and r["TopK"] == 3 and r["SearchVector"] == [{"N": "0.5"}, {"N": "0.25"}]


def test_format_hits_shortens_text_and_keeps_link():
    items = [{"Item": {"id": {"S": "ley#207436-67-0"}, "title": {"S": "Código del Trabajo"}, "text": {"S": "Art. 67.- " + "x " * 400},
                       "url": {"S": "https://www.bcn.cl/leychile/navegar?idNorma=207436"}}}]
    [hit] = h.format_hits(items)
    assert hit["norma"] == "Código del Trabajo" and hit["url"].endswith("207436")
    assert len(hit["texto"]) <= h.SNIPPET + 1 and hit["texto"].endswith("…")


def test_handler_routes_page_and_rejects_bad_code(monkeypatch):
    monkeypatch.setattr(h, "EVENT_CODE", "scd")
    page = h.lambda_handler({"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}, None)
    assert page["statusCode"] == 200 and "text/html" in page["headers"]["content-type"]
    assert page["headers"]["cache-control"] == "no-cache"
    bad = h.lambda_handler({"rawPath": "/ask", "queryStringParameters": {"q": "hola", "code": "no"},
                            "requestContext": {"http": {"method": "GET"}}}, None)
    assert bad["statusCode"] == 403 and "error" in json.loads(bad["body"])


def test_bedrock_client_assumes_role_and_refreshes(monkeypatch):
    # Event mode: the event account has 0 Bedrock quota, so embeddings run under a role in the lab account.
    made, assumed = [], []

    class Sts:
        def assume_role(self, RoleArn, RoleSessionName, DurationSeconds):
            assumed.append(RoleArn)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "s", "SessionToken": "t"}}

    def client(svc, **kw):
        if svc == "sts":
            return Sts()
        made.append((svc, kw.get("aws_access_key_id")))
        return object()
    monkeypatch.setattr(h.boto3, "client", client)
    clock = [1000.0]
    monkeypatch.setattr(h.time, "time", lambda: clock[0])
    monkeypatch.setattr(h, "BEDROCK_ROLE_ARN", "arn:aws:iam::ACCOUNT:role/x")
    h._br_cache.clear()
    h.bedrock_client(); h.bedrock_client()  # second call reuses the session
    assert assumed == ["arn:aws:iam::ACCOUNT:role/x"] and made == [("bedrock-runtime", "a")]
    clock[0] += 3500  # past the refresh margin
    h.bedrock_client()
    assert len(assumed) == 2


def test_bedrock_client_without_role_uses_own_credentials(monkeypatch):
    monkeypatch.setattr(h, "BEDROCK_ROLE_ARN", "")
    monkeypatch.setattr(h.boto3, "client", lambda svc, **kw: ("plain", svc, kw.get("aws_access_key_id")))
    h._br_cache.clear()
    assert h.bedrock_client() == ("plain", "bedrock-runtime", None)


def test_page_offers_example_questions_that_search_on_tap():
    # Checked against the live index on 2026-10-09: each returns the right norm in the top 3.
    for corpus in ("ley", "so"):
        for q in h.EXAMPLES[corpus] + h.SURPRISE[corpus]:
            assert json.dumps(q, ensure_ascii=False) in h.PAGE and len(q) <= h.MAX_CHARS
        assert 4 <= len(h.EXAMPLES[corpus]) <= 8 and len(h.SURPRISE[corpus]) >= 5
    assert "Sorpréndeme" in h.PAGE and "class=\"ex\"" in h.PAGE


def test_page_asks_for_the_event_code_instead_of_failing():
    # 2026-10-09: opening the bare address showed "código de evento inválido"; the page must ask for it.
    assert 'id="codebox"' in h.PAGE and "localStorage" in h.PAGE and "403" in h.PAGE


def test_page_tells_you_if_the_code_worked():
    # Safari report 2026-10-09: a wrong code silently brought the box back.
    assert "Código incorrecto" in h.PAGE and "Estás dentro" in h.PAGE and 'id="cerr"' in h.PAGE


def test_race_reports_each_store_and_survives_a_sleeping_or_broken_one(monkeypatch):
    # The live demo: DynamoDB answers, S3 Vectors agrees 2/3, OpenSearch is scaled to zero, Aurora errors.
    monkeypatch.setattr(h, "VECTOR_BUCKET", "b")
    monkeypatch.setattr(h, "AOSS_ENDPOINT", "https://x")
    monkeypatch.setattr(h, "AURORA_ARN", "arn")
    monkeypatch.setattr(h, "s3v_ids", lambda v, c: ["a", "b", "z"])

    def sleeping(v, c):
        raise h.Waking()

    def broken(v, c):
        raise RuntimeError("boom")
    monkeypatch.setattr(h, "aoss_ids", sleeping)
    monkeypatch.setattr(h, "aurora_ids", broken)
    hits, carrera = h.race([0.1], lambda v: [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    by = {r["store"]: r for r in carrera}
    assert [r["store"] for r in carrera] == ["ddb", "s3v", "aoss", "aurora"] and len(hits) == 3
    assert by["ddb"]["coinciden"] == 3 and by["s3v"]["coinciden"] == 2 and by["s3v"]["ms"] >= 0
    assert by["aoss"]["estado"] == "despertando" and by["aurora"]["estado"] == "error"


def test_race_is_dynamodb_only_in_student_mode(monkeypatch):
    for k in ("VECTOR_BUCKET", "AOSS_ENDPOINT", "AURORA_ARN"):
        monkeypatch.setattr(h, k, "")
    _, carrera = h.race([0.1], lambda v: [{"id": "a"}])
    assert [r["store"] for r in carrera] == ["ddb"]


def test_store_queries_mirror_the_lab():
    assert h.aurora_sql().endswith("ORDER BY v <=> CAST(:v AS vector) LIMIT 3")
    url, body = h.aoss_request([0.5])
    b = json.loads(body)
    assert url.endswith("/_search") and b["query"]["knn"]["v"] == {"vector": [0.5], "k": 3} and b["_source"] is False


def test_page_shows_the_race_in_the_deck_style():
    assert 'id="race"' in h.PAGE and "DM Sans" in h.PAGE and "despertando" in h.PAGE
    for color in ("#3d5afe", "#e8590c", "#0e9f9a", "#a433f3"):  # store colors from slides/build/build_deck.py
        assert color in h.PAGE
    assert "data:image/webp;base64," in h.PAGE


def test_each_corpus_has_its_own_index_in_every_store():
    assert h.aurora_sql("so").startswith("SELECT id FROM so_chunks ") and h.aoss_request([0.5], "so")[0].endswith("/so-cohere/_search")
    with pytest.raises(AssertionError):
        h.aurora_sql("ley; DROP TABLE x")


def test_nickname_is_fixed_per_phone_and_only_uses_our_words():
    a, b = h.nickname("0123456789abcdef"), h.nickname("fedcba9876543210")
    assert a == h.nickname("0123456789abcdef") and a != b
    noun, adj, num = a.split(" ")
    assert noun in h.NOUN and adj in h.ADJ and 100 <= int(num) <= 999


def test_winner_is_the_fastest_store_that_answered_and_needs_a_race():
    c = [{"store": "ddb", "estado": "ok", "ms": 15.0}, {"store": "aurora", "estado": "ok", "ms": 12.0},
         {"store": "aoss", "estado": "despertando"}]
    assert h.winner(c) == "aurora"
    assert h.winner(c[:1]) is None  # one runner is not a race


class FakeScores:
    def __init__(self):
        self.items = {}

    def update_item(self, Key, ExpressionAttributeValues, **kw):
        it = self.items.setdefault(Key["id"]["S"], {"id": Key["id"], "pts": {"N": "0"}, "plays": {"N": "0"}})
        it["pts"]["N"] = str(int(it["pts"]["N"]) + int(ExpressionAttributeValues[":p"]["N"]))
        it["plays"]["N"] = str(int(it["plays"]["N"]) + 1)
        return {"Attributes": it}

    def scan(self, **kw):
        return {"Items": list(self.items.values()) + [{"id": {"S": "queries"}, "n": {"N": "9"}}][:0]}


def test_bets_score_on_the_server_and_rank_by_points_then_fewer_plays():
    d = FakeScores()
    alice, bob = "a" * 16, "b" * 16
    assert h.settle_bet(d, "t", alice, "aurora", "aurora")["acierto"] is True
    assert h.settle_bet(d, "t", bob, "ddb", "aurora") == {"tu": "ddb", "gano": "aurora", "acierto": False, "puntos": 0,
                                                          "apodo": h.nickname(bob)}
    h.settle_bet(d, "t", bob, "aurora", "aurora")  # bob: 1 point in 2 plays, alice: 1 in 1
    top = h.leaderboard(d, "t", bob)
    assert [x["apodo"] for x in top["top"]] == [h.nickname(alice), h.nickname(bob)] and top["tu_lugar"] == 2
    assert top["jugadores"] == 2


def test_unknown_corpus_is_rejected_before_any_cost(monkeypatch):
    monkeypatch.setattr(h, "EVENT_CODE", "")
    monkeypatch.setattr(h, "TABLE", "t")
    monkeypatch.setattr(h, "_clients", lambda: pytest.fail("no AWS call for a bad corpus"))
    r = h.lambda_handler({"rawPath": "/ask", "queryStringParameters": {"q": "hola", "corpus": "../x"}}, None)
    assert r["statusCode"] == 400


def test_page_has_bet_and_ranking_with_a_random_first_bet():
    assert 'id="bet"' in h.PAGE and 'id="board"' in h.PAGE and "Math.random()" in h.PAGE
    assert "getRandomValues" in h.PAGE  # player id: random per phone, never typed
    assert "CC BY-SA" in h.PAGE  # Stack Overflow attribution


def test_page_explains_overload_instead_of_a_bare_error():
    assert "Mucha gente preguntando a la vez" in h.PAGE and "r.status===429||r.status>=500" in h.PAGE


def test_nothing_runs_before_the_event_opens(monkeypatch):
    # 2026-10-09: the site must refuse every call until 05:00 Chile time on Oct 24, before any cost.
    monkeypatch.setattr(h, "OPEN_AT", 1792828800)
    monkeypatch.setattr(h.time, "time", lambda: 1792828800 - 60)
    monkeypatch.setattr(h, "_clients", lambda: pytest.fail("no AWS call before opening"))
    for path in ("/ask", "/top"):
        r = h.lambda_handler({"rawPath": path, "queryStringParameters": {"q": "hola", "code": "x"}}, None)
        assert r["statusCode"] == 425 and "Todavía no es hora" in json.loads(r["body"])["error"]
    monkeypatch.setattr(h, "EVENT_CODE", "x")
    for path in ("/state", "/bet"):  # the giveaway can be rehearsed before opening: these never call Bedrock
        r = h.lambda_handler({"rawPath": path, "queryStringParameters": {"code": "nope"}}, None)
        assert r["statusCode"] == 403  # past the gate, the event code still applies
    assert h.lambda_handler({"rawPath": "/"}, None)["statusCode"] == 200  # the page still loads and says so
    assert 'id="soon"' in h.PAGE and "America/Santiago" in h.PAGE


def test_gate_lifts_on_its_own_at_opening(monkeypatch):
    monkeypatch.setattr(h, "OPEN_AT", 1792828800)
    monkeypatch.setattr(h, "EVENT_CODE", "scd")
    monkeypatch.setattr(h.time, "time", lambda: 1792828800 + 1)
    r = h.lambda_handler({"rawPath": "/ask", "queryStringParameters": {"q": "hola", "code": "no"}}, None)
    assert r["statusCode"] == 403  # past the gate, normal checks apply


class FakeTable:
    """Just enough of DynamoDB for the game: one table keyed by id."""
    def __init__(self):
        self.items = {}

    def get_item(self, TableName, Key):
        it = self.items.get(Key["id"]["S"])
        return {"Item": it} if it else {}

    def put_item(self, TableName, Item):
        self.items[Item["id"]["S"]] = Item

    def delete_item(self, TableName, Key):
        self.items.pop(Key["id"]["S"], None)

    def scan(self, TableName, FilterExpression, ExpressionAttributeValues, **kw):
        pre = ExpressionAttributeValues[":p"]["S"]
        return {"Items": [v for k, v in self.items.items() if k.startswith(pre)]}


def _round(d, scenario="simple"):
    g = h.game(d) | {"mode": "sorteo", "round": 1, "scenario": scenario, "phase": "abierta", "q": "x"}
    h._put(d, "game", g)
    return g


def test_bets_only_while_open_and_one_per_player():
    d = FakeTable()
    g = _round(d)
    assert h.place_bet(d, g, "a" * 16, "aurora") and h.place_bet(d, g, "a" * 16, "ddb")  # changed her mind: last one counts
    assert not h.place_bet(d, g, "b" * 16, "nope")
    assert h.bets(d, 1) == {"a" * 16: "ddb"}
    g["phase"] = "cerrada"
    assert not h.place_bet(d, g, "c" * 16, "ddb")


def test_draw_picks_only_correct_bets_never_twice_and_hides_the_code():
    import random
    d = FakeTable()
    g = _round(d)
    for p, st in (("a" * 16, "aurora"), ("b" * 16, "ddb"), ("c" * 16, "aurora")):
        h.place_bet(d, g, p, st)
    h._put(d, "codes", ["CODE-1", "CODE-2"])
    g |= {"phase": "resultado", "gano": "aurora"}
    right = [p for p, st in h.bets(d, 1).items() if st == "aurora"]
    first = h.draw(d, g, right, random.Random(1))
    second = h.draw(d, g, right, random.Random(1))
    winners = {w["p"] for w in g["winners"]}
    assert winners == {"a" * 16, "c" * 16} and "b" * 16 not in winners  # wrong bet never wins; nobody wins twice
    assert first["quedan"] == 1 and second["quedan"] == 0 and h.draw(d, g, right)["error"]
    seen_by_loser = h.public_state(d, g, "b" * 16)
    assert "CODE" not in json.dumps(seen_by_loser)  # codes reach only their winner
    mine = h.public_state(d, g, g["winners"][0]["p"])
    assert mine["yo"]["codigo"] == "CODE-1"


def test_admin_needs_the_key_and_projector_state_has_no_codes(monkeypatch):
    monkeypatch.setattr(h, "ADMIN_CODE", "secreto")
    r = h.lambda_handler({"rawPath": "/admin/api", "headers": {"x-admin-key": "nope"}}, None)
    assert r["statusCode"] == 403
    r = h.lambda_handler({"rawPath": "/admin/api", "queryStringParameters": {"key": "secreto"}}, None)
    assert r["statusCode"] == 403  # the key in the URL is not accepted
    d = FakeTable()
    h._put(d, "codes", ["CODE-9"])
    st = h.admin(d, None, {"action": "open"}, "")["state"]
    assert st["round"] == 1 and st["phase"] == "abierta"
    assert st["codigos_restantes"] == 1 and "CODE-9" not in json.dumps(st)
    assert "error" in h.admin(d, None, {"action": "codes"}, "A1")  # codes load from event-ops.py, not the page


def test_each_scenario_reaches_every_store_the_lab_way():
    ley_filter = {"corpus": "ley", "attr": "codigo-civil"}
    assert "WHERE attr = :a" in h.aurora_sql(ley_filter)
    assert "JOIN so_docs d USING (doc_id)" in h.aurora_sql({"corpus": "so", "context": True})
    body = json.loads(h.aoss_request([0.5], ley_filter)[1])
    assert body["query"]["knn"]["v"]["filter"] == {"term": {"attr": "codigo-civil"}}
    r = h.search_request("t", [0.5], 3, "codigo-civil")
    assert r["SearchConditionExpression"] == "#a = :a" and r["ExpressionAttributeNames"]["#a"] == "attr"
    for key, sc in h.SCENARIOS.items():
        assert sc["corpus"] in ("ley", "so") and sc["q"] and sc["label"]


def test_admin_page_and_round_panel_exist():
    assert "Sortear 1 código" in h.ADMIN_PAGE and "Códigos restantes" in h.ADMIN_PAGE and 'id="next"' in h.ADMIN_PAGE
    assert 'id="round"' in h.PAGE and 'id="prize"' in h.PAGE and "state?" in h.PAGE


def test_test_players_bet_only_in_an_open_round_and_are_labeled():
    d = FakeTable()
    assert "error" in h.admin(d, None, {"action": "bots"}, "")  # no open round yet
    h.admin(d, None, {"action": "open"}, "")
    r = h.admin(d, None, {"action": "bots", "n": "10"}, "")
    assert r["bots"] == 10 and sum(r["state"]["apuestas"].values()) == 10
    assert all(p.startswith(h.BOT) for p in h.bets(d, 1)) and h.nickname("bot-01").startswith("🤖 ")
    assert not h.nickname("a" * 32).startswith("🤖") and not h.PLAYER.fullmatch("bot-01")


def test_a_sleeping_store_reports_its_time_once_it_wakes(monkeypatch):
    # Rehearsal 2026-10-15: Aurora/OpenSearch asleep showed "despertando" forever. /wake asks them again.
    monkeypatch.setattr(h, "VECTOR_BUCKET", "")
    monkeypatch.setattr(h, "AURORA_ARN", "")
    monkeypatch.setattr(h, "AOSS_ENDPOINT", "https://x")
    awake = {"v": False}

    def aoss(v, c):
        if not awake["v"]:
            raise h.Waking()
        return ["a", "b", "z"]
    monkeypatch.setattr(h, "aoss_ids", aoss)
    d = FakeTable()
    t0 = h.time.time()
    hits, carrera = h.race([0.1], lambda v: [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    assert h.save_wake(d, "wk#r", [0.1], "ley", carrera, {x["id"] for x in hits}, t0 - 10)  # first try 10 s ago
    assert h.wake(d, "wk#r")[0]["estado"] == "despertando" and h._get(d, "wk#r")  # still asleep: keep trying
    awake["v"] = True
    row = h.wake(d, "wk#r")[0]
    assert row["estado"] == "ok" and row["desperto"] and row["ms"] >= 10000 and row["coinciden"] == 2  # wake-up included
    assert h._get(d, "wk#r") is None and h.wake(d, "wk#r") == []


def test_rounds_cycle_through_the_4_questions_one_code_each(monkeypatch):
    d = FakeTable()
    monkeypatch.setattr(h, "embed", lambda br, q: [0.1])
    monkeypatch.setattr(h, "corpora", lambda: {"ley": "t", "so": "t2"})
    monkeypatch.setattr(h, "race", lambda vec, leg, sc: ([], [{"store": "ddb", "estado": "ok", "ms": 9.0},
                                                            {"store": "s3v", "estado": "ok", "ms": 50.0}]))
    monkeypatch.setattr(h, "COUNTER_TABLE", "c")
    monkeypatch.setattr(h, "take_quota", lambda *a: True)
    h._put(d, "codes", ["C1", "C2"])
    labels = []
    for n in range(1, 6):
        st = h.admin(d, None, {"action": "open", "round": "9", "scenario": "otra"}, "")["state"]  # overrides are ignored
        assert st["round"] == n
        labels.append(st["escenario"]["label"])
        assert "error" in h.admin(d, None, {"action": "open"}, "")  # cannot skip a round in progress
        h.place_bet(d, h.game(d), f"{n:016x}", "ddb")
        h.admin(d, None, {"action": "run"}, "")
        r = h.admin(d, None, {"action": "draw"}, "")
        if n <= 2:
            assert r["apodo"] == h.nickname(f"{n:016x}")
            assert "error" in h.admin(d, None, {"action": "draw"}, "")  # one code per round
        else:
            assert r["error"] == "no quedan códigos"
    assert labels == [h.SCENARIOS[k]["label"] for k in h.ORDER] + [h.SCENARIOS[h.ORDER[0]]["label"]]
