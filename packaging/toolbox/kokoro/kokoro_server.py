"""kokoro-server — FastAPI wrapper around kokoro-onnx.

Implements the contract the hal0 KokoroProvider expects (see
src/hal0/providers/kokoro.py):

  GET  /health                  -> {status: "ok", ...}
  GET  /v1/models               -> {data: [{id: "kokoro"}]}
  POST /v1/audio/speech         -> OpenAI-compat TTS, returns raw audio bytes
  GET  /v1/audio/voices         -> {voices: [...]}  (compatibility extension)

CLI flags mirror KokoroProvider.start_cmd():
  --model_path     Host-mounted model dir; expects kokoro-v0_19.onnx + voices.bin
  --default_voice  Default voice id (e.g. af_bella)
  --port           Bind port (slot default 8090)
  --host           Bind host (default 0.0.0.0)

Body shape for /v1/audio/speech (OpenAI compatible):
    {
      "model":           "kokoro",
      "input":           "Hello, world.",
      "voice":           "af_bella",
      "response_format": "mp3" | "wav" | "opus" | "flac" | "pcm",
      "speed":           1.0
    }
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel


def _download(url: str, dest: str) -> None:
    """Stream-download a URL to a path; small helper so we don't pull
    in requests/httpx just for two GETs."""
    import urllib.request

    tmp = dest + ".part"
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:  # noqa: S310 — known URL
        while True:
            buf = r.read(1 << 20)
            if not buf:
                break
            f.write(buf)
    os.replace(tmp, dest)

log = logging.getLogger("kokoro-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

SAMPLE_RATE = 24_000  # kokoro native sample rate

_state: dict[str, object] = {
    "model": None,
    "default_voice": "af_bella",
    "voices": [],
    "loaded": False,
}


# ── Model load ────────────────────────────────────────────────────────────────
def _resolve_paths(model_path: str | None) -> tuple[str | None, str | None]:
    """Locate kokoro-v*.onnx and voices-*.bin under model_path, if present."""
    if not model_path:
        return None, None
    root = Path(model_path)
    if not root.is_dir():
        return None, None
    candidates = list(root.rglob("kokoro-*.onnx"))
    voices = list(root.rglob("voices*.bin"))
    onnx = str(candidates[0]) if candidates else None
    vbin = str(voices[0]) if voices else None
    return onnx, vbin


def _load_model(model_path: str | None, default_voice: str) -> None:
    """Load Kokoro ONNX and stash on _state."""
    try:
        from kokoro_onnx import Kokoro  # type: ignore
    except ImportError as exc:  # pragma: no cover — image install is the contract
        raise RuntimeError(
            "kokoro-onnx not installed; this image is broken"
        ) from exc

    onnx_path, voices_path = _resolve_paths(model_path)

    if not (onnx_path and voices_path):
        # Auto-fetch from upstream HF mirror. Kokoro's ONNX weights and
        # the voices pack live at:
        #   https://github.com/thewh1teagle/kokoro-onnx/releases
        # We fetch into HF_HOME so subsequent restarts reuse the cache.
        cache_root = Path(os.environ.get("HF_HOME", "/var/lib/hal0/.cache/huggingface"))
        cache_dir = cache_root / "kokoro-onnx"
        cache_dir.mkdir(parents=True, exist_ok=True)
        onnx_path = str(cache_dir / "kokoro-v1.0.onnx")
        voices_path = str(cache_dir / "voices-v1.0.bin")
        if not Path(onnx_path).exists():
            log.info("downloading kokoro ONNX weights to %s", onnx_path)
            _download(
                "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
                onnx_path,
            )
        if not Path(voices_path).exists():
            log.info("downloading kokoro voices pack to %s", voices_path)
            _download(
                "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
                voices_path,
            )

    log.info("loading kokoro: onnx=%s voices=%s", onnx_path, voices_path)
    model = Kokoro(onnx_path, voices_path)

    _state["model"] = model
    _state["default_voice"] = default_voice
    try:
        _state["voices"] = list(model.get_voices())
    except Exception:
        _state["voices"] = [default_voice]
    _state["loaded"] = True
    log.info("kokoro loaded: voices=%d default=%s", len(_state["voices"]), default_voice)


# ── Audio helpers ─────────────────────────────────────────────────────────────
_FORMAT_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}


def _encode_audio(samples: np.ndarray, response_format: str) -> tuple[bytes, str]:
    """Encode float32 mono samples to the requested format."""
    fmt = response_format.lower()
    if fmt == "pcm":
        # Signed 16-bit PCM little-endian (OpenAI's pcm contract)
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        return pcm.tobytes(), _FORMAT_MIME["pcm"]
    if fmt == "wav":
        buf = io.BytesIO()
        sf.write(buf, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue(), _FORMAT_MIME["wav"]
    if fmt == "flac":
        buf = io.BytesIO()
        sf.write(buf, samples, SAMPLE_RATE, format="FLAC")
        return buf.getvalue(), _FORMAT_MIME["flac"]
    if fmt in ("mp3", "opus"):
        # Shell to ffmpeg for compressed formats — keeps the runtime
        # deps small and avoids dragging in lameenc/ogg-opus.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_f:
            sf.write(wav_f.name, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
            wav_path = wav_f.name
        out_path = wav_path + "." + fmt
        try:
            codec = {"mp3": "libmp3lame", "opus": "libopus"}[fmt]
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, "-c:a", codec, out_path],
                check=True,
            )
            with open(out_path, "rb") as f:
                return f.read(), _FORMAT_MIME[fmt]
        finally:
            for p in (wav_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    raise HTTPException(status_code=400, detail=f"unsupported response_format={fmt!r}")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="hal0-kokoro", version="1.0.0")


class SpeechRequest(BaseModel):
    model: str = "kokoro"
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    speed: float = 1.0


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok" if _state["loaded"] else "loading",
        "model_loaded": bool(_state["loaded"]),
        "default_voice": _state.get("default_voice"),
    }


@app.get("/v1/models")
async def models() -> dict[str, object]:
    if not _state["loaded"]:
        return {"data": []}
    return {
        "data": [
            {"id": "kokoro", "object": "model", "owned_by": "hexgrad"},
        ]
    }


@app.get("/v1/audio/voices")
async def voices() -> dict[str, object]:
    return {"voices": list(_state.get("voices") or [])}


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest) -> Response:
    if not _state["loaded"]:
        raise HTTPException(status_code=503, detail="model not loaded")
    if not req.input.strip():
        raise HTTPException(status_code=400, detail="empty input")
    voice = req.voice or str(_state["default_voice"])
    speed = max(0.5, min(2.0, float(req.speed or 1.0)))

    model = _state["model"]
    try:
        samples, sample_rate = model.create(  # type: ignore[union-attr]
            req.input, voice=voice, speed=speed, lang="en-us"
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("synthesis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    samples = np.asarray(samples, dtype=np.float32)
    if sample_rate != SAMPLE_RATE:
        # Cheap linear-interp resample.
        ratio = SAMPLE_RATE / sample_rate
        n_out = int(round(len(samples) * ratio))
        samples = np.interp(
            np.linspace(0, len(samples) - 1, n_out, dtype=np.float64),
            np.arange(len(samples), dtype=np.float64),
            samples,
        ).astype(np.float32)

    audio_bytes, mime = _encode_audio(samples, req.response_format)
    return Response(content=audio_bytes, media_type=mime)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="hal0 kokoro TTS server")
    p.add_argument("--model_path", default="", help="local model dir (optional)")
    p.add_argument("--default_voice", default="af_bella")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    try:
        _load_model(args.model_path or None, args.default_voice)
    except Exception:
        log.exception("model load failed at startup; /health will report loading=false")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
