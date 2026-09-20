from rocky.models import Item, Snapshot
from rocky.perceive import changed, perceive
from rocky.platform.fake import Fake

from .conftest import button

GOAL = "Take a photo in Photo Booth"


def test_perceive_filters_echoes_disabled_and_duplicates_then_renumbers():
    items = [
        Item(index=0, role="AXStaticText", label="rocky: take a photo in Photo Booth", x=0, y=0, w=300, h=20),
        button(1, "Take Photo"),
        button(2, "Effects", y=150, enabled=False),
        button(3, "Take Photo"),  # same role, label and center as item 1
        button(4, "Take Photo", x=400),  # same label, different place: kept
    ]
    snapshot, kept = perceive(Fake(items=items), GOAL)
    assert [it.label for it in kept] == ["Take Photo", "Take Photo"]
    assert [it.index for it in kept] == [1, 2]
    assert kept[1].x == 400
    assert len(snapshot.items) == 5  # the raw snapshot is returned untouched


def test_perceive_caps_items():
    items = [button(i, f"B{i}", x=i * 60) for i in range(10)]
    _, kept = perceive(Fake(items=items), GOAL, max_items=3)
    assert len(kept) == 3


def test_changed_compares_app_title_labels_and_focus():
    a = Snapshot(app="Photo Booth", window_title="", items=[button(1, "Take Photo")])
    same = Snapshot(app="Photo Booth", window_title="", items=[button(1, "Take Photo", x=999)])
    assert not changed(a, same)  # position alone is not a change
    assert changed(a, Snapshot(app="Finder", window_title="", items=a.items))
    assert changed(a, Snapshot(app="Photo Booth", window_title="Preview", items=a.items))
    assert changed(a, Snapshot(app="Photo Booth", window_title="", items=[button(1, "Retake")]))
    assert changed(
        a, Snapshot(app="Photo Booth", window_title="", items=[button(1, "Take Photo", focused=True)])
    )
