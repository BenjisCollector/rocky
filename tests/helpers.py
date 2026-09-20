"""Offline helpers for the router-side tests: a Jev that answers from a script, shaped like the real API."""

from __future__ import annotations

from typing import Any


def choice(name: str, criteria: dict[str, Any], conf: float = 0.9) -> dict[str, Any]:
    keys = list(criteria)
    rest = round((1 - conf) / max(len(keys) - 1, 1), 4)
    return {
        "choice": name,
        "confidence": conf,
        "probabilities": {k: (conf if k == name else rest) for k in keys},
    }


def yes_no(p: float) -> dict[str, Any]:
    return {"noul": p, "confidence": abs(p - 0.5) * 2}


class ScriptedJev:
    """Answers every question from `picks` (choice name, or noul probability); unused choice heads get
    their first criterion so validate_choice stays happy."""

    def __init__(self, picks: dict[str, Any], conf: float = 0.9) -> None:
        self.picks = picks
        self.conf = conf
        self.latency_ms = 7
        self.usage: dict[str, Any] = {}
        self.calls: list[tuple[dict, dict]] = []

    def ask(self, state: dict, questions: dict) -> dict:
        self.calls.append((state, questions))
        answers = {}
        for name, q in questions.items():
            if q["type"] == "noul":
                answers[name] = yes_no(float(self.picks.get(name, 0.05)))
            else:
                pick = self.picks.get(name, next(iter(q["criteria"])))
                crit = dict(q["criteria"])
                if pick not in crit:  # an answer outside the offered criteria, for the refusal test
                    crit[pick] = None
                answers[name] = choice(pick, crit, self.conf)
        return answers
