"""Step 2: fill build/stage1.pptx (31 template slides in talk order) with the talk content.

Every number comes from the committed results (results/2026-10-08, results/2026-10-09); nothing is typed by hand
except labels. Run from the repo root:
  uv run --no-project --with python-pptx --with pillow --with qrcode python slides/build/build_deck.py
"""
import csv
import glob
import json
import os
from copy import deepcopy

from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
R8, R9 = os.path.join(ROOT, "results", "2026-10-08"), os.path.join(ROOT, "results", "2026-10-09")
OUT = os.path.join(ROOT, "slides", "Fast-and-Vectorious_SCD-Chile-2026.pptx")
PHOTO = os.path.expanduser("~/Downloads/profilepic.png")
REPO = "https://github.com/andrezc98/fast-and-vectorious"
EVENT_HOST = "scd2026.builtbyandres.com"


def event_url():
    """Live demo URL with the event code (code lives in the git-ignored free-tier/event.tfvars)."""
    import re
    f = os.path.join(ROOT, "free-tier", "event.tfvars")
    m = re.search(r'event_code\s*=\s*"([a-z0-9]+)"', open(f).read()) if os.path.exists(f) else None
    return f"https://{EVENT_HOST}/?code={m.group(1)}" if m else f"https://{EVENT_HOST}/"


def event_code():
    return event_url().partition("code=")[2]

# --- palette (template: violet A433F3, magenta D054FC, ink 434343 on light, white on dark) ---
VIOLET, MAGENTA, INK, WHITE = "A433F3", "D054FC", "434343", "FFFFFF"
MUTED, CARD_LIGHT, CARD_DARK = "6B6B80", "F4F0FC", "2A2160"
STORE = {"aurora": ("Aurora pgvector", "A433F3"), "ddb": ("DynamoDB", "3D5AFE"),
         "aoss": ("OpenSearch", "0E9F9A"), "s3v": ("S3 Vectors", "E8590C")}
ORDER = ["aurora", "ddb", "aoss", "s3v"]
FONT, FONT_X = "DM Sans", "DM Sans ExtraBold"


# --- data ---------------------------------------------------------------------------------------------
def bench(day, store, corpus, case):
    r = [x for x in csv.DictReader(open(os.path.join(day, f"bench-{store}-{corpus}-cohere-{case}.csv"))) if x["ok"] == "True"]
    lat = sorted(float(x["latency_ms"]) for x in r)
    rec = [float(x["recall"]) for x in r if x["recall"] != ""]
    return {"p50": lat[int(.5 * (len(lat) - 1))], "p95": lat[int(.95 * (len(lat) - 1))], "recall": sum(rec) / len(rec)}


W = {(s, c, k): bench(R9, s, c, k) for s in ORDER for c in ("so", "ley") for k in ("plain", "filter", "join")}
EF = {ef: bench(R9, "aurora", "so", f"plain-ef{ef}") for ef in (100, 200)}
EF[40] = W[("aurora", "so", "plain")]
BAKE = {r["model"]: float(r["recall_at_10"]) for r in csv.DictReader(open(os.path.join(R8, "bakeoff.csv")))}
COLD = json.load(open(os.path.join(R9, "cold-start.json")))["stores"]
COST = json.load(open(os.path.join(R9, "costs.json")))
FLOOR = COST["notes"]["always_awake_floor_month_usd"]
PER1K, REST = COST["so"]["per_1k_queries_usd"], COST["so"]["at_rest_month_usd"]
SHORT = {"aurora": "Aurora", "ddb": "DynamoDB", "aoss": "OpenSearch", "s3v": "S3 Vectors"}
REL = json.load(open(os.path.join(R9, "relevance-ley-cohere.json")))


def es(x, nd=1):  # Spanish decimal comma
    return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --- helpers ------------------------------------------------------------------------------------------
def rgb(h):
    return RGBColor.from_string(h)


def set_text(shape, text):
    """Replace the text but keep the first run's formatting (font, size, color) from the template."""
    tf = shape.text_frame
    p0 = tf.paragraphs[0]
    for p in tf.paragraphs[1:]:
        p._p.getparent().remove(p._p)
    for r in p0.runs[1:]:
        r._r.getparent().remove(r._r)
    p0.runs[0].text = text


def set_title(slide, t):
    sh = title_shape(slide)
    content_title = sh.text_frame.text.strip() == "Titulo diapo centrado"  # cover/speaker keep their own box
    set_text(sh, t)
    if content_title:
        sh.left, sh.width = Inches(1.25), Inches(7.0)
        if len(t) > 27:
            sh.text_frame.paragraphs[0].runs[0].font.size = Pt(24)


def title_shape(slide):
    for sh in slide.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip() in ("Titulo diapo centrado", "Título Charla", "Nombre de Speaker"):
            return sh


def clear_body(slide):
    """Drop the template's sample body text and sample picture; keep title, logos and background art."""
    for sh in list(slide.shapes):
        is_ph = sh.shape_type == MSO_SHAPE_TYPE.PLACEHOLDER
        is_sample_pic = sh.shape_type == MSO_SHAPE_TYPE.PICTURE and sh.width > Inches(3) and sh.top > Inches(1)
        if is_ph or is_sample_pic:
            sh._element.getparent().remove(sh._element)


def text(slide, x, y, w, h, runs, size=14, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT, space=4):
    """runs: str, or list of paragraphs; a paragraph is str or list of (text, {opts}) runs."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap, tf.vertical_anchor = True, anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment, p.space_after = align, Pt(space)
        for t, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run()
            r.text = t
            f = r.font
            f.name, f.size, f.bold = o.get("font", font), Pt(o.get("size", size)), o.get("bold", bold)
            f.color.rgb = rgb(o.get("color", color))
    return tb


def card(slide, x, y, w, h, fill=CARD_LIGHT, line=None):
    s = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.adjustments[0] = 0.08
    s.fill.solid()
    s.fill.fore_color.rgb = rgb(fill)
    if line:
        s.line.color.rgb, s.line.width = rgb(line), Pt(1)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    return s


def dot(slide, x, y, d, color):
    s = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    s.fill.solid()
    s.fill.fore_color.rgb = rgb(color)
    s.line.fill.background()
    return s


def style_chart(ch, dark=False, legend=False, fmt='0.0', size=11):
    fg = WHITE if dark else INK
    ch.font.name, ch.font.size = FONT, Pt(size)
    ch.font.color.rgb = rgb(fg)
    ch.has_legend = legend
    if legend:
        ch.legend.position, ch.legend.include_in_layout = XL_LEGEND_POSITION.TOP, False
        ch.legend.font.size, ch.legend.font.name = Pt(10), FONT
    va = ch.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb("DDD6EE" if not dark else "4A3F8A")
    va.format.line.fill.background()
    va.tick_labels.font.size, va.tick_labels.font.color.rgb = Pt(9), rgb(MUTED if not dark else "C9C2E8")
    va.tick_labels.number_format, va.tick_labels.number_format_is_linked = fmt, False
    ca = ch.category_axis
    ca.format.line.color.rgb = rgb("BDB3DA")
    ca.tick_labels.font.size = Pt(10)
    plot = ch.plots[0]
    plot.has_data_labels = True
    dl = plot.data_labels
    dl.number_format, dl.number_format_is_linked = fmt, False
    dl.font.size, dl.font.bold, dl.font.name = Pt(10), True, FONT
    dl.position = XL_LABEL_POSITION.OUTSIDE_END
    return ch


def color_points(series, colors):
    for i, c in enumerate(colors):
        pt = series.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(c)


def bar_chart(slide, x, y, w, h, cats, series, colors=None, horizontal=False, fmt='0.0', legend=False, title=None, gap=60):
    cd = CategoryChartData()
    cd.categories = cats
    for name, vals in series:
        cd.add_series(name, vals)
    kind = XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED
    gf = slide.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), cd)
    ch = style_chart(gf.chart, legend=legend, fmt=fmt)
    ch.plots[0].gap_width = gap
    if title:
        ch.has_title = True
        ch.chart_title.text_frame.text = title
        tr = ch.chart_title.text_frame.paragraphs[0].runs[0].font
        tr.size, tr.bold, tr.name, tr.color.rgb = Pt(11), True, FONT, rgb(INK)
    else:
        ch.has_title = False
    if colors and len(series) == 1:
        color_points(ch.series[0], colors)
    elif colors:
        for s, c in zip(ch.series, colors):
            s.format.fill.solid()
            s.format.fill.fore_color.rgb = rgb(c)
    if horizontal:
        ch.category_axis.reverse_order = True
    return ch


def notes(slide, s):
    slide.notes_slide.notes_text_frame.text = s


def crop_photo(path, aspect, out):
    im = Image.open(path).convert("RGB")
    W_, H_ = im.size
    # face + torso: centered on the speaker (left-middle of the frame)
    ch_ = int(H_ * 0.58)
    cw = int(ch_ * aspect)
    cx, top = int(W_ * 0.40), int(H_ * 0.40)
    left = max(0, min(W_ - cw, cx - cw // 2))
    im.crop((left, top, left + cw, top + ch_)).save(out, quality=92)
    return out


def replace_picture(slide, old, img_path):
    """New picture at the old one's geometry, keeping its shape mask (rounded frame) and z-order."""
    new = slide.shapes.add_picture(img_path, old.left, old.top, old.width, old.height)
    old_geom = old._element.spPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}prstGeom")
    if old_geom is not None:
        sp = new._element.spPr
        g = sp.find("{http://schemas.openxmlformats.org/drawingml/2006/main}prstGeom")
        sp.replace(g, deepcopy(old_geom))
    old._element.addprevious(new._element)
    old._element.getparent().remove(old._element)


