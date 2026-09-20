from __future__ import annotations

import pytest

from rocky import config
from rocky.models import Decision, Item, Plan
from rocky.risk import check, classify, confirm, is_secret_field, requires_confirmation


def item(label: str) -> Item:
    return Item(index=1, role="AXButton", label=label, x=0, y=0, w=1, h=1)


@pytest.mark.parametrize(
    ("action", "want"),
    [
        (Plan(kind="system", args={"op": "lock"}), "destructive"),
        (Plan(kind="system", args={"op": "sleep_display"}), "destructive"),
        (Plan(kind="system", args={"op": "dark_mode"}), "reversible"),
        (Plan(kind="shortcut", args={"shortcut": "delete_line"}), "destructive"),
        (Plan(kind="shortcut", args={"shortcut": "send_message"}), "destructive"),
        (Plan(kind="shortcut", args={"shortcut": "copy"}), "reversible"),
        (Plan(kind="goal", args={"goal": "delete all my emails"}), "destructive"),
        (Plan(kind="goal", args={"goal": "buy the blue one"}), "destructive"),
        (Plan(kind="goal", args={"goal": "transfer 50 dollars to bob"}), "destructive"),
        (Plan(kind="goal", args={"goal": "read me the last email"}), "reversible"),
        (Plan(kind="type", args={"text": "delete this"}), "reversible"),  # typing a word is not deleting
        (Plan(kind="open_app", args={"app": "Notes"}), "reversible"),
        (Decision(kind="click_item", item=item("Empty Trash")), "destructive"),
        (Decision(kind="click_item", item=item("Send")), "destructive"),
        (Decision(kind="click_item", item=item("Pay now")), "destructive"),
        (Decision(kind="click_item", item=item("Compose")), "reversible"),
        (Decision(kind="type_text", item=item("Password")), "destructive"),
        (Decision(kind="type_text", item=item("Subject")), "reversible"),
        (Decision(kind="press_enter"), "reversible"),
        ("click_item [7] Empty Trash", "destructive"),
        ("type_text [3] Message", "reversible"),
        ("remove the second item from the cart", "destructive"),
    ],
)
def test_classify(action, want):
    assert classify(action) == want
    assert requires_confirmation(action) is (want == "destructive")


@pytest.mark.parametrize(
    ("label", "secret"),
    [
        ("Password", True),
        ("Confirm passphrase", True),
        ("PIN", True),
        ("Card number", True),
        ("CVV", True),
        ("Social Security Number", True),
        ("API key", True),
        ("Username", False),
        ("Search", False),
        ("Spinach", False),  # 'pin' inside a word does not count
        ("", False),
        (None, False),
    ],
)
def test_is_secret_field(label, secret):
    assert is_secret_field(label) is secret


@pytest.mark.parametrize(
    ("reply", "ok"),
    [("yes", True), ("Yes.", True), ("confirm", True), ("y", False), ("no", False), ("", False)],
)
def test_confirm_only_accepts_yes_or_confirm(reply, ok):
    assert confirm(lambda _: reply) is ok


def test_confirm_treats_eof_as_no():
    def eof(_):
        raise EOFError

    assert confirm(eof) is False


def test_check_reversible_passes_without_asking():
    assert check(Plan(kind="open_app", args={"app": "Notes"}), 0.5, lambda _: pytest.fail("asked")) == ""


def test_check_destructive_needs_confidence_then_a_yes():
    lock = Plan(kind="system", args={"op": "lock"})
    low = config.DESTRUCTIVE_MIN_CONFIDENCE - 0.01
    assert check(lock, low, lambda _: "yes").startswith("That looks destructive")
    assert check(lock, 0.99, lambda _: "no") == "Cancelled."
    assert check(lock, 0.99, lambda _: "yes") == ""


def test_check_never_types_into_a_secret_field_even_with_confirmation():
    d = Decision(kind="type_text", item=item("Password"), confidence=1.0)
    assert check(d, 1.0, lambda _: "yes") == "I never type into password or card fields."
