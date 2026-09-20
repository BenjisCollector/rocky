"""The one path every utterance takes, from the CLI, the app, or a test.

route (one Jev call) -> split compound requests -> planner for unfamiliar requests -> execute -> log -> speak.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from . import events, history
from .models import Plan


def plan_line(plan: Plan) -> str:
    a = ", ".join(str(v) for k, v in plan.args.items() if k != "goal")
    return f"{plan.kind}({a})  conf={plan.confidence:.2f}  {plan.latency_ms}ms"


def act(
    utterance: str,
    jev: Any,
    platform: Any,
    do: bool = True,
    *,
    dry: bool = False,
    prompt_fn: Callable[[str], str] = input,
    log: Callable[[str], None] = print,
    speak: bool = True,
    depth: int = 0,
) -> tuple[str, str]:
    """Handle one utterance end to end. Returns (spoken reply, plan line). `do` drives screen tasks."""
    from .fast import execute
    from .jev import JevError
    from .router import route, split_compound

    try:
        plan = route(jev, platform, utterance)
    except JevError as e:
        history.record("failed", utterance=utterance, stage="route", error=str(e))
        log(f"  ! {e}")
        return "", "route failed"
    line = plan_line(plan)
    log(line)
    history.record(
        "plan",
        utterance=utterance,
        kind=plan.kind,
        args=plan.args,
        confidence=round(plan.confidence, 3),
        needs_screen=plan.needs_screen,
        addressed=plan.addressed,
        compound=plan.compound,
        latency_ms=plan.latency_ms,
        front=plan.raw.get("front"),
        depth=depth,
    )
    if dry:
        return "", line

    if plan.compound and depth == 0 and plan.kind != "play":  # "find X and play it" is one play request
        parts = split_compound(utterance)
        if len(parts) > 1:
            log(f"  compound: {parts}")
            replies = [
                act(p, jev, platform, do, prompt_fn=prompt_fn, log=log, speak=False, depth=1)[0]
                for p in parts
            ]
            return _say(platform, " ".join(r for r in replies if r), speak), line

    from . import config

    unsure = plan.kind == "none" or plan.confidence < config.ACTION_MIN_CONFIDENCE
    if (plan.tier == "goal" or unsure) and depth == 0:
        from .planner import decompose

        steps = decompose(utterance, platform.frontmost_app())
        history.record("planner", utterance=utterance, steps=steps)
        if steps:
            log(f"  plan: {steps}")
            replies = []
            for step in steps:
                goal = step[len("on screen:") :].strip() if step.lower().startswith("on screen:") else None
                r, _ = act(
                    goal or step, jev, platform, do, prompt_fn=prompt_fn, log=log, speak=False, depth=1
                )
                replies.append(r)
                if r.lower().startswith(("that failed", "focus moved", "i never")):
                    break
            return _say(platform, " ".join(r for r in replies if r), speak), line

    try:
        reply = execute(plan, platform, do, jev=jev, prompt_fn=prompt_fn)
        history.record("executed", utterance=utterance, kind=plan.kind, reply=reply, act=do, depth=depth)
        if did_something(plan, reply):
            from .router import remember

            remember(utterance, plan.kind, plan.args)
            with contextlib.suppress(Exception):
                platform.beep()
        events.emit("done" if plan.kind not in ("none", "fun") else "idle")
    except Exception as e:  # noqa: BLE001  one bad action must not kill the voice loop
        reply = f"That failed: {e}"
        history.record("failed", utterance=utterance, kind=plan.kind, error=str(e), depth=depth)
        events.emit("error")
    return _say(platform, reply, speak), line


REFUSALS = ("not sure", "i never", "focus moved", "that failed", "i don't know", "i could not")


def did_something(plan: Plan, reply: str) -> bool:
    """True when a real task ran (as opposed to chatter, a refusal, or Rocky playing with itself)."""
    if plan.kind in ("none", "fun") or not plan.addressed:
        return False
    return not reply.lower().startswith(REFUSALS)


def _say(platform: Any, reply: str, speak: bool) -> str:
    if reply and speak:
        events.emit("said:" + reply)
        with contextlib.suppress(Exception):  # no voice is better than no action
            platform.speak(reply)
    return reply
