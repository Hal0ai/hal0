"""PCM16 <-> WAV wrapping and output frame slicing for the Realtime engine.

The batch STT route (``POST /v1/audio/transcriptions``) needs a decodable
container, not raw pcm (spec §2a: ``v1.py:1568``); the Realtime wire carries
raw pcm16 frames. This module bridges the two directions with **stdlib only**
(``wave`` + ``struct`` + ``base64``) — no scipy/librosa/soundfile pulled in for
the MVP (spec §6 risk 6).

Both directions are fixed at **mono 16-bit signed LE @ 24 kHz** for inc-1, which
matches the demo client's ``-sample-rate 24000`` default and kokoro's native
pcm output (spec §3, §2a) — no resample on either leg.
"""

from __future__ import annotations

import base64
import io
import struct
import wave

#: Fixed MVP audio format (spec decision 4b). Mono, 16-bit signed LE.
DEFAULT_SAMPLE_RATE = 24000
SAMPLE_WIDTH_BYTES = 2  # int16
CHANNELS = 1


def pcm16_to_wav(pcm: bytes, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """Wrap raw pcm16 mono LE bytes in a RIFF/WAV container.

    The STT route rejects raw pcm (``v1.py:1568``); it wants a container it can
    decode. A WAV header is the cheapest lossless wrapper and the moonshine
    backend resamples 24k->16k internally (``moonshine_server.py:209-219``), so
    the gateway need not resample.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH_BYTES)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int]:
    """Return ``(pcm16_bytes, sample_rate)`` from a WAV container.

    Used by the TTS leg when a backend returns a WAV blob rather than raw pcm
    (kokoro can emit either; ``response_format="pcm"`` gives raw L16, but a
    ``wav`` fallback must still frame cleanly).
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        frames = w.readframes(w.getnframes())
        return frames, w.getframerate()


def looks_like_wav(blob: bytes) -> bool:
    """True if ``blob`` starts with a RIFF/WAVE header."""
    return len(blob) >= 12 and blob[0:4] == b"RIFF" and blob[8:12] == b"WAVE"


def frame_bytes_for_ms(ms: int, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> int:
    """Bytes in ``ms`` of mono pcm16 at ``sample_rate`` (even, whole samples)."""
    samples = max(1, round(sample_rate * ms / 1000))
    return samples * SAMPLE_WIDTH_BYTES


def slice_pcm_frames(
    pcm: bytes, *, frame_ms: int = 20, sample_rate: int = DEFAULT_SAMPLE_RATE
) -> list[bytes]:
    """Slice a pcm16 blob into ``frame_ms`` frames (last frame may be shorter).

    Frames are sample-aligned; a trailing partial sample byte (odd-length input)
    is dropped rather than corrupting alignment.
    """
    step = frame_bytes_for_ms(frame_ms, sample_rate=sample_rate)
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH_BYTES)
    if not usable:
        return []
    body = pcm[:usable]
    return [body[i : i + step] for i in range(0, usable, step)]


def b64_encode(pcm: bytes) -> str:
    """Base64-encode pcm bytes for a ``response.output_audio.delta`` payload."""
    return base64.b64encode(pcm).decode("ascii")


def b64_decode(data: str) -> bytes:
    """Decode a base64 ``input_audio_buffer.append`` payload to pcm bytes.

    Tolerates missing padding (some clients strip ``=``). Raises ``ValueError``
    on genuinely malformed input so the caller can emit a typed ``error`` event.
    """
    try:
        pad = (-len(data)) % 4
        return base64.b64decode(data + ("=" * pad), validate=False)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid base64 audio payload: {exc}") from exc


def rms_level(pcm: bytes) -> float:
    """Normalized RMS (0.0-1.0) of a pcm16 mono LE buffer.

    Pure stdlib so the VAD has no numpy hard-dependency at import time; numpy is
    used by :mod:`hal0.realtime.vad` when present for speed but this is the
    correctness reference.
    """
    n = len(pcm) // SAMPLE_WIDTH_BYTES
    if n == 0:
        return 0.0
    total = 0
    for (sample,) in struct.iter_unpack("<h", pcm[: n * SAMPLE_WIDTH_BYTES]):
        total += sample * sample
    return (total / n) ** 0.5 / 32768.0


__all__ = [
    "CHANNELS",
    "DEFAULT_SAMPLE_RATE",
    "SAMPLE_WIDTH_BYTES",
    "b64_decode",
    "b64_encode",
    "frame_bytes_for_ms",
    "looks_like_wav",
    "pcm16_to_wav",
    "rms_level",
    "slice_pcm_frames",
    "wav_to_pcm16",
]
