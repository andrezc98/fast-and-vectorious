"""Render the article's editorial benchmark figures."""

import csv
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import matplotlib as mpl
import numpy as np
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle, FancyBboxPatch
from matplotlib.ticker import NullLocator

IMAGE_DIR = Path(__file__).resolve().parents[1]
ARTICLE_DIR = IMAGE_DIR.parent
RESULTS_DIR = ARTICLE_DIR.parent / "results" / "2026-10-09"
FIX_DIR = ARTICLE_DIR.parent / "results" / "2026-11-24"
TEXT = "#1a1a19"
SECONDARY = "#5f5e5a"
GREY = "#c9c8c3"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
STORES = {
    "aurora": ("Aurora pgvector", ORANGE),
    "ddb": ("DynamoDB", BLUE),
    "aoss": ("OpenSearch Serverless", "#1baf7a"),
    "s3v": ("S3 Vectors", "#b0369d"),
}


def load_points():
    with (ARTICLE_DIR / "per-tag-so-filter.csv").open(newline="") as source:
        return [
            dict(store=row["store"], tag=row["tag"], rows=int(row["matching_rows"]),
                 requests=int(row["requests"]), recall=float(row["mean_recall"]),
                 latency=float(row["p50_ms"]))
            for row in csv.DictReader(source)
        ]


def plot_points(ax, points, store, metric, color, *, cover=False):
    others = [p for p in points if p["store"] != store]
    focus = [p for p in points if p["store"] == store]
    ax.scatter([p["rows"] for p in others], [p[metric] for p in others],
               s=10, color=GREY, alpha=0.55 if cover else 1, linewidths=0, clip_on=False, zorder=2)
    requests = np.array([p["requests"] for p in focus])
    sizes = np.minimum(10 + 3 * requests, 220) if cover else np.minimum(6 + 2.2 * requests, 160)
    ax.scatter([p["rows"] for p in focus], [p[metric] for p in focus],
               s=sizes, color=color, alpha=0.85,
               edgecolors="white", linewidths=0.6, clip_on=False, zorder=3)
    return {p["tag"]: p for p in focus}


def label(ax, text, point, metric, position, *, align="left", color=SECONDARY):
    ax.annotate(text, xy=(point["rows"], point[metric]), xytext=position,
                ha=align, va="center", fontsize=9.5, color=color, zorder=4,
                arrowprops=dict(arrowstyle="-", color="#9a9994", lw=0.65,
                                shrinkA=3, shrinkB=3))


def javascript_label(ax, point, metric, value, position):
    label(ax, f"{point['tag']}, {point['rows']:,} rows: {value}",
          point, metric, position, align="right")


def new_figure(height):
    fig = Figure(figsize=(8, height), dpi=200)
    FigureCanvasAgg(fig)
    return fig


def style_axes(ax):
    ax.grid(axis="y", color="#ecebe7", linewidth=0.6)
    ax.tick_params(color=GREY, width=0.8, length=3, pad=4, labelsize=9)


def per_tag(points, heading):
    fig = new_figure(7.2)
    recall, latency = fig.subplots(2, 1, sharex=True)
    fig.subplots_adjust(left=0.085, right=0.983, top=0.906, bottom=0.086, hspace=0.42)
    # Extend the band above the plotting area to keep its label clear of recall=1 dots.
    for ax, summary in [(recall, "mean recall 0.299"), (latency, "p50 0.8 ms")]:
        style_axes(ax)
        ax.axvspan(0.7, 100, ymax=1.10, color="#f4f3f0", linewidth=0, zorder=0,
                   clip_on=False)
        ax.text(0.85, 1.05, f"under 0.1% of rows: {summary}", va="center",
                transform=ax.get_xaxis_transform(), color=SECONDARY)

    latency.set_xscale("log")
    latency.set_xlim(0.7, 30_000)
    latency.set_xticks([1, 10, 100, 1_000, 10_000],
                       labels=["1", "10", "100", "1,000", "10,000"])
    latency.xaxis.set_minor_locator(NullLocator())
    latency.set_xlabel("Rows in the index that match the query's tag (log scale, 100,000 total)",
                       labelpad=7)
    recall.set_ylim(0, 1.05)
    recall.set_yticks([0, 0.25, 0.5, 0.75, 1], labels=["0", "0.25", "0.5", "0.75", "1.0"])
    latency.set_ylim(0, 90)
    latency.set_yticks([0, 20, 40, 60, 80])

    ddb = plot_points(recall, points, "ddb", "recall", BLUE)
    aurora = plot_points(latency, points, "aurora", "latency", ORANGE)
    label(recall, "other three stores", aurora["bucles"], "recall", (2.3, 0.86))
    label(recall, "DynamoDB", ddb["r"], "recall", (740, 0.63), color=BLUE)
    js = ddb["javascript"]
    javascript_label(recall, js, "recall", f"{js['recall']:.3f}", (24000, 0.71))

    grey_point = next(p for p in points if p["store"] == "s3v" and p["tag"] == "bucles")
    label(latency, "other three stores", grey_point, "latency", (3, 73))
    label(latency, "Aurora pgvector", aurora["c#"], "latency", (2500, 45),
          align="right", color=ORANGE)
    js = aurora["javascript"]
    javascript_label(latency, js, "latency", f"{js['latency']:.0f} ms", (12100, 84))
    latency.text(0.995, 0.071, "dot size = number of\nqueries with that tag",
                 transform=latency.transAxes, ha="right", va="center",
                 fontsize=9.5, color=SECONDARY)

    recall.set_title("Recall@10, DynamoDB highlighted", loc="left", pad=25, fontproperties=heading)
    latency.set_title("p50 latency (ms), Aurora pgvector highlighted", loc="left", pad=25,
                      fontproperties=heading)
    fig.savefig(IMAGE_DIR / "03-per-tag.png")


