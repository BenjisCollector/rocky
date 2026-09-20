"""Tiny in-process pub/sub so the voice loop can tell the overlay what is happening.

States: idle, hearing (speech detected), thinking (Jev or the writer is deciding), speaking (Rocky is
talking back), done (an action just executed), error.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

_subs: list[Callable[[str], None]] = []
_lock = threading.Lock()


def subscribe(fn: Callable[[str], None]) -> None:
    with _lock:
        _subs.append(fn)


def emit(state: str) -> None:
    with _lock:
        subs = list(_subs)
    for fn in subs:
        with contextlib.suppress(Exception):  # a broken listener must never break the voice loop
            fn(state)
