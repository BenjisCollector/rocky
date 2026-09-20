"""macOS Platform: pyobjc for Accessibility (AX), Quartz for synthetic input, `open` and osascript for the rest.

Coordinates: every point in and out of this module is in the global display space that both AX and
CGEvent share: origin at the top-left of the MAIN display, y growing downward, one unit = one point
(HiDPI scaling is invisible here). A second display sits at its own offset in that same space, so an
Item frame taken from snapshot() can be handed straight to click() whichever display it is on.
Snapshot.screen_w/h report the main display only.

Adapted in places from kevinbadi/jev-voice (actions.py) and awlevin/typesafe-computer-use (macos.py),
both MIT; see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path

import ApplicationServices as AS
import AVFoundation
import Quartz
from AppKit import NSDate, NSEvent, NSRunLoop, NSWorkspace

from ..models import Item, Snapshot

APP_DIRS = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
)

# Carbon virtual key codes (US layout). Letters and punctuation only matter with modifiers held;
# a bare character is typed as a Unicode event instead, so the active keyboard layout does not matter.
KEYCODES: dict[str, int] = {
    "return": 36,
    "enter": 36,
    "tab": 48,
    "space": 49,
    "backspace": 51,
    "delete": 51,
    "forward_delete": 117,
    "escape": 53,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "home": 115,
    "end": 119,
    "pageup": 116,
    "pagedown": 121,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "=": 24,
    "9": 25,
    "7": 26,
    "-": 27,
    "8": 28,
    "0": 29,
    "]": 30,
    "o": 31,
    "u": 32,
    "[": 33,
    "i": 34,
    "p": 35,
    "l": 37,
    "j": 38,
    "'": 39,
    "k": 40,
    ";": 41,
    "\\": 42,
    ",": 43,
    "/": 44,
    "n": 45,
    "m": 46,
    ".": 47,
    "`": 50,
}

MODIFIER_FLAGS = {
    "cmd": Quartz.kCGEventFlagMaskCommand,
    "command": Quartz.kCGEventFlagMaskCommand,
    "ctrl": Quartz.kCGEventFlagMaskControl,
    "control": Quartz.kCGEventFlagMaskControl,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "option": Quartz.kCGEventFlagMaskAlternate,
    "shift": Quartz.kCGEventFlagMaskShift,
}

MOUSE = {
    "left": (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft),
    "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
    "middle": (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGMouseButtonCenter),
}

SHORTCUTS: dict[str, tuple[str, list[str]]] = {
    "copy": ("c", ["cmd"]),
    "paste": ("v", ["cmd"]),
    "cut": ("x", ["cmd"]),
    "undo": ("z", ["cmd"]),
    "redo": ("z", ["cmd", "shift"]),
    "select_all": ("a", ["cmd"]),
    "save": ("s", ["cmd"]),
    "find": ("f", ["cmd"]),
    "new": ("n", ["cmd"]),
    "new_tab": ("t", ["cmd"]),
    "close_tab": ("w", ["cmd"]),
    "close_window": ("w", ["cmd", "shift"]),
    "reopen_closed_tab": ("t", ["cmd", "shift"]),
    "quit": ("q", ["cmd"]),
    "hide_app": ("h", ["cmd"]),
    "switch_app": ("tab", ["cmd"]),
    "spotlight": ("space", ["cmd"]),
    "screenshot": ("4", ["cmd", "shift"]),
    "go_back": ("[", ["cmd"]),
    "go_forward": ("]", ["cmd"]),
    "reload": ("r", ["cmd"]),
    "address_bar": ("l", ["cmd"]),
    "zoom_in": ("=", ["cmd"]),
    "zoom_out": ("-", ["cmd"]),
    "zoom_reset": ("0", ["cmd"]),
    "minimize": ("m", ["cmd"]),
    "fullscreen": ("f", ["cmd", "ctrl"]),
    "next_tab": ("tab", ["ctrl"]),
    "previous_tab": ("tab", ["ctrl", "shift"]),
    "delete_line": ("backspace", ["cmd"]),
    "delete_word": ("backspace", ["alt"]),
    "line_start": ("left", ["cmd"]),
    "line_end": ("right", ["cmd"]),
    "bold": ("b", ["cmd"]),
    "italic": ("i", ["cmd"]),
    "underline": ("u", ["cmd"]),
    "send": ("return", ["cmd"]),
    "emoji_picker": ("space", ["cmd", "ctrl"]),
    "lock_screen": ("q", ["cmd", "ctrl"]),
    "show_desktop": ("f11", []),
}

# AX roles worth numbering for the model. AXStaticText joins only when it carries an AXPress action.
# AXSecureTextField is deliberately absent: password fields are never listed and never read.
KEEP_ROLES = {
    "AXButton",
    "AXCheckBox",
    "AXRadioButton",
    "AXPopUpButton",
    "AXMenuButton",
    "AXTextField",
    "AXTextArea",
    "AXComboBox",
    "AXLink",
    "AXTab",
    "AXCell",
    "AXRow",
    "AXSlider",
    "AXDisclosureTriangle",
}
TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox"}
VALUE_ROLES = TEXT_ROLES | {"AXCheckBox", "AXRadioButton", "AXSlider", "AXPopUpButton"}
SKIP_SUBTREES = {"AXMenu", "AXMenuBar"}
AX_MESSAGE_TIMEOUT = 0.2
# ponytail: caps keep a Chromium page or a mile-long list from stalling the goal loop; raise if a real
# window is being cut short (the walk is breadth-first, so what is lost is the deepest, smallest stuff).
NODE_CAP = 4000
TIME_CAP = 1.5
LABEL_CHARS = 120
TEXT_CHARS = 4000
FANOUT = 8
MIN_SIDE = 4.0  # thinner is a row clipped by its scroll area, or a Chromium sliver: not clickable

_NX_MEDIA_KEYS = {"play_pause": 16, "next": 17, "previous": 18}
_VOLUME_STEP = 10


def _osascript(script: str) -> str:
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "osascript failed")
    return out.stdout.strip()


def _post(event) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.01)


def _front_application():
    """NSWorkspace only learns about activation changes when a run loop turns, so turn it briefly."""
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))
    return NSWorkspace.sharedWorkspace().frontmostApplication()


def _flags(modifiers: list[str] | None) -> int:
    flags = 0
    for m in modifiers or []:
        try:
            flags |= MODIFIER_FLAGS[m.lower()]
        except KeyError:
            raise ValueError(f"unknown modifier {m!r}; use cmd, ctrl, alt, shift") from None
    return flags


def _display_union() -> tuple[float, float, float, float]:
    """Bounding box of every active display as (x0, y0, x1, y1), the space frames are clamped to."""
    err, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
    if err or not ids:
        ids = [Quartz.CGMainDisplayID()]
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for d in ids:
        b = Quartz.CGDisplayBounds(d)
        x0, y0 = min(x0, b.origin.x), min(y0, b.origin.y)
        x1, y1 = max(x1, b.origin.x + b.size.width), max(y1, b.origin.y + b.size.height)
    return x0, y0, x1, y1


# ------------------------------------------------------------------ AX helpers


def _ax(element, name: str):
    """One attribute, or None. A dead or hostile element raises from the bridge; that is a miss."""
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
    except Exception:  # noqa: BLE001  the bridge raises objc.error or ValueError for a dead element
        return None
    return value if err == 0 else None


def _ax_str(element, name: str) -> str:
    v = _ax(element, name)
    if isinstance(v, str):
        return " ".join(v.split())
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return ""


def _ax_frame(element) -> tuple[float, float, float, float] | None:
    pos, size = _ax(element, "AXPosition"), _ax(element, "AXSize")
    if pos is None or size is None:
        return None
    ok_p, pt = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
    ok_s, sz = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    if not (ok_p and ok_s):
        return None
    return float(pt.x), float(pt.y), float(sz.width), float(sz.height)


def _ax_actions(element) -> list[str]:
    try:
        err, names = AS.AXUIElementCopyActionNames(element, None)
    except Exception:  # noqa: BLE001  same bridge behaviour as _ax
        return []
    return [str(n) for n in names] if err == 0 and names else []


def _ax_children(element) -> list:
    return list(_ax(element, "AXChildren") or [])


def _own_label(element, role: str) -> str:
    """AXTitle on AppKit, AXDescription on web and Electron, then the softer fallbacks."""
    for attr in ("AXTitle", "AXDescription"):
        if s := _ax_str(element, attr):
            return s
    if role in TEXT_ROLES and (s := _ax_str(element, "AXPlaceholderValue")):
        return s
    if s := _ax_str(element, "AXLabelValue"):
        return s
    # A subrole means the role description is specific ("close button", "search text field").
    if _ax_str(element, "AXSubrole") and (s := _ax_str(element, "AXRoleDescription")):
        return s
    if s := _ax_str(element, "AXHelp"):
        return s
    if role not in TEXT_ROLES and 0 < len(s := _ax_str(element, "AXValue")) <= LABEL_CHARS:
        return s
    return ""


def _child_label(element) -> str:
    """Rows, cells and icon buttons keep their text in a shallow AXStaticText or AXImage."""
    kids = _ax_children(element)[:FANOUT]
    for depth in (kids, [g for k in kids for g in _ax_children(k)[:FANOUT]]):
        for k in depth:
            role = _ax_str(k, "AXRole")
            if role == "AXStaticText" and (s := _ax_str(k, "AXValue") or _ax_str(k, "AXTitle")):
                return s[:LABEL_CHARS]
            if role == "AXImage" and (s := _ax_str(k, "AXDescription")):
                return s[:LABEL_CHARS]
    return ""


class MacOS:
    name = "macos"

    def __init__(self) -> None:
        self._apps: list[str] | None = None

    # --- apps and navigation ---

    def installed_apps(self) -> list[str]:
        if self._apps is None:
            names = {p.stem for d in APP_DIRS if d.is_dir() for p in d.iterdir() if p.suffix == ".app"}
            self._apps = sorted(names, key=str.lower)
        return list(self._apps)

    def frontmost_app(self) -> str:
        app = _front_application()
        return str(app.localizedName()) if app else ""

    def _frontmost_pid(self) -> int:
        app = _front_application()
        return int(app.processIdentifier()) if app else 0

    def open_app(self, name: str) -> None:
        out = subprocess.run(["open", "-a", name], capture_output=True, text=True, check=False)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or f"could not open {name}")

    def focus_app(self, name: str, timeout: float = 2.0) -> bool:
        if self.frontmost_app().lower() == name.lower():
            return True
        try:
            _osascript(f'tell application "{name}" to activate')  # `open -a` will not raise a running app
        except RuntimeError:
            self.open_app(name)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.frontmost_app().lower() == name.lower():
                time.sleep(0.15)  # let the window take key focus
                return True
            time.sleep(0.05)
        return False

    def open_url(self, url: str) -> None:
        if "://" not in url:
            url = "https://" + url
        subprocess.run(["open", url], capture_output=True, check=True)

    # --- input ---

    def type_text(self, text: str) -> None:
        """Unicode key events, one character at a time: any script, any keyboard layout."""
        for ch in text:
            if ch == "\n":
                self.press("return")
                continue
            if ch == "\t":
                self.press("tab")
                continue
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch.encode("utf-16-le")) // 2, ch)
                _post(ev)

    def press(self, key: str, modifiers: list[str] | None = None, times: int = 1) -> None:
        k = key.lower()
        flags = _flags(modifiers)
        if not flags and len(key) == 1:
            for _ in range(max(1, times)):
                self.type_text(key)
            return
        try:
            code = KEYCODES[k]
        except KeyError:
            raise ValueError(f"unknown key {key!r}") from None
        for _ in range(max(1, times)):
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
                Quartz.CGEventSetFlags(ev, flags)
                _post(ev)

    def scroll(self, direction: str, amount: int = 3) -> None:
        """Scroll wheel events land under the cursor, so it is parked over the front window first."""
        sign = {"up": 1, "down": -1, "left": 1, "right": -1}.get(direction)
        if sign is None:
            raise ValueError(f"unknown scroll direction {direction!r}")
        self._park_cursor()
        for _ in range(max(1, amount)):
            if direction in ("up", "down"):
                ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, sign * 3)
            else:
                ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, 0, sign * 3)
            _post(ev)

    def _park_cursor(self) -> None:
        bounds = self._front_window_bounds()
        if bounds is None:
            return
        x, y, w, h = bounds
        loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        if x <= loc.x <= x + w and y <= loc.y <= y + h:
            return
        pt = (x + w / 2, y + h / 2)
        _post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft))

    def _front_window_bounds(self) -> tuple[float, float, float, float] | None:
        pid = self._frontmost_pid()
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
            if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
                b = w["kCGWindowBounds"]
                if b["Width"] > 50 and b["Height"] > 50:
                    return float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"])
        return None

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        try:
            down, up, btn = MOUSE[button]
        except KeyError:
            raise ValueError(f"unknown mouse button {button!r}") from None
        pt = (float(x), float(y))
        _post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, btn))
        for n in range(1, max(1, clicks) + 1):
            for kind in (down, up):
                ev = Quartz.CGEventCreateMouseEvent(None, kind, pt, btn)
                Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, n)
                _post(ev)

    # --- system ---

    def volume(self, op: str) -> str:
        if op == "mute":
            _osascript("set volume with output muted")
            return "Muted."
        if op == "unmute":
            _osascript("set volume without output muted")
            return "Unmuted."
        if op not in ("up", "down"):
            raise ValueError(f"unknown volume op {op!r}")
        cur = int(_osascript("output volume of (get volume settings)"))
        level = max(0, min(100, cur + (_VOLUME_STEP if op == "up" else -_VOLUME_STEP)))
        _osascript(f"set volume output volume {level}")
        return f"Volume {level}."

    def media(self, op: str) -> None:
        """HID media key event; works for Music, Spotify and browsers alike (from jev-voice)."""
        try:
            key = _NX_MEDIA_KEYS[op]
        except KeyError:
            raise ValueError(f"unknown media op {op!r}") from None
        for down in (True, False):
            flags = 0xA00 if down else 0xB00
            data1 = (key << 16) | ((0xA if down else 0xB) << 8)
            ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                14, (0, 0), flags, 0, 0, None, 8, data1, -1
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev.CGEvent())

    def system(self, op: str) -> str:
        if op == "dark_mode":
            _osascript(
                'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode'
            )
            return "Toggled dark mode."
        if op == "lock":
            self.press("q", ["cmd", "ctrl"])
            return "Locking."
        if op == "sleep_display":
            subprocess.Popen(["pmset", "displaysleepnow"])
            return "Sleeping the display."
        if op == "screenshot":
            out = Path.home() / "Desktop" / f"Screenshot {time.strftime('%Y-%m-%d %H.%M.%S')}.png"
            self.screenshot(out)
            return f"Screenshot saved to the Desktop as {out.name}."
        raise ValueError(f"unknown system op {op!r}")

    # --- perception ---

    def screenshot(self, path: Path | None = None) -> Path:
        out = Path(path) if path else Path(tempfile.gettempdir()) / f"rocky-{int(time.time() * 1000)}.png"
        subprocess.run(["screencapture", "-x", str(out)], check=True, capture_output=True)
        return out

    def beep(self) -> None:
        subprocess.Popen(
            ["afplay", "-v", "0.6", "/System/Library/Sounds/Tink.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def speak(self, text: str) -> None:
        """System voice, in the background, one utterance at a time (a new one cuts the previous)."""
        if not text:
            return
        subprocess.run(["pkill", "-x", "say"], capture_output=True, check=False)
        subprocess.Popen(["say", "-r", "195", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def focused_role(self) -> str:
        """Role of the focused element in the frontmost app. Secure fields are never listed by snapshot(),
        so this is how a caller learns that a password box has focus."""
        app = AS.AXUIElementCreateApplication(self._frontmost_pid())
        AS.AXUIElementSetMessagingTimeout(app, AX_MESSAGE_TIMEOUT)
        focused = _ax(app, "AXFocusedUIElement")
        return _ax_str(focused, "AXRole") if focused is not None else ""

    def snapshot(self, max_items: int = 120) -> Snapshot:
        t0 = time.perf_counter()
        pid = self._frontmost_pid()
        app = AS.AXUIElementCreateApplication(pid)
        AS.AXUIElementSetMessagingTimeout(app, AX_MESSAGE_TIMEOUT)
        for flag in (
            "AXEnhancedUserInterface",
            "AXManualAccessibility",
        ):  # browsers hide web content until asked
            with contextlib.suppress(Exception):  # apps without the attribute raise; that is fine
                AS.AXUIElementSetAttributeValue(app, flag, True)
        window = _ax(app, "AXFocusedWindow") or _ax(app, "AXMainWindow")
        if window is None:
            windows = _ax(app, "AXWindows") or []
            window = windows[0] if windows else None
        root = window if window is not None else app
        items, texts = self._walk(root, max_items)
        main = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return Snapshot(
            app=self.frontmost_app(),
            window_title=_ax_str(window, "AXTitle") if window is not None else "",
            items=items,
            text="\n".join(texts)[:TEXT_CHARS],
            screen_w=int(main.size.width),
            screen_h=int(main.size.height),
            taken_ms=int((time.perf_counter() - t0) * 1000),
        )

    def _walk(self, root, max_items: int) -> tuple[list[Item], list[str]]:
        """Breadth-first over the AX tree: numbered controls on screen plus the static text seen."""
        sx0, sy0, sx1, sy1 = _display_union()
        deadline = time.monotonic() + TIME_CAP
        queue = deque([root])
        visited: set = set()
        keys: set[tuple] = set()
        items: list[Item] = []
        texts: list[str] = []
        seen = 0
        while queue and len(items) < max_items and seen < NODE_CAP and time.monotonic() < deadline:
            node = queue.popleft()
            try:
                ident = hash(node)
            except TypeError:
                ident = id(node)
            if ident in visited:
                continue
            visited.add(ident)
            seen += 1
            role = _ax_str(node, "AXRole")
            if role in SKIP_SUBTREES:
                continue
            frame = _ax_frame(node)
            if frame is not None:
                x, y, w, h = frame
                cx0, cy0 = max(x, sx0), max(y, sy0)
                cx1, cy1 = min(x + w, sx1), min(y + h, sy1)
                frame = (cx0, cy0, cx1 - cx0, cy1 - cy0)
            on_screen = frame is not None and min(frame[2], frame[3]) >= MIN_SIDE
            if role == "AXStaticText" and on_screen:
                if s := _ax_str(node, "AXValue") or _ax_str(node, "AXTitle"):
                    texts.append(s)
                keep = "AXPress" in _ax_actions(node)
            else:
                keep = role in KEEP_ROLES
            if keep and on_screen and _ax(node, "AXEnabled") is not False:
                label = _own_label(node, role) or _child_label(node)
                if label:
                    x, y, w, h = frame
                    key = (role, label, round(x), round(y), round(w), round(h))
                    if key not in keys:
                        keys.add(key)
                        items.append(
                            Item(
                                index=len(items) + 1,
                                role=role,
                                label=label[:LABEL_CHARS],
                                x=x,
                                y=y,
                                w=w,
                                h=h,
                                value=_ax_str(node, "AXValue")[:LABEL_CHARS] if role in VALUE_ROLES else "",
                                focused=_ax(node, "AXFocused") is True,
                            )
                        )
            queue.extend(_ax_children(node))
        return items, texts

    # --- shortcuts and permissions ---

    def shortcuts(self) -> dict[str, tuple[str, list[str]]]:
        return {k: (key, list(mods)) for k, (key, mods) in SHORTCUTS.items()}

    def check_permissions(self) -> dict[str, bool]:
        mic = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
        try:
            listen = bool(Quartz.CGPreflightListenEventAccess())
        except (AttributeError, ValueError):  # older pyobjc or macOS without the call
            listen = False
        return {
            "accessibility": bool(AS.AXIsProcessTrusted()),
            "microphone": mic == AVFoundation.AVAuthorizationStatusAuthorized,
            "input_monitoring": listen,
        }