def cover(points, heading):
    fig = new_figure(4.5)
    recall, latency = fig.subplots(1, 2, sharex=True)
    fig.subplots_adjust(left=0.085, right=0.95, bottom=0.23, top=0.80, wspace=0.27)
    for ax in (recall, latency):
        style_axes(ax)
    recall.set_xscale("log")
    recall.set_xlim(1, 20_000)
    recall.set_xticks([1, 100, 10_000], labels=["1", "100", "10k"])
    recall.xaxis.set_minor_locator(NullLocator())
    recall.set_ylim(0, 1.05)
    recall.set_yticks([0, 0.25, 0.5, 0.75, 1], labels=["0", "0.25", "0.5", "0.75", "1.0"])
    latency.set_ylim(0, 90)
    latency.set_yticks([0, 20, 40, 60, 80])
    plot_points(recall, points, "ddb", "recall", BLUE, cover=True)
    plot_points(latency, points, "aurora", "latency", ORANGE, cover=True)
    recall.set_title("DynamoDB recall@10", loc="left", pad=10, fontproperties=heading)
    latency.set_title("Aurora pgvector p50 latency (ms)", loc="left", pad=10, fontproperties=heading)
    fig.supxlabel("rows matching the filter (log scale)", y=0.10, fontsize=9.5)
    fig.savefig(IMAGE_DIR / "00-cover.png")


def plain_metrics(store, suffix=""):
    path = RESULTS_DIR / f"bench-{store}-so-cohere-plain{suffix}.csv"
    with path.open(newline="") as source:
        rows = [row for row in csv.DictReader(source) if row["ok"].lower() == "true"]
    return (np.median([float(row["latency_ms"]) for row in rows]),
            np.mean([float(row["recall"]) for row in rows]))


def plain_search():
    fig = new_figure(4.6)
    ax = fig.subplots()
    fig.subplots_adjust(left=0.105, right=0.965, top=0.94, bottom=0.16)
    style_axes(ax)
    ax.set_xscale("log")
    ax.set_xlim(0.8, 100)
    ticks = [1, 2, 5, 10, 20, 50, 100]
    ax.set_xticks(ticks, labels=[str(tick) for tick in ticks])
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlabel("p50 latency (ms, log scale)", labelpad=7)
    ax.set_ylim(0.90, 1.00)
    ax.set_yticks([0.90, 0.92, 0.94, 0.96, 0.98, 1.00])
    ax.set_ylabel("Recall@10 (axis starts at 0.90)", labelpad=7)

    sweep = [plain_metrics("aurora", suffix) for suffix in ("", "-ef100", "-ef200")]
    ax.plot(*zip(*sweep), color=ORANGE, lw=1, zorder=2)
    labels = ["Aurora, ef_search 40 (default)", "ef_search 100", "ef_search 200"]
    for i, (xy, name) in enumerate(zip(sweep, labels)):
        ax.scatter(*xy, s=90, facecolor="white" if i else ORANGE, edgecolor=ORANGE,
                   linewidth=1.5, zorder=3)
        ax.annotate(name, xy, xytext=(9, 0), textcoords="offset points", va="center")
    for store, offset, align in [("ddb", (9, 0), "left"), ("aoss", (-9, 5), "right"),
                                 ("s3v", (0, 13), "center")]:
        name, color = STORES[store]
        xy = plain_metrics(store)
        ax.scatter(*xy, s=90, color=color)
        ax.annotate(name, xy, xytext=offset, textcoords="offset points", ha=align, va="center")
    ax.text(0.985, 0.025, "up and left is better", transform=ax.transAxes, ha="right",
            color=SECONDARY)
    fig.savefig(IMAGE_DIR / "02-plain-search.png")


