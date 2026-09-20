from __future__ import annotations

import pytest

from rocky.fast import execute
from rocky.jev import JevError
from rocky.platform.fake import Fake
from rocky.router import domain_guess, route, split_compound, text_candidates
from tests.helpers import ScriptedJev


def test_open_photo_booth_routes_to_open_app_and_fake_records_it():
    fake = Fake()
    jev = ScriptedJev({"kind": "open_app", "app": "Photo Booth", "addressed": 0.95})
    plan = route(jev, fake, "open photo booth")
    assert plan.kind == "open_app" and plan.args == {"app": "Photo Booth"}
    assert plan.tier == "fast" and plan.addressed and not plan.compound
    assert plan.confidence == pytest.approx(0.9) and plan.latency_ms == 7
    assert execute(plan, fake) == "Opening Photo Booth."
    assert fake.calls == [("open_app", "Photo Booth")]


def test_one_request_with_the_full_fan_out_and_state():
    jev = ScriptedJev({"kind": "open_app", "app": "Notes"})
    route(jev, Fake(), "open notes")
    assert len(jev.calls) == 1
    state, questions = jev.calls[0]
    assert set(state) == {"utterance", "frontmost_app", "apps", "candidates", "recent_commands"}
    assert state["frontmost_app"] == "Finder" and "Notes" in state["apps"]
    expected = {
        "kind", "addressed", "compound", "needs_screen", "app", "site", "engine", "shortcut",
        "scroll_dir", "volume_op", "media_op", "system_op", "text",
    }  # fmt: skip
    assert set(questions) == expected
    assert set(questions["app"]["criteria"]) == {"Photo Booth", "Safari", "Notes", "System Settings", "none"}
    assert set(questions["shortcut"]["criteria"]) == {"copy", "paste", "undo", "close_window", "none"}
    assert questions["text"]["criteria"] == state["candidates"]


def test_choice_outside_offered_criteria_is_refused():
    jev = ScriptedJev({"kind": "open_app", "app": "Calculator"})  # not installed
    with pytest.raises(JevError):
        route(jev, Fake(), "open calculator")


def test_needs_screen_is_ignored_for_blind_kinds_and_kept_for_type():
    fake = Fake()
    blind = route(ScriptedJev({"kind": "volume", "volume_op": "up", "needs_screen": 0.9}), fake, "louder")
    assert not blind.needs_screen and blind.tier == "fast" and blind.args == {"op": "up"}
    typed = route(ScriptedJev({"kind": "type", "text": "c0", "needs_screen": 0.9}), fake, "type hello there")
    assert typed.needs_screen and typed.tier == "goal" and typed.args["text"] == "hello there"
    assert typed.args["goal"] == "type hello there"


def test_goal_kind_carries_the_utterance():
    plan = route(ScriptedJev({"kind": "goal"}), Fake(), "reply to the last email saying yes")
    assert plan.tier == "goal" and plan.args == {"goal": "reply to the last email saying yes"}


def test_search_uses_the_selected_candidate_and_engine():
    utt = "search youtube for lofi beats"
    cands = text_candidates(utt)
    key = next(k for k, v in cands.items() if v == "lofi beats")
    plan = route(ScriptedJev({"kind": "search", "text": key, "engine": "youtube"}), Fake(), utt)
    assert plan.args == {"query": "lofi beats", "engine": "youtube"}


def test_open_url_offers_a_domain_candidate_from_the_utterance():
    jev = ScriptedJev({"kind": "open_url", "site": "url"})
    plan = route(jev, Fake(), "open github.com")
    assert plan.args == {"url": "https://github.com", "site": "github.com"}
    assert "url" in jev.calls[0][1]["site"]["criteria"]
    catalog = route(ScriptedJev({"kind": "open_url", "site": "youtube"}), Fake(), "go to youtube")
    assert catalog.args["url"] == "https://www.youtube.com"
    nothing = route(ScriptedJev({"kind": "open_url", "site": "none", "text": "c0"}), Fake(), "open flurb")
    assert nothing.kind == "search"


def test_no_domain_candidate_when_nothing_looks_like_one():
    jev = ScriptedJev({"kind": "open_app", "app": "Notes"})
    route(jev, Fake(), "hello there")
    assert "url" not in jev.calls[0][1]["site"]["criteria"]


@pytest.mark.parametrize(
    ("utt", "want"),
    [
        ("open github.com", "github.com"),
        ("go to reddit dot com", "reddit.com"),
        ("take me to the hacker news site", "hackernews.com"),
        ("what time is it", ""),
    ],
)
def test_domain_guess(utt, want):
    assert domain_guess(utt) == want


def test_text_candidates_cut_the_payload_and_end_with_the_whole_utterance():
    c = list(text_candidates('type "hello world" in Notes and hit enter').values())
    assert c[0] == "hello world"
    assert c[-1] == 'type "hello world" in Notes'
    c = list(text_candidates("type meet me at noon in Notes").values())
    assert "meet me at noon" in c and "meet me at noon in Notes" in c
    c = list(text_candidates("google the weather in paris").values())
    assert c[0] == "the weather in paris"
    assert len(text_candidates("mumble")) == 1


def test_split_compound():
    assert split_compound("open safari and then go to youtube") == ["open safari", "go to youtube"]
    assert split_compound("open notes, then type hello") == ["open notes", "type hello"]
    assert split_compound("mute and lock the screen") == ["mute", "lock the screen"]
    assert split_compound("open safari") == ["open safari"]
