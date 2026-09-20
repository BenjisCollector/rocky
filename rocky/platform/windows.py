"""Windows Platform on the `uiautomation` package (UI Automation) and pywin32.

UNTESTED on real Windows as of 2026-09-20; tested only for import-safety and with mocked uiautomation.
Written against uiautomation 2.0.29 source (yinkaisheng/Python-UIAutomation-for-Windows). Nothing in
this file has run on a Windows machine yet; treat every method as a first draft until it has.

Coordinates are virtual-screen pixels: origin at the top-left of the PRIMARY monitor, y growing downward.
UIA BoundingRectangle and SetCursorPos share that space, so an Item frame goes straight to click().
Snapshot.screen_w/h report the primary monitor only.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path

from ..models import Item, Snapshot

try:  # only importable on Windows; rocky/platform/__init__.py never imports this module elsewhere
    import uiautomation as auto
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover
    auto = win32api = win32con = win32gui = win32process = None

START_MENUS = (
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
)

VK: dict[str, int] = {
    "return": 0x0D,
    "enter": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x08,
    "forward_delete": 0x2E,
    "escape": 0x1B,
    "esc": 0x1B,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN = 0x10, 0x11, 0x12, 0x5B
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
VK_MEDIA = {"next": 0xB0, "previous": 0xB1, "play_pause": 0xB3}
MODIFIER_VK = {
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "alt": VK_MENU,
    "option": VK_MENU,
    "shift": VK_SHIFT,
    "cmd": VK_LWIN,  # macOS vocabulary lands on the Windows key; shortcuts() below already says ctrl
    "command": VK_LWIN,
    "win": VK_LWIN,
}

SHORTCUTS: dict[str, tuple[str, list[str]]] = {
    "copy": ("c", ["ctrl"]),
    "paste": ("v", ["ctrl"]),
    "cut": ("x", ["ctrl"]),
    "undo": ("z", ["ctrl"]),
    "redo": ("y", ["ctrl"]),
    "select_all": ("a", ["ctrl"]),
    "save": ("s", ["ctrl"]),
    "find": ("f", ["ctrl"]),
    "new": ("n", ["ctrl"]),
    "new_tab": ("t", ["ctrl"]),
    "close_tab": ("w", ["ctrl"]),
    "close_window": ("f4", ["alt"]),
    "reopen_closed_tab": ("t", ["ctrl", "shift"]),
    "quit": ("f4", ["alt"]),
    "switch_app": ("tab", ["alt"]),
    "spotlight": ("s", ["win"]),
    "screenshot": ("s", ["win", "shift"]),
    "go_back": ("left", ["alt"]),
    "go_forward": ("right", ["alt"]),
    "reload": ("f5", []),
    "address_bar": ("l", ["ctrl"]),
    "zoom_in": ("=", ["ctrl"]),
    "zoom_out": ("-", ["ctrl"]),
    "zoom_reset": ("0", ["ctrl"]),
    "minimize": ("down", ["win"]),
    "fullscreen": ("f11", []),
    "next_tab": ("tab", ["ctrl"]),
    "previous_tab": ("tab", ["ctrl", "shift"]),
    "delete_line": ("backspace", ["ctrl", "shift"]),
    "delete_word": ("backspace", ["ctrl"]),
    "line_start": ("home", []),
    "line_end": ("end", []),
    "bold": ("b", ["ctrl"]),
    "italic": ("i", ["ctrl"]),
    "underline": ("u", ["ctrl"]),
    "send": ("return", ["ctrl"]),
    "emoji_picker": (".", ["win"]),
    "lock_screen": ("l", ["win"]),
    "show_desktop": ("d", ["win"]),
}

KEEP_TYPES = {
    "ButtonControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "ComboBoxControl",
    "EditControl",
    "HyperlinkControl",
    "TabItemControl",
    "ListItemControl",
    "MenuItemControl",
    "TreeItemControl",
    "DataItemControl",
}
VALUE_TYPES = {"EditControl", "ComboBoxControl"}
PATTERN_VALUE = 10002  # UIA_ValuePatternId
# ponytail: same caps as macOS; a Chromium page can hand UIA tens of thousands of nodes.
NODE_CAP = 4000
TIME_CAP = 1.5
LABEL_CHARS = 120
TEXT_CHARS = 4000
MIN_SIDE = 4


def _need_auto() -> None:
    if auto is None:
        raise RuntimeError("uiautomation and pywin32 are Windows-only; install rocky on Windows to use this")


class Windows:
    name = "windows"

    def __init__(self) -> None:
        self._apps: dict[str, Path] | None = None

    # --- apps and navigation ---

    def _shortcuts_on_disk(self) -> dict[str, Path]:
        if self._apps is None:
            found: dict[str, Path] = {}
            for root in START_MENUS:
                if root.is_dir():
                    for lnk in root.rglob("*.lnk"):
                        if not lnk.stem.lower().startswith("uninstall"):
                            found.setdefault(lnk.stem, lnk)
            self._apps = found
        return self._apps

    def installed_apps(self) -> list[str]:
        return sorted(self._shortcuts_on_disk(), key=str.lower)

    def _foreground(self) -> tuple[int, str, str]:
        """(hwnd, window title, exe stem) of the foreground window."""
        _need_auto()
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) if hwnd else ""
        exe = ""
        with contextlib.suppress(Exception):  # protected or vanished process
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            exe = Path(win32process.GetModuleFileNameEx(h, 0)).stem
        return hwnd, title, exe

    def frontmost_app(self) -> str:
        """What people call the app: the last " - " segment of the title ("Untitled - Notepad" is Notepad),
        else the exe stem."""
        _, title, exe = self._foreground()
        if " - " in title:
            return title.rsplit(" - ", 1)[1].strip()
        return exe or title

    def open_app(self, name: str) -> None:
        for stem, lnk in self._shortcuts_on_disk().items():
            if stem.lower() == name.lower():
                os.startfile(str(lnk))
                return
        os.startfile(name)  # PATH and App Paths registry: "notepad", "calc", "msedge"

    def focus_app(self, name: str, timeout: float = 2.0) -> bool:
        _need_auto()
        want = name.lower()
        if self.frontmost_app().lower() == want:
            return True
        for win in auto.GetRootControl().GetChildren():
            if want in (win.Name or "").lower():
                win.SetActive()
                break
        else:
            self.open_app(name)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if want in self.frontmost_app().lower():
                time.sleep(0.15)
                return True
            time.sleep(0.05)
        return False

    def open_url(self, url: str) -> None:
        if "://" not in url:
            url = "https://" + url
        webbrowser.open(url)

    # --- input ---

    def type_text(self, text: str) -> None:
        """KEYEVENTF_UNICODE per character via uiautomation.SendUnicodeChar: any script, any layout."""
        _need_auto()
        for ch in text:
            if ch == "\n":
                auto.SendKey(VK["return"], waitTime=0)
            elif ch == "\t":
                auto.SendKey(VK["tab"], waitTime=0)
            else:
                auto.SendUnicodeChar(ch)
            time.sleep(0.005)

    def _vk_for(self, key: str) -> int:
        k = key.lower()
        if k in VK:
            return VK[k]
        if len(key) == 1:
            import ctypes  # Windows-only path

            res = ctypes.windll.user32.VkKeyScanW(ord(key))
            if res != -1:
                return res & 0xFF
        raise ValueError(f"unknown key {key!r}")

    def press(self, key: str, modifiers: list[str] | None = None, times: int = 1) -> None:
        _need_auto()
        mods = []
        for m in modifiers or []:
            try:
                mods.append(MODIFIER_VK[m.lower()])
            except KeyError:
                raise ValueError(f"unknown modifier {m!r}; use ctrl, alt, shift, win") from None
        if not mods and len(key) == 1:
            for _ in range(max(1, times)):
                auto.SendUnicodeChar(key)
            return
        vk = self._vk_for(key)
        for _ in range(max(1, times)):
            for m in mods:
                auto.PressKey(m, waitTime=0)
            auto.SendKey(vk, waitTime=0)
            for m in reversed(mods):
                auto.ReleaseKey(m, waitTime=0)
            time.sleep(0.02)

    def scroll(self, direction: str, amount: int = 3) -> None:
        _need_auto()
        n = max(1, amount)
        if direction == "down":
            auto.WheelDown(n, waitTime=0)
        elif direction == "up":
            auto.WheelUp(n, waitTime=0)
        elif direction in ("left", "right"):  # Shift + wheel scrolls sideways in most apps
            auto.PressKey(VK_SHIFT, waitTime=0)
            (auto.WheelUp if direction == "left" else auto.WheelDown)(n, waitTime=0)
            auto.ReleaseKey(VK_SHIFT, waitTime=0)
        else:
            raise ValueError(f"unknown scroll direction {direction!r}")

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        _need_auto()
        fn = {"left": auto.Click, "right": auto.RightClick, "middle": auto.MiddleClick}.get(button)
        if fn is None:
            raise ValueError(f"unknown mouse button {button!r}")
        for _ in range(max(1, clicks)):
            fn(int(x), int(y), waitTime=0.05)

    # --- system ---

    def volume(self, op: str) -> str:
        _need_auto()
        if op == "mute" or op == "unmute":  # one toggle key; the OS decides which way it goes
            auto.SendKey(VK_VOLUME_MUTE, waitTime=0)
            return "Muted." if op == "mute" else "Unmuted."
        if op not in ("up", "down"):
            raise ValueError(f"unknown volume op {op!r}")
        for _ in range(5):  # each press is one 2% step
            auto.SendKey(VK_VOLUME_UP if op == "up" else VK_VOLUME_DOWN, waitTime=0)
        return "Louder." if op == "up" else "Quieter."

    def media(self, op: str) -> None:
        _need_auto()
        try:
            auto.SendKey(VK_MEDIA[op], waitTime=0)
        except KeyError:
            raise ValueError(f"unknown media op {op!r}") from None

    def system(self, op: str) -> str:
        if op == "lock":
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
            return "Locking."
        if op == "dark_mode":
            import winreg  # Windows-only stdlib module

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                0,
                winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            with key:
                light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if light else 1)
            return "Dark mode on." if light else "Dark mode off."
        if op == "sleep_display":
            import ctypes

            ctypes.windll.user32.SendMessageW(
                0xFFFF, 0x0112, 0xF170, 2
            )  # HWND_BROADCAST WM_SYSCOMMAND SC_MONITORPOWER off
            return "Sleeping the display."
        if op == "screenshot":
            out = Path.home() / "Pictures" / f"Screenshot {time.strftime('%Y-%m-%d %H.%M.%S')}.png"
            self.screenshot(out)
            return f"Screenshot saved to Pictures as {out.name}."
        raise ValueError(f"unknown system op {op!r}")

    # --- perception ---

    def screenshot(self, path: Path | None = None) -> Path:
        out = Path(path) if path else Path(tempfile.gettempdir()) / f"rocky-{int(time.time() * 1000)}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):  # GDI+ path failed: fall through to PowerShell
            _need_auto()
            bmp = auto.Bitmap.FromControl(auto.GetRootControl())
            if bmp is not None and bmp.ToFile(str(out)):
                return out
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
            f"$bmp.Save('{out}');"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
        return out

    def _screen_union(self) -> tuple[int, int, int, int]:
        rects = auto.GetMonitorsRect() or []
        if not rects:
            w, h = auto.GetScreenSize()
            return 0, 0, w, h
        return (
            min(r.left for r in rects),
            min(r.top for r in rects),
            max(r.right for r in rects),
            max(r.bottom for r in rects),
        )

    def beep(self) -> None:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", "[console]::beep(880,120)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def speak(self, text: str) -> None:
        """SAPI through PowerShell, in the background."""
        if not text:
            return
        safe = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe}')"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def focused_role(self) -> str:
        """ControlTypeName of the focused control, with "Password" appended when UIA marks it as one."""
        _need_auto()
        try:
            control = auto.GetFocusedControl()
        except Exception:  # noqa: BLE001  UIA raises COM errors when focus is in flux
            return ""
        if control is None:
            return ""
        role = getattr(control, "ControlTypeName", "") or ""
        return role + " Password" if getattr(control, "IsPassword", False) else role

    def snapshot(self, max_items: int = 120) -> Snapshot:
        _need_auto()
        t0 = time.perf_counter()
        _, title, _ = self._foreground()
        top = auto.GetForegroundControl()
        items, texts = self._walk(top, max_items)
        w, h = auto.GetScreenSize()
        return Snapshot(
            app=self.frontmost_app(),
            window_title=title,
            items=items,
            text="\n".join(texts)[:TEXT_CHARS],
            screen_w=int(w),
            screen_h=int(h),
            taken_ms=int((time.perf_counter() - t0) * 1000),
        )

    def _walk(self, top, max_items: int) -> tuple[list[Item], list[str]]:
        sx0, sy0, sx1, sy1 = self._screen_union()
        deadline = time.monotonic() + TIME_CAP
        keys: set[tuple] = set()
        items: list[Item] = []
        texts: list[str] = []
        seen = 0
        for control, _depth in auto.WalkControl(top, includeTop=False):
            seen += 1
            if seen > NODE_CAP or time.monotonic() > deadline or len(items) >= max_items:
                break
            try:
                kind = control.ControlTypeName
                if control.IsOffscreen:
                    continue
                r = control.BoundingRectangle
            except Exception:  # noqa: BLE001  comtypes raises COMError when an element vanishes
                continue
            x0, y0 = max(r.left, sx0), max(r.top, sy0)
            x1, y1 = min(r.right, sx1), min(r.bottom, sy1)
            if min(x1 - x0, y1 - y0) < MIN_SIDE:
                continue
            name = " ".join((control.Name or "").split())
            if kind == "TextControl":
                if name:
                    texts.append(name)
                continue
            if kind not in KEEP_TYPES or not control.IsEnabled:
                continue
            if getattr(control, "IsPassword", False):
                continue  # never listed, never read
            label = name or control.AutomationId or control.HelpText or ""
            if not label:
                continue
            key = (kind, label, x0, y0, x1 - x0, y1 - y0)
            if key in keys:
                continue
            keys.add(key)
            value = ""
            if kind in VALUE_TYPES:
                try:
                    pattern = control.GetPattern(PATTERN_VALUE)
                    value = (pattern.Value or "")[:LABEL_CHARS] if pattern else ""
                except Exception:  # noqa: BLE001  pattern not supported after all
                    value = ""
            items.append(
                Item(
                    index=len(items) + 1,
                    role=kind,
                    label=label[:LABEL_CHARS],
                    x=float(x0),
                    y=float(y0),
                    w=float(x1 - x0),
                    h=float(y1 - y0),
                    source="uia",
                    value=value,
                    focused=bool(control.HasKeyboardFocus),
                )
            )
        return items, texts

    # --- shortcuts and permissions ---

    def shortcuts(self) -> dict[str, tuple[str, list[str]]]:
        return {k: (key, list(mods)) for k, (key, mods) in SHORTCUTS.items()}

    def check_permissions(self) -> dict[str, bool]:
        """UIA and SendInput need no permission. The microphone is a privacy toggle: read the consent
        store for desktop (NonPackaged) apps; anything unreadable counts as not granted."""
        mic = False
        try:
            import winreg

            base = (
                r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
            )
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
                allowed = winreg.QueryValueEx(k, "Value")[0] == "Allow"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\NonPackaged") as k:
                mic = allowed and winreg.QueryValueEx(k, "Value")[0] == "Allow"
        except (ImportError, OSError):
            mic = False
        return {"accessibility": auto is not None, "microphone": mic, "input_monitoring": auto is not None}
