"""The only place free text can come from. First select a span the user already said (zero LLM calls);
only then ask a small OpenAI-compatible model, and accept nothing but a tiny {"text": ...} object."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from . import config
from .risk import is_secret_field
from .router import spans

MAX_CHARS = 200
HTTP = httpx.Client(timeout=30.0)

# Which span sources a field may take. Quoted and 'type ...' spans fit any field; a 'search for ...'
# span only fits a search-like field; a 'saying ...' span only a body-like field.
_FIELD_FOR: dict[str, re.Pattern[str]] = {
    "search": re.compile(r"search|find|query|filter|look", re.IGNORECASE),
    "title": re.compile(r"message|body|note|text|comment|content|subject|title|name|reply", re.IGNORECASE),
}

SYSTEM = (
    "You fill exactly one text field on a user's screen. You get the user's goal, the field's label, and "
    'what the user said. Reply with a JSON object {"text": "..."} holding the exact string to type, under '
    "200 characters, nothing else. Never invent credentials, personal data, or payment details; if the "
    'field should stay empty reply {"text": ""}.'
)


def select_span(field_label: str, utterance: str) -> str:
    """The shortest span from the utterance that fits the field, or '' when none does. Shortest wins
    because the wider cut of the same phrase still carries command words ('youtube for lofi beats')."""
    fitting = [
        text
        for source, text in spans(utterance)
        if source != "whole" and (source not in _FIELD_FOR or _FIELD_FOR[source].search(field_label or ""))
    ]
    return min(fitting, key=len, default="")


def compose(goal: str, field_label: str, utterance: str | None = None) -> str:
    """The string to type into `field_label`, or '' when nothing should be typed."""
    if is_secret_field(field_label):
        return ""
    span = select_span(field_label, utterance or "")
    if span:
        return span
    return _ask_llm(goal, field_label, utterance or "")


def _ask_llm(goal: str, field_label: str, utterance: str) -> str:
    key = config.TEXT_MODEL_API_KEY
    if not key:
        return ""  # nothing is guessed in code
    base = config.TEXT_MODEL_BASE_URL.rstrip("/")
    body: dict[str, Any] = {
        "model": config.TEXT_MODEL,
        "max_tokens": 80,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"goal": goal, "field": field_label, "utterance": utterance}),
            },
        ],
    }
    if "api.deepseek.com" in base:
        body["thinking"] = {"type": "disabled"}
    try:
        r = HTTP.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
        if r.is_error:
            return ""
        return parse(r.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return ""


def parse(content: str) -> str:
    """Strict: a JSON object with exactly one string key 'text' of 1..MAX_CHARS characters, else ''."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict) or set(data) != {"text"} or not isinstance(data["text"], str):
        return ""
    text = data["text"].strip()
    return text if 0 < len(text) <= MAX_CHARS else ""
