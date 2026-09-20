"""Append-only JSONL log of everything Rocky hears and does, for improving it over time.

One line per event at RUNS_DIR/history.jsonl: heard (incl. ignored chatter), plan, executed, refused, failed,
goal runs (with the run folder), planner decompositions. Never logs API keys.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import config

_lock = threading.Lock()


def path() -> Path:
    return Path(config.RUNS_DIR) / "history.jsonl"


def record(event: str, **fields: Any) -> None:
    """Write one event. Values must be JSON-serialisable; anything else is stringified."""
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # a full disk must never break the voice loop


def tail(n: int = 20) -> list[dict[str, Any]]:
    p = path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(x) for x in lines if x.strip()]
