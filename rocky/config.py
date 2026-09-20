"""Settings from the environment (.env is loaded by `uv run --env-file .env` or the CLI)."""

from __future__ import annotations

import os
from pathlib import Path


def _f(key: str, default: float) -> float:
    return float(os.environ.get(key, default))


TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_API_KEY = os.environ.get("TYPESAFE_API_KEY", "")
TYPESAFE_MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")

TEXT_MODEL_API_KEY = os.environ.get("TEXT_MODEL_API_KEY", "")
TEXT_MODEL_BASE_URL = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "deepseek-chat")

WAKE_WORD = os.environ.get("WAKE_WORD", "watermelon").lower()
STT_BACKEND = os.environ.get("STT_BACKEND", "auto")
STT_LANGUAGE = os.environ.get("STT_LANGUAGE", "en-US")

ACTION_MIN_CONFIDENCE = _f("ACTION_MIN_CONFIDENCE", 0.45)
DESTRUCTIVE_MIN_CONFIDENCE = _f("DESTRUCTIVE_MIN_CONFIDENCE", 0.8)
GOAL_MIN_CONFIDENCE = _f("GOAL_MIN_CONFIDENCE", 0.5)
MAX_STEPS = int(os.environ.get("MAX_STEPS", "12"))

RUNS_DIR = Path(os.environ.get("ROCKY_RUNS_DIR", "runs"))


def load_env_file(path: Path = Path(".env")) -> None:
    """Minimal .env loader so `rocky` works without uv's --env-file. Existing env wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split("#", 1)[0].strip())
