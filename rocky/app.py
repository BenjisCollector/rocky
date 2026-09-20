"""The Mac app shell: one NSApplication that owns the menu bar item, the Rocky overlay, the control panel
and the voice loop. `python -m rocky.app` or the `rocky-app` script runs it; scripts/build_app.sh wraps it
in dist/Rocky.app.

The voice loop runs on a background thread and calls the same route/execute path as `rocky --text`. Every
UI update crosses to the main thread through `call:`. Set ROCKY_NO_VOICE=1 to run the shell without a
microphone (UI work, screenshots).
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from . import __version__, config, events
from .models import Plan

ACCENT = (0.30, 0.95, 0.45)  # watermelon green, used sparingly
PW, PH = 340, 520  # panel size in points
PAD = 24
CW = PW - 2 * PAD
TOGGLE = 56
HISTORY = 5

STATUS_WORD = {
    "idle": "Idle",
    "hearing": "Hearing",
    "thinking": "Thinking",
    "speaking": "Speaking",
    "done": "Done",
    "error": "Error",
}
PERMISSIONS = (
    ("accessibility", "Accessibility", "Privacy_Accessibility"),
    ("microphone", "Microphone", "Privacy_Microphone"),
    ("input_monitoring", "Input Monitoring", "Privacy_ListenEvent"),
)
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?{pane}"


def plan_line(plan: Plan) -> str:
    """open_app(Photo Booth) 1.00 412ms"""
    primary = next(iter(plan.args.values()), "")
    return f"{plan.kind}({primary}) {plan.confidence:.2f} {plan.latency_ms}ms"


if sys.platform == "darwin":
    import objc
    import Quartz  # noqa: F401  registers CGColorRef so layer colors bridge without a pointer warning
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSAttributedString,
        NSBackingStoreBuffered,
        NSBezelStyleRounded,
        NSBezierPath,
        NSBitmapImageFileTypePNG,
        NSBitmapImageRep,
        NSButton,
        NSButtonTypeMomentaryChange,
        NSButtonTypeToggle,
        NSCalibratedRGBColorSpace,
        NSColor,
        NSControlSizeSmall,
        NSControlStateValueOff,
        NSControlStateValueOn,
        NSFloatingWindowLevel,
        NSFont,
        NSFontAttributeName,
        NSFontWeightMedium,
        NSFontWeightRegular,
        NSFontWeightSemibold,
        NSForegroundColorAttributeName,
        NSGraphicsContext,
        NSImage,
        NSImageOnly,
        NSImageScaleProportionallyDown,
        NSImageSymbolConfiguration,
        NSImageView,
        NSLineBreakByTruncatingTail,
        NSMakeRect,
        NSMenu,
        NSMenuItem,
        NSPanel,
        NSScreen,
        NSSegmentedControl,
        NSSegmentStyleRounded,
        NSSegmentSwitchTrackingSelectOne,
        NSStatusBar,
        NSTextAlignmentCenter,
        NSTextAlignmentRight,
        NSTextField,
        NSThread,
        NSTimer,
        NSUserDefaults,
        NSVariableStatusItemLength,
        NSView,
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectMaterialSidebar,
        NSVisualEffectStateActive,
        NSVisualEffectView,
        NSWindowStyleMaskBorderless,
        NSWindowStyleMaskNonactivatingPanel,
        NSWorkspace,
    )
    from Foundation import NSURL, NSObject

    from .overlay import Overlay

    DOT_COLORS = {
        "idle": lambda: NSColor.systemGrayColor(),
        "hearing": lambda: NSColor.colorWithCalibratedRed_green_blue_alpha_(*ACCENT, 1.0),
        "thinking": lambda: NSColor.systemBlueColor(),
        "speaking": lambda: NSColor.systemPinkColor(),
        "done": lambda: NSColor.colorWithCalibratedRed_green_blue_alpha_(*ACCENT, 1.0),
        "error": lambda: NSColor.systemRedColor(),
    }

    def _accent() -> NSColor:
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(*ACCENT, 1.0)

    def _font(size: float, weight=NSFontWeightRegular, mono: bool = False) -> NSFont:
        if mono:
            return NSFont.monospacedSystemFontOfSize_weight_(size, weight)
        return NSFont.systemFontOfSize_weight_(size, weight)

    def _label(text: str, size: float = 13, weight=NSFontWeightRegular, color=None, mono=False, align=None):
        f = NSTextField.labelWithString_(text)
        f.setFont_(_font(size, weight, mono))
        if color is not None:
            f.setTextColor_(color)
        if align is not None:
            f.setAlignment_(align)
        f.setLineBreakMode_(NSLineBreakByTruncatingTail)
        f.setMaximumNumberOfLines_(1)
        return f

    def _symbol(name: str, size: float, *colors) -> NSImage:
        """An SF Symbol at `size`; `colors` are palette colors, glyph first, then the backing shape."""
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        conf = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, NSFontWeightMedium)
        if colors:
            conf = conf.configurationByApplyingConfiguration_(
                NSImageSymbolConfiguration.configurationWithPaletteColors_(list(colors))
            )
        return img.imageWithSymbolConfiguration_(conf)

    def emoji_bitmap(px: int, glyph: str = "🍉") -> NSBitmapImageRep:
        """The watermelon emoji rendered into a px by px bitmap. Menu bar image and the .icns share it."""
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, px, px, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
        )
        rep.setSize_((px, px))
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(ctx)
        font = NSFont.fontWithName_size_("Apple Color Emoji", px * 0.76) or NSFont.systemFontOfSize_(
            px * 0.76
        )
        s = NSAttributedString.alloc().initWithString_attributes_(glyph, {NSFontAttributeName: font})
        size = s.size()
        s.drawAtPoint_(((px - size.width) / 2, (px - size.height) / 2))
        ctx.flushGraphics()
        NSGraphicsContext.restoreGraphicsState()
        return rep

    def write_png(rep: NSBitmapImageRep, path: Path) -> None:
        rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None).writeToFile_atomically_(
            str(path), True
        )

    def _status_image() -> NSImage:
        img = NSImage.alloc().initWithSize_((18, 18))
        img.addRepresentation_(emoji_bitmap(36))  # 2x for retina menu bars
        return img

    def _toggle_image(on: bool) -> NSImage:
        """A filled circle with a mic symbol: green while listening, quiet grey while paused."""

        def draw(rect):
            (_accent() if on else NSColor.labelColor().colorWithAlphaComponent_(0.08)).set()
            NSBezierPath.bezierPathWithOvalInRect_(rect).fill()
            color = NSColor.whiteColor() if on else NSColor.secondaryLabelColor()
            sym = _symbol("mic.fill" if on else "mic.slash.fill", 22, color)
            s = sym.size()
            sym.drawInRect_(NSMakeRect((TOGGLE - s.width) / 2, (TOGGLE - s.height) / 2, s.width, s.height))
            return True

        return NSImage.imageWithSize_flipped_drawingHandler_((TOGGLE, TOGGLE), False, draw)

    class _Panel(NSPanel):
        def canBecomeKeyWindow(self):
            return True

    class AppController(NSObject):
        """Owns every window and the voice thread. Selectors (`name_`) run on the main thread; the voice
        thread hands work over through `call:`."""

        def init(self):
            self = objc.super(AppController, self).init()
            if self is None:
                return None
            self.paused = False
            self.act = False  # screen tasks dry-run until the user flips the segmented control
            self.history: deque[tuple[str, str]] = deque(maxlen=HISTORY)
            self.platform = None
            self.jev = None
            self.state = "idle"
            return self

        # ------------------------------------------------------------------ building

        def build(self):
            """Create the status item, the overlay and the panel. No windows are shown yet."""
            self.app = NSApplication.sharedApplication()
            self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            self.overlay = Overlay()
            self.buildMenu()
            self.buildPanel()
            events.subscribe(self.onEvent)
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                3.0, self, "refreshPermissions:", None, True
            )

        def buildMenu(self):
            self.menu = NSMenu.alloc().init()
            items = (
                ("Show Control Panel", "showPanel:"),
                ("Pause Listening", "togglePause:"),
                ("Hide Rocky", "toggleOverlay:"),
                ("Dry Run for Screen Tasks", "toggleDry:"),
                (None, None),
                ("Quit Rocky", "quit:"),
            )
            for title, action in items:
                if title is None:
                    self.menu.addItem_(NSMenuItem.separatorItem())
                    continue
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
                item.setTarget_(self)
                self.menu.addItem_(item)
            self.pause_item, self.overlay_item, self.dry_item = (self.menu.itemAtIndex_(i) for i in (1, 2, 3))
            self.dry_item.setState_(NSControlStateValueOn)
            self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
            self.status_item.button().setImage_(_status_image())
            self.status_item.button().setToolTip_("Rocky")
            self.status_item.setMenu_(self.menu)

        def buildPanel(self):
            vf = NSScreen.mainScreen().visibleFrame()
            frame = NSMakeRect(
                vf.origin.x + vf.size.width - PW - 16, vf.origin.y + vf.size.height - PH - 16, PW, PH
            )
            self.panel = _Panel.alloc().initWithContentRect_styleMask_backing_defer_(
                frame,
                NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
                NSBackingStoreBuffered,
                False,
            )
            p = self.panel
            p.setOpaque_(False)
            p.setBackgroundColor_(NSColor.clearColor())
            p.setHasShadow_(True)
            p.setLevel_(NSFloatingWindowLevel)
            p.setFloatingPanel_(True)
            p.setHidesOnDeactivate_(False)
            p.setMovableByWindowBackground_(True)
            p.setTitle_("Rocky")
            p.setFrameAutosaveName_("RockyControlPanel")

            root = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, PW, PH))
            root.setMaterial_(NSVisualEffectMaterialSidebar)
            root.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            root.setState_(NSVisualEffectStateActive)
            root.setWantsLayer_(True)
            root.layer().setCornerRadius_(14)
            root.layer().setMasksToBounds_(True)
            p.setContentView_(root)
            self.root = root

            def place(view, x, top, w, h):
                view.setFrame_(NSMakeRect(x, PH - top - h, w, h))
                root.addSubview_(view)
                return view

            secondary = NSColor.secondaryLabelColor()
            tertiary = NSColor.tertiaryLabelColor()

            # (a) header: dot, one word, wake word; a second muted line for replies and errors
            y = 16
            self.dot = place(NSView.alloc().init(), PAD, y + 5, 10, 10)
            self.dot.setWantsLayer_(True)
            self.dot.layer().setCornerRadius_(5)
            self.status = place(_label("Idle", 15, NSFontWeightMedium), PAD + 18, y, 160, 20)
            wake = _label(f"“{config.WAKE_WORD}”", 12, color=tertiary, align=NSTextAlignmentRight)
            place(wake, PW - PAD - 120, y + 1, 120, 18)
            y += 24
            self.sub = place(
                _label("Say the wake word, then what you want.", 12, color=secondary), PAD, y, CW, 16
            )

            # (b) the big round toggle
            y += 32
            self.toggle = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, TOGGLE, TOGGLE))
            self.toggle.setButtonType_(NSButtonTypeToggle)
            self.toggle.setBordered_(False)
            self.toggle.setImagePosition_(NSImageOnly)
            self.toggle.setImage_(_toggle_image(False))
            self.toggle.setAlternateImage_(_toggle_image(True))
            self.toggle.setState_(NSControlStateValueOn)
            self.toggle.setTarget_(self)
            self.toggle.setAction_("listenToggled:")
            place(self.toggle, (PW - TOGGLE) / 2, y, TOGGLE, TOGGLE)
            y += TOGGLE + 8
            self.listen_label = place(
                _label("Listening", 13, NSFontWeightMedium, align=NSTextAlignmentCenter), PAD, y, CW, 16
            )
            y += 16
            place(
                _label("or hold Right Option", 11, color=tertiary, align=NSTextAlignmentCenter),
                PAD,
                y,
                CW,
                14,
            )

            # (c) last commands
            y += 14 + 16
            place(_label("Last commands", 11, NSFontWeightSemibold, color=secondary), PAD, y, CW, 16)
            y += 18
            self.rows = []
            for _ in range(HISTORY):
                utter = place(_label("", 12), PAD, y, CW, 15)
                line = place(_label("", 11, color=secondary, mono=True), PAD, y + 15, CW, 13)
                self.rows.append((utter, line))
                y += 28
            self.rows[0][0].setStringValue_("Nothing yet.")
            self.rows[0][0].setTextColor_(tertiary)
            self.rows[0][1].setStringValue_(f"Try: {config.WAKE_WORD}, open Safari")
            self.rows[0][1].setTextColor_(tertiary)

            # (d) screen tasks
            y += 12
            place(_label("Screen tasks", 13), PAD, y + 4, 140, 16)
            seg = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(0, 0, 150, 24))
            seg.setSegmentCount_(2)
            seg.setLabel_forSegment_("Dry run", 0)
            seg.setLabel_forSegment_("Do it", 1)
            for i in (0, 1):
                seg.setWidth_forSegment_(72, i)
            seg.setSegmentStyle_(NSSegmentStyleRounded)
            seg.setTrackingMode_(NSSegmentSwitchTrackingSelectOne)
            seg.setControlSize_(NSControlSizeSmall)
            seg.setFont_(_font(11))
            seg.setSelectedSegment_(0)
            seg.setTarget_(self)
            seg.setAction_("segmentChanged:")
            self.segment = place(seg, PW - PAD - 150, y, 150, 24)

            # (e) permissions
            y += 24 + 16
            place(_label("Permissions", 11, NSFontWeightSemibold, color=secondary), PAD, y, CW, 16)
            y += 20
            self.perm_views = {}
            for i, (key, title, _pane) in enumerate(PERMISSIONS):
                place(_label(title, 13), PAD, y + 4, 180, 16)
                check = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
                check.setImage_(_symbol("checkmark.circle.fill", 13, NSColor.whiteColor(), _accent()))
                check.setImageScaling_(NSImageScaleProportionallyDown)
                place(check, PW - PAD - 16, y + 4, 16, 16)
                btn = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 22))
                btn.setTitle_("Open Settings")
                btn.setBezelStyle_(NSBezelStyleRounded)
                btn.setControlSize_(NSControlSizeSmall)
                btn.setFont_(_font(11))
                btn.setTag_(i)
                btn.setTarget_(self)
                btn.setAction_("openSettings:")
                place(btn, PW - PAD - 100, y + 1, 100, 22)
                self.perm_views[key] = (check, btn)
                y += 24

            # (f) footer
            y += 8
            place(_label(f"Rocky {__version__}", 11, color=tertiary), PAD, y, 120, 16)
            quit_btn = self.textButton("Quit", "quit:", secondary)
            qw = quit_btn.frame().size.width
            place(quit_btn, PW - PAD - qw, y, qw, 16)
            runs_btn = self.textButton("Open runs folder", "openRuns:", secondary)
            rw = runs_btn.frame().size.width
            place(runs_btn, PW - PAD - qw - 16 - rw, y, rw, 16)

            self.applyState_("idle")
            self.refreshPermissions_(None)

        @objc.python_method
        def textButton(self, title: str, action: str, color) -> NSButton:
            attrs = {NSFontAttributeName: _font(11), NSForegroundColorAttributeName: color}
            s = NSAttributedString.alloc().initWithString_attributes_(title, attrs)
            b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, s.size().width + 2, 16))
            b.setBordered_(False)
            b.setButtonType_(NSButtonTypeMomentaryChange)
            b.setAttributedTitle_(s)
            b.setTarget_(self)
            b.setAction_(action)
            return b

        # ------------------------------------------------------------------ running

        @objc.python_method
        def start(self, voice: bool = True) -> None:
            """Show the overlay, open the panel on first launch, start listening."""
            from .jev import Jev
            from .platform import get_platform

            self.platform = get_platform()
            self.jev = Jev()
            self.overlay.show()
            defaults = NSUserDefaults.standardUserDefaults()
            if not defaults.boolForKey_("RockyDidLaunch"):
                defaults.setBool_forKey_(True, "RockyDidLaunch")
                self.showPanel_(None)
            if voice:
                threading.Thread(target=self.voiceLoop, daemon=True, name="rocky-voice").start()
            else:
                self.sub.setStringValue_("Microphone off (ROCKY_NO_VOICE).")

        @objc.python_method
        def voiceLoop(self) -> None:
            try:
                from .voice import listen_forever

                listen_forever(
                    self.onUtterance,
                    wake_word=config.WAKE_WORD,
                    backend=config.STT_BACKEND,
                    language=config.STT_LANGUAGE,
                )
            except Exception as e:  # noqa: BLE001  the shell stays up so the user can read the error
                message = f"Voice stopped: {e}"
                self.main(lambda: self.showError(message))
                events.emit("error")

        @objc.python_method
        def onUtterance(self, text: str) -> None:
            """Same path as `rocky --text`: route, then execute. Runs on the voice thread."""
            if self.paused:
                return
            events.emit("thinking")
            try:
                reply, line = self.runUtterance(text)
            except Exception as e:  # noqa: BLE001  one bad command must not kill the loop
                events.emit("error")
                error = str(e)
                self.main(lambda: self.record(text, "failed", error))
                return
            self.main(lambda: self.record(text, line, reply))
            events.emit("speaking" if reply else "done")
            time.sleep(1.2)  # ponytail: lets the overlay finish its beam before the voice loop resets to idle

        @objc.python_method
        def runUtterance(self, text: str) -> tuple[str, str]:
            from .fast import execute
            from .router import route, split_compound

            plan = route(self.jev, self.platform, text)
            line = plan_line(plan)
            parts = split_compound(text) if plan.compound else [text]
            if len(parts) == 1:
                return execute(plan, self.platform, self.act, jev=self.jev, prompt_fn=self.confirm), line
            replies = []
            for part in parts:
                sub = route(self.jev, self.platform, part)
                replies.append(execute(sub, self.platform, self.act, jev=self.jev, prompt_fn=self.confirm))
            return " ".join(r for r in replies if r), line

        @objc.python_method
        def confirm(self, prompt: str) -> str:
            """The risk gate's yes/no: ask in the panel, listen once."""
            from .voice import listen_once

            self.main(lambda: self.sub.setStringValue_(prompt))
            return listen_once(5.0, backend=config.STT_BACKEND, language=config.STT_LANGUAGE)

        # ------------------------------------------------------------------ main-thread plumbing

        @objc.python_method
        def main(self, fn: Callable[[], None]) -> None:
            if NSThread.isMainThread():
                fn()
            else:
                self.performSelectorOnMainThread_withObject_waitUntilDone_("call:", fn, False)

        def call_(self, fn):
            fn()

        @objc.python_method
        def onEvent(self, state: str) -> None:
            self.performSelectorOnMainThread_withObject_waitUntilDone_("applyState:", state, False)

        def applyState_(self, state):
            state = str(state)
            if state not in STATUS_WORD:
                return
            self.state = state
            shown = "paused" if self.paused else state
            color = NSColor.systemGrayColor() if self.paused else DOT_COLORS[state]()
            self.dot.layer().setBackgroundColor_(color.CGColor())
            self.status.setStringValue_("Paused" if shown == "paused" else STATUS_WORD[state])

        @objc.python_method
        def showError(self, message: str) -> None:
            self.sub.setStringValue_(message)
            self.sub.setTextColor_(NSColor.systemRedColor())

        @objc.python_method
        def record(self, utterance: str, line: str, reply: str = "") -> None:
            """Push one command onto the five-row list; the header sub line shows the reply."""
            self.history.appendleft((utterance, line))
            for i, (utter, plan) in enumerate(self.rows):
                u, ln = self.history[i] if i < len(self.history) else ("", "")
                utter.setStringValue_(u)
                utter.setTextColor_(NSColor.labelColor())
                plan.setStringValue_(ln)
                plan.setTextColor_(NSColor.secondaryLabelColor())
            if line == "failed":
                self.showError(reply)
            else:
                self.sub.setStringValue_(reply or "Done.")
                self.sub.setTextColor_(NSColor.secondaryLabelColor())

        def refreshPermissions_(self, timer):
            if timer is not None and not self.panel.isVisible():
                return
            try:
                from .platform import get_platform

                perms = (self.platform or get_platform()).check_permissions()
            except Exception:  # noqa: BLE001  a missing framework must not break the panel
                perms = {}
            for key, (check, btn) in self.perm_views.items():
                ok = bool(perms.get(key))
                check.setHidden_(not ok)
                btn.setHidden_(ok)

        # ------------------------------------------------------------------ actions

        def showPanel_(self, sender):
            self.refreshPermissions_(None)
            self.panel.orderFrontRegardless()

        def listenToggled_(self, sender):
            self.setPaused(sender.state() != NSControlStateValueOn)

        def togglePause_(self, sender):
            self.setPaused(not self.paused)

        @objc.python_method
        def setPaused(self, paused: bool) -> None:
            self.paused = paused
            self.pause_item.setTitle_("Resume Listening" if paused else "Pause Listening")
            self.toggle.setState_(NSControlStateValueOff if paused else NSControlStateValueOn)
            self.listen_label.setStringValue_("Paused" if paused else "Listening")
            self.applyState_(self.state)

        def toggleOverlay_(self, sender):
            if self.overlay.window.isVisible():
                self.overlay.hide()
                self.overlay_item.setTitle_("Show Rocky")
            else:
                self.overlay.show()
                self.overlay_item.setTitle_("Hide Rocky")

        def toggleDry_(self, sender):
            self.setAct(not self.act)

        def segmentChanged_(self, sender):
            self.setAct(sender.selectedSegment() == 1)

        @objc.python_method
        def setAct(self, act: bool) -> None:
            self.act = act
            self.dry_item.setState_(NSControlStateValueOff if act else NSControlStateValueOn)
            self.segment.setSelectedSegment_(1 if act else 0)

        def openSettings_(self, sender):
            pane = PERMISSIONS[sender.tag()][2]
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(SETTINGS_URL.format(pane=pane)))

        def openRuns_(self, sender):
            runs = Path(config.RUNS_DIR).resolve()
            runs.mkdir(parents=True, exist_ok=True)
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(runs)))

        def quit_(self, sender):
            self.app.terminate_(None)


def main() -> int:
    if sys.platform != "darwin":
        print("The Rocky app shell is macOS only for now; use `rocky` from a terminal.")
        return 1
    config.load_env_file()
    importlib.reload(config)
    controller = AppController.alloc().init()
    controller.build()
    controller.start(voice=not os.environ.get("ROCKY_NO_VOICE"))
    print(f"Rocky is in the menu bar. Panel at {controller.panel.frame()}", flush=True)
    controller.app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
