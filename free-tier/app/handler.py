"""Ley Chile search: question -> Cohere Embed v4 (Bedrock) -> DynamoDB vector search -> top 3 articles.

Event mode also races the same embedding against S3 Vectors, OpenSearch Serverless and Aurora (Data API) in the lab
account (free-tier/event-stores) and returns each store's latency, measured from this Lambda, network included.

One Lambda serves the page (GET /) and the API (GET /ask?q=...). Works behind a Function URL (student mode)
or an HTTP API (event mode); both send payload format 2.0. Request shapes mirror the lab runner
(runner/fv/stores/dynamo.py, runner/fv/embed/bedrock.py).
"""
import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, wait

import boto3
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import ClientError

TABLE = os.environ.get("TABLE", "")
TABLE_SO = os.environ.get("TABLE_SO", "")  # event mode: second corpus, Stack Overflow en español
DOCS_TABLE = os.environ.get("DOCS_TABLE", "")  # event mode: doc_id -> title, the "context" round's second read
ADMIN_CODE = os.environ.get("ADMIN_CODE", "")  # event mode: unlocks /admin (giveaway rounds); empty = no admin
COUNTER_TABLE = os.environ.get("COUNTER_TABLE", "")
MODEL_ID = os.environ.get("MODEL_ID", "cohere.embed-v4:0")
MAX_QUERIES = int(os.environ.get("MAX_QUERIES", "1000"))  # hard cap before any Bedrock call
EVENT_CODE = os.environ.get("EVENT_CODE", "")  # set only for the live talk
OPEN_AT = int(os.environ.get("OPEN_AT") or 0)  # epoch s; before it every call is refused, before any cost
BEDROCK_ROLE_ARN = os.environ.get("BEDROCK_ROLE_ARN", "")  # event mode: embed under a role in another account
MAX_CHARS, TOP_K, SNIPPET = 300, 3, 420
# event mode: the other three stores (empty = DynamoDB only, as in the student project)
# (one index per corpus in each: "<corpus>-cohere", Aurora table "<corpus>_chunks")
VECTOR_BUCKET = os.environ.get("VECTOR_BUCKET", "")
AOSS_ENDPOINT = os.environ.get("AOSS_ENDPOINT", "")
AURORA_ARN, AURORA_SECRET = os.environ.get("AURORA_ARN", ""), os.environ.get("AURORA_SECRET", "")
STORES_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
STORE_TIMEOUT_S = 8  # a store asleep past this shows "despertando"; /wake asks it again (Aurora took 14 s, OpenSearch 23 s)
WAKE_GIVEUP_S = 180  # after this, "sin respuesta". Aurora paused >24 h sleeps deeper: "30 seconds or longer" (Aurora
# User Guide, auto-pause, read 2026-10-15); the rehearsal that day still was not up after 60 s
STORE_CFG = Config(connect_timeout=3, read_timeout=STORE_TIMEOUT_S, retries={"max_attempts": 1})
_pool = ThreadPoolExecutor(4)
_remote = {}
_http = urllib3.PoolManager(maxsize=4)  # keep-alive, like the boto3 clients: no TLS handshake per question

_ddb = None
_br_cache = {}  # {"client": ..., "until": epoch seconds}
SESSION_S, REFRESH_MARGIN_S = 3600, 300


def bedrock_client():
    """Own credentials, or (event mode) a cached assumed-role session refreshed before it expires."""
    now = time.time()
    if _br_cache and now < _br_cache["until"]:
        return _br_cache["client"]
    if BEDROCK_ROLE_ARN:
        c = boto3.client("sts").assume_role(RoleArn=BEDROCK_ROLE_ARN, RoleSessionName="ley-search",
                                            DurationSeconds=SESSION_S)["Credentials"]
        br = boto3.client("bedrock-runtime", region_name=os.environ.get("BEDROCK_REGION", "us-east-1"),
                          aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                          aws_session_token=c["SessionToken"])
        _br_cache.update(client=br, creds=c, until=now + SESSION_S - REFRESH_MARGIN_S)
    else:
        br = boto3.client("bedrock-runtime")
        _br_cache.update(client=br, creds=None, until=float("inf"))
    return br


def _creds_kw() -> dict:
    bedrock_client()  # refreshes the assumed-role session when due
    c = _br_cache["creds"]
    return {} if not c else {"aws_access_key_id": c["AccessKeyId"], "aws_secret_access_key": c["SecretAccessKey"],
                             "aws_session_token": c["SessionToken"]}


def remote_client(svc: str):
    """Clients for the lab-account stores, rebuilt whenever the assumed-role session is."""
    kw = _creds_kw()
    if _remote.get("until") != _br_cache["until"]:
        _remote.clear()
        _remote["until"] = _br_cache["until"]
    if svc not in _remote:
        _remote[svc] = boto3.client(svc, region_name=STORES_REGION, config=STORE_CFG, **kw)
    return _remote[svc]


def _clients():
    global _ddb
    if _ddb is None:
        _ddb = boto3.client("dynamodb")
    return _ddb, bedrock_client()


def clean_question(q) -> str:
    return (q or "").strip()[:MAX_CHARS]


def code_ok(given, expected: str) -> bool:
    return not expected or hmac.compare_digest((given or "").encode(), expected.encode())


def take_quota(ddb, table: str, cap: int) -> bool:
    """Atomically count one query; refuse once the cap is reached (checked before paying for an embedding)."""
    try:
        ddb.update_item(TableName=table, Key={"id": {"S": "queries"}}, UpdateExpression="ADD n :one",
                        ConditionExpression="attribute_not_exists(n) OR n < :cap",
                        ExpressionAttributeValues={":one": {"N": "1"}, ":cap": {"N": str(cap)}})
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def cohere_body(text: str) -> dict:
    return {"texts": [text], "input_type": "search_query", "embedding_types": ["float"], "output_dimension": 1024,
            "truncate": "RIGHT"}


def embed(br, text: str) -> list:
    r = json.loads(br.invoke_model(modelId=MODEL_ID, body=json.dumps(cohere_body(text)))["body"].read())
    e = r["embeddings"]
    return (e["float"] if isinstance(e, dict) else e)[0]


def search_request(table: str, vec, k: int, attr=None) -> dict:
    r = {"TableName": table, "IndexName": "vecf", "SearchVector": [{"N": repr(float(x))} for x in vec], "TopK": k,
         "ProjectionExpression": "id, doc_id, title, #t, #u", "ExpressionAttributeNames": {"#t": "text", "#u": "url"}}
    if attr:  # the attribute is an INLINE_FILTER of the vector index (same request as the lab's filter case)
        r |= {"SearchConditionExpression": "#a = :a", "ExpressionAttributeValues": {":a": {"S": attr}}}
        r["ExpressionAttributeNames"]["#a"] = "attr"
    return r


def ddb_hits(ddb, table: str, sc: dict):
    """DynamoDB's leg of the race; with context it does the same second read as S3 Vectors and OpenSearch."""
    def run(vec):
        res = ddb.search_vectors(**search_request(table, vec, TOP_K, sc.get("attr")))["SearchResults"]
        if sc.get("context"):
            doc_titles([r["Item"].get("doc_id", {}).get("S", "") for r in res])
        return format_hits(res)
    return run


def format_hits(results: list) -> list:
    out = []
    for r in results:
        it = r["Item"]
        text = " ".join(it.get("text", {}).get("S", "").split())
        out.append({"id": it.get("id", {}).get("S", ""), "norma": it.get("title", {}).get("S", ""), "url": it.get("url", {}).get("S", ""),
                    "texto": text[:SNIPPET] + ("…" if len(text) > SNIPPET else "")})
    return out


class Waking(Exception):
    """The store is scaled to zero and resuming."""


def corpora() -> dict:
    return {k: t for k, t in (("ley", TABLE), ("so", TABLE_SO)) if t}


def _sc(corpus) -> dict:
    """A scenario: {"corpus": "ley"|"so", "attr": str|None (filter), "context": bool (also fetch each result's title)}."""
    return corpus if isinstance(corpus, dict) else {"corpus": corpus}


def doc_titles(doc_ids: list) -> list:
    """The second read the lab measured for every store but Aurora: titles from a separate table (same-account DynamoDB)."""
    keys = list(dict.fromkeys(doc_ids))
    got = _clients()[0].batch_get_item(RequestItems={DOCS_TABLE: {"Keys": [{"doc_id": {"S": d}} for d in keys]}})
    titles = {i["doc_id"]["S"]: i["title"]["S"] for i in got["Responses"].get(DOCS_TABLE, [])}
    return [titles.get(d, "") for d in doc_ids]