def cold_start():
    with (RESULTS_DIR / "cold-start.json").open() as source:
        stores = json.load(source)["stores"]
    totals = {store: run["connect_s"] + run["first_three_queries_ms"][0][0] / 1000
              for store, run in stores.items()}
    order = sorted(totals, key=totals.get, reverse=True)
    fig = new_figure(3)
    ax = fig.subplots()
    fig.subplots_adjust(left=0.225, right=0.96, bottom=0.24, top=0.965)
    ax.tick_params(axis="y", length=0, pad=7, labelsize=9.5)
    ax.tick_params(axis="x", color=GREY, width=0.8, length=3, pad=4, labelsize=9)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, 25)
    ax.set_xticks(range(0, 26, 5))
    ax.set_xlabel("seconds to first result after about 16 minutes idle, including client setup",
                  labelpad=7)
    for y, store in enumerate(order):
        total = totals[store]
        color = STORES[store][1]
        if store == "aurora":
            connect = stores[store]["connect_s"]
            ax.barh(y, connect, height=0.55, color=color)
            ax.barh(y, total - connect, left=connect, height=0.55, color="#f6b49a")
            ax.text(connect / 2, y, f"resume and connect {connect:.1f} s", ha="center",
                    va="center", color="white")
        else:
            ax.barh(y, total, height=0.55, color=color)
        value = f"{total:.1f} s" if total >= 1 else f"{total * 1000:.0f} ms"
        ax.annotate(value, (total, y), xytext=(4, 0), textcoords="offset points", va="center")
    ax.set_yticks(range(len(order)), labels=[STORES[store][0] for store in order])
    ax.invert_yaxis()
    fig.savefig(IMAGE_DIR / "05-cold-start.png")


def recall_concept():
    fig = new_figure(1.6)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set(xlim=(0, 8), ylim=(0.4, 2), aspect="equal")
    ax.set_axis_off()
    rows = [(1.55, "Exact top 10 (answer key)", range(1, 11)),
            (0.85, "What the store returned", [3, 1, 5, 2, 6, 4, 8, 10, 7, "x"])]
    for y, title, numbers in rows:
        ax.text(0.18, y, title, color=SECONDARY, va="center", fontsize=9.5)
        for i, number in enumerate(numbers):
            x = 2.35 + i * 0.305
            fill = "white" if y == 1.55 else "#ecebe7" if number == "x" else "#dbe8f8"
            ax.add_patch(FancyBboxPatch(
                (x, y - 0.11), 0.22, 0.22,
                boxstyle="round,pad=0,rounding_size=0.035",
                facecolor=fill, edgecolor=TEXT if y == 1.55 else "none", linewidth=0.8))
            ax.text(x + 0.11, y, str(number), ha="center", va="center",
                    fontsize=9.5, color=SECONDARY if number == "x" else TEXT)
    ax.text(5.78, 1.2, "recall@10 = 9 / 10 = 0.9", fontsize=12, va="center")
    fig.savefig(IMAGE_DIR / "01a-recall.png")


