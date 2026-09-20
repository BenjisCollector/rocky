"""Shared data types. Every module codes against these, nothing else."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Tier = Literal["fast", "goal"]


@dataclass
class Item:
    """One actionable thing on screen, numbered for the model. Source is 'ax' (macOS Accessibility),
    'uia' (Windows UI Automation) or 'ocr'. Coordinates are screen points, origin top-left."""

    index: int
    role: str
    label: str
    x: float
    y: float
    w: float
    h: float
    source: str = "ax"
    value: str = ""
    focused: bool = False
    enabled: bool = True

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)


@dataclass
class Snapshot:
    """One atomic read of the screen state."""

    app: str
    window_title: str
    items: list[Item]
    text: str = ""  # visible text, capped by the platform
    screen_w: int = 0
    screen_h: int = 0
    taken_ms: int = 0


@dataclass
class Plan:
    """What the router decided to do with one utterance."""

    kind: str  # open_app, open_url, search, shortcut, scroll, volume, media, system, type, goal, none
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    needs_screen: bool = False
    addressed: bool = True
    compound: bool = False
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def tier(self) -> Tier:
        return "goal" if self.kind == "goal" or self.needs_screen else "fast"


@dataclass
class Decision:
    """One step of the goal loop: what to do on the current snapshot."""

    kind: str  # click_item, type_text, press_enter, press_escape, scroll_down, scroll_up, wait, done, none
    item: Item | None = None
    confidence: float = 0.0
    kind_confidence: float = 0.0
    item_confidence: float = 0.0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    step: int
    decision: Decision
    executed: bool
    changed: bool
    note: str = ""
