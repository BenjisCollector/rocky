import pytest

from rocky.decide import build_questions, decide, is_editable, verify_typed
from rocky.models import Item, Snapshot

from .conftest import FakeJev, button, choice, textfield

GOAL = "Search for cats"
ITEMS = [button(1, "Take Photo"), textfield(2, "Search", value="")]
SNAP = Snapshot(app="Safari", window_title="Start Page", items=ITEMS, text="Favorites  Reading List")


def kinds(items):
    return list(build_questions(items)["kind"]["criteria"])


def test_questions_offer_click_and_type_heads_only_when_targets_exist():
    q = build_questions(ITEMS)
    assert set(q) == {"kind", "click_target", "type_target"}
    assert list(q["click_target"]["criteria"]) == ["1", "2"]
    assert list(q["type_target"]["criteria"]) == ["2"]
    assert q["click_target"]["criteria"]["1"] == "[1] AXButton Take Photo"
    assert "type_text" not in kinds([button(1, "Take Photo")])
    assert "click_item" not in kinds([]) and "type_text" not in kinds([])
    assert "done" in kinds([]) and "none" in kinds([])


def test_is_editable_covers_macos_and_windows_roles():
    assert is_editable(Item(index=1, role="AXTextArea", label="", x=0, y=0, w=1, h=1))
    assert is_editable(Item(index=1, role="EditControl", label="", x=0, y=0, w=1, h=1))
    assert is_editable(Item(index=1, role="ComboBoxControl", label="", x=0, y=0, w=1, h=1))
    assert not is_editable(Item(index=1, role="AXButton", label="", x=0, y=0, w=1, h=1))


def test_decide_uses_only_the_head_matching_the_kind_and_takes_min_confidence():
    jev = FakeJev(
        {
            "kind": choice("type_text", kinds(ITEMS), 0.9),
            "click_target": choice("1", ["1", "2"], 0.99),  # the unused head cannot steer the decision
            "type_target": choice("2", ["2"], 0.7),
        }
    )
    d = decide(jev, GOAL, SNAP, ITEMS, ["click_item [1] Take Photo: no change"])
    assert d.kind == "type_text" and d.item.label == "Search"
    assert d.confidence == pytest.approx(0.7) and d.kind_confidence == 0.9 and d.item_confidence == 0.7
    state, questions = jev.calls[0]
    assert (
        state["goal"] == GOAL
        and state["app"] == "Safari"
        and state["recent_actions"] == ["click_item [1] Take Photo: no change"]
    )
    assert state["items"][0] == {"i": 1, "role": "AXButton", "label": "Take Photo"}
    assert set(questions) == {"kind", "click_target", "type_target"}


def test_decide_without_target_keeps_kind_confidence():
    jev = FakeJev({"kind": choice("done", kinds(ITEMS), 0.8)})
    d = decide(jev, GOAL, SNAP, ITEMS, [])
    assert d.kind == "done" and d.item is None and d.confidence == 0.8


def test_choice_outside_criteria_is_refused():
    with pytest.raises(ValueError):
        decide(
            FakeJev({"kind": choice("open_browser", [*kinds(ITEMS), "open_browser"])}), GOAL, SNAP, ITEMS, []
        )
    with pytest.raises(ValueError):
        decide(
            FakeJev(
                {"kind": choice("click_item", kinds(ITEMS)), "click_target": choice("9", ["1", "2", "9"])}
            ),
            GOAL,
            SNAP,
            ITEMS,
            [],
        )
    with pytest.raises(ValueError):  # a type target that is not editable
        decide(
            FakeJev({"kind": choice("type_text", kinds(ITEMS)), "type_target": choice("1", ["1", "2"])}),
            GOAL,
            SNAP,
            ITEMS,
            [],
        )


def test_verify_typed_reads_the_noul():
    jev = FakeJev({"ok": {"noul": 0.83}})
    field = textfield(2, "Search")
    after = textfield(2, "Search", value="cats")
    assert verify_typed(jev, GOAL, field, "cats", after) == pytest.approx(0.83)
    state, questions = jev.calls[0]
    assert state["text_typed"] == "cats" and state["field_value_now"] == "cats"
    assert questions["ok"]["type"] == "noul"
