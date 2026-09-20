"""Offline test support. Stubs for rocky.jev, rocky.risk and rocky.writer are installed only when the real
module is absent, so these tests run before the other agents' files land and keep running after."""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from rocky.models import Item


def _stub(name: str, **attrs) -> None:
    if importlib.util.find_spec(name) is not None:
        return
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module


def _validate_choice(answer, allowed):
    if not isinstance(answer, dict) or answer.get("choice") not in allowed:
        raise ValueError("choice outside the offered ids")
    return answer


class _Jev:
    def ask(self, state, questions):
        raise RuntimeError("stub Jev has no network")


_stub("rocky.jev", Jev=_Jev, validate_choice=_validate_choice)
_stub("rocky.risk", requires_confirmation=lambda action: False, is_secret_field=lambda label: False)
_stub("rocky.writer", compose=lambda goal, field_label, utterance: f"text for {field_label}")


def choice(pick: str, ids: list[str], confidence: float = 0.9) -> dict:
    """A well-formed choice answer: the pick gets `confidence`, the rest share the remainder."""
    rest = (1 - confidence) / max(len(ids) - 1, 1)
    return {
        "choice": pick,
        "confidence": confidence,
        "probabilities": {i: (confidence if i == pick else rest) for i in ids},
    }


class FakeJev:
    """Returns canned answers, one per ask() call, in order."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[dict, dict]] = []

    def ask(self, state: dict, questions: dict) -> dict:
        self.calls.append((state, questions))
        return self.answers.pop(0)


def button(index: int, label: str, x: float = 100, y: float = 100, **kw) -> Item:
    return Item(index=index, role="AXButton", label=label, x=x, y=y, w=50, h=20, **kw)


def textfield(index: int, label: str, x: float = 100, y: float = 200, **kw) -> Item:
    return Item(index=index, role="AXTextField", label=label, x=x, y=y, w=200, h=24, **kw)


@pytest.fixture
def goal_loop(monkeypatch, tmp_path):
    """For the goal-loop tests only: no sleeps, runs under tmp_path, floors and the sibling modules pinned."""
    from rocky import config, loop, risk, writer

    monkeypatch.setattr(loop, "SETTLE", 0.0)
    monkeypatch.setattr(loop, "SETTLE_MAX", 0.0)
    monkeypatch.setattr(loop, "POLL", 0.0)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "GOAL_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr(risk, "requires_confirmation", lambda action: False)
    monkeypatch.setattr(risk, "is_secret_field", lambda label: label.lower() == "password")
    monkeypatch.setattr(writer, "compose", lambda goal, field_label, utterance: f"text for {field_label}")