def filter_concept(heading):
    fig = new_figure(3.2)
    rng = np.random.default_rng(73)
    points = rng.uniform([0.06, 0.30], [1.34, 0.95], size=(260, 2))
    question = np.array([0.67, 0.59])
    distances = np.linalg.norm(points - question, axis=1)
    nearest = np.argsort(distances)
    # Eleven of 260 rows match (about 4%); exactly two are in the first 25 candidates.
    matching = np.concatenate((nearest[[4, 18]], rng.choice(nearest[25:], 9, replace=False)))
    matches = np.zeros(len(points), dtype=bool)
    matches[matching] = True
    nearest_matches = matching[np.argsort(distances[matching])[:10]]
    radius = (distances[nearest[24]] + distances[nearest[25]]) / 2
    panels = [fig.add_axes([left, 0.02, 0.445, 0.78]) for left in (0.035, 0.52)]
    leader = dict(arrowstyle="-", color="#9a9994", lw=0.65, shrinkA=3, shrinkB=4)
    for i, (ax, title) in enumerate(zip(panels, ["Search first, then filter",
                                               "Filter first, then search"])):
        ax.set(xlim=(0, 1.4), ylim=(0, 1), aspect="equal")
        ax.set_axis_off()
        ax.set_title(title, loc="left", pad=12, fontproperties=heading)
        ax.scatter(*points[~matches].T, s=11, color=GREY,
                   alpha=0.15 if i else 1, linewidths=0, zorder=2)
        if i == 0:
            ax.add_patch(Circle(question, radius, fill=False, edgecolor="#9a9994",
                                linewidth=0.8, linestyle=(0, (4, 3)), zorder=1))
        else:
            for point in points[nearest_matches]:
                ax.plot([question[0], point[0]], [question[1], point[1]],
                        color="#9a9994", lw=0.65, zorder=1)
        ax.scatter(*points[matches].T, s=23, color=BLUE, linewidths=0,
                   edgecolors="white", zorder=3)
        ax.scatter(*question, marker="*", s=125, color=ORANGE,
                   edgecolors="white", linewidths=0.6, zorder=4)

    left, right = panels
    left.annotate("nearest candidates:\nonly 2 pass the filter",
                  xy=question + [0, -radius], xytext=(0.77, 0.28),
                  ha="center", va="top", fontsize=9.5, color=SECONDARY,
                  annotation_clip=False, arrowprops=leader)
    low_match = matching[np.argmin(points[matching, 1])]
    left.annotate("matches the filter", xy=points[low_match], xytext=(0.04, 0.29),
                  va="top", fontsize=9.5, color=SECONDARY, arrowprops=leader)
    right.annotate("question", xy=question, xytext=(0.67, 0.49), ha="center",
                   va="top", fontsize=9.5, color=SECONDARY, arrowprops=leader)
    right.text(0.7, 0.25, "10 nearest among matching rows", ha="center", va="top",
               fontsize=9.5, color=SECONDARY)
    fig.savefig(IMAGE_DIR / "01b-filter.png")


def bucket_metrics(path, store):
    """Mean recall and p50 latency per filter-width bucket (same buckets as the article)."""
    with (ARTICLE_DIR / "query-selectivity-so.csv").open(newline="") as source:
        share = {int(r["qi"]): int(r["matching_rows"]) / 100_000 for r in csv.DictReader(source)}
    groups = [[] for _ in range(4)]
    with path.open(newline="") as source:
        for row in csv.DictReader(source):
            if row["store"] == store and row["ok"] == "True":
                s = share[int(row["qi"])]
                groups[(s >= 0.001) + (s >= 0.01) + (s >= 0.05)].append(row)
    return ([np.mean([float(r["recall"]) for r in g]) for g in groups],
            [np.median([float(r["latency_ms"]) for r in g]) for g in groups])


def fixes(heading):
    ddb = FIX_DIR / "guide-filter-requests.csv"
    before_recall = bucket_metrics(ddb, "ddb")[0]
    after_recall = bucket_metrics(ddb, "ddb_hash")[0]
    before_ms = bucket_metrics(FIX_DIR / "guide-aurora-all-default.csv", "aurora_all_default")[1]
    after_ms = bucket_metrics(FIX_DIR / "guide-aurora-all-forced-ef100.csv",
                              "aurora_all_forced_ef100")[1]
    fig = new_figure(3.6)
    left, right = fig.subplots(1, 2)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.2, wspace=0.28)
    x = np.arange(4)
    names = ["under 0.1%", "0.1 to 1%", "1 to 5%", "5% or more"]
    panels = [
        (left, before_recall, after_recall, BLUE, "DynamoDB recall@10",
         "INLINE_FILTER", "HASH partition key", (0, 1.12), [0, 0.25, 0.5, 0.75, 1.0]),
        (right, before_ms, after_ms, ORANGE, "Aurora pgvector p50 latency (ms)",
         "default settings", "forced settings, ef_search 100", (0, 80), [0, 20, 40, 60, 80]),
    ]
    for ax, before, after, color, title, before_name, after_name, ylim, yticks in panels:
        style_axes(ax)
        ax.set_ylim(*ylim)
        ax.set_yticks(yticks)
        ax.set_xlim(-0.45, 3.45)
        ax.set_xticks(x, labels=names)
        ax.tick_params(axis="x", length=0)
        ax.vlines(x, np.minimum(before, after), np.maximum(before, after),
                  color=GREY, lw=1.5, zorder=2)
        ax.scatter(x, before, s=60, facecolor="white", edgecolor=SECONDARY, lw=1.3, zorder=3,
                   clip_on=False)
        ax.scatter(x, after, s=60, color=color, zorder=4, clip_on=False)
        ax.text(0, 1.1, title, transform=ax.transAxes, fontproperties=heading, va="bottom")
        ax.set_xlabel("share of rows that match the filter", labelpad=6, color=SECONDARY)
        # Before label beside the widest gap; after label above the low-left dots, clear of the rest.
        i, ha, dx = (0, "left", 10) if ax is left else (3, "right", -10)
        ax.annotate(before_name, (i, before[i]), xytext=(dx, 0), textcoords="offset points",
                    ha=ha, va="center", color=SECONDARY)
        ax.annotate(after_name, (0, after[0]), xytext=(-6, 13 if ax is left else 16),
                    textcoords="offset points", ha="left", va="center", color=TEXT)
    fig.savefig(IMAGE_DIR / "06-fixes.png")


