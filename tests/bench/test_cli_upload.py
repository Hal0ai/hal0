"""CLI wiring for `hal0 bench upload` (and `bundle --upload`).

Drives main(argv) directly like the other verb tests. The network is never
touched: urllib.request.urlopen is monkeypatched at the module the CLI imports
it through, so these assert the REQUEST the CLI would send and how it reports
each response, not the worker's behaviour (which is covered by the worker's own
suite in hal0-web).
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from hal0.bench.cli import main
from hal0.bench.store import Store

TOKEN_ENV = "HAL0_BENCH_TOKEN"


def _seed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    store = Store()
    store.append_record(
        {
            "run_id": "2026-08-01T00:00:00Z-aaa111",
            "cell_key": "sha256:k1",
            "suite": "roster",
            "trigger": "manual",
            "identity": {
                "model": {"id": "qwen3-30b"},
                "lane": "rocm",
                "workload": {"kind": "tg", "depth": 2048},
            },
            "host": {"name": "box", "hal0_version": "1.0"},
            "outcome": "ok",
            "summary": {"decode_ts_med": 42.0},
            "schema": 2,
        }
    )


def _bundle(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out_file = tmp_path / "b.hal0bench.tar.gz"
    assert main(["bundle", "--runs", "2026-08-01T00:00:00Z-aaa111", "-o", str(out_file)]) == 0
    return out_file


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _capture(monkeypatch, payload: dict) -> dict:
    """Patch urlopen to record the outgoing request and return `payload`."""
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        seen["body"] = req.data
        seen["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr("hal0.bench.cli.urllib.request.urlopen", fake_urlopen)
    return seen


def test_upload_posts_bundle_with_bearer_token(tmp_path, monkeypatch, capsys):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")
    seen = _capture(monkeypatch, {"bundle_id": "sha256:abc", "records": 1})

    rc = main(["upload", str(bundle)])

    assert rc == 0
    assert seen["url"] == "https://api.hal0.dev/v1/bundles"
    assert seen["method"] == "POST"
    assert seen["headers"]["authorization"] == "Bearer s3cret"
    assert seen["headers"]["content-type"] == "application/gzip"
    assert seen["body"] == bundle.read_bytes()
    assert "uploaded sha256:abc (1 record(s))" in capsys.readouterr().out


def test_upload_honours_api_url_override(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")
    seen = _capture(monkeypatch, {"bundle_id": "sha256:abc", "records": 1})

    assert main(["upload", str(bundle), "--api-url", "http://localhost:8787/"]) == 0
    # Trailing slash on the base must not produce a doubled separator.
    assert seen["url"] == "http://localhost:8787/v1/bundles"


def test_upload_reports_republish_distinctly(tmp_path, monkeypatch, capsys):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")
    _capture(monkeypatch, {"bundle_id": "sha256:abc", "records": 3, "republished": True})

    assert main(["upload", str(bundle)]) == 0
    assert "republished sha256:abc (3 record(s) restored)" in capsys.readouterr().out


def test_upload_without_token_fails_before_any_request(tmp_path, monkeypatch, capsys):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.delenv(TOKEN_ENV, raising=False)

    def explode(*_a, **_kw):  # pragma: no cover - must never run
        raise AssertionError("upload attempted a request without a token")

    monkeypatch.setattr("hal0.bench.cli.urllib.request.urlopen", explode)

    assert main(["upload", str(bundle)]) == 1
    assert TOKEN_ENV in capsys.readouterr().err


def test_upload_missing_file_is_a_clean_error(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")

    assert main(["upload", str(tmp_path / "nope.tar.gz")]) == 1
    assert "no such bundle" in capsys.readouterr().err


def test_upload_surfaces_worker_validation_errors(tmp_path, monkeypatch, capsys):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url,
            422,
            "Unprocessable Entity",
            {},
            io.BytesIO(
                json.dumps({"errors": ["records.jsonl line 2: bad cell_key format"]}).encode()
            ),
        )

    monkeypatch.setattr("hal0.bench.cli.urllib.request.urlopen", fake_urlopen)

    assert main(["upload", str(bundle)]) == 1
    err = capsys.readouterr().err
    assert "HTTP 422" in err
    # The rejection names the offending line — that detail is the whole point.
    assert "bad cell_key format" in err


def test_upload_401_hints_at_the_token(tmp_path, monkeypatch, capsys):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "stale")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"errors":["bad token"]}')
        )

    monkeypatch.setattr("hal0.bench.cli.urllib.request.urlopen", fake_urlopen)

    assert main(["upload", str(bundle)]) == 1
    assert TOKEN_ENV in capsys.readouterr().err


def test_upload_unreachable_api_is_a_clean_error(tmp_path, monkeypatch, capsys):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr("hal0.bench.cli.urllib.request.urlopen", fake_urlopen)

    assert main(["upload", str(bundle)]) == 1
    assert "could not reach" in capsys.readouterr().err


def test_bundle_upload_flag_writes_then_uploads(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")
    seen = _capture(monkeypatch, {"bundle_id": "sha256:abc", "records": 1})
    out_file = tmp_path / "b.hal0bench.tar.gz"

    rc = main(["bundle", "--runs", "2026-08-01T00:00:00Z-aaa111", "-o", str(out_file), "--upload"])

    assert rc == 0
    assert out_file.exists()
    assert seen["body"] == out_file.read_bytes()
    out = capsys.readouterr().out
    assert "wrote" in out and "uploaded sha256:abc" in out


def test_bundle_without_upload_flag_makes_no_request(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, "s3cret")

    def explode(*_a, **_kw):  # pragma: no cover - must never run
        raise AssertionError("bundle uploaded without --upload")

    monkeypatch.setattr("hal0.bench.cli.urllib.request.urlopen", explode)

    out_file = tmp_path / "b.hal0bench.tar.gz"
    assert main(["bundle", "--runs", "2026-08-01T00:00:00Z-aaa111", "-o", str(out_file)]) == 0


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_token_is_treated_as_unset(tmp_path, monkeypatch, capsys, value):
    bundle = _bundle(tmp_path, monkeypatch)
    monkeypatch.setenv(TOKEN_ENV, value)

    def explode(*_a, **_kw):  # pragma: no cover - must never run
        raise AssertionError("upload attempted a request with a blank token")

    monkeypatch.setattr("hal0.bench.cli.urllib.request.urlopen", explode)

    assert main(["upload", str(bundle)]) == 1
    assert TOKEN_ENV in capsys.readouterr().err