def qr(url, out):
    import qrcode
    img = qrcode.make(url, border=1, box_size=12)
    img.save(out)
    return out


def table(slide, x, y, w, rows, col_w, row_h=0.36, size=11, dark=False, head=VIOLET, hi=None):
    """rows[0] is the header. hi: {(r, c): color} to highlight a cell's text."""
    gf = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(row_h * len(rows)))
    tb = gf.table
    for c, cw in enumerate(col_w):
        tb.columns[c].width = Inches(cw)
    for r, row in enumerate(rows):
        tb.rows[r].height = Inches(row_h)
        for c, val in enumerate(row):
            cell = tb.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(head if r == 0 else ((CARD_DARK if r % 2 else "352A75") if dark else (WHITE if r % 2 else CARD_LIGHT)))
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER
            run = p.add_run()
            run.text = str(val)
            f = run.font
            f.name, f.size = FONT, Pt(size)
            f.bold = r == 0 or c == 0
            f.color.rgb = rgb(WHITE if (r == 0 or dark) else (hi or {}).get((r, c), INK))
            if r and (hi or {}).get((r, c)):
                f.bold, f.color.rgb = True, rgb(hi[(r, c)])
    return tb


def pct(x, nd=1):
    return f"{es(x * 100, nd)}%"


def big_number(slide, x, y, w, value, unit, label, color=INK, sub=None, dark=False, size=40):
    text(slide, x, y, w, 0.8, [[(value, {"size": size, "font": FONT_X, "color": color}), (f" {unit}", {"size": 16, "color": MUTED if not dark else "C9C2E8"})]])
    text(slide, x, y + 0.85, w, 0.35, label, size=13, bold=True, color=WHITE if dark else INK)
    if sub:
        text(slide, x, y + 1.2, w, 0.5, sub, size=10, color="C9C2E8" if dark else MUTED)


# --- extra data for v2 ----------------------------------------------------------------------------------
WU = {k: bench(R9, k, "so", "plain-warmup") for k in ORDER}  # first 1,000 queries of the day, before the measured reps


def load_seconds():
    """Wall time of each `fv load` for Stack Overflow 100k (includes waiting for the index to be searchable)."""
    from datetime import datetime
    ts = lambda t: datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
    log = open(os.path.join(R8, "so-pipeline.log")).read()
    out = {}
    for k in ("ddb", "s3v", "aoss"):
        a = re.search(rf"== load --store {k} --corpus so .*?start (\S+)", log).group(1)
        b = re.search(rf"EXIT=0 \[load --store {k} --corpus so .*?end (\S+)", log).group(1)
        out[k] = (ts(b) - ts(a)).total_seconds()
    al = open(os.path.join(R8, "load-so-aurora.log")).read()
    out["aurora"] = (ts(re.search(r"end (\S+)", al).group(1)) - ts(re.search(r"start (\S+)", al).group(1))).total_seconds()
    return out


import math  # noqa: E402
import re  # noqa: E402
LOAD = load_seconds()
ACU_H = float(re.search(r"aurora_acu_hour \| ([\d.]+)", open(os.path.join(ROOT, "results", "prices.md")).read()).group(1))
SO_ACU = float(re.search(r"SO, bench Aurora .*?\| ([\d.]+) \|", open(os.path.join(R8, "capacity.md")).read()).group(1))
AURORA_SO_MONTH = SO_ACU * 730 * ACU_H  # always-awake compute at the 100k bench's average ACU
OOM = re.search(r"to (\d+) bytes", open(os.path.join(R8, "so-pipeline.log")).read()).group(1)


def explain():
    """Aurora EXPLAIN ANALYZE samples: plan nodes, planner row estimate after the filter, total ms."""
    d = json.load(open(os.path.join(R9, "aurora-explain.json")))["plans"]
    out = []
    for k, v in d.items():
        ms = float(re.search(r"actual time=[\d.]+\.\.([\d.]+)", v[0]).group(1))
        rows = [int(re.search(r"rows=(\d+)", l).group(1)) for l in v if "Sort  (" in l]
        out.append({"key": k, "attr": k.partition("attr=")[2], "ms": ms, "rows": rows[0] if rows else None,
                    "hnsw": any("hnsw" in l for l in v)})
    return out


EXPL = explain()
ATTRN = dict(json.load(open(os.path.join(R9, "aurora-explain.json")))["attr_counts"])  # real rows per tag


def cold(k):  # connect/resume + first query, same boundary for every store
    return COLD[k]["connect_s"] * 1000 + COLD[k]["first_three_queries_ms"][0][0]


# --- build --------------------------------------------------------------------------------------------
# Story, planned backwards from the goal ("I can pick a store for my project"): to pick, you need what each store is good
# at; for that, five questions answered with measurements; to read them, p50/p95 and recall; to measure, a model and data;
# to care, the hook and the four options. Every slide title is the question a student would ask at that step.
# Side threads live in the speaker notes, not on slides.
prs = Presentation(os.path.join(HERE, "stage1.pptx"))
S = list(prs.slides)
assert len(S) == 32, len(S)
SO = {s: W[(s, "so", "plain")] for s in ORDER}
F = {k: W[(k, "so", "filter")] for k in ORDER}
J = {k: W[(k, "so", "join")] for k in ORDER}
L = {k: W[(k, "ley", "plain")] for k in ORDER}
LF = {k: W[(k, "ley", "filter")] for k in ORDER}
LJ = {k: W[(k, "ley", "join")] for k in ORDER}
A_WARM, A_COLD = SO["aurora"]["p50"], cold("aurora") / 1000
QS = ["¿Cuál responde más rápido?", "¿Y si filtro por categoría?", "¿Y si necesito datos de otra tabla?",
      "¿Qué pasa después de una pausa?", "¿Cuánto cuesta?"]
n = iter(S)
BODY = 14


def chart_card(slide, x=0.7, y=1.3, w=4.4, h=2.85):
    card(slide, x, y, w, h, fill=WHITE, line="DDD6EE")


def right_col(slide, paras, size=13, space=8, color=INK):
    """Takeaway column on its own card, so it never sits on the skyline art."""
    card(slide, 5.25, 1.3, 3.95, 2.85, fill=CARD_LIGHT)
    text(slide, 5.45, 1.42, 3.6, 2.65, paras, size=size, space=space, color=color)


def head(t, color=VIOLET):
    return [(t, {"bold": True, "color": color, "size": 17})]


# 1 cover ------------------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Fast & Vectorious")
for sh in s.shapes:
    if sh.has_text_frame and sh.text_frame.text.strip() == "Subtitulo Charla":
        set_text(sh, "DynamoDB, S3, OpenSearch y Aurora con los mismos vectores, medidos")
text(s, 0.79, 3.5, 4.6, 0.3, [[("Andrés Zeballos", {"bold": True}), ("  ·  Auditorio, Torre 1  ·  12:15", {})]], size=12, color=WHITE)
notes(s, "[0:00–0:30] Bienvenida. Pregunta a la sala, a mano alzada: ¿quién ha armado un buscador con IA o un RAG? "
         "¿Y quién lo tuvo que mostrar en vivo?")

# 2 hook -------------------------------------------------------------------------------------------------
s = next(n)
set_title(s, f"La misma base: {es(A_WARM)} ms o {es(A_COLD, 0)} s")
clear_body(s)
for i, (val, unit, lab, sub) in enumerate([
        (es(A_WARM), "ms", "Con la base encendida", "Mediana de 3.000 consultas"),
        (es(A_COLD), "s", "Después de 16 minutos sin uso", "Se apagó sola y tuvo que volver a encenderse")]):
    x = 0.95 + i * 4.25
    card(s, x, 1.35, 3.85, 2.2, fill=CARD_DARK)
    big_number(s, x + 0.3, 1.5, 3.4, val, unit, lab, color=WHITE if i == 0 else MAGENTA, sub=sub, dark=True, size=48)
text(s, 0.95, 3.68, 8.1, 0.3, "¿Cuál de los dos le toca a tu usuario?", size=16, bold=True, color="E6DFFF", align=PP_ALIGN.CENTER)
notes(s, "[0:30–1:30] Imagina que mañana presentas tu proyecto. Armaste un buscador, funciona y en el ensayo responde al instante. "
         f"Lo dejas sin uso un rato y, cuando empieza la presentación, haces la primera pregunta. En mi laboratorio, Aurora respondió en "
         f"{es(A_WARM)} ms mientras estaba encendida. Después de 16 minutos sin uso, la primera respuesta tardó {es(A_COLD)} segundos. "
         "Misma base, mismos datos. Hoy vamos a ver por qué pasa esto y cómo elegir. "
         "Fuentes: results/2026-10-09/bench-aurora-so-cohere-plain.csv; cold-start.json (12,52 s en reanudar y conectar + 1,46 s la primera consulta). "
         "Es una sola observación; la documentación de Aurora indica unos 15 s para reanudar, así que es lo esperable.")

# 3 speaker ----------------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Andrés Zeballos")
pic = next(sh for sh in s.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE and sh.width > Inches(2))
replace_picture(s, pic, crop_photo(PHOTO, pic.width / pic.height, os.path.join(HERE, "photo.jpg")))
body = next(sh for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.startswith("Introducir"))
body._element.getparent().remove(body._element)
text(s, 4.79, 1.66, 4.1, 2.3, [
    [("Solutions Architect en phData", {"bold": True})],
    "AWS Community Builder · AWS Golden Jacket",
    "Apasionado por los datos, DevOps e IA",
    "",
    [("Fuera del trabajo", {"bold": True, "color": "E6DFFF"})],
    "Instructor de ski",
    "Tiro con arco compuesto",
], size=13, color=WHITE)

