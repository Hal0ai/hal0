"""moonshine-server — FastAPI wrapper around useful-moonshine-onnx.

Implements the contract the hal0 MoonshineProvider expects (see
src/hal0/providers/moonshine.py):

  GET  /health                  -> {status, model_loaded, model_arch, model_id}
  GET  /v1/models               -> {data: [{id, ...}]}
  POST /v1/audio/transcriptions -> OpenAI-compat multipart upload
  WS   /v1/audio/stream         -> live PCM16 @ 16kHz mono frames

CLI flags mirror MoonshineProvider.start_cmd():
  --model_path <path>      Host-mounted model directory (optional; package
                           weights are downloaded on first use if absent).
  --model_arch <arch>      tiny | tiny_streaming | base | small | small_streaming
  --port <port>            Bind port (slot default 8089).
  --host <host>            Bind host (default 0.0.0.0).

This file is baked into the hal0-toolbox-moonshine image. The runtime
container is non-root (hal0:hal0, UID 1000); model dirs are bind-mounted
read-only from the host's HAL0_HOME/models.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import JSONResponse

log = logging.getLogger("moonshine-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# ── State ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16_000

_state: dict[str, object] = {
    "model": None,
    "model_arch": None,
    "model_id": None,
    "model_path": None,
    "loaded": False,
}


# ── Model load ────────────────────────────────────────────────────────────────
def _load_model(model_arch: str, model_path: str | None) -> None:
    """Load the Moonshine ONNX model and stash on _state."""
    try:
        import moonshine_onnx  # type: ignore
    except ImportError as exc:  # pragma: no cover — image install is the contract
        raise RuntimeError(
            "useful-moonshine-onnx not installed; this image is broken"
        ) from exc

    # useful-moonshine-onnx's MoonshineOnnxModel takes a model_name like
    # "moonshine/tiny" or a local directory path. If we got a local
    # model_path that exists, prefer that; else fall back to the canonical
    # HF name keyed off arch.
    canonical = {
        "tiny": "moonshine/tiny",
        "tiny_streaming": "moonshine/tiny",
        "base": "moonshine/base",
        "small": "moonshine/base",
        "small_streaming": "moonshine/base",
    }
    if model_arch not in canonical:
        raise ValueError(f"unknown model_arch={model_arch!r}; expected one of {list(canonical)}")
    model_name = canonical[model_arch]

    load_target = model_name
    if model_path and Path(model_path).is_dir():
        load_target = model_path

    log.info("loading moonshine arch=%s target=%s", model_arch, load_target)
    model = moonshine_onnx.MoonshineOnnxModel(model_name=load_target)
    _state["model"] = model
    _state["model_arch"] = model_arch
    _state["model_id"] = f"moonshine-{model_arch}-en"
    _state["model_path"] = model_path or load_target
    _state["loaded"] = True
    log.info("moonshine loaded: model_id=%s", _state["model_id"])


# ── Audio helpers ─────────────────────────────────────────────────────────────
class UnsupportedAudioFormat(Exception):
    """ffmpeg refused to decode the upload.

    Raised by ``_decode_audio`` instead of letting ``CalledProcessError``
    propagate — that exception's ``cmd`` attribute carries the full
    ffmpeg argv including the user-supplied tempfile path, which then
    leaks into the API response.  The route handler translates this to
    a 415 with the hal0 envelope shape and no subprocess argv.
    """

    def __init__(self, returncode: int) -> None:
        super().__init__(f"ffmpeg decode failed (rc={returncode})")
        self.returncode = returncode


def _redact_ffmpeg_argv(argv: list[str], input_path: str) -> list[str]:
    """Return a copy of ``argv`` with the user-supplied input path masked.

    Replaces any element equal to ``input_path`` with the placeholder
    ``<input>`` so the server-side log line carries no temp-file path or
    user-controlled filename. Everything else (codec flags, sample rate,
    output path which is derived from the same temp name) is masked the
    same way if it starts with ``input_path``.
    """
    redacted: list[str] = []
    for arg in argv:
        if arg == input_path:
            redacted.append("<input>")
        elif arg.startswith(input_path):
            # out_path is `in_path + ".wav"`; keep the suffix visible for debugging.
            redacted.append("<input>" + arg[len(input_path) :])
        else:
            redacted.append(arg)
    return redacted


def _decode_audio(raw: bytes, filename: str | None) -> np.ndarray:
    """Decode arbitrary audio bytes to mono float32 @ 16kHz via ffmpeg.

    Raises :class:`UnsupportedAudioFormat` when ffmpeg refuses the input,
    so the route handler can translate to a 415 with the hal0 envelope
    instead of letting :class:`subprocess.CalledProcessError` propagate —
    that exception's ``.cmd`` carries the full argv (and therefore the
    user-supplied temp path) into the API response.
    """
    # Fast path: if soundfile can read it natively (wav/flac/ogg), no ffmpeg.
    try:
        data, sr = sf.read(io.BytesIO(raw), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != SAMPLE_RATE:
            # Cheap resample via numpy linear interp; for production we'd
            # use scipy or librosa, but moonshine is robust to mild
            # resampling artifacts and we keep deps small.
            ratio = SAMPLE_RATE / sr
            n_out = int(round(len(data) * ratio))
            data = np.interp(
                np.linspace(0, len(data) - 1, n_out, dtype=np.float64),
                np.arange(len(data), dtype=np.float64),
                data,
            ).astype(np.float32)
        return data
    except Exception:
        pass

    # Slow path: shell out to ffmpeg.
    suffix = ""
    if filename and "." in filename:
        suffix = "." + filename.rsplit(".", 1)[-1].lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as in_f:
        in_f.write(raw)
        in_path = in_f.name
    out_path = in_path + ".wav"
    argv = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", in_path,
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "wav", out_path,
    ]
    try:
        try:
            subprocess.run(argv, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            # Log the full failure server-side with the user-supplied
            # tempfile path masked. stderr from ffmpeg may include the
            # input path too, so redact it the same way.
            redacted_argv = _redact_ffmpeg_argv(argv, in_path)
            stderr_text = ""
            if exc.stderr:
                try:
                    stderr_text = exc.stderr.decode("utf-8", errors="replace")
                except Exception:
                    stderr_text = repr(exc.stderr)
                stderr_text = stderr_text.replace(in_path, "<input>")
            log.warning(
                "ffmpeg decode failed rc=%d argv=%r stderr=%s",
                exc.returncode,
                redacted_argv,
                stderr_text[:500],
            )
            raise UnsupportedAudioFormat(exc.returncode) from None
        data, _ = sf.read(out_path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data.astype(np.float32)
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _transcribe(pcm: np.ndarray) -> str:
    """Run moonshine inference. Returns plain text."""
    model = _state.get("model")
    if model is None:
        raise RuntimeError("model not loaded")
    # moonshine_onnx accepts (1, n_samples) float32
    if pcm.ndim == 1:
        pcm = pcm.reshape(1, -1)
    tokens = model.generate(pcm)
    # generate() returns a list-of-list of token ids; decode using upstream
    # tokenizer accessor.
    try:
        from moonshine_onnx import load_tokenizer  # type: ignore
        tok = load_tokenizer()
        text = tok.decode_batch(tokens)
        if isinstance(text, list):
            return " ".join(t.strip() for t in text).strip()
        return str(text).strip()
    except Exception:
        # Older API returns text directly
        return str(tokens).strip()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="hal0-moonshine", version="1.0.0")


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok" if _state["loaded"] else "loading",
        "model_loaded": bool(_state["loaded"]),
        "model_arch": _state.get("model_arch"),
        "model_id": _state.get("model_id"),
    }


@app.get("/v1/models")
async def models() -> dict[str, object]:
    if not _state["loaded"]:
        return {"data": []}
    return {
        "data": [
            {
                "id": _state["model_id"],
                "object": "model",
                "owned_by": "moonshine",
            }
        ]
    }


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str | None = Form(None),
    response_format: str = Form("json"),
    language: str | None = Form(None),  # noqa: ARG001 — English-only model
    prompt: str | None = Form(None),  # noqa: ARG001 — not used
    temperature: float | None = Form(None),  # noqa: ARG001 — not used
) -> JSONResponse:
    if not _state["loaded"]:
        raise HTTPException(status_code=503, detail="model not loaded")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio upload")
    try:
        pcm = _decode_audio(raw, file.filename)
    except UnsupportedAudioFormat as exc:
        # TODO(#32): swap HTTPException for a typed BadRequest subclass
        # of Hal0Error once #32 lands so the envelope shape comes from
        # the shared middleware. For now we hand-build the envelope to
        # match the hal0 main proxy contract.
        #
        # Crucially: do NOT include ``exc.cmd`` / ``exc.stdout`` /
        # ``exc.stderr`` from the underlying CalledProcessError — those
        # carry the user-supplied tempfile path and stderr text from
        # the upload, both of which would leak through the proxy.
        raise HTTPException(
            status_code=415,
            detail={
                "error": {
                    "code": "audio.unsupported_format",
                    "message": "Failed to decode audio input",
                    # Field name is "decoder_returncode" not "ffmpeg_*" so
                    # the response body contains no reference to the
                    # underlying tool — issue #33 explicitly forbids the
                    # substring "ffmpeg" in the response.
                    "details": {"decoder_returncode": exc.returncode},
                }
            },
        ) from None
    try:
        text = _transcribe(pcm)
    except Exception as exc:  # noqa: BLE001 — return as 500
        log.exception("transcription failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if response_format in ("text", "txt"):
        return JSONResponse(content=text, media_type="text/plain")
    if response_format == "verbose_json":
        return JSONResponse(
            content={
                "task": "transcribe",
                "language": "en",
                "duration": float(len(pcm) / SAMPLE_RATE),
                "text": text,
                "segments": [],
            }
        )
    return JSONResponse(content={"text": text})


@app.websocket("/v1/audio/stream")
async def stream(ws: WebSocket) -> None:
    """Live PCM16 streaming endpoint.

    Frame protocol:
      - Client sends raw PCM16 little-endian frames @ 16kHz mono.
      - Server batches into ~1s windows and emits partial transcripts as
        JSON text frames: {"text": "...", "is_final": false}.
      - Final result on close: {"text": "...", "is_final": true}.
    """
    await ws.accept()
    if not _state["loaded"]:
        await ws.send_json({"error": "model not loaded"})
        await ws.close()
        return
    buf = bytearray()
    last_emit = b""
    chunk_samples = SAMPLE_RATE  # 1s
    try:
        while True:
            data = await ws.receive_bytes()
            buf.extend(data)
            if len(buf) >= chunk_samples * 2:
                pcm = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
                try:
                    text = await asyncio.get_running_loop().run_in_executor(
                        None, _transcribe, pcm
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception("stream transcribe failed")
                    await ws.send_json({"error": str(exc)})
                    continue
                if text and text != last_emit:
                    last_emit = text
                    await ws.send_json({"text": text, "is_final": False})
    except Exception as exc:  # noqa: BLE001 — connection drop is fine
        log.info("ws closed: %s", exc)
    finally:
        try:
            await ws.send_json({"text": last_emit, "is_final": True})
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="hal0 moonshine STT server")
    p.add_argument("--model_path", default="", help="local model dir (optional)")
    p.add_argument("--model_arch", default="small_streaming")
    p.add_argument("--port", type=int, default=8089)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    try:
        _load_model(args.model_arch, args.model_path or None)
    except Exception:
        log.exception("model load failed at startup; /health will report loading=false")
        # Don't exit — the server still binds so /health is reachable
        # and the slot health-probe surfaces the failure cleanly.

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
