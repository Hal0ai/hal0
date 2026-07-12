"""qwen3tts-server — FastAPI wrapper around the qwen-tts package.

Implements the same OpenAI-compatible contract the hal0 TTS path expects
(mirrors packaging/toolbox/kokoro/kokoro_server.py), so this slot is a
drop-in alternative voice engine alongside Kokoro:

  GET  /health                  -> {status, model_loaded, ...}
  GET  /v1/models               -> {data: [{id: "qwen3-tts"}]}
  POST /v1/audio/speech         -> OpenAI-compat TTS, returns raw audio bytes
  GET  /v1/audio/voices         -> {voices: [...]}  (compatibility extension)

Unlike Kokoro this engine is GPU (ROCm) and multilingual (10 languages) with
description-style timbre control. It loads Qwen3-TTS-12Hz-1.7B-CustomVoice.

CLI flags (mirror the kokoro server so the systemd unit / provider stays
uniform):
  --model_path       Local model dir (e.g. .../Qwen3-TTS-12Hz-1.7B-CustomVoice)
  --default_voice    Default speaker/timbre id (default: Ryan)
  --default_language Default synthesis language (default: Auto)
  --port             Bind port (default 8087)
  --host             Bind host (default 0.0.0.0)

Body shape for /v1/audio/speech (OpenAI compatible + extensions):
    {
      "model":           "qwen3-tts",
      "input":           "Hello, world.",
      "voice":           "Ryan",          # speaker/timbre name
      "response_format": "mp3" | "wav" | "opus" | "flac" | "pcm",
      "speed":           1.0,
      "language":        "Auto" | "English" | "German" | ...,   # extension
      "instruct":        "Speak warmly."                         # extension
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

log = logging.getLogger("qwen3tts-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# CustomVoice built-in timbres (Qwen3-TTS-12Hz-1.7B-CustomVoice).
KNOWN_SPEAKERS = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",  # Chinese / dialects
    "Ryan", "Aiden",                                    # English
    "Ono_Anna",                                         # Japanese
    "Sohee",                                            # Korean
]
# Qwen3-TTS covers 10 languages; "Auto" lets the model detect from the text,
# which is what we want for translated output.
KNOWN_LANGUAGES = [
    "Auto", "Chinese", "English", "Japanese", "Korean", "German",
    "French", "Russian", "Portuguese", "Spanish", "Italian",
]

_state: dict[str, object] = {
    "model": None,
    "default_voice": "Ryan",
    "default_language": "Auto",
    "sample_rate": 24_000,
    "loaded": False,
}


# ── Model load ────────────────────────────────────────────────────────────────
def _resolve_model_dir(model_path: str | None) -> str | None:
    """Return a usable local model dir, or None to fall back to the HF id.

    Accepts either the model dir itself (containing config.json) or a parent
    dir holding a single Qwen3-TTS-*CustomVoice* checkout.
    """
    if not model_path:
        return None
    root = Path(model_path)
    if not root.is_dir():
        return None
    if (root / "config.json").is_file():
        return str(root)
    # Parent dir: find the CustomVoice checkout under it.
    for child in sorted(root.glob("*CustomVoice*")):
        if (child / "config.json").is_file():
            return str(child)
    return None


def _load_model(model_path: str | None, default_voice: str, default_language: str) -> None:
    """Load Qwen3-TTS CustomVoice onto the GPU and stash on _state.

    ROCm note: torch maps ``cuda:0`` to the HIP device, so ``device_map="cuda:0"``
    is correct on this box. We deliberately omit ``attn_implementation`` so
    transformers picks a backend that exists without flash-attn (sdpa/eager),
    which does not build cleanly on ROCm.
    """
    try:
        import torch
        from qwen_tts import Qwen3TTSModel  # type: ignore
    except ImportError as exc:  # pragma: no cover — image install is the contract
        raise RuntimeError("qwen-tts / torch not installed; this image is broken") from exc

    src = _resolve_model_dir(model_path) or "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    log.info("loading qwen3-tts CustomVoice from %s (device=cuda:0, dtype=bf16)", src)

    model = Qwen3TTSModel.from_pretrained(
        src,
        device_map="cuda:0",
        dtype=torch.bfloat16,
    )

    _state["model"] = model
    _state["default_voice"] = default_voice if default_voice in KNOWN_SPEAKERS else "Ryan"
    _state["default_language"] = default_language if default_language in KNOWN_LANGUAGES else "Auto"
    _state["loaded"] = True
    log.info(
        "qwen3-tts loaded: default_voice=%s default_language=%s",
        _state["default_voice"], _state["default_language"],
    )

    # Warm the kernels so the first real request isn't paying JIT/allocation cost.
    try:
        wavs, sr = model.generate_custom_voice(
            text="Ready.", language="English", speaker=str(_state["default_voice"]),
        )
        _state["sample_rate"] = int(sr)
        log.info("qwen3-tts warmup ok: sr=%d", int(sr))
    except Exception:  # noqa: BLE001
        log.exception("warmup synth failed (continuing; /health stays loaded)")


# ── Audio helpers (shared shape with kokoro_server.py) ──────────────────────────
_FORMAT_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}


def _encode_audio(samples: np.ndarray, sample_rate: int, response_format: str, speed: float) -> tuple[bytes, str]:
    """Encode float32 mono samples to the requested format, applying ``speed``.

    Speed is applied via ffmpeg's atempo filter for compressed/wav outputs.
    Qwen3-TTS has no native speed knob, so we post-process; pcm/raw paths
    skip tempo change to stay dependency-free.
    """
    fmt = response_format.lower()
    speed = max(0.5, min(2.0, float(speed or 1.0)))

    if fmt == "pcm":
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        return pcm.tobytes(), _FORMAT_MIME["pcm"]

    # For everything else we go through ffmpeg when speed != 1.0 (atempo),
    # otherwise encode directly with libsndfile for wav/flac.
    if fmt in ("wav", "flac") and abs(speed - 1.0) < 1e-3:
        buf = io.BytesIO()
        subtype = "PCM_16" if fmt == "wav" else None
        sf.write(buf, samples, sample_rate, format=fmt.upper(), subtype=subtype)
        return buf.getvalue(), _FORMAT_MIME[fmt]

    if fmt in ("wav", "flac", "mp3", "opus"):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_f:
            sf.write(wav_f.name, samples, sample_rate, format="WAV", subtype="PCM_16")
            wav_path = wav_f.name
        out_path = wav_path + "." + fmt
        try:
            af = [] if abs(speed - 1.0) < 1e-3 else ["-filter:a", f"atempo={speed:.3f}"]
            codec = {"mp3": "libmp3lame", "opus": "libopus", "wav": "pcm_s16le", "flac": "flac"}[fmt]
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, *af, "-c:a", codec, out_path],
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
app = FastAPI(title="hal0-qwen3tts", version="1.0.0")


class SpeechRequest(BaseModel):
    model: str = "qwen3-tts"
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    speed: float = 1.0
    language: str | None = None
    instruct: str | None = None


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok" if _state["loaded"] else "loading",
        "model_loaded": bool(_state["loaded"]),
        "default_voice": _state.get("default_voice"),
        "default_language": _state.get("default_language"),
    }


@app.get("/v1/models")
async def models() -> dict[str, object]:
    if not _state["loaded"]:
        return {"data": []}
    return {"data": [{"id": "qwen3-tts", "object": "model", "owned_by": "qwen"}]}


@app.get("/v1/audio/voices")
async def voices() -> dict[str, object]:
    return {"voices": list(KNOWN_SPEAKERS), "languages": list(KNOWN_LANGUAGES)}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest) -> Response:
    # Sync def on purpose: model.generate_custom_voice is blocking/GPU-bound,
    # so Starlette runs it in a threadpool and the event loop stays responsive.
    if not _state["loaded"]:
        raise HTTPException(status_code=503, detail="model not loaded")
    if not req.input.strip():
        raise HTTPException(status_code=400, detail="empty input")

    speaker = req.voice or str(_state["default_voice"])
    if speaker not in KNOWN_SPEAKERS:
        speaker = str(_state["default_voice"])
    language = req.language or str(_state["default_language"])
    if language not in KNOWN_LANGUAGES:
        language = str(_state["default_language"])

    model = _state["model"]
    try:
        kwargs: dict[str, object] = {"text": req.input, "language": language, "speaker": speaker}
        if req.instruct:
            kwargs["instruct"] = req.instruct
        wavs, sr = model.generate_custom_voice(**kwargs)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        log.exception("synthesis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    samples = np.asarray(wavs[0], dtype=np.float32)
    sample_rate = int(sr)
    _state["sample_rate"] = sample_rate

    audio_bytes, mime = _encode_audio(samples, sample_rate, req.response_format, req.speed)
    return Response(content=audio_bytes, media_type=mime)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="hal0 qwen3-tts server")
    p.add_argument("--model_path", default="", help="local model dir (optional)")
    p.add_argument("--default_voice", default="Ryan")
    p.add_argument("--default_language", default="Auto")
    p.add_argument("--port", type=int, default=8087)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    try:
        _load_model(args.model_path or None, args.default_voice, args.default_language)
    except Exception:
        log.exception("model load failed at startup; /health will report loading=false")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