# 4 the question + promises ------------------------------------------------------------------------------
s = next(n)
set_title(s, "¿Dónde guardo mis vectores?")
clear_body(s)
text(s, 0.75, 1.1, 8.5, 0.25, "Cuatro servicios de AWS ya guardan vectores. Al final vas a poder elegir uno para tu proyecto.",
     size=13, color="E6DFFF", align=PP_ALIGN.CENTER)
homes = [("ddb", "DynamoDB", "«Milisegundos de un dígito y más de 99% de recall»", "Desde agosto 2026"),
         ("s3v", "S3 Vectors", "«Unos 100 ms o menos en consultas frecuentes»", "Desde diciembre 2025"),
         ("aoss", "OpenSearch Serverless", "«Escala a cero y pagas solo lo que usas»", "Desde mayo 2026"),
         ("aurora", "Aurora PostgreSQL", "«pgvector filtra con WHERE y une tablas con JOIN»", "pgvector 0.8")]
for i, (k, name, promise, when) in enumerate(homes):
    x = 0.75 + i * 2.17
    card(s, x, 1.5, 2.0, 2.2, fill=CARD_DARK)
    dot(s, x + 0.18, 1.65, 0.28, STORE[k][1])
    text(s, x + 0.18, 1.95, 1.7, 0.53, name, size=13, color=WHITE, bold=True, anchor=MSO_ANCHOR.BOTTOM)
    text(s, x + 0.18, 2.52, 1.7, 0.8, promise, size=12, color="DCD3FA")
    text(s, x + 0.18, 3.4, 1.7, 0.2, when, size=9, color=MAGENTA, bold=True)
notes(s, "[2:00–3:30] Esta es la meta de hoy: que salgas sabiendo dónde guardar los vectores de tu proyecto. Hace un par de años la "
         "respuesta era 'instala una base vectorial'. Hoy cuatro servicios que probablemente ya usas guardan vectores, y cada anuncio trae "
         "una promesa. Para elegir, primero hay que entender qué es buscar por significado, después medir y al final comparar. "
         "No vengo a desmentir a nadie: vengo a medir. Al final volvemos a estas promesas. "
         "Citas traducidas de los anuncios What's New, revisadas el 13 oct 2026: "
         "https://aws.amazon.com/about-aws/whats-new/2026/08/amazon-dynamodb-vector-search/ · "
         "https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-s3-vectors-generally-available/ · "
         "https://aws.amazon.com/about-aws/whats-new/2026/05/amazon-opensearch-serverless-next-generation-generally-available/ · "
         "https://aws.amazon.com/about-aws/whats-new/2025/04/pgvector-0-8-0-aurora-postgresql/ (en mi clúster: pgvector 0.8.2).")

# 5 vector search in one minute --------------------------------------------------------------------------
s = next(n)
set_title(s, "Buscar por significado")
clear_body(s)
card(s, 0.7, 1.35, 4.3, 2.75, fill=WHITE, line="DDD6EE")
pts = [(1.0, 1.65, "¿Cómo leo un CSV con pandas?", VIOLET), (1.3, 2.05, "Leer un archivo CSV en Python", VIOLET),
       (2.55, 2.7, "¿Qué es un índice en SQL?", "9A93B5"), (1.6, 3.3, "Error de permisos en Linux", "9A93B5")]
for x, y, lab, c in pts:
    dot(s, x, y, 0.16, c)
    text(s, x + 0.22, y - 0.03, 2.6, 0.25, lab, size=10, color=INK if c == VIOLET else MUTED)
text(s, 0.85, 3.78, 4.0, 0.25, "Cerca = significado parecido, con otras palabras", size=10, color=VIOLET, bold=True)
right_col(s, [
    [("1.", {"bold": True}), (" Un modelo convierte cada texto en 1.024 números: un vector", {})],
    [("2.", {"bold": True}), (" Textos con significado parecido quedan cerca", {})],
    [("3.", {"bold": True}), (" Buscar es traer los 10 vectores más cercanos a tu pregunta", {})],
    [("Las cuatro bases hacen esto. Cambia qué tan rápido, qué tan bien y a qué precio.", {"color": VIOLET, "bold": True})],
])
notes(s, "[3:30–4:30] Sin matemáticas: puntos en un mapa. Las dos preguntas sobre CSV usan palabras distintas y quedan juntas. "
         "Para medir la cercanía uso la distancia coseno.")

# 6 the engine: model bake-off ---------------------------------------------------------------------------
s = next(n)
set_title(s, "Antes de la base, el modelo")
clear_body(s)
chart_card(s)
ch = bar_chart(s, 0.8, 1.35, 4.2, 2.75, ["Cohere Embed v4", "Qwen3-Embedding-8B", "Nova 2"],
               [("acierto", [BAKE["cohere"], BAKE["qwen"], BAKE["nova"]])],
               colors=[VIOLET, "8F7FC2", "B9B1D6"], horizontal=True, fmt='0.0%',
               title="Respuesta correcta entre los 10 primeros · 2.000 preguntas · eje desde 85%", gap=50)
ch.value_axis.minimum_scale, ch.value_axis.maximum_scale = 0.85, 1.0
right_col(s, [
    head(f"Cohere Embed v4: {pct(BAKE['cohere'])}"),
    "Encontró la respuesta correcta entre los 10 primeros. Es el que uso en todo lo que sigue",
    [("Qwen", {"bold": True}), (f", de código abierto, casi empata ({pct(BAKE['qwen'])}), pero necesitas tu propia GPU", {})],
    [("Si el modelo no entiende la pregunta, ninguna base lo arregla", {"color": VIOLET, "bold": True})],
], space=8)
notes(s, "[4:30–5:30] Respuesta correcta = la respuesta aceptada en Stack Overflow (results/2026-10-08/bakeoff.csv). Nova 2: "
         f"{pct(BAKE['nova'])}. Eje recortado desde 85%: entre Cohere y Qwen hay 0,6 puntos. Dato de respaldo para preguntas: con las leyes "
         f"chilenas, el artículo del que salió cada pregunta aparece entre los 3 primeros resultados {pct(REL['article@3'], 0)} de las veces "
         "(relevance-ley-cohere.json, búsqueda exacta). Por eso la demo muestra 3 resultados.")

# 7 the track: two collections ---------------------------------------------------------------------------
s = next(n)
set_title(s, "Con qué datos medí")
clear_body(s)
for col, (num, what, det) in enumerate([
        ("100.000", "fragmentos de Stack Overflow en español", "69.402 preguntas reales · abril 2024"),
        ("8.851", "fragmentos de leyes chilenas", "Constitución y 10 códigos · Biblioteca del Congreso Nacional")]):
    x = 0.85 + col * 4.25
    card(s, x, 1.35, 3.95, 2.25)
    text(s, x + 0.25, 1.45, 3.5, 0.75, num, size=40, color=VIOLET, bold=True, font=FONT_X)
    text(s, x + 0.25, 2.25, 3.5, 0.65, what, size=15, bold=True)
    text(s, x + 0.25, 2.95, 3.5, 0.6, det, size=12, color=MUTED)
text(s, 0.85, 3.7, 4.4, 0.3, "1.000 preguntas, las mismas para las cuatro bases", size=12, bold=True)
notes(s, "[5:30–6:15] Stack Overflow es la colección grande: la de 100.000 vectores. Las leyes chilenas son más chicas y más cercanas; son "
         "las de la demo. Las preguntas de Stack Overflow son títulos reales; las de leyes las generé con Claude Haiku 5.5 a partir de "
         "cada artículo. Licencia Stack Overflow: CC BY-SA.")

# 8 rules of the race ------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Cómo medí")
clear_body(s)
chart_card(s)
card(s, 0.95, 2.3, 1.25, 0.85, fill=CARD_LIGHT, line="BDB3DA")
text(s, 0.95, 2.42, 1.25, 0.6, [[("EC2", {"bold": True})], "misma región"], size=10, align=PP_ALIGN.CENTER)
for i, k in enumerate(ORDER):
    y = 1.45 + i * 0.66
    card(s, 3.0, y, 1.9, 0.5, fill=STORE[k][1])
    text(s, 3.0, y + 0.13, 1.9, 0.25, STORE[k][0], size=11, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
    ln = s.shapes.add_connector(1, Inches(2.2), Inches(2.72), Inches(3.0), Inches(y + 0.25))
    ln.line.color.rgb, ln.line.width = rgb("BDB3DA"), Pt(1.25)
right_col(s, [
    "Los mismos vectores en las cuatro",
    "Cada pregunta pide los 10 más cercanos",
    "1.000 preguntas, 3 veces, de una en una",
    "Antes de medir, 1.000 consultas de calentamiento",
    "El tiempo se mide desde una máquina en la misma región",
], space=8)
notes(s, "[6:15–7:15] Mido lo que siente un usuario, no cuánto aguanta la base con muchos usuarios a la vez. El tiempo incluye la red y "
         "el SDK, igual que en tu aplicación. Configuración de cada índice, por si preguntan: Aurora HNSW con m = 16, ef_construction = 64, "
         "ef_search = 40 (runner/fv/stores/aurora.py); DynamoDB índice vectorial con coseno y la etiqueta como filtro (dynamo.py); OpenSearch "
         "knn_vector con coseno y compresión 1x, el valor por defecto es 32x (aoss.py); S3 Vectors con coseno y filtro en modo ENHANCED (s3v.py). "
         "Precios: Price List API de AWS, us-east-1, 8 oct 2026. Código y CSV en el repo.")

# 9 how to read times ------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Cómo leer los tiempos: p50 y p95")
clear_body(s)
chart_card(s)
lat = sorted(float(x["latency_ms"]) for x in csv.DictReader(open(os.path.join(R9, "bench-aoss-so-cohere-plain.csv"))) if x["ok"] == "True")
qs = [lat[int(q / 100 * (len(lat) - 1))] for q in range(5, 100, 5)]
ch = bar_chart(s, 0.8, 1.35, 4.2, 2.75, [f"{q}" for q in range(5, 100, 5)], [("ms", qs)],
               colors=[MAGENTA if q in (50, 95) else "D3C4F5" for q in range(5, 100, 5)], fmt='0', gap=30,
               title="Milisegundos de 3.000 consultas reales, de la más rápida a la más lenta")
ch.plots[0].has_data_labels = False
right_col(s, [
    "Ordena las consultas de la más rápida a la más lenta",
    [("p50", {"bold": True, "color": MAGENTA}), (f": la del medio. Lo normal: {es(SO['aoss']['p50'])} ms", {})],
    [("p95", {"bold": True, "color": MAGENTA}), (f": 95 de cada 100 son más rápidas. Un mal día: {es(SO['aoss']['p95'])} ms", {})],
    [("El promedio esconde las consultas lentas; el p95, no", {"color": VIOLET, "bold": True})],
])
notes(s, "[7:15–8:15] Gráfico con datos reales de OpenSearch (bench-aoss-so-cohere-plain.csv): una barra cada 5 percentiles; en magenta el "
         "p50 y el p95. Si tu aplicación hace varias búsquedas por respuesta, el mal día llega más seguido.")

# 10 how to read accuracy --------------------------------------------------------------------------------
s = next(n)
set_title(s, "Cómo leer el acierto: recall")
clear_body(s)
card(s, 0.85, 1.35, 3.95, 2.75)
text(s, 1.1, 1.5, 3.5, 0.4, "Recall@10", size=18, color=VIOLET, bold=True)
text(s, 1.1, 1.95, 3.5, 0.9, "La búsqueda exacta compara tu pregunta con todos los vectores: es lenta, pero no se equivoca. Los índices toman atajos para ir más rápido.", size=12)
for i in range(10):  # 9 of 10 found
    sq = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.1 + i * 0.33), Inches(3.05), Inches(0.26), Inches(0.26))
    sq.fill.solid()
    sq.fill.fore_color.rgb = rgb(VIOLET if i < 9 else "D9D2EA")
    sq.line.fill.background()
