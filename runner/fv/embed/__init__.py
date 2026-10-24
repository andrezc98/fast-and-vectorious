import os

import numpy as np


def get_embedder(name: str):
    if name in ("cohere", "nova"):
        from fv.embed.bedrock import BedrockEmbedder
        return BedrockEmbedder(name)
    if name == "qwen":
        from fv.embed.qwen import QwenEmbedder
        return QwenEmbedder()
    raise ValueError(f"unknown model {name}")


def embed_all(fn, texts, purpose, corpus, out_dir, shard: int = 2000) -> np.ndarray:
    """Shard-by-shard so a crash or throttle storm resumes where it stopped; order is input order."""
    os.makedirs(out_dir, exist_ok=True)
    parts = []
    for n, start in enumerate(range(0, len(texts), shard)):
        path = os.path.join(out_dir, f"shard_{n:05d}.npy")
        if not os.path.exists(path):
            tmp = path[:-4] + ".tmp.npy"  # np.save appends .npy unless the name already ends with it
            np.save(tmp, fn(texts[start:start + shard], purpose, corpus))
            os.replace(tmp, path)  # atomic: a crash mid-write never leaves a partial final shard
        parts.append(np.load(path))
    return np.concatenate(parts) if parts else np.zeros((0, 0), dtype=np.float32)
