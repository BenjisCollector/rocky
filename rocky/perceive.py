"""Turn one Platform snapshot into the numbered list the model chooses from, and tell two snapshots apart.

The goal-echo filter is adapted from awlevin/typesafe-computer-use (MIT, see THIRD_PARTY_NOTICES.md).
"""

from __future__ import annotations

from dataclasses import replace

from .models import Item, Snapshot
from .platform.base import Platform

ECHO_CHARS = 24


def goal_echoes(goal: str) -> set[str]:
    """Substrings that identify a screen line as the command that launched this run."""
    norm = " ".join(goal.lower().split())
    return {norm[:ECHO_CHARS], norm[-ECHO_CHARS:]} if len(norm) >= ECHO_CHARS else {norm}


def is_echo(label: str, echoes: set[str]) -> bool:
    norm = " ".join(label.lower().split())
    return bool(norm) and any(e in norm for e in echoes)


def perceive(platform: Platform, goal: str, max_items: int = 120) -> tuple[Snapshot, list[Item]]:
    """Snapshot the screen and keep the items worth offering: enabled, not a goal echo, no duplicates.

    Items are renumbered 1..N in snapshot order so the criteria ids and the state list always agree.
    """
    # ponytail: a goal that literally equals a control's whole label drops that control too;
    # exempt AX controls from the echo check if that ever bites.
    snapshot = platform.snapshot(max_items=max_items)
    echoes = goal_echoes(goal)
    seen: set[tuple[str, str, int, int]] = set()
    items: list[Item] = []
    for it in snapshot.items:
        if not it.enabled or is_echo(it.label, echoes):
            continue
        cx, cy = it.center
        key = (it.role, it.label, round(cx), round(cy))
        if key in seen:
            continue
        seen.add(key)
        items.append(replace(it, index=len(items) + 1))
        if len(items) >= max_items:
            break
    return snapshot, items


def fingerprint(snapshot: Snapshot) -> tuple:
    focused = next(((it.role, it.label, it.value) for it in snapshot.items if it.focused), None)
    labels = frozenset((it.role, it.label) for it in snapshot.items)
    return (snapshot.app, snapshot.window_title, labels, focused)


def changed(prev: Snapshot, cur: Snapshot) -> bool:
    """Did the app, the window title, the set of (role, label) or the focused item move? Used for stalls."""
    return fingerprint(prev) != fingerprint(cur)