text(s, 1.1, 3.45, 3.5, 0.45, "Encontró 9 de los 10 = 90% de recall", size=12, color=MUTED, bold=True)
card(s, 5.1, 1.35, 4.05, 2.75, fill=WHITE, line="DDD6EE")
text(s, 5.35, 1.5, 3.6, 2.5, [
    head("Recall no es relevancia", MAGENTA),
    [("Recall", {"bold": True}), (": ¿el atajo encontró lo mismo que la búsqueda exacta?", {})],
    [("Relevancia", {"bold": True}), (": ¿lo que volvió responde tu pregunta? Eso depende del modelo, no de la base", {})],
], size=13, space=9)
notes(s, "[8:15–9:15] En cada medición comparo contra la búsqueda exacta sobre los mismos vectores: así mido el atajo de cada índice. "
         "La relevancia ya la vimos con el modelo.")

# 11 map of the race -------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Cinco preguntas antes de elegir")
clear_body(s)
laps = ["Con la base ya encendida", "Solo resultados de una etiqueta", "Título y puntaje de la pregunta original",
        "La primera consulta tras 16 minutos sin uso", "Por búsqueda y por mes"]
for i, (name, sub) in enumerate(zip(QS, laps)):
    y = 1.3 + i * 0.52
    card(s, 0.85, y, 8.3, 0.44)
    text(s, 1.05, y + 0.06, 0.4, 0.3, str(i + 1), size=16, bold=True, color=VIOLET, font=FONT_X)
    text(s, 1.5, y + 0.09, 3.6, 0.3, name, size=14, bold=True)
    text(s, 5.2, y + 0.11, 3.9, 0.3, sub, size=12, color=MUTED)
notes(s, "[9:15–9:45] Para elegir, tu proyecto tiene que responder estas cinco preguntas. Voy una por una, con lo que medí, "
         "y cada respuesta termina en 'si necesitas esto, usa esto'. Después lo prueban en su celular. Guarden el celular hasta entonces.")

# 12 lap 1: speed ----------------------------------------------------------------------------------------
s = next(n)
set_title(s, QS[0])
clear_body(s)
chart_card(s)
bar_chart(s, 0.8, 1.35, 4.2, 2.75, [SHORT[k] for k in ORDER],
          [("p50", [SO[k]["p50"] for k in ORDER]), ("p95", [SO[k]["p95"] for k in ORDER])],
          colors=[VIOLET, "D3C4F5"], fmt='0.0', legend=True, title="Milisegundos · Stack Overflow, 100.000 vectores", gap=40)
right_col(s, [
    head(f"Aurora responde en {es(SO['aurora']['p50'])} ms"),
    [("DynamoDB", {"bold": True, "color": STORE["ddb"][1]}), (f": {es(SO['ddb']['p50'])} ms, y {es(SO['ddb']['p95'])} en p95", {})],
    [("OpenSearch", {"bold": True, "color": STORE["aoss"][1]}), (f": {es(SO['aoss']['p50'])} ms", {})],
    [("S3 Vectors", {"bold": True, "color": STORE["s3v"][1]}), (f": {es(SO['s3v']['p50'])} ms, bajo los 100 ms que promete", {})],
    [("Con la base ya encendida y la caché lista", {"size": 11, "color": MUTED})],
], space=8)
notes(s, "[9:45–11:00] Gana Aurora, mientras está encendida. Dos razones: el índice está en memoria y el cliente mantiene una conexión "
         "abierta a Postgres, mientras las otras tres hacen una llamada HTTPS firmada en cada consulta. Para un RAG, el modelo de lenguaje "
         "tarda segundos: aquí todas son rápidas. La diferencia importa si haces muchas búsquedas por respuesta. En la demo del celular "
         "Aurora va por la Data API (una llamada HTTPS, como las demás) y sigue ganando: lo que pesa es el índice en memoria. Fuente: results/2026-10-09.")

# 13 lap 1b: accuracy ------------------------------------------------------------------------------------
s = next(n)
set_title(s, "¿Y encuentra lo correcto?")
clear_body(s)
chart_card(s)
cats = ["Aurora ef=40", "Aurora ef=100", "Aurora ef=200", "DynamoDB", "OpenSearch", "S3 Vectors"]
vals = [EF[40]["recall"], EF[100]["recall"], EF[200]["recall"], SO["ddb"]["recall"], SO["aoss"]["recall"], SO["s3v"]["recall"]]
ch = bar_chart(s, 0.8, 1.35, 4.2, 2.75, cats, [("recall@10", vals)],
               colors=["C9B3F7", "B38CF5", VIOLET, STORE["ddb"][1], STORE["aoss"][1], STORE["s3v"][1]],
               horizontal=True, fmt='0.0%', title="Recall@10 · Stack Overflow · eje desde 90%", gap=45)
ch.value_axis.minimum_scale, ch.value_axis.maximum_scale = 0.9, 1.0
ch.value_axis.tick_labels.number_format = '0%'
right_col(s, [
    head("Rápido no basta: también tiene que encontrar"),
    [("Aurora", {"bold": True}), (f", como viene: {pct(EF[40]['recall'])}", {})],
    [("Subiendo un parámetro (ef_search)", {"bold": True}), (f": {pct(EF[200]['recall'])}, y pasa de {es(EF[40]['p50'])} a {es(EF[200]['p50'])} ms", {})],
    [("DynamoDB", {"bold": True}), (f": {pct(SO['ddb']['recall'])} sin tocar nada", {})],
    [("Compara tiempos con el mismo nivel de acierto", {"color": VIOLET, "bold": True})],
], space=8)
notes(s, f"[11:00–12:15] ef_search = cuántos candidatos revisa Aurora antes de responder. 40 / 100 / 200: recall {pct(EF[40]['recall'])} / "
         f"{pct(EF[100]['recall'])} / {pct(EF[200]['recall'])}, p50 {es(EF[40]['p50'])} / {es(EF[100]['p50'])} / {es(EF[200]['p50'])} ms. "
         f"Aun con 200, Aurora sigue siendo la más rápida. Ojo: la medición con 100 tuvo un p95 irregular ({es(EF[100]['p95'])} ms) que no "
         "atribuyo al parámetro.")

# 14 lap 2: filter ---------------------------------------------------------------------------------------
s = next(n)
set_title(s, QS[1])
clear_body(s)
chart_card(s)
bar_chart(s, 0.8, 1.35, 4.2, 2.75, [SHORT[k] for k in ORDER],
          [("sin filtro", [SO[k]["p50"] for k in ORDER]), ("con filtro", [F[k]["p50"] for k in ORDER])],
          colors=["D3C4F5", VIOLET], legend=True, fmt='0.0', title="Milisegundos (p50) · Stack Overflow", gap=40)
right_col(s, [
    head("Con filtro, cambia quién gana"),
    "Ejemplo: los 10 más cercanos, pero solo con la etiqueta javascript",
    [("OpenSearch", {"bold": True}), (f": {pct(F['aoss']['recall'], 2)} de acierto en {es(F['aoss']['p50'])} ms", {})],
    [("DynamoDB", {"bold": True}), (f": el más rápido ({es(F['ddb']['p50'])} ms), pero acierta {pct(F['ddb']['recall'])}", {})],
    [("Aurora", {"bold": True}), (f": acierta 100%, pero pasa de {es(SO['aurora']['p50'])} a {es(F['aurora']['p50'])} ms", {})],
], size=13, space=7)
notes(s, "[12:15–13:30] Este es el caso que prometía el resumen: con filtro, cambia quién gana. Gana OpenSearch: casi todo el acierto y "
         f"buen tiempo. S3 Vectors en modo ENHANCED: {pct(F['s3v']['recall'])} en {es(F['s3v']['p50'])} ms. DynamoDB pierde 1 de cada 10 "
         "resultados al filtrar en esta colección. ¿Y por qué Aurora se volvió 35 veces más lenta? Siguiente lámina.")

