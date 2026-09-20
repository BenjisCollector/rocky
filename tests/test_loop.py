import json

import pytest

from rocky import config
from rocky.decide import build_questions
from rocky.loop import run_goal
from rocky.models import Item
from rocky.platform.fake import Fake

from .conftest import FakeJev, button, choice, textfield

pytestmark = pytest.mark.usefixtures("goal_loop")

GOAL = "Take a photo in Photo Booth"
TAKE = button(1, "Take Photo", x=100, y=100)  # center (125, 110)
SHOT = Item(index=1, role="AXImage", label="Photo taken", x=0, y=0, w=300, h=300)


def kinds(items):
    return list(build_questions(items)["kind"]["criteria"])


class Scripted(Fake):
    """Fake whose items advance to the next screen after every click."""

    def __init__(self, *screens):
        super().__init__(items=list(screens[0]))
        self.front = "Photo Booth"
        self.screens = list(screens[1:])

    def click(self, x, y, button="left", clicks=1):
        super().click(x, y, button, clicks)
        if self.screens:
            self.items = list(self.screens.pop(0))


class Ticking(Fake):
    """Fake whose screen differs on every snapshot, so nothing ever stalls."""

    def snapshot(self, max_items=120):
        self.tick = getattr(self, "tick", 0) + 1
        self.items = [button(1, f"Frame {self.tick}")]
        return super().snapshot(max_items)


def test_two_step_run_clicks_once_and_ends_done():
    platform = Scripted([TAKE], [SHOT])
    jev = FakeJev(
        {"kind": choice("click_item", kinds([TAKE]), 0.9), "click_target": choice("1", ["1"], 0.84)},
        {"kind": choice("done", kinds([SHOT]), 0.95), "click_target": choice("1", ["1"], 0.5)},
    )
    lines = []
    state = run_goal(platform, jev, GOAL, act=True, log=lines.append)
    assert state.outcome == "done"
    assert platform.calls == [("click", 125.0, 110.0, "left", 1)]
    assert [s.executed for s in state.steps] == [True, False]
    assert state.steps[0].changed is True
    assert state.history == ["click_item [1] Take Photo: changed"]
    assert lines[0].startswith("step 1  click_item [1] Take Photo  conf=0.84  ")
    assert lines[0].endswith("ms")
    assert jev.calls[1][0]["recent_actions"] == state.history


def test_nothing_changing_ends_stalled():
    platform = Fake(items=[TAKE])
    step = {"kind": choice("click_item", kinds([TAKE]), 0.9), "click_target": choice("1", ["1"], 0.9)}
    state = run_goal(
        platform, FakeJev(dict(step), dict(step), dict(step)), GOAL, act=True, log=lambda _: None
    )
    assert state.outcome == "stalled"
    assert len(state.steps) == 2 and len(platform.calls) == 2


def test_low_confidence_stops_before_acting():
    platform = Fake(items=[TAKE])
    jev = FakeJev({"kind": choice("click_item", kinds([TAKE]), 0.9), "click_target": choice("1", ["1"], 0.3)})
    state = run_goal(platform, jev, GOAL, act=True, log=lambda _: None)
    assert state.outcome == "low confidence"
    assert platform.calls == []


def test_dry_run_executes_nothing():
    platform = Fake(items=[TAKE])
    jev = FakeJev({"kind": choice("click_item", kinds([TAKE]), 0.9), "click_target": choice("1", ["1"], 0.9)})
    lines = []
    state = run_goal(platform, jev, GOAL, act=False, log=lines.append)
    assert state.outcome == "dry run"
    assert platform.calls == []
    assert "would do: click_item [1] Take Photo" in lines


def test_none_ends_nothing_helps_and_step_limit_is_respected():
    platform = Fake(items=[TAKE])
    state = run_goal(
        platform, FakeJev({"kind": choice("none", kinds([TAKE]))}), GOAL, act=True, log=lambda _: None
    )
    assert state.outcome == "nothing helps"
    waits = [{"kind": choice("wait", kinds([TAKE]))} for _ in range(3)]
    state = run_goal(Ticking(), FakeJev(*waits), GOAL, act=True, log=lambda _: None, max_steps=3)
    assert state.outcome == "step limit" and len(state.steps) == 3
    assert all(s.executed and s.changed for s in state.steps)


def test_secret_field_is_never_typed():
    field = textfield(1, "Password")
    platform = Fake(items=[field])
    jev = FakeJev({"kind": choice("type_text", kinds([field]), 0.9), "type_target": choice("1", ["1"], 0.9)})
    state = run_goal(platform, jev, GOAL, act=True, log=lambda _: None, max_steps=1)
    assert state.outcome == "step limit"
    assert not any(c[0] == "type_text" for c in platform.calls)
    assert state.steps[0].executed is False and state.steps[0].note == "refused: secret field"


def test_type_text_composes_clicks_types_and_verifies():
    field = textfield(1, "Search")
    platform = Fake(items=[field])
    jev = FakeJev(
        {"kind": choice("type_text", kinds([field]), 0.9), "type_target": choice("1", ["1"], 0.9)},
        {"ok": {"noul": 0.9}},
        {"kind": choice("done", kinds([field]), 0.9)},
    )
    state = run_goal(
        platform, jev, "Search for cats", act=True, log=lambda _: None, utterance="search for cats"
    )
    assert state.outcome == "done"
    assert platform.calls == [("click", 200.0, 212.0, "left", 1), ("type_text", "text for Search")]
    assert state.steps[0].note == "typed 'text for Search', verified 0.90"


def test_needs_confirmation_without_approval(monkeypatch):
    from rocky import risk

    monkeypatch.setattr(risk, "requires_confirmation", lambda action: True)
    platform = Fake(items=[TAKE])
    step = {"kind": choice("click_item", kinds([TAKE]), 0.9), "click_target": choice("1", ["1"], 0.9)}
    state = run_goal(platform, FakeJev(dict(step)), GOAL, act=True, log=lambda _: None)
    assert state.outcome == "needs confirmation" and platform.calls == []
    platform = Scripted([TAKE], [SHOT])
    jev = FakeJev(dict(step), {"kind": choice("done", kinds([SHOT]))})
    state = run_goal(platform, jev, GOAL, act=True, log=lambda _: None, confirm=lambda what: True)
    assert state.outcome == "done" and len(platform.calls) == 1


def test_run_json_is_written_with_the_outcome():
    platform = Fake(items=[TAKE])
    jev = FakeJev({"kind": choice("click_item", kinds([TAKE]), 0.9), "click_target": choice("1", ["1"], 0.9)})
    state = run_goal(platform, jev, GOAL, act=False, log=lambda _: None)
    assert state.run_dir.parent == config.RUNS_DIR
    run = json.loads((state.run_dir / "run.json").read_text())
    assert run["goal"] == GOAL and run["outcome"] == "dry run"
    assert run["steps"][0]["kind"] == "click_item" and run["steps"][0]["label"] == "Take Photo"
    assert set(run["timings"][0]) == {"perceive", "decide"}
    assert run["config"] == {"act": False, "max_steps": config.MAX_STEPS, "min_confidence": 0.5}
    assert sorted(p.name for p in state.run_dir.iterdir()) == ["run.json"]
