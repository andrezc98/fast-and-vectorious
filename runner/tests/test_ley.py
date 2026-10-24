import os

from fv.corpus import ley

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ley_sample.xml")


def test_parse_reads_metadata_and_articles():
    out = ley.parse(open(FIX, "rb").read())
    assert "constituci" in out["title"].lower()  # BCN titles are upper case
    assert out["date"] and out["body_org"]
    assert len(out["articles"]) == 2
    assert out["articles"][0]["text"].strip()


def test_parse_replaces_sentinel_version_date():
    xml = (b'<Norma xmlns="http://www.leychile.cl/esquemas" fechaVersion="2222-02-02">'
           b'<Identificador fechaPublicacion="1874-11-12"><Organismos><Organismo>X</Organismo></Organismos></Identificador></Norma>')
    assert ley.parse(xml)["date"] == "1874-11-12"


def test_fetch_rejects_empty_body_and_does_not_cache(tmp_path, monkeypatch):
    import io
    import urllib.request

    import pytest

    monkeypatch.setattr(ley.time, "sleep", lambda s: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b" \n"))
    with pytest.raises(ValueError, match="123"):
        ley.fetch(123, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_prepare_drops_duplicate_chunk_texts(tmp_path, monkeypatch):
    xml = open(FIX, "rb").read()
    monkeypatch.setattr(ley, "fetch", lambda i, c: xml)
    norms = tmp_path / "norms.tsv"
    norms.write_text("1\tA\tx\n2\tB\tx\n")
    once = ley.prepare(str(norms), "", str(tmp_path / "o1"))
    assert once["dropped_duplicates"] == once["chunks"]  # second norm is an exact copy of the first
    assert once["chunks"] > 0


def test_prepare_titles_docs_with_the_short_name(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(ley, "fetch", lambda i, c: open(FIX, "rb").read())
    norms = tmp_path / "norms.tsv"
    norms.write_text("1\tconstitucion\tConstitución Política de la República\n")
    ley.prepare(str(norms), "", str(tmp_path / "o"))
    doc = json.loads((tmp_path / "o" / "docs.jsonl").read_text().splitlines()[0])
    assert doc["title"] == "Constitución Política de la República"  # readable on the live demo
    assert "constituci" in doc["extra"].lower()  # BCN official title kept in extra


def test_prepare_skips_repealed_articles(tmp_path, monkeypatch):
    arts = [{"name": "1", "text": "Art. 27. DEROGADO"},
            {"name": "2", "text": "ART. 250 bis A. Derogado"},
            {"name": "3", "text": "Art. 1991. Derogado. D.F.L. Nº 178, de 1931, M. Del Trabajo, Art. 174"},
            {"name": "4", "text": "Artículo 5.- El contrato derogado por las partes produce efectos " + "hasta su término legal " * 5}]
    monkeypatch.setattr(ley, "fetch", lambda i, c: b"")
    monkeypatch.setattr(ley, "parse", lambda b: {"title": "T", "date": "d", "body_org": "o", "articles": arts})
    norms = tmp_path / "norms.tsv"
    norms.write_text("1\ta\tA\n")
    out = ley.prepare(str(norms), "", str(tmp_path / "o"))
    assert out["dropped_repealed"] == 3 and out["chunks"] >= 1  # long article mentioning "derogado" stays