def s3v_ids(vec, corpus="ley") -> list:
    sc = _sc(corpus)
    req = {"vectorBucketName": VECTOR_BUCKET, "indexName": f"{sc['corpus']}-cohere", "topK": TOP_K,
           "queryVector": {"float32": [float(x) for x in vec]}, "returnMetadata": bool(sc.get("context"))}
    if sc.get("attr"):
        req |= {"filter": {"attr": {"$eq": sc["attr"]}}, "queryMode": "ENHANCED"}  # same request as the lab's filter case
    r = remote_client("s3vectors").query_vectors(**req)
    if sc.get("context"):
        doc_titles([v["metadata"]["doc_id"] for v in r["vectors"]])
    return [v["key"] for v in r["vectors"]]


def aurora_sql(corpus="ley") -> str:
    sc = _sc(corpus)
    c = sc["corpus"]
    assert c in ("ley", "so")  # goes into SQL as an identifier
    where = " WHERE attr = :a" if sc.get("attr") else ""
    if sc.get("context"):  # one SQL with a JOIN: the lab's enrichment case
        return (f"SELECT c.id, d.title FROM (SELECT id, doc_id, v <=> CAST(:v AS vector) AS dist FROM {c}_chunks{where} "
                f"ORDER BY dist LIMIT {TOP_K}) c JOIN {c}_docs d USING (doc_id) ORDER BY c.dist")
    return f"SELECT id FROM {c}_chunks{where} ORDER BY v <=> CAST(:v AS vector) LIMIT " + str(TOP_K)


def aurora_ids(vec, corpus="ley") -> list:
    sc = _sc(corpus)
    rds = remote_client("rds-data")
    params = [{"name": "v", "value": {"stringValue": json.dumps([float(x) for x in vec])}}]
    if sc.get("attr"):
        params.append({"name": "a", "value": {"stringValue": sc["attr"]}})
    try:
        r = rds.execute_statement(resourceArn=AURORA_ARN, secretArn=AURORA_SECRET, database="postgres", sql=aurora_sql(sc),
                                  parameters=params)
    except rds.exceptions.DatabaseResumingException as e:  # paused at 0 ACU; this call started the resume
        raise Waking() from e
    return [row[0]["stringValue"] for row in r["records"]]


def aoss_request(vec, corpus="ley") -> tuple:
    sc = _sc(corpus)
    knn = {"vector": [float(x) for x in vec], "k": TOP_K}
    if sc.get("attr"):
        knn["filter"] = {"term": {"attr": sc["attr"]}}
    body = json.dumps({"size": TOP_K, "_source": ["doc_id"] if sc.get("context") else False, "query": {"knn": {"v": knn}}})
    url = AOSS_ENDPOINT.rstrip("/") + f"/{sc['corpus']}-cohere/_search"
    return url, body


def aoss_ids(vec, corpus="ley") -> list:
    sc = _sc(corpus)
    url, body = aoss_request(vec, sc)
    kw = _creds_kw()
    creds = (Credentials(kw["aws_access_key_id"], kw["aws_secret_access_key"], kw["aws_session_token"]) if kw
             else boto3.Session().get_credentials())
    req = AWSRequest("POST", url, data=body, headers={"content-type": "application/json",
                                                      "x-amz-content-sha256": hashlib.sha256(body.encode()).hexdigest()})
    SigV4Auth(creds, "aoss", STORES_REGION).add_auth(req)
    try:  # a sleeping collection queues the request ~10 s instead of failing
        r = _http.request("POST", url, body=body.encode(), headers=dict(req.headers), retries=False,
                          timeout=urllib3.Timeout(connect=3, read=STORE_TIMEOUT_S))
    except urllib3.exceptions.TimeoutError as e:
        raise Waking() from e
    if r.status != 200:
        raise RuntimeError(f"aoss {r.status}: {r.data[:200]!r}")
    hits = json.loads(r.data)["hits"]["hits"]
    if sc.get("context"):
        doc_titles([h["_source"]["doc_id"] for h in hits])
    return [h["_id"] for h in hits]


def _timed(fn, vec):
    t = time.perf_counter()
    try:
        res = fn(vec)
        return {"res": res, "elapsed": round((time.perf_counter() - t) * 1000, 1), "estado": "ok"}
    except Waking:
        return {"estado": "despertando"}
    except Exception as e:  # noqa: BLE001 - one store failing must not sink the answer
        print("store error:", type(e).__name__, str(e)[:200])
        return {"estado": "error"}


def race(vec, ddb_leg, corpus="ley", only=None, ref=None) -> tuple:
    """Same vector to every configured store, in parallel; each one's time is its own call, network included.
    only/ref: re-ask just these stores (the ones that were asleep), comparing with DynamoDB's earlier ids."""
    jobs = {"ddb": ddb_leg} if ddb_leg else {}
    if VECTOR_BUCKET:
        jobs["s3v"] = lambda v: s3v_ids(v, corpus)
    if AOSS_ENDPOINT:
        jobs["aoss"] = lambda v: aoss_ids(v, corpus)
    if AURORA_ARN:
        jobs["aurora"] = lambda v: aurora_ids(v, corpus)
    jobs = {k: f for k, f in jobs.items() if only is None or k in only}
    futs = {k: _pool.submit(_timed, fn, vec) for k, fn in jobs.items()}
    wait(futs.values(), timeout=STORE_TIMEOUT_S + 1)
    out = {k: (f.result() if f.done() else {"estado": "despertando"}) for k, f in futs.items()}
    hits = out.get("ddb", {}).get("res") or []
    ref = {h["id"] for h in hits} if ref is None else ref
    carrera = []
    for k in jobs:
        o = out[k]
        row = {"store": k, "estado": o["estado"]}
        if o["estado"] == "ok":
            ids = [h["id"] for h in o["res"]] if k == "ddb" else o["res"]
            row |= {"ms": o["elapsed"], "coinciden": len(ref & set(ids))}
        carrera.append(row)
    return hits, carrera


def save_wake(ddb, key: str, vec, sc, carrera: list, ref, t0: float) -> bool:
    """Keep what /wake needs to ask the sleeping stores again; False if every store answered."""
    pend = [r["store"] for r in carrera if r["estado"] == "despertando"]
    if pend:
        _put(ddb, key, {"v": [float(x) for x in vec], "sc": _sc(sc), "ref": sorted(ref), "t0": t0, "pend": pend})
    return bool(pend)


def wake(ddb, key: str) -> list:
    """Ask again only the stores that were asleep. Their time counts from the first try, so it includes the wake-up."""
    w = _get(ddb, key)
    if not w:
        return []
    start = time.time()
    _, rows = race(w["v"], None, w["sc"], only=w["pend"], ref=set(w["ref"]))
    for r in rows:
        if r["estado"] == "ok":
            r |= {"ms": round((start - w["t0"]) * 1000 + r["ms"], 1), "desperto": True}
        elif time.time() - w["t0"] > WAKE_GIVEUP_S:
            r["estado"] = "error"
    w["pend"] = [r["store"] for r in rows if r["estado"] == "despertando"]
    if w["pend"]:
        _put(ddb, key, w)
    else:
        ddb.delete_item(TableName=COUNTER_TABLE, Key={"id": {"S": key}})
    return rows


def merge_rows(carrera: list, rows: list) -> list:
    new = {r["store"]: r for r in rows}
    return [new.get(r["store"], r) for r in carrera]


# --- the bet: each phone backs a store; the server picks the winner, so nobody can fake a point ---
STORES = ("ddb", "s3v", "aoss", "aurora")
ADJ = ["Veloz", "Dormilón", "Coseno", "Furioso", "Cuántico", "Serverless", "Despierto", "Elástico", "Vectorial", "Turbo",
       "Escalable", "Frío", "Tibio", "Paralelo", "Indexado", "Liviano"]
NOUN = ["Vector", "Embedding", "Pudú", "Cóndor", "Huemul", "Índice", "Clúster", "Shard", "Coseno", "Kraken", "Puma",
        "Pingüino", "Chinchilla", "Quirquincho", "Zorro", "Copihue"]
PLAYER = re.compile(r"[0-9a-f]{16,32}")
BOT = "bot-"  # admin test players; outside PLAYER's alphabet, so no browser can pose as one


def nickname(player: str) -> str:
    """Random-looking but fixed per phone, built only from our word lists: nothing a participant typed reaches the screen."""
    h = int(hashlib.sha256(player.encode()).hexdigest(), 16)
    bot = "🤖 " if player.startswith(BOT) else ""  # test players are always labeled on screen
    return f"{bot}{NOUN[h % len(NOUN)]} {ADJ[(h >> 8) % len(ADJ)]} {(h >> 16) % 900 + 100}"


