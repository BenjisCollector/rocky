"""Regression checks for the four High findings in docs/REVIEW-2026-09-20.md."""

import pytest

from rocky import fast, risk, writer
from rocky.loop import run_goal
from rocky.models import Decision, Plan
from rocky.platform.fake import Fake

from .conftest import FakeJev, button, choice


def test_h1_writer_refuses_control_characters():
    assert writer.parse('{"text": "Thanks,\\nYousef"}') == ""
    assert writer.parse('{"text": "a\\tb"}') == ""
    assert writer.parse('{"text": "Thanks, Yousef"}') == "Thanks, Yousef"


def test_h2_fast_path_refuses_keystrokes_when_focus_moved():
    platform = Fake()
    platform.front = "Microsoft Teams"  # focus moved after the utterance was routed in TextEdit
    plan = Plan(kind="shortcut", args={"shortcut": "copy"}, confidence=0.99, raw={"front": "TextEdit"})
    reply = fast.execute(plan, platform, act=True)
    assert "Focus moved" in reply and not [c for c in platform.calls if c[0] == "press"]


def test_h2_goal_loop_refuses_action_when_focus_moved():
    take = button(1, "Take Photo")

    class Stolen(Fake):
        def frontmost_app(self):
            return "Microsoft Teams"  # differs from the snapshot's app at act time

    platform = Stolen(items=[take])
    step = {
        "kind": choice("click_item", ["click_item", "done", "none"], 0.9),
        "click_target": choice("1", ["1"], 0.9),
    }
    state = run_goal(
        platform, FakeJev(dict(step), dict(step), dict(step)), "take a photo", act=True, log=lambda _: None
    )
    assert not [c for c in platform.calls if c[0] == "click"]
    assert "focus moved" in state.steps[0].note


def test_h3_fast_type_refuses_focused_secure_field():
    platform = Fake()
    platform.focused = "AXSecureTextField"
    plan = Plan(kind="type", args={"text": "hunter2"}, confidence=0.99, raw={"front": platform.front})
    assert "never type" in fast.execute(plan, platform, act=True)
    assert not [c for c in platform.calls if c[0] == "type_text"]


@pytest.mark.parametrize("buttons", ["Cancel | Delete", "Move to Bin", "Confirm", "Publish"])
def test_h4_press_enter_with_destructive_default_button_needs_confirmation(buttons):
    decision = Decision(kind="press_enter", confidence=0.9, raw={"buttons": buttons})
    assert risk.classify(decision) == "destructive"
    assert (
        risk.classify(Decision(kind="press_enter", confidence=0.9, raw={"buttons": "OK | Next"}))
        == "reversible"
    )
