from __future__ import annotations

import httpx
import pytest

from rocky import jev as jevmod
from rocky.jev import Jev, JevError, noul, validate_choice

ALLOWED = {"open_app", "none"}


def good(choice: str = "open_app") -> dict:
    return {"choice": choice, "confidence": 0.9, "probabilities": {"open_app": 0.9, "none": 0.1}}


def test_validate_choice_accepts_offered_choice():
    assert validate_choice(good(), ALLOWED)["choice"] == "open_app"


@pytest.mark.parametrize(
    "answer",
    [
        good("rm_rf"),  # choice outside the criteria
        {"choice": "open_app", "confidence": 0.9, "probabilities": {"open_app": 0.5, "shutdown": 0.5}},
        {"choice": "open_app", "confidence": 1.5, "probabilities": {"open_app": 1.0}},
        {"choice": "open_app", "confidence": True, "probabilities": {"open_app": 1.0}},
        {"choice": "open_app"},
        "open_app",
        None,
    ],
)
def test_validate_choice_refuses_anything_outside_criteria(answer):
    with pytest.raises(JevError):
        validate_choice(answer, ALLOWED)


def test_noul_reads_both_shapes():
    assert noul({"noul": 0.8}) == 0.8
    assert noul({"probabilities": {"true": 0.3, "false": 0.7}}) == 0.3


def _jev(responses: list, monkeypatch) -> tuple[Jev, list]:
    """A Jev whose HTTP goes to a MockTransport that pops from `responses` (int status, dict, or exc)."""
    seen: list[dict] = []
    monkeypatch.setattr(jevmod.config, "TYPESAFE_API_KEY", "k")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.read() and __import__("json").loads(request.read()))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, int):
            return httpx.Response(r, text="boom")
        return httpx.Response(200, json=r)

    j = Jev(api_key="k")
    j.http = httpx.Client(transport=httpx.MockTransport(handler), headers=j.http.headers)
    return j, seen


OK = {"answers": {"kind": good()}, "model": "jev-latest", "usage": {"input_tokens": 3}}


def test_ask_returns_answers_and_records_latency_and_usage(monkeypatch):
    j, seen = _jev([OK], monkeypatch)
    answers = j.ask({"utterance": "hi"}, {"kind": {"type": "choice", "criteria": {}}})
    assert answers["kind"]["choice"] == "open_app"
    assert j.usage == {"input_tokens": 3}
    assert j.latency_ms >= 0
    assert seen[0]["model"] == j.model and seen[0]["state"] == {"utterance": "hi"}
    assert seen[0]["questions"]["kind"]["type"] == "choice"


def test_ask_retries_once_on_5xx(monkeypatch):
    j, seen = _jev([503, OK], monkeypatch)
    assert j.ask({}, {})["kind"]["choice"] == "open_app"
    assert len(seen) == 2


def test_ask_gives_up_after_second_5xx(monkeypatch):
    j, _ = _jev([500, 502], monkeypatch)
    with pytest.raises(JevError, match="502"):
        j.ask({}, {})


def test_ask_retries_once_on_timeout(monkeypatch):
    j, _ = _jev([httpx.ReadTimeout("slow"), OK], monkeypatch)
    assert j.ask({}, {})["kind"]["choice"] == "open_app"


def test_ask_401_is_a_clear_key_error(monkeypatch):
    j, _ = _jev([401], monkeypatch)
    with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
        j.ask({}, {})


def test_ask_without_key_refuses_before_any_request():
    with pytest.raises(JevError, match="not set"):
        Jev(api_key="").ask({}, {})
