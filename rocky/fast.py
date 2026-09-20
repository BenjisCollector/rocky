"""Execute a Plan. Fast kinds map straight onto Platform calls; goal-tier plans hand off to the loop."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any
from urllib.parse import quote_plus

from . import config, risk
from .models import Plan
from .platform.base import Platform
from .router import ENGINES

INPUT_KINDS = frozenset({"type", "shortcut", "scroll"})  # keystrokes and wheel go to whatever is in front


def execute(
    plan: Plan,
    platform: Platform,
    act: bool = True,
    *,
    jev: Any = None,
    prompt_fn: Callable[[str], str] = input,
) -> str:
    """Run the plan and return one spoken line ('' when there is nothing to say). The fast path always
    acts; `act` only decides whether the goal loop drives the machine or dry-runs."""
    if plan.kind == "none" or not plan.addressed:
        return ""
    if plan.confidence < config.ACTION_MIN_CONFIDENCE:
        return "Not sure what you meant."
    refusal = risk.check(plan, plan.confidence, prompt_fn)
    if refusal:
        return refusal
    if plan.tier == "goal":
        return _goal(plan, platform, act, jev)
    a = plan.args
    kind = plan.kind
    if kind == "open_app":
        if a["app"] == "none":
            return "I don't see that app."
        platform.open_app(a["app"])
        return f"Opening {a['app']}."
    if kind == "open_url":
        platform.open_url(a["url"])
        return f"Opening {a['site'].replace('_', ' ')}."
    if kind == "search":
        platform.open_url(ENGINES[a["engine"]].format(q=quote_plus(a["query"])))
        return f"Searching {a['engine']} for {a['query']}."
    if kind == "play":
        from .media import youtube_first

        url = youtube_first(a["query"])
        if not url:
            platform.open_url(ENGINES["youtube"].format(q=quote_plus(a["query"])))
            return f"I could not pick a video, so here are the results for {a['query']}."
        platform.open_url(url)
        return f"Playing {a['query']}."
    if kind == "stop":
        from . import events

        events.emit("idle")
        with contextlib.suppress(Exception):
            platform.speak("")  # cuts any speech in progress
        platform.media("play_pause")  # pauses the video or track that is playing
        return "Stopped."
    if kind == "fun":
        from . import events

        events.emit(a["op"])
        return "" if a["op"] == "wave" else "Let's go!"
    front = plan.raw.get("front")
    if kind in INPUT_KINDS and front and platform.frontmost_app() != front:
        return (
            f"Focus moved to {platform.frontmost_app()}. Nothing sent."  # the Teams-window class of incident
        )
    if kind == "type":
        return _type(a["text"], platform)
    if kind == "shortcut":
        if a["shortcut"] == "none":
            return "I don't know that shortcut."
        key, mods = platform.shortcuts()[a["shortcut"]]
        platform.press(key, mods)
        return a["shortcut"].replace("_", " ").capitalize() + "."
    if kind == "scroll":
        platform.scroll(a["op"])
        return ""
    if kind == "volume":
        return platform.volume(a["op"])
    if kind == "media":
        platform.media(a["op"])
        return ""
    if kind == "system":
        return platform.system(a["op"])
    return ""


def _type(text: str, platform: Platform) -> str:
    """Type into whatever is focused, unless that field or the request itself is about a secret."""
    if risk.is_secret_field(text):
        return "I never type passwords or card details."
    # ponytail: a full snapshot per fast type; add a platform.focused_item() when this measures slow.
    if risk.is_secret_role(platform.focused_role()):
        return "I never type into password or card fields."
    focused = next((i for i in platform.snapshot().items if i.focused), None)
    if focused and risk.is_secret_field(focused.label):
        return "I never type into password or card fields."
    platform.type_text(text)
    return "Done."


OUTCOME_WORDS = {
    "done": "Done",
    "nothing helps": "I could not find anything on screen for that",
    "low confidence": "I was not sure enough to click anything",
    "stalled": "I clicked but nothing changed",
    "step limit": "I ran out of steps",
    "dry run": "Dry run, nothing clicked. Switch screen tasks to Do it",
    "needs confirmation": "That needs a yes from you",
}


def _goal(plan: Plan, platform: Platform, act: bool, jev: Any) -> str:
    from .loop import run_goal  # lazy: the loop is a separate module with its own dependencies

    if jev is None:
        from .jev import Jev

        jev = Jev()
    state = run_goal(platform, jev, plan.args["goal"], act, print)
    n = len(state.steps)
    return (
        OUTCOME_WORDS.get(state.outcome, state.outcome.capitalize()) + f" ({n} step{'s' if n != 1 else ''})."
    )
