"""Offline voice tests: wake-word stripping and the VAD endpointer on synthetic PCM. No microphone."""

from __future__ import annotations

import numpy as np
import pytest

from rocky import voice
from rocky.voice import FRAME, SAMPLE_RATE, Endpointer, VADConfig, strip_wake


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("watermelon open safari", (True, "open safari")),
        ("Watermelon, open safari.", (True, "open safari")),
        ("water melon open safari", (True, "open safari")),
        ("Water melon, open safari", (True, "open safari")),
        ("hey watermelon open safari", (True, "open safari")),
        ("Watermelons open safari", (True, "open safari")),
        ("open safari watermelon", (True, "open safari")),
        ("watermelon", (True, "")),
        ("open safari", (False, "open safari")),
        ("I want some water", (False, "I want some water")),
        ("Open the melon recipe", (False, "Open the melon recipe")),
    ],
)
def test_strip_wake(text, expected):
    assert strip_wake(text, "watermelon") == expected


def test_strip_wake_other_words_and_arabic():
    assert strip_wake("Rocky, open notes", "rocky") == (True, "open notes")
    assert strip_wake("Rocki open notes", "rocky") == (True, "open notes")
    assert strip_wake("hey rocky what time is it", "hey rocky") == (True, "what time is it")
    assert strip_wake("بطيخ افتح سفاري", "بطيخ") == (True, "افتح سفاري")
    assert strip_wake("افتح سفاري", "بطيخ") == (False, "افتح سفاري")
    assert strip_wake("anything", "") == (False, "anything")


def test_clean_drops_whisper_blanks_and_hallucinations():
    assert voice._clean("[BLANK_AUDIO]") == ""
    assert voice._clean(" Thank you. ") == ""
    assert voice._clean("open safari") == "open safari"
    assert voice._whisper_lang("ar-SA") == "ar" and voice._whisper_lang("auto") == "auto"


# ------------------------------------------------------------------ VAD


def _frames(seconds: float, amplitude: float, seed: int = 0) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE) // FRAME
    return [(rng.standard_normal(FRAME) * amplitude).astype(np.float32) for _ in range(n)]


def _run(frames: list[np.ndarray], ep: Endpointer) -> list[np.ndarray]:
    out = []
    for f in frames:
        u = ep.feed(f)
        if u is not None:
            out.append(u)
    return out


def test_silence_yields_nothing():
    ep = Endpointer()
    assert _run(_frames(3.0, 0.001), ep) == []
    assert not ep.in_speech


def test_speech_burst_is_cut_after_trailing_silence():
    starts = []
    ep = Endpointer(VADConfig(), on_speech_start=lambda: starts.append(True))
    frames = _frames(1.0, 0.001) + _frames(1.0, 0.2, seed=1) + _frames(1.0, 0.001, seed=2)
    utts = _run(frames, ep)
    assert len(utts) == 1 and starts == [True]
    dur = len(utts[0]) / SAMPLE_RATE
    # 1 s of speech plus pre-roll and the end-silence tail, nothing more
    assert 1.0 <= dur <= 1.0 + (ep.vad.pre_roll_ms + ep.vad.end_silence_ms) / 1000 + 0.1
    assert not ep.in_speech


def test_two_utterances_are_separated():
    ep = Endpointer()
    frames = (
        _frames(0.6, 0.001)
        + _frames(0.8, 0.2, seed=1)
        + _frames(0.8, 0.001, seed=2)
        + _frames(0.8, 0.2, seed=3)
        + _frames(0.8, 0.001, seed=4)
    )
    assert len(_run(frames, ep)) == 2


def test_click_shorter_than_min_speech_is_dropped():
    ep = Endpointer(VADConfig(min_speech_ms=250))
    frames = _frames(0.6, 0.001) + _frames(0.12, 0.2, seed=1) + _frames(1.0, 0.001, seed=2)
    assert _run(frames, ep) == []


def test_long_speech_is_force_cut_at_max():
    ep = Endpointer(VADConfig(max_speech_ms=2000))
    utts = _run(_frames(0.6, 0.001) + _frames(5.0, 0.2, seed=1), ep)
    assert len(utts) >= 2
    assert all(len(u) <= 2000 * SAMPLE_RATE // 1000 + FRAME for u in utts)


def test_noise_floor_adapts_to_a_loud_room():
    ep = Endpointer()
    _run(_frames(3.0, 0.05), ep)  # a fan: steady room noise
    assert ep.noise > 0.03
    assert _run(_frames(1.0, 0.05, seed=5), ep) == []
