"""Find the actual thing to play. No browser automation: one HTTP fetch, one regex, one URL to open."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx

_VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"


def youtube_first(query: str) -> str | None:
    """URL of the first video in YouTube's results for `query`, or None."""
    try:
        r = httpx.get(
            f"https://www.youtube.com/results?search_query={quote_plus(query)}",
            headers={"User-Agent": _UA, "Accept-Language": "en"},
            timeout=8.0,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None
    m = _VIDEO_ID.search(r.text)
    return f"https://www.youtube.com/watch?v={m.group(1)}" if m else None
