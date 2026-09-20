"""Every Platform implementation defines every method of the Protocol. Windows is imported with mocked
`uiautomation` and `win32*` modules so this runs on macOS too."""

from __future__ import annotations

import importlib
import sys
import types

import pytest

from rocky.models import Snapshot
from rocky.platform import base
from rocky.platform.fake import Fake

PROTOCOL_METHODS = sorted(
    name for name, v in vars(base.Platform).items() if callable(v) and not name.startswith("_")
)


def _mock_windows_modules(monkeypatch) -> types.ModuleType:
    auto = types.ModuleType("uiautomation")
    for name in ("win32api", "win32con", "win32gui", "win32process"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "uiautomation", auto)
    sys.modules.pop("rocky.platform.windows", None)
    return auto


def test_protocol_has_the_expected_surface():
    assert "snapshot" in PROTOCOL_METHODS and "check_permissions" in PROTOCOL_METHODS
    assert "name" in base.Platform.__annotations__


@pytest.mark.parametrize("method", PROTOCOL_METHODS)
def test_fake_defines(method):
    assert callable(getattr(Fake, method))


@pytest.mark.skipif(sys.platform != "darwin", reason="pyobjc only imports on macOS")
@pytest.mark.parametrize("method", PROTOCOL_METHODS)
def test_macos_defines(method):
    from rocky.platform.macos import MacOS

    assert MacOS.name == "macos"
    assert callable(getattr(MacOS, method))


@pytest.mark.parametrize("method", PROTOCOL_METHODS)
def test_windows_defines(method, monkeypatch):
    _mock_windows_modules(monkeypatch)
    windows = importlib.import_module("rocky.platform.windows")
    assert windows.Windows.name == "windows"
    assert callable(getattr(windows.Windows, method))


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _Control:
    def __init__(
        self, kind, name, rect, enabled=True, offscreen=False, focus=False, password=False, value=None
    ):
        self.ControlTypeName = kind
        self.Name = name
        self.BoundingRectangle = _Rect(*rect)
        self.IsEnabled = enabled
        self.IsOffscreen = offscreen
        self.HasKeyboardFocus = focus
        self.IsPassword = password
        self.AutomationId = ""
        self.HelpText = ""
        self._value = value

    def GetPattern(self, pattern_id):
        return types.SimpleNamespace(Value=self._value) if self._value is not None else None


def test_windows_walk_with_mocked_uia(monkeypatch):
    auto = _mock_windows_modules(monkeypatch)
    controls = [
        _Control("ButtonControl", "Save", (10, 10, 90, 40)),
        _Control("ButtonControl", "Save", (10, 10, 90, 40)),  # duplicate: same role, label, frame
        _Control("EditControl", "Search", (100, 10, 300, 40), focus=True, value="hello"),
        _Control("EditControl", "Password", (100, 50, 300, 80), password=True),
        _Control("ButtonControl", "Disabled", (10, 50, 90, 80), enabled=False),
        _Control("ButtonControl", "Offscreen", (10, 90, 90, 120), offscreen=True),
        _Control("ButtonControl", "Sliver", (10, 130, 90, 131)),
        _Control("ButtonControl", "Beyond the screen", (5000, 10, 5080, 40)),
        _Control("TextControl", "Some visible text", (10, 200, 200, 220)),
        _Control("PaneControl", "Layout", (0, 0, 800, 600)),
    ]
    auto.WalkControl = lambda top, includeTop=False: iter((c, 1) for c in controls)
    auto.GetMonitorsRect = lambda: [_Rect(0, 0, 1920, 1080)]
    auto.GetScreenSize = lambda: (1920, 1080)
    auto.GetForegroundControl = lambda: object()
    windows = importlib.import_module("rocky.platform.windows")
    win = windows.Windows()
    monkeypatch.setattr(win, "_foreground", lambda: (1, "Untitled - Notepad", "notepad"))
    snap = win.snapshot()
    assert isinstance(snap, Snapshot)
    assert snap.app == "Notepad" and snap.window_title == "Untitled - Notepad"
    assert [(i.index, i.role, i.label) for i in snap.items] == [
        (1, "ButtonControl", "Save"),
        (2, "EditControl", "Search"),
    ]
    assert snap.items[1].value == "hello" and snap.items[1].focused and snap.items[1].source == "uia"
    assert snap.text == "Some visible text"
    assert (snap.screen_w, snap.screen_h) == (1920, 1080)
