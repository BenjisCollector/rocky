"""Pick the platform implementation once. Everything else imports `get_platform()`."""

from __future__ import annotations

import sys

from .base import Platform


def get_platform() -> Platform:
    if sys.platform == "darwin":
        from .macos import MacOS

        return MacOS()
    if sys.platform == "win32":
        from .windows import Windows

        return Windows()
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