# 15 lap 2b: why Aurora slowed down ----------------------------------------------------------------------
s = next(n)
set_title(s, "¿Por qué Aurora se volvió lenta?")
clear_body(s)
filt = sorted([e for e in EXPL if e["attr"]], key=lambda e: -ATTRN[e["attr"]])
plain_ms = [e["ms"] for e in EXPL if not e["attr"]]
card(s, 0.85, 1.35, 3.4, 2.75, fill=WHITE, line="DDD6EE")
text(s, 1.05, 1.47, 3.0, 2.55, [
    [("Sin filtro", {"bold": True, "color": VIOLET, "size": 14})],
    f"Postgres usa el índice vectorial: menos de {es(max(plain_ms), 0)} ms",
    [("Con filtro", {"bold": True, "color": MAGENTA, "size": 14})],
    "Postgres prefiere el índice normal de la etiqueta y compara una por una todas las filas que pasan",
    [("Acierta todo, pero tarda según cuántas filas pasen", {"bold": True, "color": VIOLET})],
], size=12, space=7)
table(s, 4.5, 1.35, 4.65, [["Etiqueta", "Filas que pasan", "Tiempo"]] +
      [[e["attr"], es(ATTRN[e["attr"]], 0), f"{es(e['ms'])} ms"] for e in filt],
      col_w=[1.45, 1.9, 1.3], row_h=0.45, size=13, hi={(1, 2): MAGENTA})
text(s, 4.5, 3.25, 2.7, 0.8, "Yo creé ese índice en la etiqueta. Sin él, Postgres habría seguido otro camino que no medí.",
     size=11, color=MUTED)
notes(s, "[13:30–14:45] EXPLAIN ANALYZE de 3 preguntas, con y sin filtro (results/2026-10-09/aurora-explain.json). Sin filtro: Index Scan "
         "sobre el índice HNSW. Con filtro: Bitmap Index Scan sobre el índice de la etiqueta, luego Sort: es búsqueda exacta, por eso acierta 100%. "
         "Filas = conteo real por etiqueta (attr_counts). Tenía activado hnsw.iterative_scan; el optimizador igual eligió el B-tree, así que "
         "la mejora de pgvector 0.8 para filtros quedó sin probar. Lección: revisa EXPLAIN con tus etiquetas reales.")

# 16 lap 3: related data ---------------------------------------------------------------------------------
s = next(n)
set_title(s, QS[2])
clear_body(s)
card(s, 0.85, 1.35, 3.6, 2.75)
text(s, 1.1, 1.5, 3.15, 2.55, [
    head("Con un JOIN, Aurora casi no suma tiempo"),
    "Para cada resultado, traer el título y el puntaje de la pregunta original",
    [("Aurora", {"bold": True}), (f": un solo SQL con JOIN, +{es(J['aurora']['p50'] - SO['aurora']['p50'])} ms", {})],
    [("Las otras", {"bold": True}), (": buscar y después leer el título en otra tabla, ~5 ms más", {})],
], size=13, space=8)
chart_card(s, 4.65, 1.3, 4.5, 2.85)
bar_chart(s, 4.75, 1.35, 4.3, 2.75, [SHORT[k] for k in ORDER],
          [("solo búsqueda", [SO[k]["p50"] for k in ORDER]), ("con datos relacionados", [J[k]["p50"] for k in ORDER])],
          colors=["D3C4F5", VIOLET], legend=True, fmt='0.0', title="Milisegundos (p50) · Stack Overflow", gap=40)
notes(s, "[14:45–16:00] Gana Aurora. Es el 'join' del resumen. Ojo con la conclusión: esto mide mi arquitectura, con el título en "
         "otra tabla. Si guardas el título junto al vector, esa segunda lectura desaparece en las otras tres. "
         f"DynamoDB {es(SO['ddb']['p50'])}→{es(J['ddb']['p50'])} ms, OpenSearch {es(SO['aoss']['p50'])}→{es(J['aoss']['p50'])}, "
         f"S3 Vectors {es(SO['s3v']['p50'])}→{es(J['s3v']['p50'])}.")

# 17 lap 4: cold start -----------------------------------------------------------------------------------
s = next(n)
set_title(s, QS[3])
clear_body(s)
cl = [("aoss", cold("aoss") / 1000, "s"), ("aurora", cold("aurora") / 1000, "s"), ("ddb", cold("ddb"), "ms"), ("s3v", cold("s3v"), "ms")]
text(s, 0.85, 1.25, 8.3, 0.3, "La primera consulta después de 16 minutos sin uso", size=14, bold=True, color=VIOLET)
for i, (k, val, unit) in enumerate(cl):
    x = 0.85 + i * 2.1
    card(s, x, 1.65, 1.9, 1.75, fill=WHITE, line="DDD6EE")
    dot(s, x + 0.2, 1.82, 0.22, STORE[k][1])
    text(s, x + 0.2, 2.15, 1.6, 0.7, [[(es(val, 1 if unit == "s" else 0), {"size": 32, "font": FONT_X, "color": MAGENTA if unit == "s" else INK}),
                                      (f" {unit}", {"size": 14, "color": MUTED})]])
    text(s, x + 0.2, 2.95, 1.6, 0.4, SHORT[k], size=13, bold=True)
text(s, 0.85, 3.5, 6.0, 0.6, "Aurora y OpenSearch se apagan solas para no cobrar. DynamoDB y S3 Vectors no tienen nada que apagar.",
     size=12)
notes(s, "[16:00–17:15] Ganan DynamoDB y S3 Vectors: prácticamente empatados. Y aquí vuelve la lámina del principio: la más rápida "
         "encendida es de las más lentas en arrancar. Una medición por base (results/2026-10-09/cold-start.json): conexión + primera consulta. "
         f"Aurora: {es(COLD['aurora']['connect_s'])} s en reanudar y conectar + {es(COLD['aurora']['first_three_queries_ms'][0][0] / 1000)} s "
         "la primera consulta. Aurora estaba en 0 ACU antes de la prueba (cold.out). Para OpenSearch no guardé la métrica de capacidad: los "
         "23 s indican que estaba en cero. Su documentación habla de unos 10 s; yo vi 23 s, una vez. Consejo práctico: si tu demo es a las "
         "12:15, haz una consulta a las 12:10.")

# 18 lap 4b: warm-up -------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Recién encendida, todavía no está lista")
clear_body(s)
text(s, 0.85, 1.25, 8.3, 0.3, "Ya encendida, Aurora todavía necesita cargar el índice en memoria", size=14, bold=True, color=VIOLET)
for i, k in enumerate(ORDER):
    x = 0.75 + i * 2.17
    card(s, x, 1.65, 2.0, 1.9, fill=WHITE, line="DDD6EE")
    dot(s, x + 0.18, 1.8, 0.22, STORE[k][1])
    text(s, x + 0.48, 1.77, 1.5, 0.3, SHORT[k], size=13, bold=True)
    text(s, x + 0.18, 2.15, 1.7, 0.25, "p95 al comenzar", size=10, color=MUTED)
    text(s, x + 0.18, 2.35, 1.7, 0.45, [[(es(WU[k]["p95"]), {"size": 22, "font": FONT_X, "color": MAGENTA if k == "aurora" else INK}), (" ms", {"size": 11, "color": MUTED})]])
    text(s, x + 0.18, 2.85, 1.7, 0.25, "p95 después", size=10, color=MUTED)
    text(s, x + 0.18, 3.05, 1.7, 0.45, [[(es(SO[k]["p95"]), {"size": 22, "font": FONT_X, "color": VIOLET}), (" ms", {"size": 11, "color": MUTED})]])
text(s, 0.75, 3.68, 4.3, 0.3, "Las otras tres casi no notan la diferencia", size=12, bold=True)
notes(s, "[17:15–18:00] Primeras 1.000 consultas de la sesión (archivos *-plain-warmup.csv) frente a las 3.000 que medí después. "
         f"Aurora: p95 de {es(WU['aurora']['p95'])} ms a {es(SO['aurora']['p95'])} ms. Si tu base se reinicia o cambia de tamaño, tus primeros "
         "usuarios pagan ese calentamiento. Por eso mido siempre después de calentar y el arranque en frío lo muestro aparte.")

# 19 lap 5: cost per search ------------------------------------------------------------------------------
s = next(n)
set_title(s, "¿Cuánto cuesta cada búsqueda?")
clear_body(s)
chart_card(s)
order_c = sorted(ORDER, key=PER1K.get)
bar_chart(s, 0.8, 1.35, 4.2, 2.75, [SHORT[k] for k in order_c], [("USD", [PER1K[k] * 1000 for k in order_c])],
          colors=[STORE[k][1] for k in order_c], fmt='"$"0.00', title="USD por millón de búsquedas · Stack Overflow", gap=50)
