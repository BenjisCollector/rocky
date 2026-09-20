"""In-memory Platform for tests and dry runs. Records every call; never touches the OS."""

from __future__ import annotations

from pathlib import Path

from ..models import Item, Snapshot


class Fake:
    name = "fake"

    def __init__(self, apps: list[str] | None = None, items: list[Item] | None = None) -> None:
        self.apps = apps or ["Photo Booth", "Safari", "Notes", "System Settings"]
        self.items = items or []
        self.calls: list[tuple] = []
        self.front = "Finder"

    def _rec(self, *a):
        self.calls.append(a)

    def installed_apps(self):
        return list(self.apps)

    def frontmost_app(self):
        return self.front

    def open_app(self, name):
        self._rec("open_app", name)
        self.front = name

    def focus_app(self, name, timeout=2.0):
        self._rec("focus_app", name)
        self.front = name
        return True

    def open_url(self, url):
        self._rec("open_url", url)

    def type_text(self, text):
        self._rec("type_text", text)

    def press(self, key, modifiers=None, times=1):
        self._rec("press", key, tuple(modifiers or []), times)

    def scroll(self, direction, amount=3):
        self._rec("scroll", direction, amount)

    def click(self, x, y, button="left", clicks=1):
        self._rec("click", x, y, button, clicks)

    def volume(self, op):
        self._rec("volume", op)
        return f"volume {op}"

    def media(self, op):
        self._rec("media", op)

    def system(self, op):
        self._rec("system", op)
        return op

    def snapshot(self, max_items=120):
        return Snapshot(app=self.front, window_title="", items=list(self.items[:max_items]))

    def screenshot(self, path=None):
        return Path(path or "fake.png")

    def shortcuts(self):
        return {
            "copy": ("c", ["cmd"]),
            "paste": ("v", ["cmd"]),
            "undo": ("z", ["cmd"]),
            "close_window": ("w", ["cmd"]),
        }

    def focused_role(self):
        return getattr(self, "focused", "")

    def speak(self, text):
        self._rec("speak", text)

    def beep(self):
        self._rec("beep")

    def check_permissions(self):
        return {"accessibility": True, "microphone": True, "input_monitoring": True}
