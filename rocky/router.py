"""One fan-out request per utterance. Every free-text value (text to type, query, domain) is cut
from the utterance in code and offered as candidates; Jev selects, it never generates."""

from __future__ import annotations

import re
from typing import Any

from .jev import Jev, noul, validate_choice
from .models import Plan
from .platform.base import Platform

KINDS: dict[str, str] = {
    "open_app": "Launch, open, or switch to an installed application ('open photo booth', 'switch to safari')",
    "open_url": "Go to a website by name or address with no search query ('go to youtube', 'open github.com')",
    "search": "Search the web or a site for something: google it, look it up, search youtube for",
    "play": "Play a specific video, song, or artist by name ('play lofi beats', 'play the Hormozi interview on youtube')",
    "fun": "Ask Rocky itself to do something playful: dance, wave, say hi, celebrate",
    "shortcut": "Press one key or keyboard shortcut: copy, paste, undo, save, close the window",
    "scroll": "Scroll the current page or document up or down",
    "volume": "Change the system volume: louder, quieter, mute, unmute",
    "media": "Control playback: play, pause, next track, previous track",
    "system": "System-level action: dark mode, lock the screen, sleep the display, take a screenshot",
    "type": "Type, write, or dictate some text into whatever is focused right now",
    "goal": (
        "A multi-step task inside an app that needs looking at the screen and clicking things "
        "('reply to the last email', 'add milk to my list in notes', 'change the wallpaper')"
    ),
    "none": "Not a command for the computer: conversation, background chatter, or unintelligible",
}

# Kinds the platform completes blind; needs_screen from the model is ignored for these.
NO_SCREEN_KINDS = frozenset(
    {
        "open_app",
        "open_url",
        "search",
        "play",
        "fun",
        "shortcut",
        "scroll",
        "volume",
        "media",
        "system",
        "none",
    }
)

SITES: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "amazon": "https://www.amazon.com",
    "wikipedia": "https://en.wikipedia.org",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "twitter_x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
}

ENGINES: dict[str, str] = {
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
}

VOLUME_OPS = {"up": "louder", "down": "quieter", "mute": "mute / silence", "unmute": "unmute / sound back on"}
MEDIA_OPS = {
    "play_pause": "play, pause, or resume",
    "next": "next track / skip",
    "previous": "previous track",
}
SYSTEM_OPS = {
    "dark_mode": "toggle dark mode / light mode",
    "lock": "lock the screen",
    "sleep_display": "put the display to sleep",
    "screenshot": "take a screenshot",
}
SCROLL_DIRS = {"down": "scroll down / further", "up": "scroll up / back up"}

# --- text spans cut from the utterance -----------------------------------------------------------

_VERBS = r"(?:please\s+)?(?:can you\s+|could you\s+)?"
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("quoted", re.compile(r"[\"“'](?P<t>[^\"”']{2,})[\"”']")),
    (
        "title",
        re.compile(r"\b(?:called|titled|named|that says|saying|with the title)\s+(?P<t>.+)$", re.IGNORECASE),
    ),
    (
        "type",
        re.compile(
            r"^" + _VERBS + r"(?:type|write|enter|dictate|input|insert|say)"
            r"(?:\s+in|\s+out|\s+the\s+words?|\s+the\s+text|\s+this|\s+that)?[:,]?\s+(?P<t>.+)$",
            re.IGNORECASE,
        ),
    ),
    (
        "search",
        re.compile(
            r"^" + _VERBS + r"(?:search|google|look\s*up|find|look\s+for|show\s+me|pull\s+up|play|put\s+on)"
            r"(?:\s+(?:on|in)\s+\w+(?:\s+\w+)?)?(?:\s+for)?[:,]?\s+(?P<t>.+)$",
            re.IGNORECASE,
        ),
    ),
    ("search", re.compile(r"^.*?\b(?:for|about)\s+(?P<t>.+)$", re.IGNORECASE)),
]
_TRAILING_IN_APP = re.compile(
    r"\s+(?:in|into|inside|on)\s+(?:the\s+)?(?:[A-Z][\w.]*(?:\s+[A-Z][\w.]*)*"
    r"|notes|safari|chrome|mail|messages|terminal|finder|slack)(?:\s+app)?\s*[.!?]?$"
)
_TRAILING_SUBMIT = re.compile(
    r"[\s,.]*(?:and|then)?\s*(?:hit|press)\s+(?:enter|return)\s*[.!]?$", re.IGNORECASE
)


def _clean(s: str) -> str:
    s = _TRAILING_SUBMIT.sub("", s.strip().strip("\"“”'")).strip()
    return s.rstrip(" .").strip()


