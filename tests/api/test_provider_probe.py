"""Tests for ``POST /api/providers/probe`` (check a provider before saving)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog
from fastapi.testclient import TestClient

from hal0.upstreams import registry as registry_mod

KEY = "sk-secret-123"


def _wire(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(wrapped)
        return httpx.AsyncClient(**kwargs)

    monkeypatch.setattr(registry_mod, "async_client", factory)
    return seen


def test_probe_ok_parses_models_and_rate_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _wire(
        monkeypatch,
        lambda r: httpx.Response(
            200,
            json={"data": [{"id": "claude-sonnet-5-5"}, {"id": "text-embedding-x"}]},
            headers={
                "x-ratelimit-remaining-requests": "499",
                "x-ratelimit-remaining-tokens": "1000",
                "set-cookie": "session=abc",
            },
        ),
    )
    res = client.post("/api/providers/probe", json={"catalog_id": "anthropic", "key": KEY})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["status"] == 200
    assert body["error"] is None
    assert body["rate_limit"] == {
        "limit_requests": None,
        "remaining_requests": 499,
        "limit_tokens": None,
        "remaining_tokens": 1000,
    }
    assert [m["id"] for m in body["models"]] == ["claude-sonnet-5-5", "text-embedding-x"]
    assert all(m["canonical_id"] is None for m in body["models"])
    assert body["models"][1]["type"] == "embed"
    assert "headers" not in body and "set-cookie" not in res.text
    assert str(seen[0].url) == "https://api.anthropic.com/v1/models"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


def test_probe_explicit_fields_win_over_preset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))
    res = client.post(
        "/api/providers/probe",
        json={
            "catalog_id": "anthropic",
            "url": "http://127.0.0.1:11434/v1",
            "auth_style": "header",
            "auth_header": "X-Token",
            "key": KEY,
        },
    )
    assert res.status_code == 200, res.text
    assert str(seen[0].url) == "http://127.0.0.1:11434/v1/models"
    assert seen[0].headers["x-token"] == KEY


def test_probe_401_returns_ok_false_with_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(401, text="unauthorized"))
    res = client.post("/api/providers/probe", json={"url": "https://p.example/v1", "key": KEY})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["status"] == 401
    assert body["models"] == []
    assert "401" in body["error"]


def test_probe_timeout_returns_ok_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    _wire(monkeypatch, boom)
    res = client.post("/api/providers/probe", json={"url": "https://p.example/v1"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["status"] is None
    assert "timeout" in body["error"].lower()


def test_probe_requires_url_or_catalog_id(client: TestClient) -> None:
    res = client.post("/api/providers/probe", json={"key": KEY})
    assert res.status_code == 400
    assert KEY not in res.text


def test_probe_catalog_id_without_base_url_needs_url(client: TestClient) -> None:
    res = client.post("/api/providers/probe", json={"catalog_id": "custom", "key": KEY})
    assert res.status_code == 400


def test_probe_unknown_catalog_id_is_400(client: TestClient) -> None:
    res = client.post("/api/providers/probe", json={"catalog_id": "nope", "key": KEY})
    assert res.status_code == 400
    assert KEY not in res.text


@pytest.mark.parametrize("bad", ["ftp://x/v1", "file:///etc/passwd", "http://u:p@h/v1", "nohost"])
def test_probe_rejects_unsafe_urls(client: TestClient, bad: str) -> None:
    res = client.post("/api/providers/probe", json={"url": bad, "key": KEY})
    assert res.status_code == 400
    assert KEY not in res.text


def test_probe_validation_error_does_not_echo_the_key(client: TestClient) -> None:
    res = client.post(
        "/api/providers/probe",
        json={"url": "https://p.example/v1", "key": KEY, "bogus": KEY, "auth_style": 5},
    )
    # The route parses the body by hand, so a bad body is a 400 envelope that
    # carries field names and messages only, never ``input``.
    assert res.status_code == 400
    assert KEY not in res.text


def test_probe_does_not_register_or_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    from hal0.config import paths

    _wire(monkeypatch, lambda r: httpx.Response(200, json={"data": [{"id": "m"}]}))
    reg = client.app.state.upstreams
    cache = getattr(client.app.state, "upstream_models", None)
    names_before = sorted(u.name for u in reg.list())
    cache_before = dict(cache) if cache is not None else None
    etc = paths.etc()
    watched = [etc / "upstreams.toml", etc / "api.env"]
    before = [(p.exists(), p.read_bytes() if p.exists() else b"") for p in watched]

    res = client.post("/api/providers/probe", json={"url": "https://p.example/v1", "key": KEY})
    assert res.status_code == 200

    assert sorted(u.name for u in reg.list()) == names_before
    if cache is not None:
        assert dict(cache) == cache_before
    after = [(p.exists(), p.read_bytes() if p.exists() else b"") for p in watched]
    assert after == before
    assert KEY not in "".join(os.environ.values())


def test_probe_never_returns_or_logs_the_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _wire(
        monkeypatch,
        lambda r: httpx.Response(
            401, text=f'{{"error": "invalid Bearer {KEY}"}}', headers={"x-echo": KEY}
        ),
    )
    with structlog.testing.capture_logs() as captured, caplog.at_level("DEBUG"):
        res = client.post("/api/providers/probe", json={"url": "https://p.example/v1", "key": KEY})
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert KEY not in res.text
    assert KEY not in caplog.text
    assert KEY not in repr(captured)


def test_probe_error_excerpt_is_capped(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(500, text="y" * 5000))
    res = client.post("/api/providers/probe", json={"url": "https://p.example/v1"})
    assert len(res.json()["error"]) < 400