right_col(s, [
    head("DynamoDB cobra 20 a 30 veces menos"),
    [("DynamoDB", {"bold": True}), (f": USD {es(PER1K['ddb'] * 1000, 2)} por millón de búsquedas", {})],
    [("Las otras", {"bold": True}), (": entre USD 2,6 y 4,0 por millón", {})],
    f"Guardar los 100.000 vectores: entre {es(min(REST.values()) * 100, 0)} y {es(max(REST.values()) * 100, 0)} centavos de dólar al mes",
    [("Aurora y OpenSearch: capacidad medida dividida por consultas, con un solo usuario. No es precio de lista.", {"size": 10, "color": MUTED})],
], size=13, space=6)
notes(s, "[18:00–19:15] Gana DynamoDB. Por mil búsquedas, divide por mil: menos de medio centavo en todas. results/2026-10-09/costs.json. "
         "DynamoDB cobra por bytes revisados; S3 Vectors por solicitud y datos procesados; para Aurora y OpenSearch dividí la capacidad que "
         "usaron durante la medición por las consultas. Almacenamiento (so.at_rest_month_usd): entre 0,4 y 1,3 GB según la base; Aurora "
         "incluye el índice HNSW; el de S3 Vectors es estimado. Precio de lista, antes de la capa gratuita.")

# 20 lap 5b: cost of staying on --------------------------------------------------------------------------
s = next(n)
set_title(s, "¿Cuánto cuesta tenerla lista?")
clear_body(s)
chart_card(s)
bar_chart(s, 0.8, 1.35, 4.2, 2.75, [SHORT[k] for k in ORDER],
          [("USD/mes", [FLOOR["aurora"], 0, FLOOR["opensearch"], 0])],
          colors=[STORE[k][1] for k in ORDER], fmt='"$"0', title="USD al mes si nunca se apaga · capacidad mínima medida", gap=50)
right_col(s, [
    head("Lo caro no es buscar: es estar encendida"),
    "Aurora y OpenSearch se apagan tras ~10 minutos sin uso. Con una consulta cada pocos minutos, no se apagan nunca",
    [(f"Aurora: ~USD {FLOOR['aurora']:.0f} a {AURORA_SO_MONTH:.0f} al mes · OpenSearch: ~USD {FLOOR['opensearch']:.0f}", {"bold": True})],
    [("DynamoDB y S3 Vectors: sin mínimo mensual", {"color": VIOLET, "bold": True})],
], size=13, space=6)
notes(s, "[19:15–20:30] Ganan DynamoDB y S3 Vectors. costs.json notes.always_awake_floor_month_usd: capacidad mínima observada "
         "(0,67 ACU; 1 SearchOCU) × 730 h × precio de lista (ACU-hora USD 0,12; OCU-hora USD 0,24; results/prices.md). Con 100.000 vectores "
         f"Aurora promedió {es(SO_ACU, 2)} ACU (capacity.md): ~USD {AURORA_SO_MONTH:.0f} al mes. Es una extrapolación de lo observado, no un "
         "mínimo garantizado. En Aurora, los 10 minutos los configuré yo (seconds_until_auto_pause = 600). Este es el precio de evitar la espera "
         "después de una pausa.")

# 20b what if it grows: estimated from the measured 100k bench, not measured ----------------------------
s = next(n)
set_title(s, "¿Y si crece a 1 o 100 millones?")
clear_body(s)
P = {k: float(v) for k, v in re.findall(r"^\| (\w+) \| ([\d.]+) \|", open(os.path.join(ROOT, "results", "prices.md")).read(), re.M)}
QM, H = 1_000_000, 730  # assumption: a million searches a month, enough to keep Aurora/OpenSearch on all month
TB = lambda nv: nv * (1024 * 4 + 64) / 2**40  # S3 Vectors bytes per vector, same estimate as costs.json


def s3v_q(nv):  # each query is billed on the whole index, tiered by vector count (aws.amazon.com/s3/pricing)
    return (P["s3v_query_request"] + TB(min(nv, 1e5)) * P["s3v_processed_tb_first_100k"]
            + TB(min(max(nv - 1e5, 0), 99e5)) * P["s3v_processed_tb_100k_10m"] + TB(max(nv - 1e7, 0)) * P["s3v_processed_tb_over_10m"])


def grow(k, nv):
    """USD a month: storage scales linearly from the 100k bench; search/capacity per store's billing rule."""
    rest = REST[k] * nv / 1e5
    if k == "ddb":  # data a search examines grows logarithmically (VectorSearchMonitoring docs)
        return rest + QM * PER1K["ddb"] / 1000 * math.log(nv) / math.log(1e5)
    if k == "s3v":
        return rest + QM * s3v_q(nv)
    if k == "aoss":  # HNSW at the default 32x compression: 1.1*(1024/8 + 8*16) B per vector, 6 GiB per OCU
        return rest + max(1, math.ceil(nv * 1.1 * (1024 / 8 + 8 * 16) / 2**30 / 6)) * P["aoss_ocu_hour"] * H
    acu = math.ceil(REST["aurora"] / P["aurora_storage_gb_month"] * nv / 1e5 / 2)  # table + index in RAM, 2 GiB per ACU
    return rest + min(acu, 256) * P["aurora_acu_hour"] * H, acu > 256


assert abs(s3v_q(1e5) * 1000 - PER1K["s3v"]) / PER1K["s3v"] < 0.05  # the rule reproduces the measured 100k cost


def usd(x):
    return f"{es(x, 1) if x < 100 else f'{x:,.0f}'.replace(',', '.')}"


text(s, 0.7, 1.08, 8.6, 0.25, "USD al mes con 1 millón de búsquedas. Medí 100.000; lo demás es estimado.", size=12, color=MUTED, align=PP_ALIGN.CENTER)
WHY = {"ddb": "Crece lo guardado; buscar cuesta casi igual", "s3v": "Cada búsqueda paga el índice completo",
       "aoss": "Más memoria para el índice", "aurora": "Más memoria; con 100 millones no cabe*"}
rows = [["", "100.000", "1 millón", "100 millones", "Por qué sube"]]
for k in ("ddb", "s3v", "aoss", "aurora"):
    now = {"ddb": REST["ddb"] + QM * PER1K["ddb"] / 1000, "s3v": REST["s3v"] + QM * PER1K["s3v"] / 1000,
           "aoss": FLOOR["opensearch"] + REST["aoss"]}.get(k)
    if k == "aurora":
        (m1, _), (m100, over) = grow(k, 1e6), grow(k, 1e8)
        rows.append([SHORT[k], f"{FLOOR['aurora']:.0f} a {AURORA_SO_MONTH:.0f}", f"desde {usd(m1)}", f"más de {usd(m100)}*" if over else usd(m100), WHY[k]])
    else:
        rows.append([SHORT[k], usd(now), usd(grow(k, 1e6)), usd(grow(k, 1e8)), WHY[k]])
table(s, 0.7, 1.4, 8.6, rows, col_w=[1.3, 1.05, 1.15, 1.5, 3.6], row_h=0.44, size=12, hi={(1, 1): VIOLET, (1, 2): VIOLET, (1, 3): VIOLET})
text(s, 0.7, 3.72, 6.0, 0.25, "* No cabe en memoria ni con el máximo de Aurora (256 ACU)", size=10, color=MUTED)
notes(s, "[20:30–21:30] Esto no lo medí: lo estimé con la regla de cobro de cada servicio a partir de lo que medí con 100.000. "
         "DynamoDB: la documentación dice que lo que revisa cada búsqueda crece con el logaritmo del tamaño del índice; lo escalé así desde "
         "lo medido, pero AWS pide medir con datos reales (VectorSearchMonitoring). Con 100 millones son ~385 GB: sobre 600 GB de tabla, crear "
         "el índice requiere pedir acceso. S3 Vectors: cada consulta paga el tamaño completo del índice, con tramos por cantidad de vectores "
         "(aws.amazon.com/s3/pricing, 14 oct 2026); hasta 2.000 millones de vectores por índice. OpenSearch: 6 GiB por OCU y compresión 32x "
         "por defecto (yo medí con 1x); como carga a memoria solo lo que necesita, podría usar menos. Aurora: 2 GiB por ACU, máximo 256; supuse "
         "tabla e índice completos en memoria, por eso dice 'desde'. Con 100 millones necesitas halfvec, cuantización binaria o leer desde disco. "
         "Carga inicial de 100 millones, una vez: ~USD 80 en S3 Vectors (USD 0,20/GB) y ~USD 200 en DynamoDB (USD 0,52/GB).")

# 21 does it hold with Chilean law? ----------------------------------------------------------------------
s = next(n)
set_title(s, "¿Se repite con las leyes chilenas?")
clear_body(s)
rows = [["", "Sin filtro", "Con filtro", "Acierto con filtro", "Datos relacionados"]]
for k in ORDER:
    rows.append([SHORT[k], f"{es(L[k]['p50'])} ms", f"{es(LF[k]['p50'])} ms", pct(LF[k]["recall"]), f"{es(LJ[k]['p50'])} ms"])
table(s, 0.7, 1.3, 8.6, rows, col_w=[1.6, 1.6, 1.6, 1.9, 1.9], row_h=0.42, size=13,
      hi={(1, 1): VIOLET, (1, 2): VIOLET, (1, 4): VIOLET, (2, 3): VIOLET})
text(s, 0.7, 3.5, 4.4, 0.6, [
    [("El orden se repite. ", {"bold": True}),
     (f"Pero aquí DynamoDB filtra con {pct(LF['ddb']['recall'])} de acierto, frente a {pct(F['ddb']['recall'])} en Stack Overflow.", {})]],
    size=12)
notes(s, "[21:30–22:30] Misma prueba con 8.851 fragmentos de leyes; el filtro es el código (Código del Trabajo, Código Civil…). "
         f"Aurora filtra rápido aquí ({es(LF['aurora']['p50'])} ms) porque cada código tiene pocas filas: lo que vimos con el filtro. "
         "Tus datos cambian el resultado: mide con los tuyos. Cambian el tamaño y el contenido a la vez, así que esto no aísla el efecto del tamaño.")