def buckets(heading):
    """Step 1: how the 1,000 test queries spread over filter widths."""
    with (ARTICLE_DIR / "query-selectivity-so.csv").open(newline="") as source:
        rows = [(r["attr"], int(r["matching_rows"]) / 100_000) for r in csv.DictReader(source)]
    names = ["under 0.1%", "0.1 to 1%", "1 to 5%", "5% or more"]
    queries, tags = [0] * 4, [set() for _ in range(4)]
    for tag, share in rows:
        b = (share >= 0.001) + (share >= 0.01) + (share >= 0.05)
        queries[b] += 1
        tags[b].add(tag)
    assert sum(queries) == 1000, queries
    fig = new_figure(2.6)
    ax = fig.subplots()
    fig.subplots_adjust(left=0.16, right=0.97, top=0.80, bottom=0.2)
    ax.tick_params(axis="y", length=0, pad=7, labelsize=9.5)
    ax.tick_params(axis="x", color=GREY, width=0.8, length=3, pad=4, labelsize=9)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, 780)
    ax.set_xlabel("test queries (1,000 total)", labelpad=6, color=SECONDARY)
    for y, (q, t) in enumerate(zip(queries, tags)):
        ax.barh(y, q, height=0.55, color=BLUE if y == 0 else GREY)
        ax.annotate(f"{q} queries, {len(t)} tags", (q, y), xytext=(5, 0),
                    textcoords="offset points", va="center")
    ax.set_yticks(range(4), labels=names)
    ax.invert_yaxis()
    ax.set_title("Share of the 100,000 rows that match each query's tag", loc="left", pad=10,
                 fontproperties=heading)
    fig.savefig(IMAGE_DIR / "07-buckets.png")


def test_loop(heading):
    """Overview of the test this guide builds, one box per step."""
    steps = [("1", "Count rows\nper filter value"), ("2", "Exact answer\nkey (NumPy)"),
             ("3", "Index and load\nfour stores"), ("4", "Same filtered\ntop-10 query"),
             ("5", "Time requests,\none row each"), ("6", "Group recall and\nlatency by width")]
    fig = new_figure(1.55)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set(xlim=(0, 12), ylim=(0, 2.4))
    ax.set_axis_off()
    w, gap = 1.72, 0.3
    for i, (n, text) in enumerate(steps):
        x = 0.12 + i * (w + gap)
        last = i == len(steps) - 1
        ax.add_patch(FancyBboxPatch((x, 0.45), w, 1.5, boxstyle="round,pad=0,rounding_size=0.12",
                                    facecolor="#dbe8f8" if last else "#f4f3f0",
                                    edgecolor=BLUE if last else GREY, linewidth=0.9))
        ax.text(x + 0.16, 1.68, f"Step {n}", fontproperties=heading, va="center",
                color=BLUE if last else TEXT)
        ax.text(x + 0.16, 1.0, text, va="center", fontsize=9, color=SECONDARY, linespacing=1.3)
        if not last:
            ax.annotate("", xy=(x + w + gap - 0.03, 1.2), xytext=(x + w + 0.03, 1.2),
                        arrowprops=dict(arrowstyle="-|>", color="#9a9994", lw=0.9))
    fig.savefig(IMAGE_DIR / "08-test-loop.png")


