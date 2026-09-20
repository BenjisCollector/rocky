"""Real Accessibility reads on macOS. The read-only test always runs on darwin when Accessibility is
granted. The test that launches TextEdit steals focus and types nothing, but it does bring a window to the
front, so it only runs with ROCKY_LIVE=1."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


@pytest.fixture
def macos():
    from rocky.platform.macos import MacOS

    p = MacOS()
    if not p.check_permissions()["accessibility"]:
        pytest.skip("Accessibility not granted for this terminal app")
    return p


def test_snapshot_of_frontmost_window_is_measured_and_labelled(macos):
    snap = macos.snapshot(max_items=50)
    assert snap.taken_ms > 0
    assert snap.screen_w > 0 and snap.screen_h > 0
    assert snap.app
    assert len(snap.items) <= 50
    for i in snap.items:
        assert i.label and i.role.startswith("AX") and i.source == "ax"
        assert i.w >= 4 and i.h >= 4
    assert [i.index for i in snap.items] == list(range(1, len(snap.items) + 1))


def test_installed_apps_and_shortcuts(macos):
    apps = macos.installed_apps()
    assert "Finder" not in apps  # Finder is not a .app in the scanned folders; the router adds it if wanted
    assert any(a in apps for a in ("Safari", "TextEdit", "Notes"))
    assert apps == sorted(apps, key=str.lower) and len(apps) == len(set(apps))
    shortcuts = macos.shortcuts()
    assert len(shortcuts) >= 30 and shortcuts["copy"] == ("c", ["cmd"])


@pytest.mark.skipif(os.environ.get("ROCKY_LIVE") != "1", reason="launches TextEdit; set ROCKY_LIVE=1")
def test_open_textedit_and_snapshot(macos):
    macos.open_app("TextEdit")
    try:
        assert macos.focus_app("TextEdit", timeout=5)
        time.sleep(1.5)
        snap = macos.snapshot()
        assert snap.app == "TextEdit"
        assert snap.taken_ms > 0
        assert any(i.label for i in snap.items)
    finally:
        subprocess.run(
            ["osascript", "-e", 'tell application "TextEdit" to quit'], capture_output=True, check=False
        )
