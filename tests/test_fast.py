from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field

import pytest

from rocky import config
from rocky.fast import execute
from rocky.models import Item, Plan
from rocky.platform.fake import Fake


def plan(kind: str, conf: float = 0.9, **args) -> Plan:
    return Plan(kind=kind, args=args, confidence=conf)


@pytest.mark.parametrize(
    ("p", "reply", "calls"),
    [
        (plan("open_app", app="Photo Booth"), "Opening Photo Booth.", [("open_app", "Photo Booth")]),
        (plan("open_app", app="none"), "I don't see that app.", []),
        (
            plan("open_url", url="https://github.com", site="github"),
            "Opening github.",
            [("open_url", "https://github.com")],
        ),
        (
            plan("search", query="lofi beats", engine="youtube"),
            "Searching youtube for lofi beats.",
            [("open_url", "https://www.youtube.com/results?search_query=lofi+beats")],
        ),
        (plan("shortcut", shortcut="copy"), "Copy.", [("press", "c", ("cmd",), 1)]),
        (plan("shortcut", shortcut="none"), "I don't know that shortcut.", []),
        (plan("scroll", op="down"), "", [("scroll", "down", 3)]),
        (plan("volume", op="up"), "volume up", [("volume", "up")]),
        (plan("media", op="next"), "", [("media", "next")]),
        (plan("system", op="dark_mode"), "dark_mode", [("system", "dark_mode")]),
        (plan("type", text="hello there"), "Done.", [("type_text", "hello there")]),
        (plan("none"), "", []),
    ],
)
def test_fast_kinds_map_to_platform_calls(p, reply, calls):
    fake = Fake()
    assert execute(p, fake) == reply
    assert fake.calls == calls


def test_low_confidence_and_not_addressed_do_nothing():
    fake = Fake()
    assert execute(plan("open_app", conf=0.2, app="Notes"), fake) == "Not sure what you meant."
    assert execute(Plan(kind="open_app", args={"app": "Notes"}, confidence=0.9, addressed=False), fake) == ""
    assert fake.calls == []


def test_destructive_plan_requires_confirmation_and_refuses_low_confidence():
    fake = Fake()
    lock = plan("system", conf=0.6, op="lock")
    reply = execute(lock, fake, prompt_fn=lambda _: "yes")
    assert reply.startswith("That looks destructive") and fake.calls == []

    lock.confidence = 0.95
    assert execute(lock, fake, prompt_fn=lambda _: "no") == "Cancelled."
    assert fake.calls == []
    assert execute(lock, fake, prompt_fn=lambda _: "yes") == "lock"
    assert fake.calls == [("system", "lock")]


def test_secret_field_is_never_typed_into():
    field_ = Item(index=1, role="AXSecureTextField", label="Password", x=0, y=0, w=10, h=10, focused=True)
    fake = Fake(items=[field_])
    assert execute(plan("type", text="hunter2"), fake) == "I never type into password or card fields."
    assert fake.calls == []
    assert (
        execute(plan("type", text="my password is hunter2"), Fake())
        == "I never type passwords or card details."
    )


@dataclass
class _RunState:
    outcome: str = "done"
    steps: list = field(default_factory=lambda: [1, 2, 3])


def test_goal_tier_hands_off_to_run_goal(monkeypatch):
    seen = {}

    def run_goal(platform, jev, goal, act, log):
        seen.update(platform=platform, jev=jev, goal=goal, act=act)
        return _RunState()

    monkeypatch.setitem(sys.modules, "rocky.loop", types.SimpleNamespace(run_goal=run_goal))
    fake = Fake()
    p = plan("goal", goal="reply to the last email")
    assert execute(p, fake, act=True, jev="J") == "Done after 3 steps."
    assert seen == {"platform": fake, "jev": "J", "goal": "reply to the last email", "act": True}
    typed = Plan(
        kind="type", args={"text": "hi", "goal": "type hi in the subject"}, confidence=0.9, needs_screen=True
    )
    assert execute(typed, fake, act=False, jev="J") == "Done after 3 steps."
    assert seen["goal"] == "type hi in the subject" and seen["act"] is False


def test_destructive_goal_is_gated_before_the_loop_runs(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "rocky.loop", types.SimpleNamespace(run_goal=lambda *a: pytest.fail("ran"))
    )
    p = plan("goal", conf=0.95, goal="delete all my emails")
    assert execute(p, Fake(), jev="J", prompt_fn=lambda _: "nope") == "Cancelled."
    p.confidence = config.DESTRUCTIVE_MIN_CONFIDENCE - 0.01
    assert execute(p, Fake(), jev="J", prompt_fn=lambda _: "yes").startswith("That looks destructive")
