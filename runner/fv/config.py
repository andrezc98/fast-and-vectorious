import os

REGION = "us-east-1"
LAB_PROFILES = ("fv-lab",)  # the lab account profile
DIM = 1024  # only size Cohere v4, Nova 2 and Qwen3 share; pgvector HNSW caps `vector` at 2,000 (docs/api-notes.md §6)
K = 10


def require_sandbox() -> None:
    """Refuse to touch AWS unless the lab profile or the lab runner is in use."""
    if os.environ.get("FV_RUNNER") == "1":  # set by the EC2 user_data; the client credentials never live there
        return
    profile = os.environ.get("AWS_PROFILE", "")
    if profile not in LAB_PROFILES:
        raise RuntimeError(f"AWS_PROFILE must be one of {LAB_PROFILES}; refusing to use default credentials")


def env(name: str) -> str:
    key = f"FV_{name}"
    if key not in os.environ:
        raise KeyError(f"{key} is not set (runner: /etc/profile.d/fv.sh; laptop: terraform output, runbook §1)")
    return os.environ[key]
