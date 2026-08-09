#!/usr/bin/env python3
"""capture_guidellm.py — capture REAL ``guidellm==0.7.3`` output fixtures.

NOT collected by pytest (no ``test_`` prefix). A manual/CI-adjacent capture
tool, run once (or whenever the pin bumps) to regenerate
``tests/bench/adapters/fixtures/guidellm/*.json`` from the REAL installed
tool — the parser tests then run against those committed fixtures only,
never against a live guidellm process (docs/superpowers/plans/
2026-08-09-bench-phase3-oss-adapters.md: "every parser test runs from
committed fixtures only — CI has no network and no tools").

What this does:
  1. Starts a stdlib ``http.server`` (no deps beyond the standard library)
     implementing the minimal slice of the OpenAI chat-completions API
     GuideLLM's ``openai_http`` backend needs: ``GET /health``,
     ``GET /v1/models``, and a STREAMING ``POST /v1/chat/completions`` that
     emits SSE chunks with a synthetic-but-plausible TTFT + inter-token delay
     (see ``_ChatHandler``), so the captured timings are not all-zero.
  2. Runs the REAL, installed ``guidellm run`` CLI (via this adapter's own
     ``build_argv``/``run_guidellm``, so the fixture is captured through the
     exact code path the adapter uses in production) against that server.
  3. Copies the resulting ``benchmarks.json`` into
     ``fixtures/guidellm/benchmarks_happy.json``.
  4. Writes a hand-truncated ``benchmarks_empty.json`` (zero requests — the
     "no successful requests" shape) and a hand-built
     ``benchmarks_malformed.json`` / ``benchmarks_missing_percentile.json``,
     since those are edge cases a healthy capture run cannot itself produce.

Requires guidellm installed (``uv pip install --python <venv> guidellm==0.7.3``,
NOT declared in pyproject.toml — integration owns that dependency) and
network access for the tokenizer download (``guidellm`` needs a real HF
tokenizer for its synthetic dataset; ``--tokenizer kind=huggingface_auto,
model=gpt2`` was used for the actual capture on 2026-08-09).

Usage::

    /path/to/venv/bin/python tests/bench/adapters/capture_guidellm.py
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hal0.bench.adapters.guidellm import (  # noqa: E402
    GuidellmRequest,
    build_argv,
)

FIXTURES = Path(__file__).parent / "fixtures" / "guidellm"

# Plausible per-token timing (seconds) — not zero, not wall-clock-flaky
# enough to break a captured-once fixture.
_TTFT_S = 0.05
_ITL_S = 0.01


class _ChatHandler(BaseHTTPRequestHandler):
    """The minimal OpenAI-compatible surface GuideLLM's ``openai_http``
    backend needs (verified against ``guidellm/backends/openai/http.py``'s
    ``validate``/``resolve`` on the installed 0.7.3 wheel): a health check,
    a models list, and a STREAMING chat-completions endpoint that ends the
    SSE body with ``data: [DONE]`` (the handler's own end-of-stream
    sentinel — see that module's ``_resolve_streaming``)."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        if self.path in ("/health", "/v1/health"):
            self._send_json(200, {"status": "ok"})
        elif self.path.startswith("/v1/models"):
            self._send_json(
                200,
                {"object": "list", "data": [{"id": "fixture-model", "object": "model"}]},
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.path.startswith("/v1/chat/completions"):
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        n_predict = int(body.get("max_completion_tokens") or body.get("max_tokens") or 8)
        model = body.get("model") or "fixture-model"
        self._stream_chat(model, n_predict)

    def _stream_chat(self, model: str, n_predict: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        stamp = int(time.time())
        rid = f"chatcmpl-fixture-{random.randint(0, 1_000_000)}"
        prompt_tokens = random.randint(20, 60)

        time.sleep(_TTFT_S)
        words = ["The", "unified", "memory", "path", "keeps", "weights", "resident", "always"]
        for i in range(max(1, n_predict)):
            chunk = {
                "id": rid,
                "object": "chat.completion.chunk",
                "created": stamp,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"{words[i % len(words)]} "},
                        "finish_reason": None,
                    }
                ],
            }
            self._write_chunk(chunk)
            if i < n_predict - 1:
                time.sleep(_ITL_S)

        final = {
            "id": rid,
            "object": "chat.completion.chunk",
            "created": stamp,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": n_predict,
                "total_tokens": prompt_tokens + n_predict,
            },
        }
        self._write_chunk(final)
        self._write_raw(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")  # end chunked transfer

    def _write_chunk(self, payload: dict) -> None:
        self._write_raw(f"data: {json.dumps(payload)}\n\n".encode())

    def _write_raw(self, data: bytes) -> None:
        """One HTTP/1.1 chunked-transfer frame (RFC 9112 §7.1): hex length,
        CRLF, the bytes, CRLF. Every SSE event — including the ``[DONE]``
        sentinel — MUST go through this, or the client's chunk parser reads
        raw SSE bytes as a chunk-size line and errors (caught the hard way:
        the first capture run sent ``[DONE]`` unframed and every request
        errored with ``RemoteProtocolError: illegal chunk header``)."""
        self.wfile.write(f"{len(data):x}\r\n".encode())
        self.wfile.write(data)
        self.wfile.write(b"\r\n")

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _run_capture(tmp_dir: Path) -> dict:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        out_path = tmp_dir / "benchmarks.json"
        request = GuidellmRequest(
            endpoint=f"http://127.0.0.1:{port}",
            model="fixture-model",
            profile_kind="constant",
            profile_options={"rate": 4},
            output_path=str(out_path),
            max_requests=6,
            tokenizer="gpt2",
            prompt_tokens=16,
            output_tokens=8,
        )
        argv = build_argv(request)
        argv[0] = os.environ.get("GUIDELLM_BIN", argv[0])
        print("[capture] running:", " ".join(argv), file=sys.stderr)
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            print(proc.stdout, file=sys.stderr)
            print(proc.stderr, file=sys.stderr)
            raise SystemExit(f"guidellm run failed: rc={proc.returncode}")
        return json.loads(out_path.read_text())
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _write_synthetic_edge_fixtures() -> None:
    """Fixtures a healthy capture run cannot itself produce: an empty
    (zero-successful-requests) doc, a structurally malformed doc, and a doc
    whose ``metrics`` block is missing percentile fields (an older/partial
    guidellm build, or a run that errored before stats accumulated)."""
    empty = {
        "metadata": {"version": 2, "guidellm_version": "0.7.3"},
        "config": {"spec": {"backend": {}, "profile": {"kind": "constant"}}},
        "benchmarks": [
            {
                "config": {"strategy": {"type_": "constant", "rate": 4.0}},
                "requests": {"successful": [], "errored": [], "incomplete": [], "total": 0},
                "metrics": {},
            }
        ],
    }
    (FIXTURES / "benchmarks_empty.json").write_text(json.dumps(empty, indent=2) + "\n")

    malformed = {"not_a_valid_guidellm_doc": True}
    (FIXTURES / "benchmarks_malformed.json").write_text(json.dumps(malformed, indent=2) + "\n")

    missing_pct = {
        "metadata": {"version": 2, "guidellm_version": "0.7.3"},
        "config": {
            "spec": {
                "backend": {"target": "http://127.0.0.1:9", "model": "fixture-model"},
                "profile": {"kind": "synchronous"},
            }
        },
        "benchmarks": [
            {
                "config": {"strategy": {"type_": "synchronous"}},
                "requests": {
                    "successful": [
                        {
                            "request_latency": 0.5,
                            "time_to_first_token_ms": 100.0,
                            "output_tokens_per_second": 20.0,
                        }
                    ],
                    "errored": [],
                    "incomplete": [],
                    "total": 1,
                },
                # `successful`/`percentiles` sub-keys deliberately absent —
                # an older/partial guidellm build that reports only a mean.
                "metrics": {"output_tokens_per_second": {"successful": {"mean": 20.0}}},
            }
        ],
    }
    (FIXTURES / "benchmarks_missing_percentile.json").write_text(
        json.dumps(missing_pct, indent=2) + "\n"
    )


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        doc = _run_capture(Path(tmp))
    (FIXTURES / "benchmarks_happy.json").write_text(json.dumps(doc, indent=2) + "\n")
    print(f"[capture] wrote {FIXTURES / 'benchmarks_happy.json'}", file=sys.stderr)
    _write_synthetic_edge_fixtures()
    print(f"[capture] wrote edge-case fixtures under {FIXTURES}", file=sys.stderr)


if __name__ == "__main__":
    main()
