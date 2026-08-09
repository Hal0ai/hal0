"""Upload client against a real local HTTP server (stdlib http.server in a
thread) — no mocking of urllib internals, so header/body handling is tested
for real. Server-side contract exercised: 200 ok, 401 unauthorized, 422 with
machine-readable errors[]."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from hal0.bench.upload import UploadError, upload_bundle


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict]] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Handler.seen.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "len": len(body)}
        )
        auth = self.headers.get("Authorization", "")
        if auth == "Bearer redirect-token":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if auth != "Bearer good-token":
            code, payload = 401, {"error": "unauthorized"}
        elif len(body) == 0:
            code, payload = 422, {"errors": ["empty body"]}
        else:
            code, payload = 200, {"bundle_id": "sha256:x", "url": "https://hal0.dev/benchmarks"}
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # keep pytest output clean
        pass


@pytest.fixture
def server():
    _Handler.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def bundle_file(tmp_path):
    p = tmp_path / "b.hal0bench.tar.gz"
    p.write_bytes(b"\x1f\x8b-fake-gzip-payload")
    return p


def test_upload_success_posts_bundle_with_bearer(server, bundle_file):
    resp = upload_bundle(bundle_file, api=server, token="good-token")
    assert resp["url"] == "https://hal0.dev/benchmarks"
    req = _Handler.seen[0]
    assert req["path"] == "/v1/bundles"
    assert req["auth"] == "Bearer good-token"
    assert req["len"] == bundle_file.stat().st_size


def test_upload_401_raises_with_status(server, bundle_file):
    with pytest.raises(UploadError) as ei:
        upload_bundle(bundle_file, api=server, token="bad-token")
    assert ei.value.status == 401


def test_upload_422_surfaces_server_errors(server, tmp_path):
    empty = tmp_path / "empty.tar.gz"
    empty.write_bytes(b"")
    with pytest.raises(UploadError, match="empty body") as ei:
        upload_bundle(empty, api=server, token="good-token")
    assert ei.value.status == 422


def test_token_from_env(server, bundle_file, monkeypatch):
    monkeypatch.setenv("HAL0_BENCH_TOKEN", "good-token")
    resp = upload_bundle(bundle_file, api=server)
    assert resp["bundle_id"] == "sha256:x"


def test_missing_token_is_a_clean_error(bundle_file, monkeypatch):
    monkeypatch.delenv("HAL0_BENCH_TOKEN", raising=False)
    with pytest.raises(UploadError, match="HAL0_BENCH_TOKEN"):
        upload_bundle(bundle_file, api="http://127.0.0.1:1")


def test_upload_does_not_follow_redirect_with_bearer_token(server, bundle_file):
    with pytest.raises(UploadError) as ei:
        upload_bundle(bundle_file, api=server, token="redirect-token")
    assert ei.value.status == 302
