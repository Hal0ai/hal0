"""Unit tests for the in-container moonshine_server FastAPI app.

The server lives at ``packaging/toolbox/moonshine/moonshine_server.py``
and is baked into the hal0-toolbox-moonshine image. It's not on the
``hal0`` import path, so we load it by file path.

Scope of this module: the audio-decode failure mode covered by hal0
issue #33. The transcription model itself is heavy + ONNX-only and not
in scope — every test here either skips the decode step entirely or
swaps the model out before calling.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# moonshine_server.py imports numpy at module top — it's a runtime dep
# of the toolbox container, not of hal0 itself, so it isn't in our dev
# extras. Skip the whole module when numpy is absent rather than erroring
# at collection time.
pytest.importorskip("numpy")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_PATH = _REPO_ROOT / "packaging" / "toolbox" / "moonshine" / "moonshine_server.py"


def _load_moonshine_server() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("moonshine_server", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["moonshine_server"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server_module() -> types.ModuleType:
    return _load_moonshine_server()


@pytest.fixture
def client(server_module: types.ModuleType) -> TestClient:
    # The route short-circuits with 503 unless ``loaded`` is true, so flip
    # the flag for the bad-input tests. _transcribe is never reached on
    # these paths (decode fails first) so we don't need a real model.
    server_module._state["loaded"] = True
    server_module._state["model_arch"] = "small_streaming"
    server_module._state["model_id"] = "moonshine-small-streaming-en"
    return TestClient(server_module.app)


# ── Redaction helper ──────────────────────────────────────────────────────────


def test_redact_ffmpeg_argv_masks_input_path(server_module: types.ModuleType) -> None:
    argv = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        "/tmp/tmp123abc.bin",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        "/tmp/tmp123abc.bin.wav",
    ]
    redacted = server_module._redact_ffmpeg_argv(argv, "/tmp/tmp123abc.bin")
    # The standalone input path is fully masked.
    assert "/tmp/tmp123abc.bin" not in redacted
    assert "<input>" in redacted
    # The output path (input + ".wav") keeps its suffix for debugging
    # but the user-controlled prefix is masked.
    assert "<input>.wav" in redacted
    # Codec / format flags are untouched.
    assert "-ar" in redacted
    assert "16000" in redacted


# ── /v1/audio/transcriptions on bad input ────────────────────────────────────


def test_text_payload_returns_415_without_ffmpeg_argv(client: TestClient) -> None:
    """Posting a text/plain payload (claimed audio/wav) must not leak ffmpeg argv.

    This is the issue #33 acceptance test: send a non-audio body and
    assert (a) the status is 4xx (415 specifically), (b) the response
    body does not contain "ffmpeg", and (c) it does not contain any
    tempfile path under /tmp.
    """
    files = {"file": ("hello.wav", b"this is not audio, just plain text", "audio/wav")}
    resp = client.post("/v1/audio/transcriptions", files=files)

    assert resp.status_code == 415, resp.text
    body_text = resp.text
    body_lower = body_text.lower()
    # Acceptance: response body must not contain the subprocess argv.
    assert "ffmpeg" not in body_lower, body_text
    # Acceptance: response body must not contain a tempfile path.
    assert "/tmp/" not in body_text, body_text
    # Acceptance: stable hal0 envelope code is set.
    payload = resp.json()
    detail = payload.get("detail") or {}
    error = detail.get("error") or {}
    assert error.get("code") == "audio.unsupported_format"
    assert "decode" in (error.get("message") or "").lower()
    # The decoder returncode is exposed for client diagnostics, but the
    # argv itself isn't. Note: field name is "decoder_returncode" — the
    # substring "ffmpeg" must not appear anywhere in the body (issue #33).
    assert "decoder_returncode" in (error.get("details") or {})


def test_single_byte_payload_returns_415(client: TestClient) -> None:
    """A 1-byte file claiming audio/wav is also rejected without leaks."""
    files = {"file": ("tiny.wav", b"x", "audio/wav")}
    resp = client.post("/v1/audio/transcriptions", files=files)

    assert resp.status_code == 415, resp.text
    assert "ffmpeg" not in resp.text.lower()
    assert "/tmp/" not in resp.text


def test_empty_upload_still_returns_400(client: TestClient) -> None:
    """The pre-existing empty-upload guard is preserved — it short-circuits
    before the decode path, so issue #33's 415 only applies to non-empty
    bad input."""
    files = {"file": ("empty.wav", b"", "audio/wav")}
    resp = client.post("/v1/audio/transcriptions", files=files)
    assert resp.status_code == 400
    assert "empty" in resp.text.lower()
