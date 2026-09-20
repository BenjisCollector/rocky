"""Turn an unfamiliar request into commands Rocky already knows, using the writer LLM.

The LLM never touches the machine: it only proposes short commands, each of which is routed through Jev and
executed by the same code as a spoken command. Anything it cannot express as a known command becomes one
'on screen:' step that the goal loop handles with the accessibility tree.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from . import config

HTTP = httpx.Client(timeout=20.0)
MAX_STEPS = 6

SYSTEM = """You turn one spoken request into a short list of simple commands a computer assistant already understands.
You will be given the frontmost app, the installed apps, the available keyboard shortcut names, and the last few commands.
Command shapes (use exactly these words):
- open <installed app name>            - go to <site or domain>
- search youtube for <query>           - google <query>
- play <video, song or artist>         - type <the exact text to type>
- press <one shortcut name from the list>   (for a new note, file, document, tab or window use: press new / press new_tab)
- scroll down / scroll up              - volume up / volume down / mute / unmute
- pause / next track / previous track  - dark mode / lock the screen / take a screenshot
- on screen: <one sentence for something that must be clicked inside the current app>
Rules: at most 6 steps, in order, each one command. Keep the user's exact words for anything typed.
If a needed app is not the frontmost app, open it first. Prefer 'play X' when the user wants to watch or listen.
If the request is chatter, a question, or impossible, return an empty list. Never include passwords or payments.
Examples:
"open notes and create a new note called groceries" -> ["open Notes", "press new", "type groceries"]
"put on some lofi and turn it down a bit" -> ["play lofi hip hop", "volume down"]
"reply to this email saying I will be late" -> ["on screen: click Reply", "type I will be late"]
Reply ONLY with JSON: {"steps": ["...", "..."]}"""


def decompose(utterance: str, front_app: str = "", context: dict[str, Any] | None = None) -> list[str]:
    """Known commands for `utterance`, or [] when the LLM is unavailable or unsure."""
    key = config.TEXT_MODEL_API_KEY
    if not key:
        return []
    base = config.TEXT_MODEL_BASE_URL.rstrip("/")
    body: dict[str, Any] = {
        "model": config.TEXT_MODEL,
        "max_tokens": 200,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"request": utterance, "frontmost_app": front_app, **(context or {})}),
            },
        ],
    }
    if "api.deepseek.com" in base:
        body["thinking"] = {"type": "disabled"}
    try:
        r = HTTP.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
        if r.is_error:
            return []
        return parse(r.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return []


def parse(content: str) -> list[str]:
    """Strict: {"steps": [str, ...]} with 1..MAX_STEPS short single-line strings, else []."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        return []
    out = []
    for s in steps:
        if not isinstance(s, str):
            return []
        s = " ".join(s.split())
        if not (0 < len(s) <= 160):
            return []
        out.append(s)
    return out
