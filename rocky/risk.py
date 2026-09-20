"""Reversible or destructive, decided in code. The model never classifies its own risk."""

from __future__ import annotations

import re
from collections.abc import Callable

from . import config
from .models import Decision, Plan

DESTRUCTIVE_SYSTEM_OPS = frozenset({"lock", "sleep", "sleep_display", "shutdown", "restart", "empty_trash"})
_DESTRUCTIVE_SHORTCUT = re.compile(r"delete|send|trash|quit|force", re.IGNORECASE)
_DESTRUCTIVE_WORDS = re.compile(
    r"\b(?:delete|remove|erase|discard|trash|empty|pay|buy|purchase|order|checkout|send|submit|transfer|"
    r"wipe|format|uninstall|unsubscribe|cancel|shutdown|shut down|restart|reboot|"
    r"move to bin|move to trash|confirm|clear|publish|post|approve|sign)\b",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r"password|passcode|passphrase|\bpin\b|cvv|cvc|card number|credit card|debit|\bssn\b|social security|"
    r"secret|token|api key|security code",
    re.IGNORECASE,
)

YES = frozenset({"yes", "confirm"})


def is_secret_field(label: str | None) -> bool:
    """True when a field label suggests a password, PIN, or payment card. Nothing is ever typed there."""
    return bool(label and _SECRET.search(label))


_SECRET_ROLES = re.compile(r"secure|password", re.IGNORECASE)


def is_secret_role(role: str | None) -> bool:
    """True for AXSecureTextField on macOS or a UIA control flagged IsPassword on Windows."""
    return bool(role and _SECRET_ROLES.search(role))


def classify(action: Plan | Decision | str) -> str:
    """'reversible' or 'destructive'. Accepts a router Plan, a goal-loop Decision, or a plain description
    such as 'click_item [7] Empty Trash' or a goal sentence."""
    if isinstance(action, Plan):
        a = action.args
        if action.kind == "system" and a.get("op") in DESTRUCTIVE_SYSTEM_OPS:
            return "destructive"
        if action.kind == "shortcut" and _DESTRUCTIVE_SHORTCUT.search(a.get("shortcut", "")):
            return "destructive"
        if action.tier == "goal" and _DESTRUCTIVE_WORDS.search(a.get("goal", "")):
            return "destructive"
        return "reversible"
    if isinstance(action, Decision):
        label = action.item.label if action.item else ""
        if action.kind == "click_item" and _DESTRUCTIVE_WORDS.search(label):
            return "destructive"
        if action.kind == "type_text" and is_secret_field(label):
            return "destructive"
        if action.kind == "press_enter" and _DESTRUCTIVE_WORDS.search(action.raw.get("buttons", "")):
            return "destructive"  # Return fires the default button, so the visible buttons decide
        return "reversible"
    return "destructive" if _DESTRUCTIVE_WORDS.search(action) else "reversible"


def requires_confirmation(action: Plan | Decision | str) -> bool:
    return classify(action) == "destructive"


def confirm(prompt_fn: Callable[[str], str]) -> bool:
    """Ask once; only a plain 'yes' or 'confirm' counts."""
    try:
        reply = prompt_fn("This looks destructive. Say or type yes to continue: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return (reply or "").strip().lower().rstrip(".!") in YES


def check(action: Plan | Decision | str, confidence: float, prompt_fn: Callable[[str], str] = input) -> str:
    """'' when the action may proceed, otherwise the reason it may not. Destructive actions always need
    confidence >= DESTRUCTIVE_MIN_CONFIDENCE and a confirmation; secret fields are never typed into."""
    if (
        isinstance(action, Decision)
        and action.kind == "type_text"
        and action.item
        and is_secret_field(action.item.label)
    ):
        return "I never type into password or card fields."
    if not requires_confirmation(action):
        return ""
    if confidence < config.DESTRUCTIVE_MIN_CONFIDENCE:
        return f"That looks destructive and I am only {confidence:.0%} sure. Nothing done."
    if not confirm(prompt_fn):
        return "Cancelled."
    return ""
