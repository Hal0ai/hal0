"""Energy-VAD unit tests with synthetic pcm16 frames (no audio hardware)."""

from __future__ import annotations

import struct

from hal0.realtime.vad import EnergyVAD

_SR = 24000


def _voiced(ms: int, amp: int = 6000) -> bytes:
    n = int(_SR * ms / 1000)
    return b"".join(struct.pack("<h", amp if i % 2 else -amp) for i in range(n))


def _silence(ms: int) -> bytes:
    n = int(_SR * ms / 1000)
    return b"\x00\x00" * n


def _vad() -> EnergyVAD:
    return EnergyVAD(
        sample_rate=_SR, energy_threshold=0.02, silence_ms=500, min_speech_ms=200, window_ms=20
    )


def test_pure_silence_produces_no_transitions() -> None:
    vad = _vad()
    assert vad.feed(_silence(1000)) == []
    assert not vad.in_speech


def test_utterance_then_silence_fires_committable_stop() -> None:
    vad = _vad()
    started = vad.feed(_voiced(300))
    assert [d.kind for d in started] == ["speech_started"]
    assert vad.in_speech
    stopped = vad.feed(_silence(600))
    assert [d.kind for d in stopped] == ["speech_stopped"]
    assert stopped[0].committable is True  # 300ms >= 200ms min_speech
    assert not vad.in_speech


def test_short_blip_is_not_committable() -> None:
    vad = _vad()
    vad.feed(_voiced(60))  # below 200ms min_speech
    stopped = vad.feed(_silence(600))
    assert [d.kind for d in stopped] == ["speech_stopped"]
    assert stopped[0].committable is False


def test_residue_carries_across_feeds() -> None:
    vad = _vad()
    # Feed sub-window byte counts split across calls; the detector must not lose
    # samples at the boundary (10ms + 10ms = one 20ms window).
    half = _voiced(10)
    assert vad.feed(half) == []  # not yet a full window
    d = vad.feed(half)
    assert [x.kind for x in d] == ["speech_started"]


def test_reset_clears_state() -> None:
    vad = _vad()
    vad.feed(_voiced(300))
    assert vad.in_speech
    vad.reset()
    assert not vad.in_speech
    assert vad.feed(_silence(600)) == []
