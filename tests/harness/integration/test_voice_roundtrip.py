"""Voice round-trip integration gate + its pure matcher helper.

The live test (text -> /v1/audio/speech -> wav -> /v1/audio/transcriptions
-> text) is the bar for "tested" in the voice bring-up plan. It SKIPS unless
HAL0_VOICE_LIVE_URL points at a running hal0 API, so the default offline
`pytest tests/` pass stays green.
"""

from __future__ import annotations

import difflib
import os
import re

import pytest

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalized_match(a: str, b: str) -> float:
    """Return a 0..1 similarity ratio between two strings after normalizing
    case, punctuation, and whitespace."""

    def norm(s: str) -> str:
        s = _PUNCT.sub(" ", s.lower())
        return _WS.sub(" ", s).strip()

    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def test_normalized_match_ignores_case_and_punctuation() -> None:
    assert normalized_match("The quick brown fox!", "the quick brown fox") >= 0.99


def test_normalized_match_detects_divergence() -> None:
    assert normalized_match("the quick brown fox", "a totally different sentence") < 0.5


TTS_MODEL = os.environ.get("HAL0_VOICE_TTS_MODEL", "kokoro-v1")
TTS_VOICE = os.environ.get("HAL0_VOICE_TTS_VOICE", "af_heart")
STT_MODEL = os.environ.get("HAL0_VOICE_STT_MODEL", "whisper-v3:turbo")
_LIVE_URL = os.environ.get("HAL0_VOICE_LIVE_URL")


@pytest.mark.skipif(not _LIVE_URL, reason="set HAL0_VOICE_LIVE_URL to run the live round-trip")
def test_voice_roundtrip_live() -> None:
    import httpx

    phrase = "the quick brown fox jumps over the lazy dog"
    base = _LIVE_URL.rstrip("/")
    with httpx.Client(timeout=180.0) as client:
        speech = client.post(
            f"{base}/v1/audio/speech",
            json={
                "model": TTS_MODEL,
                "input": phrase,
                "voice": TTS_VOICE,
                "response_format": "wav",
            },
        )
        assert speech.status_code == 200, speech.text
        audio = speech.content
        assert len(audio) > 1000, f"audio too small: {len(audio)}"

        fmt = "wav" if audio[:4] == b"RIFF" else "mp3"
        stt = client.post(
            f"{base}/v1/audio/transcriptions",
            files={"file": (f"speech.{fmt}", audio, f"audio/{fmt}")},
            data={"model": STT_MODEL},
        )
        assert stt.status_code == 200, stt.text
        text = stt.json()["text"]

    ratio = normalized_match(phrase, text)
    assert ratio >= 0.8, f"round-trip mismatch ratio={ratio:.2f}: {text!r}"
