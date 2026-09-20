from __future__ import annotations

import json

import httpx
import pytest

from rocky import writer
from rocky.writer import compose, parse, select_span


@pytest.fixture
def no_llm(monkeypatch):
    """Any HTTP call fails the test; also pretend a writer key is set so the LLM path is reachable."""

    def handler(_request):
        pytest.fail("LLM was called")

    monkeypatch.setattr(writer, "HTTP", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(writer.config, "TEXT_MODEL_API_KEY", "k")


def _llm(monkeypatch, content: str | dict, status: int = 200) -> list[dict]:
    seen: list[dict] = []
    body = content if isinstance(content, str) else json.dumps(content)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": json.loads(request.read())})
        return httpx.Response(status, json={"choices": [{"message": {"content": body}}]})

    monkeypatch.setattr(writer, "HTTP", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(writer.config, "TEXT_MODEL_API_KEY", "k")
    monkeypatch.setattr(writer.config, "TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(writer.config, "TEXT_MODEL", "deepseek-chat")
    return seen


@pytest.mark.usefixtures("no_llm")
@pytest.mark.parametrize(
    ("field", "utterance", "want"),
    [
        ("Message", 'reply saying "see you at noon"', "see you at noon"),
        ("Search", "search youtube for lofi beats", "lofi beats"),
        ("Subject", "email bob with the title quarterly numbers", "quarterly numbers"),
        ("Search", "type hello there", "hello there"),
    ],
)
def test_span_from_the_utterance_means_zero_llm_calls(field, utterance, want):
    assert compose("any goal", field, utterance) == want


def test_span_must_fit_the_field():
    assert select_span("To", "search youtube for lofi beats") == ""  # a search span does not fit a To field
    assert select_span("To", "email bob saying hi") == ""  # nor does a 'saying' span
    assert select_span("Body", "email bob saying hi") == "hi"
    assert select_span("To", "open notes") == ""  # the whole utterance is never a span


@pytest.mark.usefixtures("no_llm")
def test_secret_field_gets_nothing_even_when_a_span_exists():
    assert compose("log in", "Password", 'type "hunter2"') == ""
    assert compose("pay", "Card number", "type 4111 1111 1111 1111") == ""


def test_llm_is_asked_only_when_no_span_fits(monkeypatch):
    seen = _llm(monkeypatch, {"text": "Hi Bob, running ten minutes late."})
    assert (
        compose("tell bob I'm late", "Message", "message bob that I'm late")
        == "Hi Bob, running ten minutes late."
    )
    assert len(seen) == 1
    req = seen[0]
    assert req["url"] == "https://api.deepseek.com/v1/chat/completions"
    body = req["body"]
    assert body["model"] == "deepseek-chat" and body["max_tokens"] == 80
    assert body["response_format"] == {"type": "json_object"} and body["thinking"] == {"type": "disabled"}
    assert json.loads(body["messages"][1]["content"]) == {
        "goal": "tell bob I'm late",
        "field": "Message",
        "utterance": "message bob that I'm late",
    }


def test_no_thinking_flag_off_deepseek(monkeypatch):
    seen = _llm(monkeypatch, {"text": "ok"})
    monkeypatch.setattr(writer.config, "TEXT_MODEL_BASE_URL", "https://api.openai.com/v1/")
    assert compose("g", "Message", None) == "ok"
    assert (
        "thinking" not in seen[0]["body"] and seen[0]["url"] == "https://api.openai.com/v1/chat/completions"
    )


def test_no_writer_key_means_nothing_is_typed(monkeypatch):
    monkeypatch.setattr(writer.config, "TEXT_MODEL_API_KEY", "")
    monkeypatch.setattr(
        writer, "HTTP", httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("called")))
    )
    assert compose("g", "Message", "hello") == ""


def test_llm_error_is_empty_not_an_exception(monkeypatch):
    _llm(monkeypatch, {"text": "x"}, status=500)
    assert compose("g", "Message", "hello") == ""


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '"just a string"',
        "[1, 2]",
        '{"text": 5}',
        '{"text": ""}',
        '{"text": "hi", "reason": "extra key"}',
        '{"txt": "hi"}',
        json.dumps({"text": "x" * 201}),
    ],
)
def test_parse_refuses_anything_but_a_small_text_object(content):
    assert parse(content) == ""


def test_parse_accepts_a_small_text_object():
    assert parse('{"text": "  Hello  "}') == "Hello"
    assert parse(json.dumps({"text": "x" * 200})) == "x" * 200
