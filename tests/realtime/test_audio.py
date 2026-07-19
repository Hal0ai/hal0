"""Audio-helper unit tests: pcm16<->wav round-trip + output framing."""

from __future__ import annotations

import struct

from hal0.realtime import audio


def _sine_pcm(ms: int, sample_rate: int = 24000, amp: int = 8000) -> bytes:
    n = int(sample_rate * ms / 1000)
    return b"".join(struct.pack("<h", amp if i % 2 else -amp) for i in range(n))


def test_pcm_wav_round_trip_is_lossless() -> None:
    pcm = _sine_pcm(50)
    wav = audio.pcm16_to_wav(pcm, sample_rate=24000)
    assert audio.looks_like_wav(wav)
    back, sr = audio.wav_to_pcm16(wav)
    assert sr == 24000
    assert back == pcm


def test_frame_bytes_for_ms_20ms_at_24k() -> None:
    # 24000 * 0.02 = 480 samples * 2 bytes = 960 bytes.
    assert audio.frame_bytes_for_ms(20, sample_rate=24000) == 960


def test_slice_pcm_frames_counts_and_last_partial() -> None:
    pcm = _sine_pcm(55)  # 55ms -> two full 20ms frames + a 15ms remainder
    frames = audio.slice_pcm_frames(pcm, frame_ms=20, sample_rate=24000)
    assert len(frames) == 3
    assert len(frames[0]) == 960
    assert len(frames[1]) == 960
    assert 0 < len(frames[2]) < 960


def test_slice_drops_trailing_odd_byte() -> None:
    frames = audio.slice_pcm_frames(b"\x01\x02\x03", frame_ms=20)
    # 3 bytes -> 2 usable (one int16), one frame.
    assert frames == [b"\x01\x02"]


def test_b64_round_trip_tolerates_missing_padding() -> None:
    raw = b"abcde"
    enc = audio.b64_encode(raw).rstrip("=")
    assert audio.b64_decode(enc) == raw


def test_rms_level_bounds() -> None:
    assert audio.rms_level(b"") == 0.0
    loud = struct.pack("<h", 30000) * 100
    assert audio.rms_level(loud) > 0.8
