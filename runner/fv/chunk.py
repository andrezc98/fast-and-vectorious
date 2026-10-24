import html
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", s or ""))).strip()


def chunk_words(text: str, max_words: int = 220, overlap: int = 30) -> list[str]:
    """~220 Spanish words ≈ 300 tokens; fixed windows keep chunk counts predictable for the benchmark."""
    words = text.split()
    if not words:
        return []
    step = max_words - overlap
    return [" ".join(words[i:i + max_words]) for i in range(0, max(len(words) - overlap, 1), step)]
