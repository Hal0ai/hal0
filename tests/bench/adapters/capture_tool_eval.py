#!/usr/bin/env python3
"""capture_tool_eval.py — regenerate the tool-eval-bench fixtures under
``tests/bench/adapters/fixtures/tool_eval/`` from the REAL pinned tool.

This is a developer utility, not a test — it is not collected by pytest and
never runs in CI (no network, no tool installed there). Run it by hand after
installing the pin into a scratch venv:

    uv venv --python 3.12 /tmp/tool-eval-scratch
    uv pip install --python /tmp/tool-eval-scratch/bin/python \\
        git+https://github.com/SeraphimSerapis/tool-eval-bench@v2.5.0
    /tmp/tool-eval-scratch/bin/python tests/bench/adapters/capture_tool_eval.py \\
        /tmp/tool-eval-scratch/bin/python

tool-eval-bench needs a live OpenAI-compatible ``/v1/chat/completions``
endpoint even for a fully offline/deterministic scoring run (no external
network — everything happens against localhost); this script starts a
minimal stdlib fake server (:class:`_FakeServer`) that never calls a real
model. The fake model never invokes a tool, so scenarios mostly FAIL/PARTIAL
against it — that is fine and expected: these fixtures exist to pin the
tool's real JSON SHAPE, not to record a good score.

What gets (re)written here:

* ``happy_run.json`` — a real completed run (3 scenarios) against the
  pinned v2.5.0.
* ``dev_version_run.json`` — a real completed run against a HEAD checkout
  (not the pin) purely to observe an authentic setuptools-scm dev-style
  version string (``2.5.1.devN+g<sha>``); pass a second interpreter arg
  pointing at that install to regenerate it, otherwise this file is skipped.
* ``dry_run_all_scenarios.json`` — real ``--dry-run --json --hardmode``
  output: the full scenario/category vocabulary (84 scenarios incl.
  category P), no server needed.
* ``dry_run_missing_task.json`` — real ``--dry-run --json --scenarios
  TC-999`` output (an unknown scenario id resolves to zero scenarios, not an
  error) — the adapter's "missing task" case.
* ``connection_error.stderr.jsonl`` — real stderr from a run against an
  unreachable base-url (pre-flight ``connection_failed``, exit 2, no
  ``--json-file`` written at all).

NOT regenerated here: ``malformed.json`` is hand-built (truncated JSON) —
flagged loudly because the real tool never produces malformed JSON in normal
operation; it exists purely to exercise the parser's defensive path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tool_eval"
MODEL_ID = "fake-fixture-model"


class _FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # silence access log
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/v1/models"):
            body = json.dumps(
                {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        if not self.path.startswith("/v1/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return

        content = "I am not able to help with that request."
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": None,
                    }
                ],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            final_chunk = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": MODEL_ID,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
            self.wfile.write(f"data: {json.dumps(final_chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return

        body = json.dumps(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _sanitize(doc: dict) -> dict:
    if doc.get("report_path"):
        doc["report_path"] = "runs/2026/08/2026-08-09T14-40-50.049093Z_fake0000.md"
    metadata = doc.get("metadata")
    if isinstance(metadata, dict):
        metadata["hostname"] = "fixture-host"
        metadata["platform_info"] = "Linux-fixture-x86_64-with-glibc2.44"
    return doc


def _run(python_exe: str, base_url: str, args: list[str], out_path: Path) -> None:
    argv = [
        python_exe,
        "-m",
        "tool_eval_bench",
        "run",
        "--model",
        MODEL_ID,
        "--backend",
        "llamacpp",
        "--base-url",
        base_url,
        *args,
        "--no-warmup",
        "--no-probe-engine",
        "--no-live",
        "--json",
        "--json-file",
        str(out_path),
    ]
    subprocess.run(argv, capture_output=True, text=True, timeout=60)
    doc = json.loads(out_path.read_text())
    _sanitize(doc)
    out_path.write_text(json.dumps(doc, indent=2) + "\n")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    python_exe = sys.argv[1]
    dev_python_exe = sys.argv[2] if len(sys.argv) > 2 else None

    FIXTURES.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.2)

    try:
        _run(
            python_exe,
            base_url,
            [
                "--scenarios",
                "TC-01",
                "TC-04",
                "TC-40",
                "--trials",
                "1",
                "--parallel",
                "1",
                "--timeout",
                "15",
                "--max-turns",
                "3",
            ],
            FIXTURES / "happy_run.json",
        )
        print("wrote happy_run.json")

        if dev_python_exe:
            _run(
                dev_python_exe,
                base_url,
                [
                    "--scenarios",
                    "TC-01",
                    "--trials",
                    "1",
                    "--parallel",
                    "1",
                    "--timeout",
                    "15",
                    "--max-turns",
                    "3",
                ],
                FIXTURES / "dev_version_run.json",
            )
            print("wrote dev_version_run.json")
        else:
            print("skipped dev_version_run.json (no second interpreter given)")

        dry_all = subprocess.run(
            [python_exe, "-m", "tool_eval_bench", "run", "--dry-run", "--json", "--hardmode"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        (FIXTURES / "dry_run_all_scenarios.json").write_text(dry_all.stdout)
        print("wrote dry_run_all_scenarios.json")

        dry_missing = subprocess.run(
            [
                python_exe,
                "-m",
                "tool_eval_bench",
                "run",
                "--dry-run",
                "--json",
                "--scenarios",
                "TC-999",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        (FIXTURES / "dry_run_missing_task.json").write_text(dry_missing.stdout)
        print("wrote dry_run_missing_task.json")

        conn_err = subprocess.run(
            [
                python_exe,
                "-m",
                "tool_eval_bench",
                "run",
                "--model",
                MODEL_ID,
                "--backend",
                "llamacpp",
                "--base-url",
                "http://127.0.0.1:1",
                "--scenarios",
                "TC-01",
                "--timeout",
                "3",
                "--no-warmup",
                "--no-probe-engine",
                "--no-live",
                "--json",
                "--json-file",
                "/tmp/_tool_eval_bench_conn_err.json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        (FIXTURES / "connection_error.stderr.jsonl").write_text(conn_err.stderr)
        print("wrote connection_error.stderr.jsonl")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
