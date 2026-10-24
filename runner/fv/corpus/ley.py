import os
import re
import time
import urllib.request

import defusedxml.ElementTree as ET

from fv.chunk import chunk_words
from fv.io import write_jsonl

URL = "https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma={}"  # legacy service, no key (api-notes §7)


def _local(el) -> str:
    return el.tag.split("}")[-1]


def _first_text(root, name: str) -> str:
    for el in root.iter():
        if _local(el) == name and (el.text or "").strip():
            return el.text.strip()
    return ""


def fetch(id_norma: int, cache_dir: str) -> bytes:
    """One request per norm, cached, 3 s apart: BCN terms forbid systematic bulk extraction."""
    path = os.path.join(cache_dir, f"{id_norma}.xml")
    if os.path.exists(path):
        return open(path, "rb").read()
    time.sleep(3)
    # the endpoint answers an empty body to the default urllib/curl agent; a browser UA gets the XML
    req = urllib.request.Request(URL.format(id_norma), headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=120).read()
    if not data.strip():
        raise ValueError(f"empty response for idNorma {id_norma}; not cached")
    os.makedirs(cache_dir, exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return data


def parse(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    articles = []
    for el in root.iter():
        if _local(el) == "EstructuraFuncional" and el.attrib.get("tipoParte", "").startswith("Art"):
            text = " ".join(t.text.strip() for t in el if _local(t) == "Texto" and t.text)
            if text:
                articles.append({"name": el.attrib.get("idParte", str(len(articles))), "text": text})
    date = root.attrib.get("fechaVersion", "")
    if not date or date.startswith("2222"):  # BCN sentinel for "no version date" (Civil, Penal)
        date = next((el.attrib["fechaPublicacion"] for el in root.iter()
                     if _local(el) == "Identificador" and "fechaPublicacion" in el.attrib), "")
    return {"title": _first_text(root, "TituloNorma"), "date": date,
            "body_org": _first_text(root, "Organismo"), "articles": articles}


_DEROGADO = re.compile(r"\bderogad[oa]\b", re.I)


def repealed(text: str) -> bool:
    """'Art. 27. Derogado. L. 19.585 ...': a stub with nothing to retrieve (800 articles in the 11 norms, 2026-10-08)."""
    return len(text.split()) <= 20 and bool(_DEROGADO.search(text[:60]))


def prepare(norms_tsv: str, cache_dir: str, out_dir: str) -> dict:
    chunks, docs, seen, dropped, repealed_n = [], [], set(), 0, 0
    for line in open(norms_tsv, encoding="utf-8"):
        if not line.strip():
            continue
        id_norma, short, name = line.rstrip("\n").split("\t")
        norm = parse(fetch(int(id_norma), cache_dir))
        # short name as title (BCN titles read "FIJA EL TEXTO REFUNDIDO..."); official title kept in extra
        docs.append({"doc_id": f"ley#n{id_norma}", "title": name or norm["title"], "score": 0,
                     "extra": f"{norm['title']} | {norm['body_org']} | {norm['date']}"})
        for ai, art in enumerate(norm["articles"]):
            if repealed(art["text"]):
                repealed_n += 1
                continue
            for i, text in enumerate(chunk_words(art["text"])):
                if text in seen:  # exact duplicate text across articles/norms: keep the first
                    dropped += 1
                    continue
                seen.add(text)
                chunks.append({"id": f"ley#{id_norma}-{ai}-{i}", "text": text, "attr": short, "doc_id": f"ley#n{id_norma}"})
    os.makedirs(out_dir, exist_ok=True)
    write_jsonl(os.path.join(out_dir, "chunks.jsonl"), chunks)
    write_jsonl(os.path.join(out_dir, "docs.jsonl"), docs)
    return {"norms": len(docs), "chunks": len(chunks), "dropped_duplicates": dropped, "dropped_repealed": repealed_n}