# 22 scoreboard ------------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Las cinco respuestas")
clear_body(s)
table(s, 0.7, 1.45, 8.6, [
    ["Si necesitas…", "Usa", "Por qué"],
    ["Velocidad", "Aurora", f"{es(SO['aurora']['p50'])} ms, encendida"],
    ["Filtrar", "OpenSearch", f"{pct(F['aoss']['recall'], 2)} de acierto en {es(F['aoss']['p50'])} ms"],
    ["Datos de otra tabla", "Aurora", f"Un JOIN suma {es(J['aurora']['p50'] - SO['aurora']['p50'])} ms"],
    ["Arrancar sin espera", "DynamoDB y S3 Vectors", f"{cold('ddb'):.0f} y {cold('s3v'):.0f} ms tras la pausa"],
    ["Gastar poco", "DynamoDB y S3 Vectors", "Sin mínimo mensual"],
], col_w=[2.3, 2.3, 4.0], row_h=0.42, size=13, dark=True, head=MAGENTA)
text(s, 0.75, 1.12, 8.5, 0.25, "Ninguna gana todo", size=14, color="E6DFFF", bold=True, align=PP_ALIGN.CENTER)
notes(s, "[22:30–23:45] Las cinco preguntas, respondidas. Ninguna gana todo: por eso la pregunta correcta no es cuál es la mejor, "
         "sino qué necesita tu proyecto.")

# 23 promises revisited ----------------------------------------------------------------------------------
s = next(n)
set_title(s, "Las promesas, revisadas")
clear_body(s)
table(s, 0.7, 1.45, 8.6, [
    ["", "Promesa", "Lo que medí"],
    ["DynamoDB", ">99% de recall, ms de un dígito", f"Recall: cumple ({pct(SO['ddb']['recall'])}). Tiempo: {es(L['ddb']['p50'])} a {es(SO['ddb']['p50'])} ms desde mi máquina, con la red"],
    ["S3 Vectors", "~100 ms o menos", f"Cumple: {es(SO['s3v']['p50'])} ms (p50) y {es(SO['s3v']['p95'])} ms (p95)"],
    ["OpenSearch", "Escala a cero", f"Cumple. Volver a encenderse tomó {es(cold('aoss') / 1000)} s"],
    ["Aurora", "Filtra con WHERE, une con JOIN", f"Cumple y acierta 100%. Con mi índice de etiquetas no usó el vectorial ({es(F['aurora']['p50'])} ms)"],
], col_w=[1.4, 2.5, 4.7], row_h=0.48, size=12, dark=True, head=MAGENTA)
text(s, 0.75, 1.12, 8.5, 0.25, "Todas cumplen. La letra chica son las condiciones.", size=14, color="E6DFFF", bold=True, align=PP_ALIGN.CENTER)
notes(s, "[23:45–25:00] Medir, no atacar. DynamoDB: el anuncio habla del tiempo dentro del servicio; yo medí desde mi máquina, con red y "
         "SDK incluidos, así que no contradice la promesa: muestra lo que vas a ver tú. OpenSearch: su documentación habla de unos 10 s para "
         "arrancar; vi 23 s una vez. Aurora: el índice que yo creé hizo que Postgres no usara el vectorial; la mejora de pgvector 0.8 para "
         "filtros quedó sin medir.")

# 24 pick two --------------------------------------------------------------------------------------------
s = next(n)
set_title(s, "¿Cuál elijo?")
clear_body(s)
routes = [("aurora", "rapidez y SQL", f"Si el uso la mantiene encendida (~USD {FLOOR['aurora']:.0f} a {AURORA_SO_MONTH:.0f} al mes) y subes ef_search"),
          ("ddb", "bajo costo y sin espera", f"Sin mínimo mensual y {cold('ddb'):.0f} ms después de una pausa. Revisa el acierto si filtras"),
          ("aoss", "filtrar sin perder acierto", f"{pct(F['aoss']['recall'], 2)} de acierto con filtro, si pagas por tenerla encendida (~USD {FLOOR['opensearch']:.0f} al mes)"),
          ("s3v", "bajo costo y mucho volumen", "Sin mínimo mensual y ~60 ms siempre. A gran escala no lo medí")]
for i, (k, need, why) in enumerate(routes):
    r_, c_ = divmod(i, 2)
    x, y = 0.75 + c_ * 4.3, 1.3 + r_ * 1.4
    card(s, x, y, 4.1, 1.3, fill=CARD_DARK)
    dot(s, x + 0.2, y + 0.2, 0.26, STORE[k][1])
    text(s, x + 0.6, y + 0.1, 3.35, 0.3, [[("Si necesitas ", {}), (need, {"bold": True})]], size=12, color=WHITE)
    text(s, x + 0.6, y + 0.38, 3.35, 0.3, [[("→ ", {"color": MAGENTA}), (SHORT[k], {"bold": True, "color": "E6DFFF"})]], size=15, color=WHITE)
    text(s, x + 0.6, y + 0.74, 3.35, 0.5, why, size=11, color="DCD3FA")
notes(s, "[25:00–26:15] El criterio del resumen. Elige las dos cosas que tu proyecto no puede sacrificar y mira qué base te las da. "
         "Es una guía por caso, no un ranking; cada tarjeta sale de una de las cinco preguntas. S3 Vectors está diseñado para volúmenes grandes, pero yo "
         "solo medí 100.000 vectores.")

# 25 your class project ----------------------------------------------------------------------------------
s = next(n)
set_title(s, "Tu proyecto de clase, en números")
clear_body(s)
ddb_month = COST["ley"]["at_rest_month_usd"]["ddb"] + COST["ley"]["per_1k_queries_usd"]["ddb"]
SESSIONS, IDLE_H = 20, 600 / 3600  # assumption: 20 sessions a month; Aurora stays on >= 10 min after each (seconds_until_auto_pause = 600)
ley_acu = FLOOR["aurora"] / (730 * ACU_H)  # 0.67 ACU observed on Ley Chile
aurora_sleep = SESSIONS * IDLE_H * ley_acu * ACU_H + COST["ley"]["at_rest_month_usd"]["aurora"]
card(s, 0.85, 1.35, 2.5, 2.3)
text(s, 1.05, 1.45, 2.2, 0.3, "Supuesto", size=13, bold=True, color=VIOLET)
text(s, 1.05, 1.82, 2.2, 1.8, ["8.851 fragmentos de leyes", "1.000 búsquedas al mes", f"{SESSIONS} sesiones de uso al mes"], size=12, space=7)
for x, (val, unit, lab, sub, k) in zip((3.55, 5.45, 7.35), [
        ("<1", "¢", "DynamoDB", "sin espera", "ddb"),
        (f"~{es(aurora_sleep)}", "USD", "Aurora", f"se apaga sola: ~{es(A_COLD, 0)} s de espera cada vez", "aurora"),
        (f"~{FLOOR['aurora']:.0f}", "USD", "Aurora", "siempre encendida: sin espera", "aurora")]):
    card(s, x, 1.35, 1.8, 2.3, fill=WHITE, line="DDD6EE")
    big_number(s, x + 0.15, 1.5, 1.6, val, unit, lab, color=STORE[k][1], sub=sub, size=30)
text(s, 0.85, 3.75, 6.2, 0.3, "Si el uso es esporádico, lo que pagas es no esperar", size=13, bold=True, color=VIOLET)
notes(s, f"[26:15–27:15] Costo al mes de vectores + búsquedas, sin API, Lambda ni embeddings. DynamoDB: costs.json, USD {es(ddb_month, 4)} "
         f"antes de los 25 GB gratis. Aurora que se apaga: supuesto de {SESSIONS} sesiones × 10 minutos encendida (seconds_until_auto_pause = 600) "
         f"× {es(ley_acu, 2)} ACU × USD {es(ACU_H, 2)} + almacenamiento = USD {es(aurora_sleep, 2)}. Es una estimación, no una medición. "
         "Aurora siempre encendida: el mínimo mensual que vimos. Aurora puede ser barata o instantánea, pero no las dos cosas. Embeddings: Cohere "
         "Embed v4, USD 0,12 por millón de tokens; el README del proyecto de ejemplo estima ~USD 0,02 por 1.000 preguntas con todo incluido.")

# 26 behind the scenes -----------------------------------------------------------------------------------
s = next(n)
set_title(s, "Lo que salió mal (y cómo lo resolví)")
clear_body(s)
fails = [("Crear el índice pidió 4 GB", "Aurora se quedó sin memoria al construir el índice HNSW. Lo resolví bajando maintenance_work_mem a 1 GB."),
         ("«Listo» no significa listo", "DynamoDB marca el índice como ACTIVE, pero las primeras búsquedas fallan un rato. Reintenta."),
         ("Cuenta nueva, cuota en cero", "En una cuenta recién creada, la cuota de Bedrock para embeddings estaba en 0. Revísala antes del día de la demo."),
         ("No había GPU", "No había GPU disponible para Qwen en Virginia ni en Oregón. Al final lo desplegué en Ohio.")]
for i, (h, b) in enumerate(fails):
    r_, c_ = divmod(i, 2)
    x, y = 0.85 + c_ * 4.25, 1.3 + r_ * 1.4
    card(s, x, y, 3.95, 1.25)
    text(s, x + 0.25, y + 0.12, 3.5, 0.3, h, size=15, bold=True, color=MAGENTA)
    text(s, x + 0.25, y + 0.5, 3.5, 0.7, b, size=12)
notes(s, "[27:15–28:30] Elegir una base también es cuánto te cuesta dejarla funcionando. Fuentes: so-pipeline.log (OutOfMemory, pidió "
         f"{es(int(OOM) / 2**30)} GiB), commit 'fix(aurora): 1GB maintenance_work_mem'; dynamo.py wait_searchable(); free-tier/README.md "
         "(cuota 0, 'Too many tokens per day'); qwen-us-east-2.log (InsufficientInstanceCapacity). Dato extra: cargar los 100.000 vectores "
         f"tomó de {LOAD['aurora']:.0f} s en Aurora a {LOAD['ddb'] / 60:.0f} min en DynamoDB con mi script, que no optimicé; en un hackatón, "
         "carga la noche anterior.")

