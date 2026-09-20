"""Rocky, the little watermelon slice at the top of your screen.

An original cute character: a watermelon slice with two dot eyes, a small smile, black seeds, stubby arms
and feet. It blinks while idle, glows and waves one arm when it hears you, looks up while thinking, bobs
while speaking, and beams when a task is done. Click-through, floats above every window, joins every Space.
macOS only (AppKit); Windows is on the roadmap.

Run `python -m rocky.overlay` to see it cycle through its states.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable

from . import events

W, H = 132, 132  # window size in points
MARGIN_TOP = 6  # below the menu bar

# palette
FLESH = (0.99, 0.33, 0.36)
FLESH_LIGHT = (1.00, 0.52, 0.50)
RIND = (0.18, 0.62, 0.30)
RIND_DARK = (0.10, 0.42, 0.20)
RIND_PALE = (0.90, 0.97, 0.85)
SEED = (0.12, 0.10, 0.12)
INK = (0.14, 0.12, 0.14)
CHEEK = (1.00, 0.80, 0.78)
GLOW = (0.45, 0.85, 1.00)
GLOW_ERR = (1.00, 0.40, 0.35)


def _pose(state: str, t: float, since: float) -> dict:
    """Everything the drawing needs for one frame, derived from state and time."""
    breathe = 1.0 + 0.02 * math.sin(t * 1.6)
    pose = {"scale": breathe, "bob": 0.0, "arm": -55.0, "glow": 0.12, "glow_rgb": GLOW, "notes": 0.0}
    if state == "hearing":
        pose["arm"] = 55.0 + 28.0 * math.sin(t * 9.0)  # the wave
        pose["glow"] = 0.35 + 0.35 * abs(math.sin(t * 5.0))
        pose["bob"] = 2.0 * abs(math.sin(t * 9.0))
    elif state == "thinking":
        pose["glow"] = 0.25 + 0.15 * math.sin(t * 2.5)
        pose["notes"] = t
        pose["arm"] = -35.0 + 6.0 * math.sin(t * 2.0)
    elif state == "speaking":
        pose["glow"] = 0.45 + 0.4 * abs(math.sin(t * 11.0))
        pose["bob"] = 1.5 * abs(math.sin(t * 11.0))
        pose["arm"] = -45.0
    elif state == "done":
        k = min(1.0, since / 0.25)
        pose["arm"] = -55.0 + k * 130.0  # arm shoots up
        pose["glow"] = 0.7 * (1.0 - min(1.0, since / 1.4))
    elif state == "error":
        pose["glow"] = 0.6 * abs(math.sin(t * 6.0))
        pose["glow_rgb"] = GLOW_ERR
        pose["bob"] = -1.0
    return pose


if sys.platform == "darwin":
    import objc
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSBezierPath,
        NSColor,
        NSGradient,
        NSMakeRect,
        NSScreen,
        NSStatusWindowLevel,
        NSTimer,
        NSView,
        NSWindow,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorStationary,
        NSWindowStyleMaskBorderless,
    )
    from Foundation import NSObject

    def _rgb(c, a=1.0):
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(c[0], c[1], c[2], a)

    def _stroke(x0, y0, x1, y1, width, color):
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(width)
        p.setLineCapStyle_(1)  # round
        p.moveToPoint_((x0, y0))
        p.lineToPoint_((x1, y1))
        color.set()
        p.stroke()

    class RockyView(NSView):
        def initWithFrame_(self, frame):
            self = objc.super(RockyView, self).initWithFrame_(frame)  # noqa: PLW0642
            if self is None:
                return None
            self.state = "idle"
            self.state_since = time.monotonic()
            self.t0 = self.state_since
            return self

        def setState_(self, state):
            if state != self.state:
                self.state = state
                self.state_since = time.monotonic()

        def drawRect_(self, rect):
            now = time.monotonic()
            t = now - self.t0
            since = now - self.state_since
            if self.state == "done" and since > 1.4:
                self.setState_("idle")
            pose = _pose(self.state, t, since)
            st = self.state

            cx, cy = W / 2, H / 2 + pose["bob"]
            s = pose["scale"]
            R = 44 * s  # slice radius
            top = cy + 14  # the flat top edge of the slice

            def slice_path(radius):
                path = NSBezierPath.bezierPath()
                path.moveToPoint_((cx - radius, top))
                path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                    (cx, top), radius, 180, 360, False
                )
                path.closePath()
                return path

            # glow ring while listening or speaking
            g = pose["glow"]
            if g > 0.01:
                ring = NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(cx - R * 1.5, top - R * 1.55, R * 3.0, R * 2.6)
                )
                grad = NSGradient.alloc().initWithStartingColor_endingColor_(
                    _rgb(pose["glow_rgb"], g), _rgb(pose["glow_rgb"], 0.0)
                )
                grad.drawInBezierPath_relativeCenterPosition_(ring, (0, -0.3))

            # feet under the rind
            for dx in (-14, 14):
                foot = NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(cx + dx * s - 8, top - R - 7, 16, 10)
                )
                _rgb(RIND_DARK).set()
                foot.fill()

            # arms: left resting, right waving from the shoulder
            def arm(side, angle_deg):
                sx = cx + side * R * 0.78
                sy = top - R * 0.45
                a = math.radians(angle_deg)
                ex = sx + side * math.cos(a) * 20
                ey = sy + math.sin(a) * 20
                _stroke(sx, sy, ex, ey, 11, _rgb(RIND_DARK))
                _stroke(sx, sy, ex, ey, 7, _rgb(RIND))
                hand = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(ex - 6.5, ey - 6.5, 13, 13))
                _rgb(RIND_PALE).set()
                hand.fill()

            arm(-1, -50.0)
            arm(1, pose["arm"])

            # the slice: rind, pale band, flesh
            outer = slice_path(R)
            _rgb(RIND).set()
            outer.fill()
            _rgb(RIND_DARK).set()
            outer.setLineWidth_(2.5)
            outer.stroke()
            _rgb(RIND_PALE).set()
            slice_path(R - 6).fill()
            flesh = slice_path(R - 11)
            grad = NSGradient.alloc().initWithStartingColor_endingColor_(_rgb(FLESH_LIGHT), _rgb(FLESH))
            grad.drawInBezierPath_angle_(flesh, -90)

            # seeds
            for dx, dy in ((-20, -6), (18, -8), (-8, -20), (10, -18), (0, -4)):
                seed = NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(cx + dx * s - 2, top + dy * s - 3.5, 4, 7)
                )
                _rgb(SEED).set()
                seed.fill()

            # cheeks
            for dx in (-16, 16):
                cheek = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx + dx * s - 5, top - 2, 10, 6))
                _rgb(CHEEK, 0.75).set()
                cheek.fill()

            # eyes: blink every ~3.2 s while idle; wide when hearing; look up when thinking; happy arcs when done
            blink = st == "idle" and (t % 3.2) < 0.12
            eye_dy = 5.0 if st == "thinking" else 3.0
            eye_r = 4.6 if st == "hearing" else 3.8
            for dx in (-10, 10):
                ex, ey = cx + dx * s, top + eye_dy
                if st == "done":
                    _stroke(ex - 5, ey - 1, ex, ey + 4, 3, _rgb(INK))
                    _stroke(ex, ey + 4, ex + 5, ey - 1, 3, _rgb(INK))
                elif blink or st == "error":
                    _stroke(ex - 5, ey, ex + 5, ey, 3, _rgb(INK))
                else:
                    eye = NSBezierPath.bezierPathWithOvalInRect_(
                        NSMakeRect(ex - eye_r, ey - eye_r, eye_r * 2, eye_r * 2)
                    )
                    _rgb(INK).set()
                    eye.fill()
                    hl = NSBezierPath.bezierPathWithOvalInRect_(
                        NSMakeRect(ex - eye_r * 0.15, ey + eye_r * 0.2, 2.6, 2.6)
                    )
                    _rgb((1, 1, 1)).set()
                    hl.fill()

            # mouth
            my0 = top - 8
            if st == "speaking":
                o = 3.0 + 3.0 * abs(math.sin(t * 11.0))
                mouth = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 4, my0 - o, 8, o * 2))
                _rgb(INK).set()
                mouth.fill()
            elif st == "error":
                _stroke(cx - 6, my0, cx + 6, my0, 2.5, _rgb(INK))
            else:
                wide = 9 if st in ("done", "hearing") else 6
                smile = NSBezierPath.bezierPath()
                smile.setLineWidth_(2.5)
                smile.setLineCapStyle_(1)
                smile.moveToPoint_((cx - wide, my0 + 1))
                smile.curveToPoint_controlPoint1_controlPoint2_(
                    (cx + wide, my0 + 1), (cx - wide / 2, my0 - 5), (cx + wide / 2, my0 - 5)
                )
                _rgb(INK).set()
                smile.stroke()

            # thinking: three dots above the head
            if pose["notes"]:
                for k in range(3):
                    ph = pose["notes"] * 3.0 - k * 0.8
                    ny = top + 18 + 3 * max(0.0, math.sin(ph))
                    nx = cx - 12 + k * 12
                    dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(nx - 3, ny - 3, 6, 6))
                    _rgb(GLOW, 0.5 + 0.5 * max(0.0, math.sin(ph))).set()
                    dot.fill()

    class _Ticker(NSObject):
        def initWithView_(self, view):
            self = objc.super(_Ticker, self).init()  # noqa: PLW0642
            self.view = view
            return self

        def tick_(self, timer):
            self.view.setNeedsDisplay_(True)

    class Overlay:
        """Floating, click-through Rocky at the top center of the main screen."""

        def __init__(self) -> None:
            self.app = NSApplication.sharedApplication()
            self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            vf = NSScreen.mainScreen().visibleFrame()
            x = vf.origin.x + (vf.size.width - W) / 2
            y = vf.origin.y + vf.size.height - H - MARGIN_TOP
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(x, y, W, H), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
            )
            self.window.setOpaque_(False)
            self.window.setBackgroundColor_(NSColor.clearColor())
            self.window.setHasShadow_(False)
            self.window.setLevel_(NSStatusWindowLevel)
            self.window.setIgnoresMouseEvents_(True)
            self.window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
            )
            self.view = RockyView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
            self.window.setContentView_(self.view)
            self._ticker = _Ticker.alloc().initWithView_(self.view)
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / 30, self._ticker, "tick:", None, True
            )
            events.subscribe(self.set_state)

        def show(self) -> None:
            self.window.orderFrontRegardless()

        def hide(self) -> None:
            self.window.orderOut_(None)

        def set_state(self, state: str) -> None:
            # called from any thread; the attribute swap is atomic and the timer redraws on the main thread
            self.view.setState_(state)

        def run(self, worker: Callable[[], None] | None = None) -> None:
            """Show Rocky and run the AppKit loop. `worker` runs on a thread; when it returns, we exit."""
            self.show()
            if worker is not None:

                def _run():
                    try:
                        worker()
                    finally:
                        self.app.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "terminate:", None, False
                        )

                threading.Thread(target=_run, daemon=True).start()
            self.app.run()

    def run_with_overlay(worker: Callable[[], None]) -> None:
        Overlay().run(worker)

else:  # pragma: no cover

    class Overlay:  # type: ignore[no-redef]
        """No overlay on this platform yet; states are accepted and ignored."""

        def __init__(self) -> None:
            events.subscribe(self.set_state)

        def show(self) -> None: ...

        def hide(self) -> None: ...

        def set_state(self, state: str) -> None: ...

        def run(self, worker: Callable[[], None] | None = None) -> None:
            if worker is not None:
                worker()

    def run_with_overlay(worker: Callable[[], None]) -> None:  # type: ignore[no-redef]
        worker()


def _demo() -> None:
    """Cycle through the states so a human can look at Rocky."""
    steps = [
        ("idle", 1.5),
        ("hearing", 2.5),
        ("thinking", 2.0),
        ("speaking", 2.0),
        ("done", 1.6),
        ("error", 1.5),
    ]
    for _ in range(5):
        for state, seconds in steps:
            events.emit(state)
            time.sleep(seconds)


if __name__ == "__main__":
    run_with_overlay(_demo)
