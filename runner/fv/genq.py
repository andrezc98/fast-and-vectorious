import random
import re
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

from fv.config import REGION

PROMPT = ("Lee este artículo de la ley chilena y escribe una sola pregunta, en español neutro y con "
          "palabras de una persona común, que este artículo responda. Responde solo con la pregunta.\n\n{text}")


def clean(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return ""
    line = re.sub(r'^\s*\d+[.)]\s*', "", lines[0]).strip().strip('"“”').strip()
    if not line:
        return ""
    if not line.startswith("¿"):
        line = "¿" + line[0].upper() + line[1:]
    return line if line.endswith("?") else line + "?"


def generate(chunks, n, model_id, seed=11):
    rt = boto3.client("bedrock-runtime", region_name=REGION, config=Config(retries={"mode": "adaptive", "max_attempts": 10}))
    picked = random.Random(seed).sample(chunks, min(n, len(chunks)))
    errors = []

    def one(c):
        try:
            r = rt.converse(modelId=model_id, messages=[{"role": "user", "content": [{"text": PROMPT.format(text=c["text"])}]}],
                            inferenceConfig={"maxTokens": 1024})  # Haiku 5.5: no temperature; reasoning eats tokens
            # Haiku 5.5 may lead with a reasoningContent block: take the first text block
            return clean(next((b["text"] for b in r["output"]["message"]["content"] if "text" in b), ""))
        except Exception as e:  # one bad call must not abort the batch; it is dropped below
            errors.append(e)
            return ""

    with ThreadPoolExecutor(8) as ex:
        texts = list(ex.map(one, picked))
    kept = [(t, c) for t, c in zip(texts, picked) if t]
    if not kept:
        first = f": {errors[0]}" if errors else ""
        raise RuntimeError(f"ley-questions: 0 questions kept out of {len(picked)}{first}")
    print(f"ley-questions: {len(picked) - len(kept)} failed or empty, {len(kept)} kept")
    return [{"qid": f"ley#q{i}", "text": t, "attr": c["attr"], "source": c["id"]} for i, (t, c) in enumerate(kept)]
