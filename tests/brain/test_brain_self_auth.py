"""O17: the steward authenticates its internal self-HTTP calls.

On an auth-enabled box (the default on non-loopback binds) the box's own /v1
and /api/* surfaces reject an anonymous request with ``auth.required``. The
steward's self-calls must therefore carry a bearer: forward the caller's
inbound Authorization when present, else present the box service identity
(env → /etc/hal0/api.env) at the least-privilege tier for the surface.

These tests drive the header/token helpers, the `/v1` completion closure, and
the platform self-API hop — no live network. See docs/rework/r4-stage-validation.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest

from hal0 import service_identity
from hal0.brain import chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    # Point api.env discovery at a non-existent dir so the box's real keys
    # (if any) never leak into these assertions.
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "etc", lambda: Path("/nonexistent-etc-hal0"))


def _request(headers: dict[str, str] | None = None, **state: Any) -> Any:
    base = {
        "self_api_base_url": "http://testserver",
        "hal0_config": Hal0Config(brain_chat=BrainChatConfig()),
    }
    base.update(state)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(**base)), headers=headers or {}
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── service_identity key discovery ──────────────────────────────────────────


def test_service_key_prefers_requested_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adm")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli")
    assert service_identity.service_key(prefer="admin") == "adm"
    assert service_identity.service_key(prefer="client") == "cli"


def test_service_key_falls_back_to_other_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the admin key exists — a client-preferring surface still authenticates.
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adm")
    assert service_identity.service_key(prefer="client") == "adm"


def test_service_key_reads_api_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "api.env").write_text("HAL0_ADMIN_KEY=file-adm\nHAL0_CLIENT_KEY=file-cli\n")
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "etc", lambda: etc)
    assert service_identity.service_key(prefer="client") == "file-cli"
    assert service_identity.service_auth_headers(prefer="admin") == {
        "Authorization": "Bearer file-adm"
    }


def test_service_key_empty_when_nothing_discoverable() -> None:
    assert service_identity.service_key() is None
    assert service_identity.service_auth_headers() == {}


# ── forward-vs-service precedence ───────────────────────────────────────────


def test_self_call_headers_forwards_inbound_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "box-adm")
    req = _request(headers={"Authorization": "Bearer caller-tok"})
    # Caller's bearer wins over the box identity for every tier.
    assert bc._self_call_headers(req, prefer="client") == {"Authorization": "Bearer caller-tok"}
    assert bc._self_call_bearer(req, prefer="admin") == "caller-tok"


def test_self_call_headers_uses_service_identity_when_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "box-adm")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "box-cli")
    req = _request(headers={})  # e.g. a cookie-authed dashboard, no Authorization
    assert bc._self_call_headers(req, prefer="client") == {"Authorization": "Bearer box-cli"}
    assert bc._self_call_headers(req, prefer="admin") == {"Authorization": "Bearer box-adm"}
    assert bc._self_call_bearer(req, prefer="admin") == "box-adm"


def test_self_call_headers_empty_on_keyless_box() -> None:
    # Loopback dev-open box: no keys, no inbound bearer -> no header attached.
    req = _request(headers={})
    assert bc._self_call_headers(req, prefer="client") == {}
    assert bc._self_call_bearer(req, prefer="admin") is None


# ── /v1 completion closure attaches the CLIENT identity ─────────────────────


class _CapturingClient:
    captured: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> _CapturingClient:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def post(self, url: str, json: Any = None, headers: Any = None) -> Any:
        _CapturingClient.captured.append({"url": url, "headers": headers})
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )


def test_primary_completion_attaches_client_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "box-adm")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "box-cli")
    _CapturingClient.captured = []
    monkeypatch.setattr(bc.httpx, "AsyncClient", _CapturingClient)
    req = _request(headers={}, board_chat_llm=None)
    llm = bc._resolve_llm(req)  # returns the production closure (no injected llm)
    _run(llm({"model": "hal0/brain", "messages": []}))
    assert len(_CapturingClient.captured) == 1
    call = _CapturingClient.captured[0]
    assert call["url"].endswith("/v1/chat/completions")
    # Least-privilege CLIENT tier for the inference call, not admin.
    assert call["headers"] == {"Authorization": "Bearer box-cli"}


def test_primary_completion_forwards_caller_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "box-cli")
    _CapturingClient.captured = []
    monkeypatch.setattr(bc.httpx, "AsyncClient", _CapturingClient)
    req = _request(headers={"Authorization": "Bearer caller-tok"}, board_chat_llm=None)
    llm = bc._resolve_llm(req)
    _run(llm({"model": "hal0/brain", "messages": []}))
    assert _CapturingClient.captured[0]["headers"] == {"Authorization": "Bearer caller-tok"}


# ── platform self-API hop attaches the ADMIN identity ───────────────────────


def _platform_capture_request(headers: dict[str, str], captured: list[str | None]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"slots": []})

    client = httpx.AsyncClient(base_url="http://testserver", transport=httpx.MockTransport(handler))
    return _request(headers=headers, platform_http=client)


def test_platform_tool_attaches_admin_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    # The platform surface spans admin ops (get_slot, slot mutations) so it
    # presents the admin identity for the whole surface.
    monkeypatch.setenv("HAL0_ADMIN_KEY", "box-adm")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "box-cli")
    captured: list[str | None] = []
    req = _platform_capture_request({}, captured)
    _run(
        bc._dispatch_platform_tool(
            req, "list_slots", {}, method="GET", path="/api/slots", mutating=False
        )
    )
    assert captured == ["Bearer box-adm"]


def test_platform_tool_forwards_caller_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "box-adm")
    captured: list[str | None] = []
    req = _platform_capture_request({"Authorization": "Bearer caller-tok"}, captured)
    _run(
        bc._dispatch_platform_tool(
            req, "list_slots", {}, method="GET", path="/api/slots", mutating=False
        )
    )
    assert captured == ["Bearer caller-tok"]
