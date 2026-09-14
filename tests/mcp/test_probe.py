"""Tests for :mod:`hal0.mcp.probe` — the generic user-server MCP prober.

Stubs ``urlopen`` rather than hitting the network; each fake response is
the JSON-RPC frame a real streamable-http MCP server would return for
``initialize``/``tools/list``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError

import pytest

from hal0.mcp import installed, probe


def _record(**overrides: object) -> installed.InstalledServer:
    defaults: dict[str, object] = {
        "id": "github",
        "name": "github",
        "spec": "https://github.example.com/manifest.json",
        "transport": "streamable-http",
        "url": "https://github.example.com/mcp",
    }
    defaults.update(overrides)
    return installed.InstalledServer(**defaults)


class _FakeResponse:
    def __init__(self, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_probe_returns_tool_names(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        _FakeResponse({"jsonrpc": "2.0", "id": "1", "result": {}}),
        _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "2",
                "result": {"tools": [{"name": "search_repositories"}, {"name": "get_file"}]},
            }
        ),
    ]

    def fake_urlopen(req: Any, timeout: float) -> _FakeResponse:
        return responses.pop(0)

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)
    result = probe.probe_installed_server_sync(_record())
    assert result["ok"] is True
    assert result["tools"] == ["search_repositories", "get_file"]
    assert result["error"] is None


def test_probe_reports_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float) -> None:
        raise URLError("connection refused")

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)
    result = probe.probe_installed_server_sync(_record())
    assert result["ok"] is False
    assert "connection refused" in result["error"]
    assert result["tools"] == []


def test_probe_reports_jsonrpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        _FakeResponse({"jsonrpc": "2.0", "id": "1", "result": {}}),
        _FakeResponse({"jsonrpc": "2.0", "id": "2", "error": {"message": "unauthorized"}}),
    ]

    def fake_urlopen(req: Any, timeout: float) -> _FakeResponse:
        return responses.pop(0)

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)
    result = probe.probe_installed_server_sync(_record())
    assert result["ok"] is False
    assert result["error"] == "unauthorized"


def test_probe_rejects_stdio_transport() -> None:
    record = _record(transport="stdio", url="", spec="npm:foo")
    result = probe.probe_installed_server_sync(record)
    assert result["ok"] is False
    assert "stdio" in result["error"]


def test_probe_rejects_missing_url() -> None:
    record = _record(url="")
    result = probe.probe_installed_server_sync(record)
    assert result["ok"] is False
    assert "url" in result["error"]


def test_build_headers_resolves_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "sekret")
    record = _record(
        env={"MCP_WORKSPACE": "/tmp/ws"},
        secrets={"Authorization": "GITHUB_MCP_TOKEN"},
    )
    headers = probe.build_headers(record)
    assert headers["Authorization"] == "sekret"
    assert headers["MCP_WORKSPACE"] == "/tmp/ws"
    assert headers["X-hal0-Agent"] == "hermes"


def test_build_headers_omits_unresolved_secret() -> None:
    record = _record(secrets={"Authorization": "NOT_SET_ANYWHERE"})
    headers = probe.build_headers(record)
    assert "Authorization" not in headers
