"""Voice layer: microphone, endpointing, speech to text, wake word, push-to-talk.

    listen_forever(on_utterance, wake_word="watermelon", backend="auto", language="en-US")
    listen_once(seconds=4.0) -> str          # one VAD-cut utterance, for confirmations
    transcribe_once(seconds=4.0) -> str      # a fixed-length recording

Backends
  whisper   whisper.cpp `whisper-server` started as a subprocess (Metal on macOS, CPU on Windows). Model:
            models/ggml-<WHISPER_MODEL>.bin, default `base` (multilingual, so Arabic works). Download once:
              mkdir -p models && curl -L -o models/ggml-base.bin \\
                https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
            `brew install whisper-cpp` provides the server on macOS. WHISPER_MODEL_DIR overrides models/.
  apple     on-device SFSpeechRecognizer (pyobjc Speech framework) for the given locale, e.g. en-US, ar-SA.
            Prompts for Speech Recognition permission on first use.
  windows   not implemented yet; see the roadmap in README.md.
  auto      apple on macOS when the Speech framework can be used for the locale, else whisper.

Wake word: stripped from the transcript with fuzzy matching, so whisper's "water melon," or "Watermelon."
still count. An utterance with the wake word is delivered without it; one without it is delivered as heard,
because the router owns the `addressed` gate. Push-to-talk (hold Right Option on macOS, Right Ctrl on
Windows) records for as long as the key is held and delivers the result the same way.

Adapted in places from kevinbadi/jev-voice (audio.py, stt.py, hotkey.py, main.py), MIT; see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import numpy as np

from . import events, history

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME = SAMPLE_RATE * FRAME_MS // 1000

WHISPER_PORT = int(os.environ.get("WHISPER_PORT", "8178"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WHISPER_MODEL_DIR = Path(os.environ.get("WHISPER_MODEL_DIR", "models"))
WHISPER_THREADS = int(os.environ.get("WHISPER_THREADS", "6"))

_BLANK = {"", "[BLANK_AUDIO]", "(silence)", "[silence]", "[inaudible]", "[Music]", "[MUSIC]", "(music)"}
_HALLUCINATIONS = {
    "thank you.",
    "thanks for watching.",
    "thank you for watching.",
    "you",
    "bye.",
    "thank you",
}
_LEAD_IN = re.compile(r"^(?:hey|hi|ok|okay|yo)\W+", re.IGNORECASE)

RIGHT_OPTION_KEYCODE = 61  # macOS virtual keycode for the right Option key


# ------------------------------------------------------------------ wake word


def _fuzzy(word: str, target: str, exact: bool = False) -> bool:
    w = word.lower().strip("'\",.!?;:،؟")
    if w == target:
        return True
    if exact or len(w) < min(4, len(target)):
        return False
    return difflib.SequenceMatcher(None, w, target).ratio() >= 0.75


def strip_wake(text: str, wake_word: str) -> tuple[bool, str]:
    """(addressed, command). The wake word may lead or sit anywhere, may be split ("water melon") or
    misspelled by the recognizer ("Watermelon,", "watermelons"). Only the first hit is removed."""
    target = wake_word.lower().replace(" ", "")
    if not target:
        return False, text.strip()
    words = text.split()
    span = max(1, len(wake_word.split())) + 1
    for i in range(len(words)):
        for n in range(1, span + 1):
            if i + n > len(words):
                break
            if _fuzzy("".join(words[i : i + n]), target, exact=n > 1):  # "water melon" yes, "the melon" no
                rest = " ".join(words[:i] + words[i + n :]).strip(" ,.،")
                return True, _LEAD_IN.sub("", rest).strip()
    return False, text.strip()


# ------------------------------------------------------------------ endpointing


@dataclass
class VADConfig:
    start_frames: int = 3  # consecutive loud frames to start
    end_silence_ms: int = 550  # silence that ends an utterance
    min_speech_ms: int = 250  # loud audio, pre-roll not counted
    max_speech_ms: int = 12000
    pre_roll_ms: int = 240  # audio kept from before speech start
    threshold_mult: float = 3.5  # loudness over the noise floor
    floor_min: float = 0.004
    calibrate_ms: int = 600  # the first frames are taken as room noise, never as speech


STOP_WORDS = re.compile(r"^(?:\W*(?:stop|enough|quiet|silence|cancel|shut up|pause)\W*)+$", re.IGNORECASE)


class Gate:
    """Keeps Rocky from hearing itself, without going deaf: the user may talk over it.

    scrub(): removes Rocky's own recent sentences from a transcript and returns what the user said.
    follow_up: after a real command, the next sentence within FOLLOW_UP_S needs no wake word.
    """

    FOLLOW_UP_S = 8.0
    REMEMBER_S = 25.0

    def __init__(self) -> None:
        self.said: list[tuple[float, str]] = []
        self.follow_up_until = 0.0

    def on_event(self, state: str) -> None:
        if state.startswith("said:"):
            self.said.append((time.monotonic(), _norm(state[5:])))
            self.said = self.said[-4:]

    def scrub(self, heard: str) -> tuple[str, bool]:
        """(what the user said, was_anything_removed). A transcript that is only Rocky's echo comes back empty;
        the user's words survive even when Rocky's sentence sits inside the same transcript."""
        text = _norm(heard)
        removed = False
        now = time.monotonic()
        for ts, said in self.said:
            if now - ts > self.REMEMBER_S or not said or not text:
                continue
            if said in text:
                text = " ".join(text.replace(said, " ").split())
                removed = True
                continue
            m = difflib.SequenceMatcher(None, text, said).find_longest_match(0, len(text), 0, len(said))
            if m.size >= max(10, int(0.6 * len(said))):  # a long run of Rocky's words inside the transcript
                text = " ".join((text[: m.a] + " " + text[m.a + m.size :]).split())
                removed = True
        if (
            removed and len(text.split()) < 2 and not STOP_WORDS.match(text)
        ):  # leftover echo noise, except "stop"
            return "", True
        return text, removed

    def open_follow_up(self) -> None:
        self.follow_up_until = time.monotonic() + self.FOLLOW_UP_S

    def follow_up_open(self) -> bool:
        return time.monotonic() < self.follow_up_until


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\u0600-\u06ff ]", " ", text.lower()).split())


class Endpointer:
    """Energy-based voice activity detection over 30 ms float32 frames. Pure: feed frames, get utterances.
    ponytail: RMS over an adaptive noise floor; swap in Silero VAD if music or fans keep tripping it."""

    def __init__(
        self, vad: VADConfig | None = None, on_speech_start: Callable[[], None] | None = None
    ) -> None:
        self.vad = vad or VADConfig()
        self.on_speech_start = on_speech_start
        self.noise = 0.01
        self._calibrating = self.vad.calibrate_ms // FRAME_MS
        self._ring: list[np.ndarray] = []
        self._speech: list[np.ndarray] = []
        self._loud_run = 0
        self._after = 0  # frames since speech was detected
        self._silence_ms = 0
        self.in_speech = False

    def reset(self) -> None:
        self._ring.clear()
        self._speech.clear()
        self._loud_run = 0
        self._after = 0
        self._silence_ms = 0
        self.in_speech = False

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        """One frame in; a finished utterance out when speech has ended, else None."""
        v = self.vad
        rms = float(np.sqrt(np.mean(frame * frame)) + 1e-9)
        if self._calibrating:
            self._calibrating -= 1
            self.noise = self.noise * 0.8 + rms * 0.2
            return None
        if not self.in_speech:  # adaptive noise floor: slow up, fast down
            self.noise = self.noise * 0.98 + rms * 0.02 if rms > self.noise else self.noise * 0.9 + rms * 0.1
        loud = rms > max(v.floor_min, self.noise * v.threshold_mult)
        if not self.in_speech:
            self._ring.append(frame)
            if len(self._ring) > v.pre_roll_ms // FRAME_MS:
                self._ring.pop(0)
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= v.start_frames:
                self.in_speech = True
                self._speech = list(self._ring)
                self._after = 0
                self._silence_ms = 0
                if self.on_speech_start:
                    self.on_speech_start()
            return None
        self._speech.append(frame)
        self._after += 1
        self._silence_ms = 0 if loud else self._silence_ms + FRAME_MS
        spoken_ms = (v.start_frames + self._after) * FRAME_MS - self._silence_ms
        if self._silence_ms >= v.end_silence_ms or len(self._speech) * FRAME_MS >= v.max_speech_ms:
            out = np.concatenate(self._speech) if spoken_ms >= v.min_speech_ms else None
            self.reset()
            return out
        return None


class Microphone:
    """sounddevice input stream, 16 kHz mono float32, frames on a queue."""

    def __init__(self, device: int | str | None = None) -> None:
        import sounddevice as sd  # lazy: needs PortAudio

        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME,
            device=device,
            callback=lambda indata, frames, t, status: self.q.put(indata[:, 0].copy()),
        )

    def __enter__(self) -> Self:
        self.stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stream.stop()
        self.stream.close()

    def drain(self) -> None:
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def frame(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


# ------------------------------------------------------------------ speech to text


def _wav_bytes(pcm: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _clean(text: str) -> str:
    text = text.strip()
    if text in _BLANK or text.lower() in _HALLUCINATIONS or len(text) < 2:
        return ""
    return text


def _whisper_lang(language: str) -> str:
    return "auto" if language.lower() == "auto" else language.split("-")[0].lower()


class WhisperBackend:
    """whisper.cpp `whisper-server`: the model stays loaded, each utterance is one HTTP POST."""

    def __init__(self, language: str = "en-US", port: int = WHISPER_PORT) -> None:
        import httpx

        self.lang = _whisper_lang(language)
        self.url = f"http://127.0.0.1:{port}"
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.http = httpx.Client(timeout=30.0)

    def _alive(self) -> bool:
        try:
            return self.http.get(self.url + "/", timeout=1.0).status_code < 500
        except Exception:  # noqa: BLE001  any transport error means "not up yet"
            return False

    def start(self) -> None:
        if self._alive():
            return
        exe = shutil.which("whisper-server")
        if not exe:
            raise RuntimeError(
                "whisper-server not on PATH: `brew install whisper-cpp` (macOS) or build whisper.cpp"
            )
        model = WHISPER_MODEL_DIR / f"ggml-{WHISPER_MODEL}.bin"
        if not model.exists():
            raise RuntimeError(
                f"Whisper model missing: {model}\n  mkdir -p {WHISPER_MODEL_DIR} && curl -L -o {model} "
                f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{WHISPER_MODEL}.bin"
            )
        cmd = [
            exe,
            "-m",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "-t",
            str(WHISPER_THREADS),
        ]
        cmd += ["-l", self.lang, "-nt"]
        if sys.platform == "win32":
            cmd.append("-ng")  # no Metal there; CPU is the honest default
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(400):
            if self._alive():
                self._warm_up()
                return
            time.sleep(0.05)
        raise RuntimeError("whisper-server failed to start within 20 s")

    def _warm_up(self) -> None:
        """First inference compiles Metal shaders (~2 s); pay that before the user speaks."""
        tone = 0.05 * np.sin(np.linspace(0, 2 * np.pi * 220 * 0.6, int(SAMPLE_RATE * 0.6)))
        with contextlib.suppress(Exception):  # warm-up only; a failure surfaces on the first real call
            self.transcribe(tone.astype(np.float32))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def transcribe(self, pcm: np.ndarray) -> str:
        files = {"file": ("audio.wav", _wav_bytes(pcm), "audio/wav")}
        data = {
            "response_format": "json",
            "temperature": "0.0",
            "no_timestamps": "true",
            "language": self.lang,
        }
        r = self.http.post(self.url + "/inference", files=files, data=data)
        r.raise_for_status()
        return _clean(r.json().get("text") or "")


class AppleBackend:
    """SFSpeechRecognizer on a WAV of the utterance, on-device when the locale supports it.
    Not exercised live in the 2026-09-20 session: Speech permission was undetermined and prompting was off."""

    def __init__(self, language: str = "en-US") -> None:
        import Foundation
        import Speech

        self.Speech, self.Foundation = Speech, Foundation
        locale = Foundation.NSLocale.alloc().initWithLocaleIdentifier_(language.replace("_", "-"))
        self.rec = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)
        if self.rec is None:
            raise RuntimeError(f"Apple speech recognition has no recognizer for locale {language}")
        self.rec.setQueue_(Foundation.NSOperationQueue.alloc().init())  # callbacks off the main queue
        self.on_device = bool(self.rec.supportsOnDeviceRecognition())

    @staticmethod
    def available() -> bool:
        try:
            import Speech
        except ImportError:
            return False
        status = Speech.SFSpeechRecognizer.authorizationStatus()
        return status in (0, 3)  # notDetermined (we will ask) or authorized

    def start(self) -> None:
        Speech = self.Speech
        if Speech.SFSpeechRecognizer.authorizationStatus() == 0:
            done = threading.Event()
            Speech.SFSpeechRecognizer.requestAuthorization_(lambda status: done.set())
            done.wait(120)
        if Speech.SFSpeechRecognizer.authorizationStatus() != 3:
            raise RuntimeError(
                "Speech Recognition permission denied: System Settings > Privacy & Security > Speech Recognition"
            )
        if not self.rec.isAvailable():
            raise RuntimeError("Apple speech recognizer is not available right now")

    def stop(self) -> None:
        pass

    def transcribe(self, pcm: np.ndarray) -> str:
        path = Path(os.environ.get("TMPDIR", "/tmp")) / f"rocky-utt-{threading.get_ident()}.wav"
        path.write_bytes(_wav_bytes(pcm))
        url = self.Foundation.NSURL.fileURLWithPath_(str(path))
        req = self.Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)
        req.setShouldReportPartialResults_(False)
        if self.on_device:
            req.setRequiresOnDeviceRecognition_(True)
        done = threading.Event()
        out: dict[str, str] = {"text": ""}

        def handler(result, error) -> None:
            if result is not None:
                out["text"] = str(result.bestTranscription().formattedString())
            if error is not None or (result is not None and result.isFinal()):
                done.set()

        self.rec.recognitionTaskWithRequest_resultHandler_(req, handler)
        done.wait(30)
        path.unlink(missing_ok=True)
        return _clean(out["text"])


def make_backend(backend: str = "auto", language: str = "en-US"):
    """One started backend with .transcribe(pcm) -> str and .stop()."""
    if backend == "auto":
        backend = "apple" if sys.platform == "darwin" and AppleBackend.available() else "whisper"
    if backend == "windows":
        raise NotImplementedError(
            "The Windows-native speech backend is on the roadmap (README.md); use STT_BACKEND=whisper for now"
        )
    if backend == "apple":
        b = AppleBackend(language)
    elif backend == "whisper":
        b = WhisperBackend(language)
    else:
        raise ValueError(f"unknown STT backend {backend!r}; use auto, apple, whisper or windows")
    b.start()
    print(f"  stt: {backend} ({language})")
    return b


# ------------------------------------------------------------------ push-to-talk


class HoldToTalk:
    """Global hold-to-talk key: Right Option on macOS (listen-only Quartz event tap, nothing persistent),
    Right Ctrl on Windows (pynput). start() returns False when the OS refused the hook."""

    def __init__(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        self.on_press, self.on_release = on_press, on_release
        self.down = False
        self._ok = threading.Event()
        self._failed = threading.Event()

    def start(self) -> bool:
        if sys.platform == "darwin":
            threading.Thread(target=self._mac_tap, daemon=True, name="rocky-ptt").start()
            while not (self._ok.is_set() or self._failed.is_set()):
                self._ok.wait(0.05)
            return self._ok.is_set()
        if sys.platform == "win32":
            return self._win_pynput()
        return False

    def _mac_tap(self) -> None:
        import Quartz

        def cb(proxy, etype, event, refcon):
            if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(tap, True)
                return event
            if (
                Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                == RIGHT_OPTION_KEYCODE
            ):
                held = bool(Quartz.CGEventGetFlags(event) & Quartz.kCGEventFlagMaskAlternate)
                if held and not self.down:
                    self.down = True
                    self.on_press()
                elif not held and self.down:
                    self.down = False
                    self.on_release()
            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            1 << Quartz.kCGEventFlagsChanged,
            cb,
            None,
        )
        if tap is None:
            self._failed.set()
            return
        src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self._ok.set()
        Quartz.CFRunLoopRun()

    def _win_pynput(self) -> bool:  # UNTESTED on real Windows as of 2026-09-20
        try:
            from pynput import keyboard
        except ImportError:
            return False

        def press(key) -> None:
            if key == keyboard.Key.ctrl_r and not self.down:
                self.down = True
                self.on_press()

        def release(key) -> None:
            if key == keyboard.Key.ctrl_r and self.down:
                self.down = False
                self.on_release()

        keyboard.Listener(on_press=press, on_release=release, daemon=True).start()
        return True


# ------------------------------------------------------------------ entry points


def _report(text: str, pcm: np.ndarray, stt_ms: int) -> None:
    print(f"  heard: {text!r}  ({len(pcm) / SAMPLE_RATE:.1f}s audio, stt {stt_ms} ms)")


def listen_forever(
    on_utterance: Callable[[str], object],
    wake_word: str = "watermelon",
    backend: str = "auto",
    language: str = "en-US",
    device: int | str | None = None,
) -> None:
    """Open mic until Ctrl-C. Each utterance is transcribed, the wake word stripped when heard, and the
    text handed to on_utterance(text). Hold Right Option (macOS) / Right Ctrl (Windows) to record without
    VAD endpointing. Events: hearing when speech starts, thinking while on_utterance runs, idle after."""
    stt = make_backend(backend, language)
    utterances: queue.Queue[tuple[np.ndarray, bool]] = queue.Queue()
    holding = threading.Event()
    endpointer = Endpointer(on_speech_start=lambda: events.emit("hearing"))
    gate = Gate()
    events.subscribe(gate.on_event)

    def on_press() -> None:
        endpointer.reset()
        holding.set()
        events.emit("hearing")

    def on_release() -> None:
        holding.clear()

    ptt = HoldToTalk(on_press, on_release).start()
    key = "Right Option" if sys.platform == "darwin" else "Right Ctrl"
    hint = f'  Say "{wake_word}, open safari"' if wake_word else "  Speak"
    print(
        hint + (f", or hold {key} to talk." if ptt else ". (hold-to-talk unavailable: no Input Monitoring)")
    )
    events.emit("idle")

    def capture(mic: Microphone) -> None:
        while True:
            frame = mic.frame(timeout=0.5)
            if frame is None:
                continue
            if holding.is_set():
                held: list[np.ndarray] = [frame]
                while holding.is_set():
                    f = mic.frame(timeout=0.1)
                    if f is not None:
                        held.append(f)
                utterances.put((np.concatenate(held), True))
                mic.drain()
                continue
            pcm = endpointer.feed(frame)
            if pcm is not None:
                utterances.put((pcm, False))

    try:
        mic = Microphone(device).__enter__()  # opened here so a denied microphone raises in the caller
    except Exception:
        stt.stop()
        raise
    threading.Thread(target=capture, args=(mic,), daemon=True, name="rocky-mic").start()
    try:
        while True:
            pcm, forced = utterances.get()
            if len(pcm) < SAMPLE_RATE * 0.25:
                events.emit("idle")
                continue
            t0 = time.perf_counter()
            text = stt.transcribe(pcm)
            stt_ms = int((time.perf_counter() - t0) * 1000)
            if not text:
                print(f"  (heard nothing, stt {stt_ms} ms)")
                events.emit("idle")
                continue
            _report(text, pcm, stt_ms)
            raw = text
            text, scrubbed = gate.scrub(text)
            addressed, cmd = strip_wake(text, wake_word) if (wake_word and text) else (False, text)
            stop = bool(STOP_WORDS.match(text or raw))
            follow_up = gate.follow_up_open()
            history.record(
                "heard",
                text=raw,
                kept=text,
                addressed=addressed,
                forced=forced,
                echo=scrubbed,
                follow_up=follow_up,
                stop=stop,
                stt_ms=stt_ms,
            )
            if not text and not stop:
                print("  (ignored: that was my own voice)")
                events.emit("idle")
                continue
            if addressed and not cmd and not forced:  # just the name: the next utterance is the command
                gate.open_follow_up()
                events.emit("idle")
                continue
            if not (addressed or forced or follow_up or stop):
                print("  (ignored: no wake word)")
                events.emit("idle")
                continue
            events.emit("thinking")
            try:
                on_utterance("stop" if stop else (cmd or text))
                gate.open_follow_up()  # a short window where the next sentence needs no wake word
            finally:
                events.emit("idle")
    except KeyboardInterrupt:
        pass
    finally:
        mic.__exit__(None, None, None)
        stt.stop()


def listen_once(seconds: float = 4.0, backend: str = "auto", language: str = "en-US") -> str:
    """Wait up to `seconds` for one VAD-cut utterance and return its text ("" on silence). For yes/no
    confirmations. Speech already in flight when time runs out is transcribed as is."""
    stt = make_backend(backend, language)
    endpointer = Endpointer(on_speech_start=lambda: events.emit("hearing"))
    pcm = None
    try:
        with Microphone() as mic:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                frame = mic.frame(timeout=0.1)
                if frame is None:
                    continue
                pcm = endpointer.feed(frame)
                if pcm is not None:
                    break
            if pcm is None and endpointer.in_speech and endpointer._speech:
                pcm = np.concatenate(endpointer._speech)
        if pcm is None or len(pcm) < SAMPLE_RATE * 0.25:
            return ""
        t0 = time.perf_counter()
        text = stt.transcribe(pcm)
        _report(text, pcm, int((time.perf_counter() - t0) * 1000))
        return text
    finally:
        events.emit("idle")
        stt.stop()


def transcribe_once(seconds: float = 4.0, backend: str = "auto", language: str = "en-US") -> str:
    """Record exactly `seconds` from the default microphone and return the transcript."""
    import sounddevice as sd

    stt = make_backend(backend, language)
    try:
        events.emit("hearing")
        pcm = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        pcm = pcm[:, 0]
        t0 = time.perf_counter()
        text = stt.transcribe(pcm)
        _report(text, pcm, int((time.perf_counter() - t0) * 1000))
        return text
    finally:
        events.emit("idle")
        stt.stop()