def winner(carrera: list):
    ok = [r for r in carrera if r["estado"] == "ok"]
    return min(ok, key=lambda r: r["ms"])["store"] if len(ok) > 1 else None


def settle_bet(ddb, table: str, player: str, bet: str, won) -> dict:
    hit = bet == won
    r = ddb.update_item(TableName=table, Key={"id": {"S": f"p#{player}"}}, UpdateExpression="ADD pts :p, plays :one",
                        ExpressionAttributeValues={":p": {"N": "1" if hit else "0"}, ":one": {"N": "1"}},
                        ReturnValues="ALL_NEW")["Attributes"]
    return {"tu": bet, "gano": won, "acierto": hit, "puntos": int(r["pts"]["N"]), "apodo": nickname(player)}


def leaderboard(ddb, table: str, player: str = "", n: int = 5) -> dict:
    items, kw = [], {"TableName": table, "FilterExpression": "begins_with(id, :p)", "ExpressionAttributeValues": {":p": {"S": "p#"}}}
    while True:  # a few hundred phones at most
        r = ddb.scan(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    rows = sorted(({"p": i["id"]["S"][2:], "puntos": int(i["pts"]["N"]), "jugadas": int(i["plays"]["N"])} for i in items),
                  key=lambda x: (-x["puntos"], x["jugadas"]))
    me = next((i + 1 for i, x in enumerate(rows) if x["p"] == player), None)
    return {"top": [{"apodo": nickname(x["p"]), "puntos": x["puntos"]} for x in rows[:n]], "tu_lugar": me, "jugadores": len(rows)}


# --- giveaway rounds (admin): the room bets on the fastest store, each round tests something different ---
# Scenarios mirror the lab's cases. The admin can override the question; filter values exist in the data
# (Ley attr = the norm's short name, SO attr = the question's main tag).
SCENARIOS = {
    "simple": {"label": "Búsqueda simple", "desc": "La pregunta tal cual, sobre la Ley Chile.", "corpus": "ley",
               "q": "¿Cuántos días de vacaciones me corresponden?"},
    "filtro": {"label": "Con filtro", "desc": "Solo dentro del Código Civil: cada base filtra a su manera.", "corpus": "ley",
               "attr": "codigo-civil", "q": "¿Qué pasa si encuentro un tesoro enterrado en mi patio?"},
    "contexto": {"label": "Con contexto", "desc": "Además de cada respuesta, traer el título de su pregunta: Aurora con un "
                 "JOIN, las demás con una segunda lectura.", "corpus": "so", "context": True, "q": "¿Cómo deshago el último commit?"},
    "otra": {"label": "Otra colección", "desc": "Stack Overflow en español: código en vez de leyes.", "corpus": "so",
             "q": "¿Cómo salgo de Vim?"},
}
ROUNDS = 4  # rounds cycle through the 4 scenarios, one code per round, until the codes run out
ORDER = list(SCENARIOS)  # round n plays ORDER[(n - 1) % 4]; no override, so the flow cannot drift


def _get(ddb, key: str):
    it = ddb.get_item(TableName=COUNTER_TABLE, Key={"id": {"S": key}}).get("Item")
    return json.loads(it["s"]["S"]) if it else None


def _put(ddb, key: str, value) -> None:
    ddb.put_item(TableName=COUNTER_TABLE, Item={"id": {"S": key}, "s": {"S": json.dumps(value, ensure_ascii=False)}})


def _scan(ddb, prefix: str) -> list:
    items, kw = [], {"TableName": COUNTER_TABLE, "FilterExpression": "begins_with(id, :p)",
                     "ExpressionAttributeValues": {":p": {"S": prefix}}}
    while True:
        r = ddb.scan(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def game(ddb) -> dict:
    return _get(ddb, "game") or {"mode": "libre", "round": 0, "phase": "", "winners": []}


def bets(ddb, rnd: int) -> dict:
    """{player: store} for one round."""
    return {i["id"]["S"].split("#", 2)[2]: json.loads(i["s"]["S"])["store"] for i in _scan(ddb, f"b#{rnd}#")}


def place_bet(ddb, g: dict, player: str, store: str) -> bool:
    if g.get("mode") != "sorteo" or g.get("phase") != "abierta" or store not in STORES:
        return False
    _put(ddb, f"b#{g['round']}#{player}", {"store": store, "t": time.time()})  # changing your mind before the close is fine
    return True


def draw(ddb, g: dict, pool: list, rng=None) -> dict:
    """One code to one random player from the pool, never to someone who already won. The code only goes to that player."""
    rng = rng or random.SystemRandom()
    already = {w["p"] for w in g["winners"]}
    pool = sorted(set(pool) - already)
    codes = _get(ddb, "codes") or []
    if not pool:
        return {"error": "nadie para sortear (ningún acierto o ya ganaron todos)"}
    if not codes:
        return {"error": "no quedan códigos"}
    p = rng.choice(pool)
    code, rest = codes[0], codes[1:]
    _put(ddb, "codes", rest)
    _put(ddb, f"w#{p}", {"code": code, "round": g["round"]})
    g["winners"].append({"p": p, "apodo": nickname(p), "round": g["round"]})
    _put(ddb, "game", g)
    return {"apodo": nickname(p), "pool": len(pool), "quedan": len(rest)}


def public_state(ddb, g: dict, player: str = "") -> dict:
    """What every phone sees; codes never leave the server except to their own winner."""
    out = {k: g.get(k) for k in ("mode", "round", "phase", "carrera", "gano", "despertando")}
    out["rounds"] = ROUNDS
    sc = SCENARIOS.get(g.get("scenario", ""), {})
    out["escenario"] = {"label": sc.get("label", ""), "desc": sc.get("desc", ""), "q": g.get("q", "")}
    out["ganadores"] = [{"apodo": w["apodo"], "round": w["round"]} for w in g.get("winners", [])]
    if player:
        b = _get(ddb, f"b#{g.get('round')}#{player}")
        w = _get(ddb, f"w#{player}")
        out["yo"] = {"apodo": nickname(player), "apuesta": b and b["store"], "codigo": w and w["code"]}
    return out


def admin(ddb, br, qs: dict, body: str) -> dict:
    g, act = game(ddb), qs.get("action", "state")
    if act == "mode":
        g["mode"] = "sorteo" if qs.get("to") == "sorteo" else "libre"
    elif act == "open":
        if g.get("phase") in ("abierta", "cerrada"):
            return {"error": "esta ronda sigue en curso"}
        n = g["round"] + 1
        key = ORDER[(n - 1) % len(ORDER)]
        g |= {"mode": "sorteo", "round": n, "scenario": key, "phase": "abierta", "carrera": None, "gano": None, "despertando": False,
              "q": SCENARIOS[key]["q"]}
    elif act == "close":
        g["phase"] = "cerrada"
    elif act == "run":  # closes the bets and races in one step
        if g.get("phase") not in ("abierta", "cerrada"):
            return {"error": "abre una ronda primero"}
        sc = SCENARIOS[g["scenario"]]
        if not take_quota(ddb, COUNTER_TABLE, MAX_QUERIES):
            return {"error": "se acabó el cupo de consultas"}
        g["phase"] = "cerrada"
        _put(ddb, "game", g)  # phones show the race as started while the race runs
        scn = {"corpus": sc["corpus"], "attr": sc.get("attr"), "context": sc.get("context", False)}
        vec = embed(br, g["q"])
        t0 = time.time()
        hits, carrera = race(vec, ddb_hits(ddb, corpora()[sc["corpus"]], scn), scn)
        sleepy = save_wake(ddb, "wk#game", vec, scn, carrera, {h["id"] for h in hits}, t0)
        g |= {"phase": "resultado", "carrera": carrera, "gano": winner(carrera), "despertando": sleepy}
    elif act == "wake":  # the admin page calls this while a store is still waking up
        if not g.get("despertando"):
            return {"state": admin_state(ddb, g)}
        g["carrera"] = merge_rows(g["carrera"], wake(ddb, "wk#game"))
        g |= {"gano": winner(g["carrera"]), "despertando": any(r["estado"] == "despertando" for r in g["carrera"])}
    elif act == "draw":  # one code among the correct bets of this round (nobody wins twice)
        if any(w["round"] == g["round"] for w in g["winners"]):
            return {"error": "esta ronda ya se sorteó"}
        right = [p for p, st in bets(ddb, g["round"]).items() if st == g.get("gano")]
        return draw(ddb, g, right) | {"state": admin_state(ddb, g)}
    elif act == "bots":  # test players bet at random in the open round (same ids every round)
        if g.get("mode") != "sorteo" or g.get("phase") != "abierta":
            return {"error": "abre una ronda primero"}
        n, rng = max(1, min(int(qs.get("n") or 10), 50)), random.SystemRandom()
        for i in range(1, n + 1):
            _put(ddb, f"b#{g['round']}#{BOT}{i:02d}", {"store": rng.choice(STORES), "t": time.time()})
        return {"bots": n, "state": admin_state(ddb, g)}
    elif act == "reset":
        for i in _scan(ddb, "b#") + _scan(ddb, "w#") + _scan(ddb, "wk#"):
            ddb.delete_item(TableName=COUNTER_TABLE, Key={"id": i["id"]})
        g = {"mode": "libre", "round": 0, "phase": "", "winners": []}
    elif act != "state":
        return {"error": "acción desconocida"}
    _put(ddb, "game", g)
    return {"state": admin_state(ddb, g)}


def all_players(ddb) -> list:
    return sorted({i["id"]["S"].split("#", 2)[2] for i in _scan(ddb, "b#")})


def admin_state(ddb, g: dict) -> dict:
    """The projector view: bet counts and winners' nicknames, never the codes themselves."""
    counts = {k: 0 for k in STORES}
    for st in bets(ddb, g.get("round", 0)).values():
        counts[st] += 1
    return public_state(ddb, g) | {"apuestas": counts, "codigos_restantes": len(_get(ddb, "codes") or []),
                                   "jugadores": len(all_players(ddb)), "orden": [SCENARIOS[k]["label"] for k in ORDER]}


def _json(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


def lambda_handler(event, context):
    path = event.get("rawPath", "/")
    if path in ("/", "/index.html", "/admin"):
        # no-cache: phones re-check the page, so a fix deployed mid-event reaches them on reload
        return {"statusCode": 200, "headers": {"content-type": "text/html; charset=utf-8", "cache-control": "no-cache"},
                "body": ADMIN_PAGE if path == "/admin" else PAGE}
    qs = event.get("queryStringParameters") or {}
    if path == "/admin/api":  # not behind the opening gate, so the round can be set up and rehearsed
        given = (event.get("headers") or {}).get("x-admin-key") or ""  # a header, not the URL: URLs land in history and logs
        if not ADMIN_CODE or not hmac.compare_digest(given.encode(), ADMIN_CODE.encode()):
            return _json(403, {"error": "clave de admin inválida"})
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode()
        ddb, br = _clients()
        return _json(200, admin(ddb, br, qs, body))
    if path not in ("/ask", "/top", "/state", "/bet", "/wake"):
        return _json(404, {"error": "no existe"})
    # 425 Too Early on what costs money (embedding + store calls). /state and /bet only touch a small
    # DynamoDB item and show nothing until the admin starts a giveaway, so it can be rehearsed any time.
    if time.time() < OPEN_AT and path in ("/ask", "/top", "/wake"):
        return _json(425, {"error": "Todavía no es hora del evento. La demo abre el día de la charla.", "abre": OPEN_AT})
    if not code_ok(qs.get("code"), EVENT_CODE):
        return _json(403, {"error": "código de evento inválido"})
    player = qs.get("p") if PLAYER.fullmatch(qs.get("p") or "") else ""
    if path == "/top":
        ddb, _ = _clients()
        return _json(200, leaderboard(ddb, COUNTER_TABLE, player))
    if path == "/wake":  # no Bedrock call: re-asks the stores that were asleep, with the vector kept by /ask
        rid = qs.get("rid") or ""
        if not re.fullmatch(r"[0-9a-f]{16}", rid):
            return _json(400, {"error": "consulta desconocida"})
        return _json(200, {"carrera": wake(_clients()[0], f"wk#{rid}")})
    if path in ("/state", "/bet"):
        ddb, _ = _clients()
        g = game(ddb)
        if path == "/bet" and not (player and place_bet(ddb, g, player, qs.get("store", ""))):
            return _json(409, {"error": "las apuestas están cerradas"})
        return _json(200, public_state(ddb, g, player))
    corpus = qs.get("corpus") or "ley"
    if corpus not in corpora():
        return _json(400, {"error": "colección desconocida"})
    q = clean_question(qs.get("q"))
    if not q:
        return _json(400, {"error": "escribe una pregunta"})
    ddb, br = _clients()
    if game(ddb).get("mode") == "sorteo":  # during the giveaway only the admin's race runs
        return _json(409, {"error": "Ahora estamos en el sorteo: apuesta en la ronda."})
    if not take_quota(ddb, COUNTER_TABLE, MAX_QUERIES):
        return _json(429, {"error": "se acabó el cupo de consultas de esta demo"})
    vec = embed(br, q)
    t0 = time.time()
    hits, carrera = race(vec, ddb_hits(ddb, corpora()[corpus], {"corpus": corpus}), corpus)
    if carrera[0]["estado"] != "ok":
        return _json(502, {"error": "No hubo respuesta. Intenta de nuevo."})
    out = {"pregunta": q, "corpus": corpus, "resultados": hits, "carrera": carrera}
    rid = secrets.token_hex(8)
    if save_wake(ddb, f"wk#{rid}", vec, corpus, carrera, {h["id"] for h in hits}, t0):
        out["rid"] = rid  # the page calls /wake?rid= until the sleeping stores answer
    bet = qs.get("bet")
    won = winner(carrera)
    if player and bet in STORES and won:  # no race (one store answered) = no bet scored
        out["apuesta"] = settle_bet(ddb, COUNTER_TABLE, player, bet, won)
    return _json(200, out)


# Example chips per corpus. Ley ones checked on the live index (2026-10-09) to return the right norm in the top 3.
EXAMPLES = {
    "ley": ["¿Qué pasa si encuentro una billetera en la calle?", "¿A qué edad se puede votar en Chile?",
            "¿Cuánto dura el permiso postnatal?", "¿Me pueden despedir sin aviso?", "¿Desde qué edad se puede trabajar?",
            "¿Cuántas horas a la semana se puede trabajar?"],
    "so": ["¿Cómo invierto un string en Python?", "¿Qué diferencia hay entre == y === en JavaScript?",
           "¿Cómo centro un div?", "¿Qué es un puntero en C?"],
}
# "Sorpréndeme": a random one of these. Funny on purpose, still answerable from the corpus.
SURPRISE = {
    "ley": ["¿Qué pasa si encuentro un tesoro enterrado en mi patio?", "¿Es legal tener un pudú de mascota?",
            "¿Qué pasa si mi perro muerde al cartero?", "¿Me pueden echar del arriendo por tener un gato?",
            "¿Puedo devolver un regalo que no me gustó?", "¿Qué pasa si el vecino corta mi árbol?",
            "¿Me pueden multar por andar en bicicleta por la vereda?", "¿Puedo casarme con mi prima?"],
    "so": ["¿Cómo salgo de Vim?", "¿Por qué mi código funciona si no cambié nada?",
           "¿Por qué NaN no es igual a NaN?", "¿Es mala idea guardar contraseñas en texto plano?",
           "¿Por qué funciona en mi computador y no en producción?", "¿Cómo deshago el último commit?"],
}

PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fast &amp; Vectorious · SCD Chile 2026</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,600;9..40,800&display=swap" rel="stylesheet"><style>
:root{--violet:#a433f3;--magenta:#d054fc;--ink:#2b2350;--muted:#6b6b80;--navy:#0f1240;--card:#fff;--soft:#f4f0fc;--line:#e4daf9}
*{box-sizing:border-box}[hidden]{display:none!important}body{font-family:"DM Sans",system-ui,sans-serif;margin:0;background:var(--soft);color:var(--ink)}
header{background:linear-gradient(90deg,#0f1240f2 0,#0f1240b0 45%,#0f124000 80%),var(--navy) url(__BANNER__) center bottom/cover no-repeat;color:#fff;padding:18px 16px 64px}
.top{max-width:680px;margin:auto;display:flex;align-items:center;gap:12px}.top img{width:64px;height:64px;flex:none}
.kicker{font-size:.75rem;letter-spacing:.08em;text-transform:uppercase;color:#e4c8ff;margin:0}
h1{font-size:1.55rem;font-weight:800;margin:2px 0 0;line-height:1.1}h1 span{background:linear-gradient(90deg,var(--magenta),#ff8ad8);-webkit-background-clip:text;background-clip:text;color:transparent}
main{max-width:680px;margin:-44px auto 0;padding:0 16px 24px;position:relative}
.panel{background:var(--card);border-radius:16px;padding:16px;box-shadow:0 8px 24px #2b235014;margin-bottom:14px}
form{display:flex;gap:8px}input{flex:1;min-width:0;padding:12px;font:inherit;font-size:1rem;border:1.5px solid var(--line);border-radius:12px;color:var(--ink)}
input:focus{outline:2px solid var(--violet);border-color:transparent}
button{padding:12px 16px;font:inherit;font-weight:600;font-size:1rem;border:0;border-radius:12px;background:linear-gradient(90deg,var(--violet),var(--magenta));color:#fff;cursor:pointer}
#cerr{color:#c2410c;font-weight:600}#ok{color:#15803d;font-size:.9rem;margin:8px 0 0}
.seg{display:flex;background:var(--soft);border-radius:12px;padding:4px;margin-bottom:12px}.seg button{flex:1;background:none;color:var(--muted);padding:8px;font-size:.9rem}
.seg button[aria-pressed=true]{background:#fff;color:var(--ink);box-shadow:0 1px 4px #2b235022}
.lbl{font-size:.8rem;font-weight:600;color:var(--muted);margin:14px 0 6px}
#ex,#bet{display:flex;flex-wrap:wrap;gap:8px}.ex,.bt{padding:7px 12px;font-size:.88rem;font-weight:400;background:var(--soft);color:#4c1d95;border:1px solid var(--line);border-radius:999px;text-align:left}
.ex.sur{background:#fff3fd;border-color:#f0b8ef;color:#a21caf;font-weight:600}
.bt{display:flex;align-items:center;gap:6px}.bt[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink);font-weight:600}
h2{font-size:1rem;font-weight:800;margin:0 0 4px}.sub{color:var(--muted);font-size:.85rem;margin:0 0 12px}
.row{display:grid;grid-template-columns:96px minmax(0,1fr);gap:4px 10px;align-items:center;margin:10px 0}
.name{font-weight:600;font-size:.88rem;display:flex;align-items:center;gap:6px}.dot{width:10px;height:10px;border-radius:50%;flex:none}
.track{height:22px;background:var(--soft);border-radius:8px;overflow:hidden;position:relative}
.bar{height:100%;width:0;border-radius:8px;transition:width .9s cubic-bezier(.2,.8,.2,1)}
.val{grid-column:2;font-size:.8rem;color:var(--muted);display:flex;flex-wrap:wrap;justify-content:space-between;gap:2px 8px}.val b{color:var(--ink);font-weight:800}
.wake .bar{width:100%!important;opacity:.35;background-image:repeating-linear-gradient(45deg,#fff6 0 8px,transparent 8px 16px)!important;animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.15}}.win{color:#15803d;font-weight:700}
.soon{border:2px solid var(--magenta)}#round{border:2px solid var(--violet)}.rq{font-weight:600;font-size:1rem;margin:10px 0;padding:10px 12px;background:var(--soft);border-radius:12px}
#round h2{font-size:1.35rem;line-height:1.2}.pick{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}
.pick .bt{justify-content:center;padding:16px 8px;font-size:1rem;border-radius:14px;border-width:2px}.pick .bt[aria-pressed=true]{box-shadow:0 0 0 3px var(--magenta)}
.note{font-size:.9rem;color:var(--muted);margin:6px 0 0}.wins{margin:14px 0 0;padding-top:10px;border-top:1px solid var(--line);font-size:.9rem}
#prize{background:linear-gradient(90deg,#dcfce7,#fdf4ff);border-radius:14px;padding:14px;margin-top:12px;color:#14532d;font-weight:600}
#prize code{display:block;font-size:1.3rem;margin:8px 0;padding:8px;background:#fff;border-radius:8px;color:var(--ink);word-break:break-all;user-select:all}
.bt:disabled{opacity:.55}.soon .sub{margin:0}#verdict,.vd{border-radius:12px;padding:10px 12px;margin:12px 0 0;font-weight:600}#verdict.yes,.vd.yes{background:#dcfce7;color:#14532d}#verdict.no,.vd.no{background:#fef3c7;color:#78350f}
ol{margin:6px 0 0;padding-left:22px}ol li{margin:3px 0}ol li.me{font-weight:800;color:#6d28d9}
article{border-top:1px solid var(--line);padding:12px 0}article:first-child{border:0;padding-top:0}
article b{color:#6d28d9}article p{margin:6px 0;line-height:1.45}a{color:#3d5afe}small{color:var(--muted)}#msg{font-weight:600;margin:0 0 8px}
</style></head><body><header><div class="top"><img src="__BADGE__" alt="Student Community Day Chile 2026"><div>
<p class="kicker">Fast &amp; Vectorious · SCD Chile 2026</p><h1 id="h1"></h1></div></div></header><main>
<section class="panel soon" id="soon" hidden><h2>⏳ Todavía no es hora del evento</h2>
<p class="sub">La demo abre el <b id="soonwhen"></b> (hora de Chile). Vuelve durante la charla para jugar.</p></section>
<section class="panel" id="round" hidden><p class="kicker" style="color:var(--violet)" id="rkick"></p>
<h2 id="rhead"></h2><p class="sub" id="rsub"></p><div class="rq" id="rq" hidden></div>
<div id="rbet" class="pick" hidden></div><p class="note" id="rnote"></p>
<div id="rbars"></div><div id="rverdict"></div>
<div id="prize" hidden>🎉 ¡Ganaste un código de créditos AWS! Es solo para ti, no lo muestres en pantalla:<code id="pcode"></code>
<button type="button" id="pcopy">Copiar</button> <small>Canjéalo en aws.amazon.com/awscredits</small></div>
<p class="note">Tu nombre en la pantalla: <b id="rme"></b></p><div class="wins" id="rw" hidden></div></section>
<section class="panel" id="search"><div class="seg" id="seg" hidden></div>
<form id="f"><input id="q" maxlength="300" required><button>Buscar</button></form>
<div id="codebox" hidden><p>Escribe el código que aparece en la pantalla de la charla:</p>
<form id="cf"><input id="c" maxlength="20" autocomplete="off" autocapitalize="none"><button>Entrar</button></form>
<p id="cerr" hidden>Código incorrecto. Revisa el que aparece en la pantalla.</p></div><p id="ok" hidden>✓ Estás dentro</p>
<div id="betbox" hidden><p class="lbl">Tu apuesta: ¿qué base responde primero?</p><div id="bet"></div></div>
<p class="lbl">Prueba con</p><div id="ex"></div></section>
<section class="panel" id="race" hidden><h2>La carrera: la misma pregunta en 4 bases</h2>
<p class="sub">Tiempo de cada búsqueda, medido desde la Lambda (red incluida).</p><div id="bars"></div><div id="verdict" hidden></div></section>
<section class="panel" id="board" hidden><h2>Ranking de apuestas</h2><p class="sub" id="me"></p><ol id="top"></ol></section>
<section class="panel" id="res" hidden><p id="msg"></p><div id="out"></div></section>
<p><small id="foot"></small></p></main><script>
const OPEN_AT=__OPEN_AT__*1000,CORPORA=__CORPORA__,EX=__EXAMPLES__,SUR=__SURPRISE__,RACE=__RACE__;
const STORES={ddb:['DynamoDB','#3d5afe'],s3v:['S3 Vectors','#e8590c'],aoss:['OpenSearch','#0e9f9a'],aurora:['Aurora','#a433f3']};
const C={ley:{h1:'Busca en la <span>Ley Chile</span>',tab:'Ley Chile',ph:'¿Cuántos días de vacaciones me corresponden?',res:n=>`Los ${n} artículos más cercanos`,link:'Ver en Ley Chile',
foot:'Fuente: Ley Chile, Biblioteca del Congreso Nacional. Búsqueda educativa, no es asesoría legal.'},
so:{h1:'Busca en <span>Stack Overflow</span>',tab:'Stack Overflow',ph:'¿Cómo leo un archivo línea por línea en Python?',res:n=>`Las ${n} respuestas más cercanas`,link:'Ver en Stack Overflow',
foot:'Fuente: Stack Overflow en español, contenido CC BY-SA 4.0 de sus autores (cada resultado enlaza a la publicación original).'}};
const $=id=>document.getElementById(id),box=$('codebox'),cerr=$('cerr'),ok=$('ok');let tried=false;
const ls={get:k=>{try{return localStorage.getItem(k)}catch(e){return null}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}}};
let code=new URLSearchParams(location.search).get('code')||ls.get('code')||'';if(code)ls.set('code',code);
let player=ls.get('player');if(!player){player=[...crypto.getRandomValues(new Uint8Array(16))].map(b=>b.toString(16).padStart(2,'0')).join('');ls.set('player',player)}
const keys=Object.keys(STORES);let bet=ls.get('bet');if(!keys.includes(bet)){bet=keys[Math.floor(Math.random()*keys.length)];ls.set('bet',bet)}
let corpus=CORPORA.includes(ls.get('corpus'))?ls.get('corpus'):CORPORA[0];
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ms=x=>x>=1000?(x/1000).toLocaleString('es-CL',{maximumFractionDigits:1})+' s':x.toLocaleString('es-CL',{maximumFractionDigits:x<10?1:0})+' ms';
function setCorpus(c){corpus=c;ls.set('corpus',c);const t=C[c];$('h1').innerHTML=t.h1;$('q').placeholder=t.ph;$('foot').textContent=t.foot;
$('seg').innerHTML=CORPORA.map(k=>`<button type="button" aria-pressed="${k===c}" data-c="${k}">${C[k].tab}</button>`).join('');$('seg').hidden=CORPORA.length<2;
$('seg').querySelectorAll('button').forEach(b=>b.onclick=()=>{setCorpus(b.dataset.c);$('res').hidden=$('race').hidden=true});
$('ex').innerHTML='<button type="button" class="ex sur" id="sur">🎲 Sorpréndeme</button>'+EX[c].map(q=>`<button type="button" class="ex">${esc(q)}</button>`).join('');
$('ex').querySelectorAll('.ex').forEach(b=>b.onclick=()=>{$('q').value=b.id==='sur'?SUR[c][Math.floor(Math.random()*SUR[c].length)]:b.textContent;$('f').requestSubmit()})}
function drawBet(){$('betbox').hidden=!RACE;$('bet').innerHTML=keys.map(k=>`<button type="button" class="bt" aria-pressed="${k===bet}" data-k="${k}"><span class="dot" style="background:${STORES[k][1]}"></span>${STORES[k][0]}</button>`).join('');
$('bet').querySelectorAll('.bt').forEach(b=>b.onclick=()=>{bet=b.dataset.k;ls.set('bet',bet);drawBet()})}
$('cf').onsubmit=e=>{e.preventDefault();code=$('c').value.trim().toLowerCase();ls.set('code',code);tried=true;if(mode==='sorteo'){box.hidden=true;poll()}else $('f').requestSubmit()};
function row(k,r){const [n,c]=STORES[k];const w=r.estado==='despertando';
const v=r.estado==='ok'?`<span><b>${ms(r.ms)}</b>${r.desperto?' · tuvo que despertar':''}</span><span>${r.coinciden}/3 iguales a DynamoDB</span>`:w?'<span>despertando… estaba apagada (escala a cero), puede tardar de 15 s a más de un minuto</span>':r.estado==='error'?'<span>sin respuesta</span>':'<span>buscando…</span>';
return `<div class="row ${w?'wake':''}" data-k="${k}"><span class="name"><span class="dot" style="background:${c}"></span>${n}</span><div class="track"><div class="bar" style="background:${c}"></div></div><div class="val">${v}</div></div>`}
function pending(){$('race').hidden=!RACE;$('verdict').hidden=true;$('bars').innerHTML=keys.map(k=>row(k,{estado:'espera'})).join('')}
function draw(carrera,box=$('bars')){const oks=carrera.filter(r=>r.estado==='ok'),max=Math.max(...oks.map(r=>r.ms),1),best=Math.min(...oks.map(r=>r.ms));
box.innerHTML=carrera.map(r=>row(r.store,r)).join('');
requestAnimationFrame(()=>requestAnimationFrame(()=>carrera.forEach(r=>{if(r.estado!=='ok')return;const el=box.querySelector(`[data-k="${r.store}"]`);
el.querySelector('.bar').style.width=Math.max(3,100*r.ms/max)+'%';if(r.ms===best&&oks.length>1)el.querySelector('.val b').insertAdjacentHTML('afterend',' <span class="win">más rápida</span>')})))}
function verdict(a){const v=$('verdict');if(!a){v.hidden=true;return}v.hidden=false;
v.className=a.acierto?'yes':'no';v.textContent=a.acierto?`🎉 ¡Acertaste, ${a.apodo}! Ganó ${STORES[a.gano][0]}. Llevas ${a.puntos} ${a.puntos===1?'punto':'puntos'}.`
:`Ganó ${STORES[a.gano][0]}. Apostaste por ${STORES[a.tu][0]}. Llevas ${a.puntos} ${a.puntos===1?'punto':'puntos'}, ${a.apodo}.`}
async function board(){if(!RACE)return;try{const r=await fetch('top?code='+encodeURIComponent(code)+'&p='+player);if(!r.ok)return;const d=await r.json();if(!d.jugadores)return;
$('board').hidden=false;$('top').innerHTML=d.top.map(x=>`<li>${esc(x.apodo)} · ${x.puntos}</li>`).join('');
$('me').textContent=d.tu_lugar?`Vas en el lugar ${d.tu_lugar} de ${d.jugadores}.`:`${d.jugadores} personas apostando. Busca algo para entrar al ranking.`}catch(e){}}
let gen=0;  // a new question stops the previous one's /wake loop
async function wakeLoop(rid,carrera,my){while(my===gen&&carrera.some(r=>r.estado==='despertando')){
try{const r=await fetch('wake?'+new URLSearchParams({rid,code}));if(!r.ok||my!==gen)return;const d=await r.json();if(!d.carrera.length)return;
d.carrera.forEach(x=>{const j=carrera.findIndex(y=>y.store===x.store);if(j>=0)carrera[j]=x});draw(carrera)}catch(e){return}}}
$('f').onsubmit=async e=>{e.preventDefault();const q=$('q').value,m=$('msg'),o=$('out'),my=++gen;
$('res').hidden=false;m.textContent='Buscando…';o.innerHTML='';pending();
try{const r=await fetch('ask?q='+encodeURIComponent(q)+'&corpus='+corpus+'&bet='+bet+'&p='+player+'&code='+encodeURIComponent(code));const d=await r.json().catch(()=>({}));
if(r.status===425){m.textContent=d.error;$('race').hidden=true;return}
if(r.status===403){m.textContent='';$('res').hidden=$('race').hidden=true;ok.hidden=true;box.hidden=false;cerr.hidden=!tried;$('c').select();return}
box.hidden=true;cerr.hidden=true;ok.hidden=false;
if(!r.ok){m.textContent=d.error||(r.status===429||r.status>=500?'Mucha gente preguntando a la vez. Intenta de nuevo en unos segundos.':'Error');$('race').hidden=true;return}
draw(d.carrera||[]);verdict(d.apuesta);if((d.carrera||[]).length<2)$('race').hidden=true;
m.textContent=C[corpus].res(d.resultados.length);
o.innerHTML=d.resultados.map((h,i)=>`<article><b>${i+1}. ${esc(h.norma)}</b><p>${esc(h.texto)}</p><a href="${esc(h.url)}" target="_blank" rel="noopener">${C[corpus].link}</a></article>`).join('');
($('race').hidden?$('res'):$('race')).scrollIntoView({behavior:'smooth',block:'start'});board();if(d.rid)wakeLoop(d.rid,d.carrera,my)}
catch(err){m.textContent='No hubo respuesta. Intenta de nuevo.';$('race').hidden=true}};
setCorpus(corpus);drawBet();if(code)board();
let mode='libre';const SN=k=>STORES[k][0];
// One screen per phase of the giveaway: wait -> pick a store -> race -> result (+ draw), round after round
function round(d){mode=d.mode;const sorteo=mode==='sorteo';$('round').hidden=!sorteo;$('soon').hidden=sorteo||Date.now()>=OPEN_AT;['search','race','board','res'].forEach(id=>{if(sorteo)$(id).hidden=true});
if(!sorteo){$('search').hidden=false;return}
const ph=d.phase||'',mine=d.yo&&d.yo.apuesta,open=ph==='abierta',inRound=['abierta','cerrada','resultado'].includes(ph),me=d.yo?d.yo.apodo:'';
const drawn=d.ganadores.filter(w=>w.round===d.round);
$('rkick').textContent='Sorteo de créditos AWS'+(inRound?` · Ronda ${(d.round-1)%4+1} de 4`:'');
const H={'':['El sorteo empieza en un momento','Deja esta página abierta. En cada ronda eliges la base que crees que responde primero; si aciertas, entras al sorteo del código de esa ronda.'],
abierta:['¿Qué base responde primero?',`<b>${esc(d.escenario.label)}.</b> ${esc(d.escenario.desc)}`],
cerrada:['¡Arrancó la carrera! Mira la pantalla',mine?`Apostaste por <b>${SN(mine)}</b>.`:'No alcanzaste a apostar en esta ronda.'],
resultado:[d.gano?`Ganó ${SN(d.gano)}`:'Resultado',`<b>${esc(d.escenario.label)}.</b> ${esc(d.escenario.desc)}`]}[ph]||['Espera la próxima ronda',''];
$('rhead').textContent=H[0];$('rsub').innerHTML=H[1];
$('rq').hidden=!(inRound&&d.escenario.q);$('rq').textContent='Pregunta: “'+d.escenario.q+'”';
$('rbet').hidden=!open;$('rbet').innerHTML=keys.map(k=>`<button type="button" class="bt" aria-pressed="${k===mine}" data-k="${k}"><span class="dot" style="background:${STORES[k][1]}"></span>${SN(k)}</button>`).join('');
$('rbet').querySelectorAll('.bt').forEach(b=>b.onclick=async()=>{const r=await fetch('bet?'+new URLSearchParams({code,p:player,store:b.dataset.k}));if(r.ok){last='';round(await r.json())}});
$('rnote').textContent=open?(mine?`✓ Elegiste ${SN(mine)}. Puedes cambiar hasta que se cierren las apuestas.`:'Toca una base para apostar.'):'';
if(ph==='resultado'&&d.carrera){draw(d.carrera,$('rbars'));
const hit=mine&&mine===d.gano,won=drawn.find(w=>w.apodo===me);
const v=!d.gano?'':!mine?'No apostaste en esta ronda. Apuesta en la próxima.':!hit?`Apostaste por ${SN(mine)}. Esta vez no; apuesta en la próxima ronda.`
:won?'¡Acertaste y ganaste el código de esta ronda!':drawn.length?`Acertaste. El código fue para ${esc(drawn[0].apodo)}. Sigue en la próxima ronda.`:'¡Acertaste! Entras al sorteo del código de esta ronda.';
$('rverdict').innerHTML=v?`<p class="vd ${hit?'yes':'no'}">${v}</p>`:''}
else{$('rbars').innerHTML='';$('rverdict').innerHTML=''}
const c=d.yo&&d.yo.codigo;$('prize').hidden=!c;if(c)$('pcode').textContent=c;$('rme').textContent=me;
$('rw').hidden=!d.ganadores.length;$('rw').innerHTML='Ganadores: '+d.ganadores.map(w=>esc(w.apodo)).join(', ')}
let last='';  // redraw only when the round changes, so bars do not restart every poll
async function poll(){if(!code||document.hidden)return;try{const r=await fetch('state?'+new URLSearchParams({code,p:player}));
if(r.status===403){box.hidden=false;return}if(!r.ok)return;const t=await r.text();if(t===last)return;last=t;round(JSON.parse(t))}catch(e){}}
$('pcopy').onclick=()=>{try{navigator.clipboard.writeText($('pcode').textContent)}catch(e){}};
if(!code){box.hidden=false}
poll();setInterval(poll,2500+Math.random()*1000);document.addEventListener('visibilitychange',poll);
if(Date.now()<OPEN_AT){$('soon').hidden=false;$('soonwhen').textContent=new Date(OPEN_AT).toLocaleString('es-CL',{timeZone:'America/Santiago',weekday:'long',day:'numeric',month:'long',hour:'2-digit',minute:'2-digit'})}</script></body></html>"""


ADMIN_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sorteo · Fast &amp; Vectorious</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,600;9..40,800&display=swap" rel="stylesheet"><style>
:root{--violet:#a433f3;--magenta:#d054fc;--ink:#2b2350;--navy:#0f1240;--panel:#1b1f5a;--track:#2a2f73;--muted:#c9c2e8}
*{box-sizing:border-box}body{font-family:"DM Sans",system-ui,sans-serif;margin:0;background:var(--navy);color:#fff}
main{max-width:1100px;margin:auto;padding:20px 16px 40px}h1{font-size:1.6rem;margin:0}h1 span{color:var(--magenta)}.muted{color:var(--muted)}
.hd{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.pill{background:var(--magenta);border-radius:999px;padding:6px 16px;font-weight:800;font-size:1.1rem}
.steps{display:flex;gap:6px;margin:14px 0;padding:0;list-style:none}.steps li{flex:1;text-align:center;padding:8px;border-radius:10px;background:var(--panel);color:var(--muted);font-weight:600}
.steps li.now{background:var(--magenta);color:#fff}.steps li.done{background:#3b3f86;color:#fff}
.grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.panel{background:var(--panel);border-radius:16px;padding:20px}.lbl{font-size:1rem;margin:0}.q{font-size:1.6rem;font-weight:800;margin:6px 0 14px;line-height:1.2}
.phase{font-size:1.3rem;font-weight:800;margin:0 0 6px}.row{display:grid;grid-template-columns:120px minmax(0,1fr) 150px;gap:10px;align-items:center;margin:12px 0;font-weight:600;font-size:1.1rem}
.track{height:30px;background:var(--track);border-radius:8px;overflow:hidden}.bar{height:100%;width:0;border-radius:8px;transition:width .8s}
.wake .bar{width:100%!important;opacity:.35;animation:pulse 1.2s ease-in-out infinite}@keyframes pulse{50%{opacity:.12}}
.win{color:#4ade80}.banner{margin-top:14px;background:#16a34a;border-radius:12px;padding:14px 18px;font-size:1.5rem;font-weight:800}
ol.w{padding-left:22px;margin:8px 0}ol.w li{margin:6px 0;font-size:1.1rem}.stat{font-size:1.1rem;margin:10px 0 0}
button,select,input,textarea{font:inherit;border-radius:10px;border:0;padding:9px 12px}button{background:var(--track);color:#fff;font-weight:600;cursor:pointer;margin:3px 2px}
button:disabled{opacity:.35;cursor:not-allowed}#next{display:block;width:100%;margin:16px 0 4px;padding:18px;font-size:1.35rem;font-weight:800;background:linear-gradient(90deg,var(--violet),var(--magenta))}
#hint{margin:0 0 16px;text-align:center}details{background:var(--panel);border-radius:16px;padding:14px 18px;margin-top:8px}summary{cursor:pointer;color:var(--muted)}
details h3{font-size:1rem;margin:16px 0 6px}textarea{width:100%;height:90px}
#toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);background:#16a34a;color:#fff;padding:14px 20px;border-radius:12px;
font-weight:700;font-size:1.05rem;opacity:0;transition:.25s;z-index:9;max-width:90vw;box-shadow:0 8px 24px #0006;pointer-events:none}
#toast.show{opacity:1;transform:translate(-50%,0)}#toast.bad{background:#dc2626}
/* auditorium: readable from the back rows */.phase{font-size:2rem}.q{font-size:1.6rem}.row{font-size:1.6rem;grid-template-columns:8em minmax(0,1fr) 7em}.track{height:44px}.banner{font-size:2.4rem}ol.w li{font-size:1.4rem}
#lock{max-width:420px;margin:15vh auto}#lock input{width:100%;margin:8px 0}#err{color:#fca5a5}
</style></head><body><main>
<div id="lock"><h1>Sorteo <span>admin</span></h1><p class="muted">Clave de admin:</p><input id="k" type="password" autocomplete="off"><button id="go">Entrar</button><p id="err"></p></div>
<div id="app" hidden>
<div class="hd"><h1>Sorteo de <span>créditos AWS</span></h1><div class="pill" id="rnd"></div></div>
<ol class="steps" id="steps"><li>1 · Apuestan</li><li>2 · Carrera</li><li>3 · Sorteo</li></ol>
<div class="grid"><section class="panel" id="proj"><p class="lbl muted" id="lbl"></p><p class="q" id="q"></p><p class="phase" id="phase"></p>
<div id="rows"></div><div class="banner" id="banner" hidden></div></section>
<section class="panel"><p class="lbl muted">Ganadores</p><ol class="w" id="wins"></ol><p class="stat" id="left"></p><p class="stat" id="players"></p></section></div>
<button id="next"></button><p class="muted" id="hint"></p>
<details><summary>Preparación y emergencias (no proyectar)</summary>
<h3>Ensayo</h3><button id="bots">🤖 +10 jugadores de prueba en la ronda abierta</button>
<h3>Emergencia</h3><button data-to="libre">Volver a modo libre (la sala busca)</button>
<h3>Empezar de cero</h3><button id="reset">Reiniciar sorteo (borra apuestas y ganadores)</button></details></div>
<div id="toast"></div><script>
const ST={ddb:['DynamoDB','#3d5afe'],s3v:['S3 Vectors','#e8590c'],aoss:['OpenSearch','#0e9f9a'],aurora:['Aurora','#a433f3']};
const $=id=>document.getElementById(id);let key='',busy=0,last=null,waking=false,plan=null;try{key=sessionStorage.getItem('k')||''}catch(e){}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ms=x=>x>=1000?(x/1000).toLocaleString('es-CL',{maximumFractionDigits:1})+' s':x.toLocaleString('es-CL',{maximumFractionDigits:1})+' ms';
async function api(params,body){const r=await fetch('admin/api?'+new URLSearchParams(params),{method:body?'POST':'GET',body,headers:{'x-admin-key':key}});
const d=await r.json();if(r.status===403)throw new Error('clave');if(d.state)show(d.state);return d}
function toast(t,bad){const e=$('toast');e.textContent=t;e.className='show'+(bad?' bad':'');clearTimeout(e._t);e._t=setTimeout(()=>e.className='',4000)}
function msg(a,d){const s=d.state||{};if(d.error)return d.error;switch(a){
case 'mode':return s.mode==='sorteo'?'Modo sorteo: los celulares esperan la primera ronda':'Modo libre: la sala puede buscar';
case 'open':return `Ronda ${s.round} abierta: ${s.escenario.label}`;
case 'run':{const w=(s.carrera||[]).find(r=>r.store===s.gano);return s.gano?`Ganó ${ST[s.gano][0]} en ${ms(w.ms)} · ${s.apuestas[s.gano]} acertaron`:'Sin ganador: respondió una sola base'}
case 'draw':return `🎉 Código para ${d.apodo} (entre ${d.pool}) · quedan ${d.quedan}`;
case 'bots':return `🤖 ${d.bots} jugadores de prueba apostaron al azar`;case 'reset':return 'Sorteo reiniciado'}return ''}
async function act(btn,params,body){const old=btn.textContent;busy++;btn.textContent=params.action==='run'?'Lanzando…':'…';document.querySelectorAll('button').forEach(b=>b.disabled=true);
try{const d=await api(params,body);const t=msg(params.action,d);if(t)toast(t,!!d.error);return d}catch(e){toast('No se pudo: '+e.message,true)}
finally{busy--;btn.textContent=old;if(last)show(last)}}
// The script of the giveaway: what the big button does next, given the state. Rounds cycle through the 4 questions,
// one code per round among the right guesses, until the codes run out.
function next(s){const n=s.round||0,ph=s.phase||'',left=s.codigos_restantes,hits=s.gano?s.apuestas[s.gano]:0,drawn=s.ganadores.some(w=>w.round===n);
if(s.mode!=='sorteo')return['Pasar a modo sorteo','Los celulares muestran «El sorteo empieza en un momento». Da un minuto para que escaneen el QR.',{action:'mode',to:'sorteo'}];
if(ph==='abierta')return['Cerrar apuestas y lanzar','La misma pregunta va a las 4 bases. Si una estaba apagada, su fila se completa sola cuando despierta.',{action:'run'}];
if(ph==='cerrada')return['Lanzar la carrera','',{action:'run'}];
if(ph==='resultado'&&!drawn&&hits>0&&left>0)return[`Sortear 1 código entre ${hits===1?'quien acertó':`los ${hits} que acertaron`}`,'Nadie gana dos veces.',{action:'draw'}];
if(left===0)return['Terminar: volver a modo libre','Se repartieron todos los códigos.',{action:'mode',to:'libre'}];
const k=n%4,why=ph==='resultado'&&!drawn?'Nadie acertó: el código queda para la próxima ronda. ':'';
return[`Abrir ronda ${k+1} de 4 · ${s.orden[k]}`,why+`Quedan ${left} ${left===1?'código':'códigos'}. Los celulares muestran las 4 bases para apostar.`,{action:'open'}]}
function show(s){last=s;$('lock').hidden=true;$('app').hidden=false;const ph=s.phase||'',inRound=['abierta','cerrada','resultado'].includes(ph),n=s.round||0;
$('rnd').textContent=s.mode!=='sorteo'?'Modo libre':inRound?`Ronda ${(n-1)%4+1} de 4`:'Esperando';
$('lbl').textContent=inRound?`${s.escenario.label}: ${s.escenario.desc}`:'';$('q').textContent=inRound&&s.escenario.q?'“'+s.escenario.q+'”':'';
const total=Object.values(s.apuestas).reduce((a,b)=>a+b,0),w=(s.carrera||[]).find(r=>r.store===s.gano);
$('phase').innerHTML=s.mode!=='sorteo'?'Modo libre: la sala busca lo que quiera':{'':'Escanea el QR y entra: el sorteo empieza en un momento',
abierta:`¡Apuesten! ¿Qué base responde primero? · ${total} ${total===1?'apuesta':'apuestas'}`,cerrada:'Apuestas cerradas. ¡Arrancó la carrera!',
resultado:s.gano?`Ganó <span class="win">${ST[s.gano][0]}</span> en ${ms(w.ms)} · ${s.apuestas[s.gano]} acertaron`+(s.despertando?' <span class="muted">· esperando a las que estaban apagadas…</span>':''):'Resultado'}[ph];
const res=ph==='resultado'&&s.carrera,oks=res?s.carrera.filter(r=>r.estado==='ok'):[],max=res?Math.max(...oks.map(r=>r.ms),1):Math.max(...Object.values(s.apuestas),1);
$('rows').innerHTML=ph==='abierta'||res?Object.keys(ST).map(k=>{const r=res?s.carrera.find(x=>x.store===k)||{}:null,v=res?(r.estado==='ok'?r.ms:0):s.apuestas[k];
const lab=res?(r.estado==='ok'?ms(r.ms)+(r.desperto?' · despertó':''):r.estado==='despertando'?'despertando…':'sin respuesta'):v+(v===1?' apuesta':' apuestas');
return `<div class="row ${res&&r.estado==='despertando'?'wake':''}"><span>${ST[k][0]}</span><div class="track"><div class="bar" style="background:${ST[k][1]};width:${Math.max(2,100*v/max)}%"></div></div><span>${lab}</span></div>`}).join(''):'';
const now=s.ganadores.filter(x=>x.round===n&&ph==='resultado');$('banner').hidden=!now.length;$('banner').textContent='🎉 Código para '+now.map(x=>x.apodo).join(', ');
$('wins').innerHTML=s.ganadores.map(x=>`<li>${esc(x.apodo)} <span class="muted">· ronda ${(x.round-1)%4+1}</span></li>`).join('')||'<li class="muted">Todavía nadie</li>';
$('left').textContent=`Códigos restantes: ${s.codigos_restantes}`;$('players').textContent=`Personas que han apostado: ${s.jugadores}`;
const drawn=s.ganadores.some(x=>x.round===n),step=ph==='abierta'?0:ph==='cerrada'||(ph==='resultado'&&!drawn)?1:ph==='resultado'?2:-1;
document.querySelectorAll('#steps li').forEach((e,i)=>e.className=i===step?'now':i<step?'done':'');
plan=next(s);$('next').textContent=plan[0];$('hint').textContent=plan[1];
if(s.despertando&&!waking){waking=true;api({action:'wake'}).catch(()=>{}).finally(()=>{waking=false})}
if(busy)return;document.querySelectorAll('button').forEach(b=>b.disabled=false);$('bots').disabled=ph!=='abierta'}
$('next').onclick=()=>act($('next'),plan[2]);
$('bots').onclick=()=>act($('bots'),{action:'bots'});
document.querySelectorAll('[data-to]').forEach(b=>b.onclick=()=>act(b,{action:'mode',to:b.dataset.to}));
$('reset').onclick=()=>{if($('reset').dataset.sure){delete $('reset').dataset.sure;act($('reset'),{action:'reset'}).then(()=>{$('reset').textContent='Reiniciar sorteo (borra apuestas y ganadores)'})}else{$('reset').dataset.sure=1;$('reset').textContent='¿Seguro? Toca otra vez'}};
$('go').onclick=()=>{key=$('k').value;try{sessionStorage.setItem('k',key)}catch(e){}start()};
async function start(){try{await api({action:'state'});setInterval(()=>{if(!busy)api({action:'state'}).catch(()=>{})},2000)}catch(e){$('err').textContent='Clave incorrecta'}}
if(key)start();</script></main></body></html>"""


def _data_uri(name: str) -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    return "data:image/webp;base64," + base64.b64encode(open(p, "rb").read()).decode() if os.path.exists(p) else ""


_race = bool(VECTOR_BUCKET or AOSS_ENDPOINT or AURORA_ARN)
PAGE = (PAGE.replace("__CORPORA__", json.dumps(list(corpora()) or ["ley"]))
        .replace("__EXAMPLES__", json.dumps(EXAMPLES, ensure_ascii=False))
        .replace("__SURPRISE__", json.dumps(SURPRISE, ensure_ascii=False))
        .replace("__RACE__", "true" if _race else "false").replace("__OPEN_AT__", str(OPEN_AT))
        .replace("__BANNER__", _data_uri("banner.webp")).replace("__BADGE__", _data_uri("badge.webp")))
