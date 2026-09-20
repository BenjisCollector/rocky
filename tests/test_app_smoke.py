"""The Mac app shell builds its menu and panel without running the AppKit loop. macOS with a display only."""

from __future__ import annotations

import sys

import pytest

from rocky.models import Plan

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


@pytest.fixture(scope="module")
def controller():
    from AppKit import NSScreen

    if NSScreen.mainScreen() is None:
        pytest.skip("no display")
    from rocky import app

    c = app.AppController.alloc().init()
    c.build()  # status item, overlay and panel; nothing is shown and no voice thread starts
    return c


def test_status_menu_titles(controller):
    titles = [i.title() for i in controller.menu.itemArray() if not i.isSeparatorItem()]
    assert titles == [
        "Show Control Panel",
        "Pause Listening",
        "Hide Rocky",
        "Dry Run for Screen Tasks",
        "Quit Rocky",
    ]
    assert controller.status_item.button().image().isValid()


def test_panel_hierarchy(controller):
    from rocky import app

    size = controller.panel.frame().size
    assert (size.width, size.height) == (app.PW, app.PH)
    assert not controller.panel.isVisible()
    assert len(controller.rows) == 5
    assert set(controller.perm_views) == {"accessibility", "microphone", "input_monitoring"}
    assert controller.segment.segmentCount() == 2
    assert controller.root.subviews().count() > 20


def test_history_keeps_five_newest_first(controller):
    from rocky import app

    plan = Plan(kind="open_app", args={"app": "Photo Booth"}, confidence=1.0, latency_ms=412)
    assert app.plan_line(plan) == "open_app(Photo Booth) 1.00 412ms"
    for i in range(7):
        controller.record(f"command {i}", app.plan_line(plan), "Opening Photo Booth.")
    assert controller.rows[0][0].stringValue() == "command 6"
    assert controller.rows[4][0].stringValue() == "command 2"
    assert controller.rows[0][1].stringValue() == "open_app(Photo Booth) 1.00 412ms"
    assert controller.sub.stringValue() == "Opening Photo Booth."


def test_state_and_pause_drive_the_header(controller):
    controller.applyState_("hearing")
    assert controller.status.stringValue() == "Hearing"
    controller.setPaused(True)
    assert controller.status.stringValue() == "Paused"
    assert controller.pause_item.title() == "Resume Listening"
    controller.setPaused(False)
    controller.applyState_("error")
    assert controller.status.stringValue() == "Error"
    controller.applyState_("bogus")  # unknown states are ignored
    assert controller.status.stringValue() == "Error"


def test_dry_run_flag_syncs_menu_and_segment(controller):
    controller.setAct(True)
    assert controller.segment.selectedSegment() == 1
    assert controller.dry_item.state() == 0
    controller.setAct(False)
    assert controller.segment.selectedSegment() == 0
    assert controller.dry_item.state() == 1