def spans(utterance: str) -> list[tuple[str, str]]:
    """(source, text) pairs that might be the payload, most specific first; 'whole' is always last."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(source: str, t: str) -> None:
        t = _clean(t)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append((source, t))

    for source, pat in _PATTERNS:
        m = pat.search(utterance)
        if m:
            add(source, m.group("t"))
            add(source, _TRAILING_IN_APP.sub("", m.group("t")))
    add("whole", utterance)
    return out


def text_candidates(utterance: str) -> dict[str, str]:
    """Candidates keyed c0..cN for the `text` question. Jev picks; code never guesses."""
    return {f"c{i}": t for i, (_, t) in enumerate(spans(utterance)[:6])}


_DOMAIN = re.compile(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", re.IGNORECASE)
_SITE_WORD = re.compile(
    r"\b(?:go to|open|visit|pull up|bring up|load|navigate to|take me to)\s+(?:the\s+)?(?:website\s+|site\s+)?"
    r"([a-z0-9][a-z0-9 .-]*?)(?:\s+(?:website|site|page|homepage))?\s*[.!?]?$",
    re.IGNORECASE,
)


def domain_guess(utterance: str) -> str:
    """A domain the user may have said ('github.com', 'reddit dot com'); '' when nothing looks like one."""
    m = _DOMAIN.search(utterance)
    if m:
        return m.group(1).lower()
    m = _SITE_WORD.search(utterance)
    if not m:
        return ""
    word = m.group(1).strip().lower().replace(" dot ", ".").replace(" ", "")
    if not word or word in ("it", "that", "this"):
        return ""
    return word if "." in word else f"{word}.com"


# --- questions -----------------------------------------------------------------------------------


def _choice(instructions: str, criteria: dict[str, Any]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def _noul(instructions: str, yes: str, no: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": yes, "false": no}}


def build_questions(
    apps: list[str], shortcuts: list[str], cands: dict[str, str], domain: str
) -> dict[str, Any]:
    sites: dict[str, Any] = {s: None for s in SITES}
    if domain:
        sites["url"] = f"The address the user said: {domain}"
    sites["none"] = "No listed site and no address matches what the user said"
    return {
        "kind": _choice(
            "The user is speaking a voice command to their computer; `utterance` is the transcript. "
            "Which single kind of action are they asking for right now?",
            KINDS,
        ),
        "addressed": _noul(
            "Is `utterance` an instruction spoken to a voice assistant that controls this computer, "
            "as opposed to conversation with another person, reading aloud, or thinking out loud?",
            "A direct instruction for the computer to do something now",
            "Not directed at the computer, or not an instruction",
        ),
        "compound": _noul(
            "Does `utterance` ask for two or more separate actions one after another "
            "(for example 'open safari and go to youtube')? One action with many words is not compound.",
            "Two or more distinct actions are requested",
            "Exactly one action is requested",
        ),
        "needs_screen": _noul(
            "Does completing `utterance` require looking at what is on screen or clicking things on it, "
            "as opposed to a blind command like opening an app, a website, a search, a key, or the volume?",
            "The screen must be read or clicked to finish this",
            "It can be done without reading or clicking the screen",
        ),
        "app": _choice(
            "Assume the user wants to open or switch to an application. Which installed application in `apps` "
            "do they mean? Match on meaning: 'settings' means System Settings, 'browser' means the default "
            "browser. Choose `none` if no listed app matches.",
            {**{a: None for a in apps}, "none": "No listed application matches what the user said"},
        ),
        "site": _choice(
            "Assume the user wants to open a website. Which site do they mean? Choose `url` if they said an "
            "address, `none` if it is neither listed nor an address.",
            sites,
        ),
        "engine": _choice(
            "Assume the user wants to search for something. Which search engine or site should run the "
            "search? If the user names none, choose google.",
            {e: None for e in ENGINES},
        ),
        "shortcut": _choice(
            "Assume the user wants a key or keyboard shortcut pressed. Which one? Choose `none` if none fits.",
            {**{k: None for k in shortcuts}, "none": "No listed shortcut matches"},
        ),
        "scroll_dir": _choice("Assume the user wants to scroll. In which direction?", SCROLL_DIRS),
        "volume_op": _choice("Assume the user wants to change the volume. What change?", VOLUME_OPS),
        "media_op": _choice("Assume the user wants to control playback. What?", MEDIA_OPS),
        "system_op": _choice("Assume the user wants a system-level action. Which one?", SYSTEM_OPS),
        "text": _choice(
            "Assume the user wants some text typed or searched. `candidates` holds spans cut from the "
            "utterance. Which candidate is exactly the payload text they intend, with no command words "
            "(like 'type', 'search for', 'in notes') and no trailing 'and press enter'?",
            dict(cands),
        ),
    }


# --- route ---------------------------------------------------------------------------------------


def route(jev: Jev, platform: Platform, utterance: str) -> Plan:
    apps = platform.installed_apps()
    shortcuts = list(platform.shortcuts())
    cands = text_candidates(utterance)
    domain = domain_guess(utterance)
    questions = build_questions(apps, shortcuts, cands, domain)
    front = platform.frontmost_app()
    state = {
        "utterance": utterance,
        "frontmost_app": front,
        "apps": apps,
        "candidates": cands,
    }
    answers = jev.ask(state, questions)
    return to_plan(utterance, answers, questions, cands, domain, jev.latency_ms, front)


_PLAY_NOISE = re.compile(
    r"^(?:(?:please|can you|could you)\s+)?(?:find|search for|look for|look up|put on|play|watch)\s+"
    r"|\s+(?:on|from|in)\s+youtube\b|\s*(?:,|\band\b)?\s*(?:play|watch|start)\s+(?:it|that|this)(?:\s+for\s+me)?\s*"
    r"|\s+for\s+me\s*$|\s*please\s*$|\s+video\s*$",
    re.IGNORECASE,
)


def clean_play_query(text: str) -> str:
    """'find the hormozi interview on youtube and play it for me' -> 'the hormozi interview'."""
    out = text
    for _ in range(4):
        new = _PLAY_NOISE.sub(" ", out)
        if new == out:
            break
        out = new
    return " ".join(out.split()).strip(" ,.") or text


def to_plan(
    utterance: str,
    answers: dict[str, Any],
    questions: dict[str, Any],
    cands: dict[str, str],
    domain: str,
    latency_ms: int,
    front: str = "",
) -> Plan:
    def pick(name: str) -> tuple[str, float]:
        a = validate_choice(answers[name], questions[name]["criteria"])
        return a["choice"], float(a["confidence"])

    kind, conf = pick("kind")
    args: dict[str, Any] = {}
    if kind == "open_app":
        args["app"], c = pick("app")
        conf = min(conf, c)
    elif kind == "open_url":
        site, c = pick("site")
        if site == "url":
            args["url"], args["site"] = f"https://{domain}", domain
        elif site != "none":
            args["url"], args["site"] = SITES[site], site
        else:
            kind = "search"  # nothing to navigate to
        conf = min(conf, c)
    if kind == "search":
        tkey, c = pick("text")
        args["query"] = cands[tkey]
        args["engine"], _ = pick("engine")
        conf = min(conf, c)
    elif kind == "play":
        tkey, c = pick("text")
        args["query"] = clean_play_query(cands[tkey])
        conf = min(conf, c)
    elif kind == "fun":
        args["op"] = "dance" if re.search(r"danc|party|celebrat|boogie", utterance, re.IGNORECASE) else "wave"
    elif kind == "type":
        tkey, c = pick("text")
        args["text"] = cands[tkey]
        conf = min(conf, c)
    elif kind in ("shortcut", "scroll", "volume", "media", "system"):
        sub = {"shortcut": "shortcut", "scroll": "scroll_dir"}.get(kind, f"{kind}_op")
        args["op" if kind != "shortcut" else "shortcut"], c = pick(sub)
        conf = min(conf, c)
    needs_screen = kind == "goal" or (kind not in NO_SCREEN_KINDS and noul(answers["needs_screen"]) > 0.5)
    if needs_screen:
        args["goal"] = utterance
    return Plan(
        kind=kind,
        args=args,
        confidence=conf,
        needs_screen=needs_screen,
        addressed=noul(answers["addressed"]) > 0.5,
        compound=noul(answers["compound"]) > 0.5,
        latency_ms=latency_ms,
        raw={**answers, "front": front},
    )


_SPLIT = re.compile(r"\s*,?\s*\b(?:and then|then|and)\b\s*", re.IGNORECASE)


def split_compound(utterance: str) -> list[str]:
    """'open safari and then go to youtube' -> ['open safari', 'go to youtube']. Only called when the
    model said the utterance is compound, so 'salt and pepper' inside one command is left alone."""
    parts = [p.strip(" ,.") for p in _SPLIT.split(utterance)]
    parts = [p for p in parts if len(p) > 1]
    return parts if len(parts) > 1 else [utterance]
