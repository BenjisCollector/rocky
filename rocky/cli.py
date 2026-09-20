"""rocky: say it, and your computer does it.

rocky                          voice mode (wake word from .env)
rocky --text "open photo booth" one utterance from text
rocky --text "..." --dry       print the plan, execute nothing
rocky --act                    let the goal path drive the machine (fast path always acts)
rocky --doctor                 permissions and key check
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Any

from . import config
from .models import Plan


def describe(plan: Plan) -> str:
    """One line per plan: open_app(Photo Booth)  conf=0.91  412ms"""
    primary = next(iter(plan.args.values()), "")
    tag = "  [screen]" if plan.needs_screen and plan.kind != "goal" else ""
    return f"{plan.kind}({primary}){tag}  conf={plan.confidence:.2f}  {plan.latency_ms}ms"


def handle(jev: Any, platform: Any, utterance: str, act: bool, dry: bool, depth: int = 0) -> None:
    from .brain import act as run

    run(utterance, jev, platform, act, dry=dry, speak=not args_text_mode(), depth=depth)


def args_text_mode() -> bool:
    """`rocky --text` prints only; voice mode also speaks."""
    return "--text" in sys.argv


def doctor() -> int:
    try:
        from .platform import get_platform

        perms = get_platform().check_permissions()
    except Exception as e:  # noqa: BLE001
        perms = {"platform": f"unavailable ({e})"}
    for k, v in perms.items():
        print(f"{k}: {v}")
    print(f"TYPESAFE_API_KEY: {'set' if config.TYPESAFE_API_KEY else 'missing'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rocky", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--text", help="run one utterance from text instead of the microphone")
    p.add_argument(
        "--act", action="store_true", help="goal path drives the machine (default: dry-run the loop)"
    )
    p.add_argument("--dry", action="store_true", help="print the plan and execute nothing")
    p.add_argument("--doctor", action="store_true", help="print permissions and whether the key is set")
    args = p.parse_args(argv)

    config.load_env_file()
    importlib.reload(config)  # its constants were read at import, before .env was loaded
    if args.doctor:
        return doctor()

    from .jev import Jev
    from .platform import get_platform

    platform = get_platform()
    jev = Jev()
    if args.text:
        handle(jev, platform, args.text, args.act, args.dry)
        return 0

    from .overlay import run_with_overlay  # lazy: AppKit
    from .voice import listen_forever  # lazy: audio stack

    def listen() -> None:
        listen_forever(
            lambda utterance: handle(jev, platform, utterance, args.act, args.dry),
            wake_word=config.WAKE_WORD,
            backend=config.STT_BACKEND,
            language=config.STT_LANGUAGE,
        )

    run_with_overlay(listen)  # the watermelon at the top of the screen; voice runs on a thread
    return 0


if __name__ == "__main__":
    sys.exit(main())