def aurora_ladder(heading):
    """Step 7: p50 for the javascript queries after each change, from the second build."""
    runs = [("guide-aurora-baseline", "no change", "bitmap scan on attr"),
            ("guide-aurora-partial_hnsw", "partial HNSW index (ignored)", ""),
            ("guide-aurora-full_hnsw_no_bitmap", "bitmap scans off (seq scan)", ""),
            ("guide-aurora-partial_hnsw_forced-auto", "partial index, bitmap + seq off", ""),
            ("guide-aurora-partial_hnsw_forced-force_custom_plan", "+ force_custom_plan",
             "partial HNSW index"),
            ("guide-aurora-partial_hnsw_forced-force_custom_plan-ef100", "+ ef_search 100",
             "partial HNSW index")]
    stats = []
    for name, _, _ in runs:
        with (FIX_DIR / f"{name}.csv").open(newline="") as source:
            ok = [r for r in csv.DictReader(source) if r["ok"] == "True"]
        stats.append((np.median([float(r["latency_ms"]) for r in ok]),
                      np.mean([float(r["recall"]) for r in ok])))
    fig = new_figure(3.3)
    ax = fig.subplots()
    fig.subplots_adjust(left=0.34, right=0.98, top=0.86, bottom=0.17)
    ax.tick_params(axis="y", length=0, pad=7, labelsize=9.5)
    ax.tick_params(axis="x", color=GREY, width=0.8, length=3, pad=4, labelsize=9)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, 125)
    ax.set_xticks([0, 20, 40, 60, 80])
    ax.set_xlabel("p50 latency (ms), 468 requests for the javascript tag", labelpad=6,
                  color=SECONDARY)
    for y, ((ms, recall), (_, _, plan)) in enumerate(zip(stats, runs)):
        fast = ms < 10
        ax.barh(y, ms, height=0.55, color=ORANGE if fast else GREY)
        ax.annotate(f"{ms:.1f} ms, recall {recall:.3f}", (ms, y), xytext=(5, 0),
                    textcoords="offset points", va="center",
                    color=TEXT if fast else SECONDARY)
    ax.set_yticks(range(len(runs)), labels=[label for _, label, _ in runs])
    ax.invert_yaxis()
    ax.set_title("Aurora: what made the broad javascript filter fast", loc="left", pad=10,
                 fontproperties=heading)
    fig.savefig(IMAGE_DIR / "09-aurora-ladder.png")


def main(only=None):
    font = Path.home() / "Library/Fonts/InterVariable.ttf"
    font_manager.fontManager.addfont(font)
    mpl.rcParams.update({
        "font.family": font_manager.FontProperties(fname=font).get_name(),
        "font.size": 9.5, "text.color": TEXT, "axes.labelcolor": TEXT,
        "axes.edgecolor": GREY, "axes.linewidth": 0.8, "axes.axisbelow": True,
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.color": SECONDARY, "ytick.color": SECONDARY,
        "xtick.direction": "out", "ytick.direction": "out",
        "figure.facecolor": "white", "axes.facecolor": "white",
    })
    if not only:
        plain_search()
        cold_start()
        recall_concept()
    points = load_points()
    # Matplotlib selects the default weight when it opens a variable font.
    with TTFont(font) as inter, NamedTemporaryFile(suffix=".ttf") as semibold:
        instantiateVariableFont(inter, {"wght": 600}, inplace=True)
        inter.save(semibold.name)
        heading = font_manager.FontProperties(fname=semibold.name, size=10.5, weight="semibold")
        if only:  # e.g. figures.py buckets test_loop: render just those
            renderers = {"plain_search": plain_search, "cold_start": cold_start, "recall_concept": recall_concept,
                         "per_tag": lambda: per_tag(points, heading), "cover": lambda: cover(points, heading), 
                         **{n: (lambda f=globals()[n]: f(heading)) for n in
                            ("filter_concept", "fixes", "buckets", "test_loop", "aurora_ladder")}}
            for name in only:
                if name not in renderers:
                    raise SystemExit(f"unknown figure {name!r}; choose from {sorted(renderers)}")
                renderers[name]()
            return
        per_tag(points, heading)
        cover(points, heading)
        filter_concept(heading)
        fixes(heading)
        buckets(heading)
        test_loop(heading)
        aurora_ladder(heading)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
