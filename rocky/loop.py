"""The goal loop: perceive, decide, act, repeat, with confidence floors, stall detection, a step limit and a
replayable run folder. Outcomes and the run folder follow awlevin/typesafe-computer-use (MIT)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config
from .decide import decide, verify_typed
from .jev import Jev
from .models import Decision, Item, Snapshot, StepResult
from .perceive import changed, perceive
from .platform.base import Platform

MAX_UNCHANGED = 2  # consecutive executed steps that changed nothing before "stalled"
SETTLE = 0.25  # seconds to wait after every action
SETTLE_MAX = 1.0  # then poll this long for the snapshot to change
POLL = 0.1


@dataclass
class RunState:
    outcome: str = "crashed"  # every way out of the loop names its own; only an exception leaves this
    steps: list[StepResult] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    run_dir: Path | None = None
    seconds: float = 0.0
    answers: list[dict] = field(default_factory=list)


def describe(decision: Decision) -> str:
    if decision.item is None:
        return decision.kind
    return f"{decision.kind} [{decision.item.index}] {decision.item.label}"


def run_goal(
    platform: Platform,
    jev: Jev,
    goal: str,
    act: bool,
    log: Callable[[str], object] = print,
    utterance: str | None = None,
    max_steps: int | None = None,
    *,
    confirm: Callable[[str], bool] | None = None,
) -> RunState:
    """Drive `goal` to one of: done, nothing helps, low confidence, stalled, step limit, dry run,
    needs confirmation. With act=False nothing touches the machine."""
    from .risk import is_secret_field, is_secret_role, requires_confirmation

    limit = max_steps or config.MAX_STEPS
    run_dir = config.RUNS_DIR / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_dir=run_dir)
    timings: list[dict[str, float]] = []
    unchanged = 0
    started = time.perf_counter()
    try:
        for n in range(1, limit + 1):
            timing: dict[str, float] = {}
            t0 = time.perf_counter()
            snapshot, items = perceive(platform, goal)
            timing["perceive"] = round(time.perf_counter() - t0, 3)
            t1 = time.perf_counter()
            decision = decide(jev, goal, snapshot, items, state.history)
            timing["decide"] = round(time.perf_counter() - t1, 3)
            timings.append(timing)
            state.answers.append(decision.raw)
            result = StepResult(step=n, decision=decision, executed=False, changed=False)
            state.steps.append(result)
            what = describe(decision)
            log(f"step {n}  {what}  conf={decision.confidence:.2f}  {decision.latency_ms}ms")

            if decision.kind == "done":
                state.outcome = "done"
                return state
            if decision.kind == "none":
                state.outcome = "nothing helps"
                return state
            if decision.confidence < config.GOAL_MIN_CONFIDENCE:
                log(f"confidence {decision.confidence:.2f} below {config.GOAL_MIN_CONFIDENCE}")
                state.outcome = "low confidence"
                return state
            if not act:
                log(f"would do: {what}")
                state.outcome = "dry run"
                return state
            decision.raw["buttons"] = " | ".join(i.label for i in items if "button" in i.role.lower())
            if requires_confirmation(decision) and not (confirm and confirm(what)):
                log(f"needs confirmation: {what}")
                state.outcome = "needs confirmation"
                return state

            platform.screenshot(run_dir / f"step-{n:03d}.png")
            t2 = time.perf_counter()
            front = platform.frontmost_app()
            if front != snapshot.app:
                result.note = (
                    f"refused: focus moved to {front}"  # the snapshot no longer describes the screen
                )
            elif decision.kind == "type_text" and (
                is_secret_field(decision.item.label) or is_secret_role(platform.focused_role())
            ):
                result.note = "refused: secret field"
            else:
                result.executed = True
                result.note = execute(platform, jev, decision, goal, utterance)
                after = settle(platform, snapshot)
                result.changed = changed(snapshot, after)
            timing["act"] = round(time.perf_counter() - t2, 3)
            state.history.append(f"{what}: {result.note or ('changed' if result.changed else 'no change')}")
            log(f"  {state.history[-1]}")

            unchanged = 0 if result.changed else unchanged + 1
            if unchanged >= MAX_UNCHANGED:
                state.outcome = "stalled"
                return state
        state.outcome = "step limit"
        return state
    finally:
        state.seconds = round(time.perf_counter() - started, 3)
        write_run(state, goal, act, utterance, limit, timings)


def execute(platform: Platform, jev: Jev, decision: Decision, goal: str, utterance: str | None) -> str:
    """Perform one decision through the Platform. Returns a short note for the history."""
    kind, item = decision.kind, decision.item
    if kind == "click_item":
        platform.click(*item.center)
    elif kind == "type_text":
        from .writer import compose

        text = compose(goal, item.label, utterance)
        if not text:
            return "writer declined"
        if not item.focused:
            platform.click(*item.center)
        platform.type_text(text)
        return f"typed {text[:60]!r}, verified {verify_typed(jev, goal, item, text, find_field(platform, item)):.2f}"
    elif kind == "press_enter":
        platform.press("enter")
    elif kind == "press_escape":
        platform.press("escape")
    elif kind == "scroll_down":
        platform.scroll("down")
    elif kind == "scroll_up":
        platform.scroll("up")
    elif kind != "wait":
        raise ValueError(f"unknown decision kind {kind!r}")
    return ""


def find_field(platform: Platform, before: Item) -> Item | None:
    """The same field in a fresh snapshot, for verify_typed."""
    time.sleep(SETTLE)
    return next(
        (it for it in platform.snapshot().items if (it.role, it.label) == (before.role, before.label)), None
    )


def settle(platform: Platform, before: Snapshot) -> Snapshot:
    """Give the app a moment, then poll until the screen differs from `before` or the budget runs out."""
    time.sleep(SETTLE)
    deadline = time.perf_counter() + SETTLE_MAX
    while True:
        after = platform.snapshot()
        if changed(before, after) or time.perf_counter() >= deadline:
            return after
        time.sleep(POLL)


def write_run(
    state: RunState, goal: str, act: bool, utterance: str | None, limit: int, timings: list[dict[str, float]]
) -> None:
    steps = [
        {
            "step": s.step,
            "kind": s.decision.kind,
            "item": s.decision.item.index if s.decision.item else None,
            "label": s.decision.item.label if s.decision.item else None,
            "confidence": s.decision.confidence,
            "kind_confidence": s.decision.kind_confidence,
            "item_confidence": s.decision.item_confidence,
            "latency_ms": s.decision.latency_ms,
            "executed": s.executed,
            "changed": s.changed,
            "note": s.note,
        }
        for s in state.steps
    ]
    summary = {
        "goal": goal,
        "utterance": utterance,
        "outcome": state.outcome,
        "seconds": state.seconds,
        "steps": steps,
        "history": state.history,
        "timings": timings,
        "answers": state.answers,
        "config": {"act": act, "max_steps": limit, "min_confidence": config.GOAL_MIN_CONFIDENCE},
    }
    (state.run_dir / "run.json").write_text(json.dumps(summary, indent=2, default=str))
