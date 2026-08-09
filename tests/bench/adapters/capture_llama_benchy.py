"""capture_llama_benchy.py — regenerate the llama-benchy fixtures from the
REAL pinned tool (tag v0.4.0, sha 446dd42fde2ebbaa1d68a0dfe9dc1e5b833f95ad).

NOT run by pytest (CI has no network and no tools — see the plan's
Architecture section). This is a manual, human-invoked capture utility: it
starts a minimal stdlib fake OpenAI-compatible endpoint (no FastAPI/uvicorn
dependency — matches this module's "stdlib-only fixture capture" mandate),
runs the real `llama-benchy` binary against it, and writes the resulting
JSON reports into ``fixtures/llama_benchy/``.

Usage (from a scratch venv with llama-benchy installed at the pinned sha)::

    uv venv /tmp/benchy-venv
    /tmp/benchy-venv/bin/pip install \\
        "git+https://github.com/eugr/llama-benchy@446dd42fde2ebbaa1d68a0dfe9dc1e5b833f95ad"
    LLAMA_BENCHY_BIN=/tmp/benchy-venv/bin/llama-benchy \\
        /tmp/benchy-venv/bin/python tests/bench/adapters/capture_llama_benchy.py

The fake endpoint's shape (``/models``, ``/v1/models``, ``/chat/completions``,
``/v1/chat/completions``, streaming SSE with a synthetic-but-plausible token
cadence, and a ``/book`` text corpus so the tool's own book-download step
never touches the network) was reverse-engineered from the pinned tag's
``tests/mock_server.py`` (a FastAPI reference fixture upstream ships for its
own test suite) and ``src/llama_benchy/client.py`` (the actual HTTP calls:
GET ``{base_url}/models`` for model auto-detect and latency-mode "api", POST
``{base_url}/chat/completions`` — note NOT ``/v1/...`` explicitly; callers
are expected to put ``/v1`` in ``--base-url`` itself, matching a real
llama-server's OpenAI-compatible mount point).

Not committed hand-built: ``empty_benchmarks.json`` (a report with zero
measured rows) and ``malformed.json`` (deliberately truncated) are
SYNTHETIC — no real tool invocation produces a truncated file or an
all-empty ``benchmarks`` array by construction (every ``--pp``/``--tg``/
``--depth`` combination always yields >=1 row), so those two exist purely to
exercise the parser's malformed/empty-input paths and are hand-authored, not
captured. Every other fixture in this directory came from a real run of the
pinned binary.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llama_benchy"
BENCHY_BIN = os.environ.get("LLAMA_BENCHY_BIN", "llama-benchy")
HOST = "127.0.0.1"
PORT = int(os.environ.get("LLAMA_BENCHY_CAPTURE_PORT", "8901"))
MODEL_ID = "mock-model"
GEN_TPS = 200.0
PP_TPS = 4000.0
BOOK_TEXT = "Sherlock Holmes sat in his armchair, contemplating the fog outside. " * 4000


class _FakeHandler(BaseHTTPRequestHandler):
    """Minimal stdlib OpenAI-compatible endpoint: only the two routes
    ``client.py`` actually calls (see module docstring), plus a ``/book``
    route so the tool's corpus download never leaves localhost."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/models", "/v1/models"):
            self._send_json(
                {
                    "object": "list",
                    "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "mock"}],
                }
            )
        elif self.path == "/book":
            body = BOOK_TEXT.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path not in ("/chat/completions", "/v1/chat/completions"):
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        messages = req.get("messages", [])
        max_tokens = req.get("max_tokens") or 10
        stream = bool(req.get("stream"))
        want_usage = bool((req.get("stream_options") or {}).get("include_usage"))
        prompt_tokens = sum(len(m.get("content", "").split()) for m in messages) or 1
        is_coherence = any("capital of France" in m.get("content", "") for m in messages)
        text = "Paris" if is_coherence else "mock "
        request_id = f"chatcmpl-{uuid.uuid4()}"
        created = int(time.time())

        if not stream:
            time.sleep(max_tokens / GEN_TPS)
            self._send_json(
                {
                    "id": request_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": MODEL_ID,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": max_tokens,
                        "total_tokens": prompt_tokens + max_tokens,
                    },
                }
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send_chunk(data: dict) -> None:
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())

        time.sleep(prompt_tokens / PP_TPS)  # simulate prompt processing
        token_interval = 1.0 / GEN_TPS
        for _ in range(max_tokens):
            time.sleep(token_interval)
            send_chunk(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": MODEL_ID,
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                }
            )
        send_chunk(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": MODEL_ID,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        if want_usage:
            send_chunk(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": MODEL_ID,
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": max_tokens,
                        "total_tokens": prompt_tokens + max_tokens,
                    },
                    "choices": [],
                }
            )
        self.wfile.write(b"data: [DONE]\n\n")


def _run_capture(args: list[str], save_result: Path) -> None:
    cmd = [
        BENCHY_BIN,
        "--base-url",
        f"http://{HOST}:{PORT}/v1",
        "--api-key",
        "EMPTY",
        "--tokenizer",
        "gpt2",
        "--book-url",
        f"http://{HOST}:{PORT}/book",
        "--no-warmup",
        "--no-adapt-prompt",
        "--skip-coherence",
        "--latency-mode",
        "none",
        "--format",
        "json",
        "--save-result",
        str(save_result),
        *args,
    ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), _FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _run_capture(
            ["--model", "mock-model", "--pp", "64", "--tg", "32", "--depth", "0", "--runs", "2"],
            FIXTURES_DIR / "happy_single_depth.json",
        )
        _run_capture(
            [
                "--model",
                "mock-model",
                "--pp",
                "64",
                "--tg",
                "32",
                "--depth",
                "0",
                "128",
                "--runs",
                "2",
            ],
            FIXTURES_DIR / "happy_multi_depth.json",
        )
        print(f"Captured fixtures under {FIXTURES_DIR}")
        print(
            "error_connection_refused.stderr.txt and the two synthetic fixtures "
            "(empty_benchmarks.json, malformed.json) are hand-authored — see this "
            "script's module docstring."
        )
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
