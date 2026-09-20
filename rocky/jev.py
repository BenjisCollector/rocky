"""The one door to TypeSafe. Jev answers typed questions with a distribution; it never generates text."""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from typing import Any

import httpx

from . import config


class JevError(RuntimeError):
    """The model could not be used. Nothing was executed."""


class Jev:
    """One HTTP client. `ask` returns the answers; `latency_ms` and `usage` describe the last call."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else config.TYPESAFE_API_KEY
        self.model = model or config.TYPESAFE_MODEL
        self.http = httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {self.api_key}"})
        self.latency_ms = 0
        self.usage: dict[str, Any] = {}

    def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise JevError("TYPESAFE_API_KEY is not set (put it in .env)")
        body = {"model": self.model, "state": state, "questions": questions}
        t0 = time.perf_counter()
        data = self._post(body)
        self.latency_ms = int((time.perf_counter() - t0) * 1000)
        self.usage = data.get("usage", {})
        try:
            return data["answers"]
        except (KeyError, TypeError):
            raise JevError("TypeSafe returned no answers; nothing executed.") from None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            try:
                r = self.http.post(config.TYPESAFE_URL, json=body)
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                raise JevError("TypeSafe timed out twice; nothing executed.") from None
            except httpx.HTTPError as e:
                raise JevError(f"Could not reach TypeSafe: {e}") from None
            if r.status_code == 401:
                raise JevError("TypeSafe rejected the API key (401). Check TYPESAFE_API_KEY in .env.")
            if r.status_code >= 500 and attempt == 0:
                continue
            if r.is_error:
                raise JevError(f"TypeSafe returned HTTP {r.status_code}: {r.text[:200]}")
            return r.json()
        raise AssertionError("unreachable")


def validate_choice(answer: dict[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    """Refuse any choice outside the criteria that were offered. The API schema is one guard; this is
    the second, so a malformed or spoofed answer can never reach the machine."""
    ids = set(allowed)
    try:
        probs = answer["probabilities"]
        numbers = [*probs.values(), answer["confidence"]]
        ok = (
            answer["choice"] in ids
            and set(probs) <= ids
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
        )
    except (KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise JevError(f"Invalid answer from TypeSafe: {answer!r}; nothing executed.")
    return answer


def noul(answer: dict[str, Any]) -> float:
    """Probability that a noul (yes/no) question is true."""
    v = answer.get("noul")
    if v is None:
        v = answer.get("probabilities", {}).get("true", 0.0)
    return float(v)
