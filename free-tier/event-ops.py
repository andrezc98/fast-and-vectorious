"""Talk-day operations for the event site (event mode only).

  reset            counter back to 0; rehearsal players, bets, winners and the sorteo removed (AWS_PROFILE=fv-site)
  codes FILE|test  load the giveaway codes, one per line (replaces the remaining ones; prints only the count);
                   `test` loads TEST-1..TEST-5 for rehearsals
  warmup           ~30 parallel questions: wakes OpenSearch and Aurora, starts Lambda instances
  keepwarm MIN     one question every 4 min for MIN minutes, so nothing scales to zero mid-talk (~USD 0.30/h)

Rehearsal 2026-10-09, 100 phones x 3 questions: cold stack p95 6.5 s (OpenSearch adding capacity, new Lambda
instances); warm stack p95 0.8 s. Run warmup ~5 min before the demo, or keepwarm from the start of the talk.
Warm-up questions carry no bet, so they never reach the ranking; they do count toward the cap.
  uv run --project ../runner python event-ops.py reset
"""
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import boto3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))
from handler import EXAMPLES  # noqa: E402

COUNTER, HOST = "ley-search-counter", "https://scd2026.builtbyandres.com"


def tfvar(name: str) -> str:
    for line in open(os.path.join(HERE, "event.tfvars")):
        k, _, v = line.partition("=")
        if k.strip() == name:
            return v.strip().strip('"')
    raise KeyError(name)


def ask(i: int) -> str:
    corpus = "ley" if i % 2 else "so"
    q = random.choice(EXAMPLES[corpus])
    url = f"{HOST}/ask?" + urllib.parse.urlencode({"q": q, "corpus": corpus, "code": tfvar("event_code")})
    try:
        d = json.load(urllib.request.urlopen(url, timeout=30))
        return " ".join(f"{r['store']}={r.get('ms', r['estado'])}" for r in d["carrera"])
    except Exception as e:  # noqa: BLE001 - report and keep going
        return f"error {e}"


def reset():
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    players, kw = [], {"TableName": COUNTER, "ProjectionExpression": "id"}
    while True:
        r = ddb.scan(**kw)
        players += [i["id"]["S"] for i in r["Items"] if i["id"]["S"].startswith(("p#", "b#", "w#", "wk#")) or i["id"]["S"] == "game"]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    for i in range(0, len(players), 25):
        ddb.batch_write_item(RequestItems={COUNTER: [{"DeleteRequest": {"Key": {"id": {"S": p}}}} for p in players[i:i + 25]]})
    ddb.put_item(TableName=COUNTER, Item={"id": {"S": "queries"}, "n": {"N": "0"}})
    print(f"reset: counter 0, {len(players)} rehearsal items removed (players, bets, winners, sorteo)")


def codes(path: str):
    """Same item the admin page reads: id=codes, s=JSON list (handler._put)."""
    lines = [f"TEST-{i}" for i in range(1, 6)] if path == "test" else open(path).read().splitlines()
    cs = [c.strip() for c in lines if c.strip()][:50]
    boto3.client("dynamodb", region_name="us-east-1").put_item(
        TableName=COUNTER, Item={"id": {"S": "codes"}, "s": {"S": json.dumps(cs, ensure_ascii=False)}})
    print(f"codes: {len(cs)} {'test' if path == 'test' else 'real'} codes loaded")


def warmup(n: int = 30):
    with ThreadPoolExecutor(n) as ex:
        for line in ex.map(ask, range(n)):
            print(line)


def keepwarm(minutes: float):
    end = time.time() + minutes * 60
    while time.time() < end:
        print(time.strftime("%H:%M"), ask(random.randrange(2)), flush=True)
        time.sleep(240)  # under the 10-minute scale-to-zero window of OpenSearch and Aurora


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "reset":
        reset()
    elif cmd == "codes":
        codes(sys.argv[2])
    elif cmd == "warmup":
        warmup()
    elif cmd == "keepwarm":
        keepwarm(float(sys.argv[2]))
    else:
        sys.exit(__doc__)