# 27 live race -------------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Pruébalo en tu celular")
clear_body(s)
card(s, 0.7, 1.3, 4.4, 2.6, fill=WHITE, line="4A3F8A")
s.shapes.add_picture(qr(event_url(), os.path.join(HERE, "qr-event.png")), Inches(0.9), Inches(1.45), Inches(2.1), Inches(2.1))
text(s, 3.15, 1.55, 1.85, 2.2, [
    [("Pruébalo tú", {"bold": True, "color": VIOLET, "size": 15})],
    "Escanea y entra",
    [(EVENT_HOST, {"bold": True, "size": 9, "color": INK})],
    [("código: ", {"size": 10, "color": MUTED}), (event_code(), {"bold": True, "size": 12, "color": VIOLET, "font": "Courier New"})],
], size=11, space=6)
text(s, 5.3, 1.35, 3.75, 2.6, [
    [("Gana créditos de AWS", {"bold": True, "color": "E6DFFF", "size": 18})],
    "1. Elige la base que crees que gana",
    "2. Mira la carrera",
    "3. Si acertaste, entras al sorteo",
    "Un código por ronda, hasta repartir los 4",
    [("1 código por ronda · 4 códigos", {"color": "C9C2E8", "size": 12})],
], size=15, color=WHITE, space=7)
notes(s, "[28:30–35:30] Guion de ~7 min desde /admin; el botón grande siempre hace el paso siguiente y no se puede saltar ni "
         "elegir ronda. 1:00 para escanear y entrar («Pasar a modo sorteo»; en el piso −1 puede faltar señal: dar tiempo). Cada ronda "
         "~1:15: «Abrir ronda N de 4» y apostar 0:30, «Cerrar apuestas y lanzar» y leer el resultado 0:20, «Sortear 1 código» entre "
         "quienes acertaron 0:25. Las rondas siguen el ciclo búsqueda simple → con filtro → con contexto (JOIN de Aurora) → otra "
         "colección, y vuelven a empezar hasta repartir los 4 códigos: un código por ronda, nadie gana dos veces, el código solo "
         "aparece en el celular del ganador. Si nadie acierta, el código pasa a la ronda siguiente. Cuando no quedan códigos, el "
         "botón ofrece volver a modo libre. Si alguna base está apagada, su fila dice «despertando» y se completa sola con el tiempo "
         "real: comentarlo, es el arranque en frío en vivo. Antes de la charla, desde free-tier: event-ops.py reset y event-ops.py "
         "codes <archivo> (AWS_PROFILE=fv-site); ~5 min antes de la demo, event-ops.py warmup. No anunciar ganador antes. "
         "Sitio cerrado hasta las 05:00 del 24/10; se borra después. Si falla la red: video.")

# 28 what the race shows ---------------------------------------------------------------------------------
s = next(n)
set_title(s, "Qué muestra la demo (y qué no)")
clear_body(s)
for col, (h, color, items) in enumerate([
        ("Lo que sí muestra", VIOLET, ["Las 4 bases respondiendo tu pregunta al mismo tiempo", "El arranque en frío en vivo, si alguna estaba apagada",
                                       "Que casi siempre devuelven los mismos resultados"]),
        ("Lo que no muestra", MAGENTA, ["El tiempo incluye la red desde Lambda",
                                                           "Aurora responde por la Data API (HTTP): no esperes 1,3 ms",
                                                           "Colecciones de 15.460 fragmentos y 3 resultados por pregunta"])]):
    x = 0.85 + col * 4.25
    card(s, x, 1.35, 3.95, 2.6)
    text(s, x + 0.25, 1.5, 3.5, 0.35, h, size=15, bold=True, color=color)
    text(s, x + 0.25, 2.0, 3.5, 1.9, [f"• {t}" for t in items], size=13, space=9)
notes(s, "[35:30–36:30] Leer el resultado con honestidad. '3/3 iguales a DynamoDB' compara resultados entre bases; no dice cuál es "
         "correcto. El texto que se muestra viene de DynamoDB. Ganar por tiempo no significa devolver el mejor contenido. Fuente: "
         "free-tier/app/handler.py (TOP_K = 3, race(), cliente rds-data).")

# 29 take it home ----------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Repítelo con tus datos")
clear_body(s)
card(s, 0.85, 1.35, 5.2, 2.75)
text(s, 1.1, 1.45, 4.8, 2.6, [
    [("El laboratorio completo", {"bold": True, "color": VIOLET, "size": 15})],
    "Terraform, scripts, preguntas y resultados en CSV",
    [("Proyecto de ejemplo: buscador de leyes con DynamoDB", {"bold": True, "color": VIOLET, "size": 15})],
    [("cd free-tier && ./deploy.sh", {"font": "Courier New", "size": 12, "bold": True})],
    "Antes, revisa tu cuota de Bedrock. Al terminar:",
    [("./destroy.sh", {"font": "Courier New", "size": 12, "bold": True})],
], size=13, space=5)
card(s, 6.3, 1.35, 2.7, 2.75, fill=WHITE, line="DDD6EE")
s.shapes.add_picture(qr(REPO, os.path.join(HERE, "qr.png")), Inches(6.6), Inches(1.45), Inches(2.1), Inches(2.1))
text(s, 6.35, 3.62, 2.6, 0.3, "github.com/andrezc98/fast-and-vectorious", size=9, color=MUTED, align=PP_ALIGN.CENTER)
notes(s, "[36:30–37:30] Para el lunes: toma las preguntas reales de tu proyecto, mide acierto y relevancia, prueba tus filtros y deja la "
         "base 15 minutos sin uso antes de medir la primera consulta. El ejemplo necesita acceso a Cohere Embed v4 en Bedrock y cuota "
         "mayor que cero (free-tier/README.md). PENDIENTE antes del 24/10: repo público y QR probado sin sesión.")

# 30 recap -----------------------------------------------------------------------------------------------
s = next(n)
set_title(s, "Tres ideas para llevarte")
clear_body(s)
text(s, 0.75, 1.1, 8.5, 0.25, f"La base de {es(A_WARM)} ms también fue la de {es(A_COLD, 0)} s. Mide las dos.",
     size=14, color="E6DFFF", bold=True, align=PP_ALIGN.CENTER)
for i, (num, h, b) in enumerate([
        ("1", "Ninguna gana todo", "Aurora, la más rápida encendida. DynamoDB, la más barata y sin espera. OpenSearch, la mejor con filtros"),
        ("2", "Mide cuatro cosas", "Acierto, p95, arranque en frío y lo que cuesta estar encendida"),
        ("3", "Repítelo", "El código del laboratorio y un ejemplo que cuesta centavos al mes")]):
    x = 0.75 + i * 2.9
    card(s, x, 1.5, 2.7, 2.3, fill=CARD_DARK)
    text(s, x + 0.25, 1.6, 0.6, 0.6, num, size=30, bold=True, color=MAGENTA, font=FONT_X)
    text(s, x + 0.25, 2.2, 2.25, 0.35, h, size=15, bold=True, color=WHITE)
    text(s, x + 0.25, 2.6, 2.25, 1.2, b, size=12, color="DCD3FA")
notes(s, "[37:30–38:30] Cierre: volvamos a la meta del principio. Ahora puedes justificar dónde guardar los vectores de tu proyecto. Aurora brilla "
         "encendida y con SQL. DynamoDB es la más barata y responde rápido incluso después de una pausa. OpenSearch mantiene el acierto "
         "cuando filtras. S3 Vectors guarda barato y sin mínimo mensual. Cada resultado tiene condiciones, y tus datos pueden cambiar la "
         "elección, como vimos con las leyes chilenas. Elige tus dos prioridades y compruébalas con tus datos. Gracias.")

# 31 thanks ----------------------------------------------------------------------------------------------
s = next(n)
for sh in s.shapes:
    if not sh.has_text_frame:
        continue
    t = sh.text_frame.text.strip()
    if t == "Nombre del Speaker":
        set_text(sh, "Andrés Zeballos")
    elif t.startswith("pegar dirección de linkedin"):
        set_text(sh, "linkedin.com/in/andreszc")
    elif t.startswith("pegar dirección de github"):
        set_text(sh, "github.com/andrezc98")
    elif t.startswith("pegar direccion de instagram"):
        ig = sh
    elif t.startswith("Gracias"):
        set_text(sh, "Gracias. ¿Preguntas?")
ig_icon = min((sh for sh in s.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE and sh.width < Inches(0.3)),
              key=lambda sh: abs(sh.top - ig.top))  # the small instagram glyph on the same row
for sh in (ig, ig_icon):
    sh._element.getparent().remove(sh._element)
card(s, 8.15, 1.3, 1.55, 1.75, fill=WHITE)
s.shapes.add_picture(qr("https://www.linkedin.com/in/andreszc", os.path.join(HERE, "qr-linkedin.png")), Inches(8.25), Inches(1.37), Inches(1.35), Inches(1.35))
text(s, 8.15, 2.74, 1.55, 0.25, "LinkedIn", size=10, bold=True, color=VIOLET, align=PP_ALIGN.CENTER)
notes(s, "[38:30–45:00] Preguntas, con el QR de feedback de la organización a la vista; mencionarlo al abrir las preguntas y al "
         "despedirse. Preguntas probables: tesis con 10.000 documentos y presupuesto cero; AWS Academy / Learner Lab; modelos gratuitos; "
         "¿y pgvector en RDS normal? (no lo medí).")

assert next(n, None) is None
prs.save(OUT)
print("wrote", OUT)
