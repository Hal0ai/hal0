"""Golden-path + unit coverage for the Hermes/brain MCP bearer fix.

Root problem (Phase 0 §4.2): provisioned MCP client configs — the main
profile's ``mcp_servers.*`` overlay (:func:`hp._build_config_overlay`), the
``hal0-brain`` steward profile (:func:`hp._build_brain_profile_mcp_servers`),
the bootstrap-time health probe (:func:`hp._probe_mcp_server`, used by
``_phase_mcp_wire`` / the ``admin_tools_list`` smoke test), and the memory
REST shim caller (:func:`hp._mcp_memory_call`, used by
``_phase_namespace_register``, ``_phase_brain_profile_seed``, and the
``memory_roundtrip`` smoke test) — previously carried only ``X-hal0-Agent``
/ ``X-hal0-Private`` headers, while both the ``/mcp`` mount and the
``/api/memory`` prefix are ADMIN-classed (``hal0.security.exposure``). The
moment ``require_auth`` is armed, every one of those calls 401s.

The fix threads the box service identity bearer
(:func:`hal0.service_identity.service_key`) into all four header
constructions — the SAME source the CLI / in-process steward self-calls
already use (``hal0.brain.chat._self_call_headers``,
``hal0.cli._shared._auth_headers``), never a new key path. It's resolved
FRESH on every call (not baked in once at import time), so a key rotation
(``POST /api/auth/rotate``) reaches the next provision/``--repair`` pass —
matching that route's own documented contract that it does not live-update
already-issued bearer/API clients; they pick up the new key next time
they're (re)provisioned.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents import hermes_provision as hp

# ── _build_config_overlay — main hermes profile ─────────────────────────────


def _overlay_keys(**over: Any) -> dict[str, Any]:
    base = dict(
        primary={
            "model_id": "qwen3:8b",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 16384,
        },
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=[{"name": "hal0-admin", "url": "http://x/mcp", "type": "http"}],
        agent_id="hermes-agent",
        system_prompt="",
        personality_name="",
        live_resolve_enabled=True,
    )
    base.update(over)
    return dict(hp._build_config_overlay(**base))


def test_overlay_attaches_bearer_when_admin_key_present(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "overlay-admin-key")
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    keys = _overlay_keys()
    assert keys["mcp_servers.hal0-admin.headers.Authorization"] == "Bearer overlay-admin-key"
    # Identity header is additive, not replaced.
    assert keys["mcp_servers.hal0-admin.headers.X-hal0-Agent"] == "hermes-agent"


def test_overlay_omits_bearer_when_no_key_discoverable(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    keys = _overlay_keys()
    assert "mcp_servers.hal0-admin.headers.Authorization" not in keys


def test_overlay_bearer_re_resolves_on_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """Not frozen at import/module time — a rotated key reaches the NEXT
    overlay build (the re-provision / ``--repair`` pass), proving this
    composes with key rotation rather than pinning a stale bearer."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "key-before-rotation")
    before = _overlay_keys()
    assert before["mcp_servers.hal0-admin.headers.Authorization"] == "Bearer key-before-rotation"

    monkeypatch.setenv("HAL0_ADMIN_KEY", "key-after-rotation")
    after = _overlay_keys()
    assert after["mcp_servers.hal0-admin.headers.Authorization"] == "Bearer key-after-rotation"


# ── _build_brain_profile_mcp_servers — hal0-brain steward profile ───────────


def test_brain_profile_servers_carry_bearer(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    from hal0.agents.personas import BRAIN_PROFILE_AGENT_ID

    monkeypatch.setenv("HAL0_ADMIN_KEY", "brain-admin-key")
    servers = hp._build_brain_profile_mcp_servers()
    assert servers["hal0-admin"]["headers"]["Authorization"] == "Bearer brain-admin-key"
    assert servers["hal0-memory"]["headers"]["Authorization"] == "Bearer brain-admin-key"
    # Identity + private-mode headers are untouched by the bearer addition.
    assert servers["hal0-admin"]["headers"]["X-hal0-Agent"] == BRAIN_PROFILE_AGENT_ID
    assert servers["hal0-memory"]["headers"]["X-hal0-Private"] == 1


def test_brain_profile_servers_omit_bearer_when_no_key(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    servers = hp._build_brain_profile_mcp_servers()
    assert "Authorization" not in servers["hal0-admin"]["headers"]
    assert "Authorization" not in servers["hal0-memory"]["headers"]


# ── _probe_mcp_server — bootstrap's own tools/list health probe ─────────────


def test_probe_mcp_server_sends_bearer_header(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """The ``admin_tools_list`` smoke test / ``_phase_mcp_wire`` probe must
    present the same bearer a provisioned config does, or provisioning's
    own health check 401s the instant auth is armed."""
    captured: dict[str, Any] = {}

    def _fake_urlopen(req: Any, timeout: float | None = None) -> Any:
        captured["auth"] = req.get_header("Authorization")
        raise OSError("stop-after-capture — header inspection only")

    monkeypatch.setenv("HAL0_ADMIN_KEY", "probe-admin-key")
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    hp._probe_mcp_server("http://127.0.0.1:8080/mcp/admin", agent_id="hermes-agent")

    assert captured["auth"] == "Bearer probe-admin-key"


def test_probe_mcp_server_omits_bearer_when_no_key(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    captured: dict[str, Any] = {}

    def _fake_urlopen(req: Any, timeout: float | None = None) -> Any:
        captured["auth"] = req.get_header("Authorization")
        raise OSError("stop-after-capture — header inspection only")

    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    hp._probe_mcp_server("http://127.0.0.1:8080/mcp/admin", agent_id="hermes-agent")

    assert captured["auth"] is None


# ── golden path — a provisioned config's headers clear the REAL /mcp gate ───


def _initialize_body() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hal0-golden-path-test", "version": "0"},
        },
    }


def _provisioned_admin_headers() -> dict[str, str]:
    """The exact ``mcp_servers.hal0-admin.headers.*`` a real provision run
    would bake into Hermes' config.yaml, given the currently-configured box
    admin key — built by the provisioner, not hand-authored by the test.

    ``Host`` is pinned to ``127.0.0.1:8080`` (the real provisioned URL) so
    FastMCP's DNS-rebinding guard doesn't reject the TestClient's default
    ``testserver`` Host — a transport-security artifact of the test harness,
    unrelated to the auth fix under test.
    """
    keys = _overlay_keys()
    return {
        "Host": "127.0.0.1:8080",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-hal0-Agent": keys["mcp_servers.hal0-admin.headers.X-hal0-Agent"],
        "Authorization": keys["mcp_servers.hal0-admin.headers.Authorization"],
    }


def test_provisioned_mcp_config_calls_tools_list_with_auth_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Golden path: auth ON, a header set BUILT BY THE PROVISIONER (not
    hand-authored) reaches the real ``/mcp/admin/mcp`` mount and completes
    the MCP initialize -> tools/list handshake instead of 401ing."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "golden-path-admin-key")
    headers = _provisioned_admin_headers()
    assert headers["Authorization"] == "Bearer golden-path-admin-key"
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")

    init = client.post("/mcp/admin/mcp", headers=headers, json=_initialize_body())
    assert init.status_code == 200, init.text
    session_id = init.headers.get("Mcp-Session-Id") or init.headers.get("mcp-session-id")

    list_headers = dict(headers)
    if session_id:
        list_headers["Mcp-Session-Id"] = session_id
    resp = client.post(
        "/mcp/admin/mcp",
        headers=list_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert resp.status_code == 200, resp.text


def test_provisioned_mcp_config_without_bearer_401s_with_auth_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the SAME request minus ``Authorization`` is exactly
    the pre-fix 401 this lane exists to close — proves the bearer is
    load-bearing, not incidental."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "golden-path-admin-key")
    headers = _provisioned_admin_headers()
    del headers["Authorization"]
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")

    resp = client.post("/mcp/admin/mcp", headers=headers, json=_initialize_body())
    assert resp.status_code == 401


# ── _mcp_memory_call — the /api/memory/* REST shim caller ───────────────────
#
# Used by _phase_namespace_register, _phase_brain_profile_seed, and the
# memory_roundtrip smoke test — all three 401 under require_auth=1 without
# this fix, since /api/memory is ADMIN-classed exactly like /mcp.


def _capture_mcp_memory_call_headers(
    monkeypatch: pytest.MonkeyPatch, *, agent_id: str = "hermes-agent", private: bool = False
) -> Any:
    """Call the REAL :func:`hp._mcp_memory_call` with ``urlopen`` mocked to
    capture the ``Request`` it builds, then hand back that ``Request`` —
    the exact headers a live call would send, not hand-authored."""
    captured: dict[str, Any] = {}

    def _fake_urlopen(req: Any, timeout: float | None = None) -> Any:
        captured["req"] = req
        raise OSError("stop-after-capture — header inspection only")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    hp._mcp_memory_call(
        "tools/call",
        {"name": "memory_add", "arguments": {"text": "probe", "dataset": "shared"}},
        agent_id=agent_id,
        private=private,
    )
    return captured["req"]


def test_mcp_memory_call_sends_bearer_header(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "memory-admin-key")
    req = _capture_mcp_memory_call_headers(monkeypatch)
    assert req.get_header("Authorization") == "Bearer memory-admin-key"
    # Identity header is additive, not replaced.
    assert req.get_header("X-hal0-agent") == "hermes-agent"


def test_mcp_memory_call_omits_bearer_when_no_key(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    req = _capture_mcp_memory_call_headers(monkeypatch)
    assert req.get_header("Authorization") is None


def test_mcp_memory_call_bearer_re_resolves_on_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """Not frozen at call-construction time — a rotated key reaches the
    NEXT call, same composition guarantee as the /mcp sites."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "key-before-rotation")
    before = _capture_mcp_memory_call_headers(monkeypatch)
    assert before.get_header("Authorization") == "Bearer key-before-rotation"

    monkeypatch.setenv("HAL0_ADMIN_KEY", "key-after-rotation")
    after = _capture_mcp_memory_call_headers(monkeypatch)
    assert after.get_header("Authorization") == "Bearer key-after-rotation"


class _StubMemoryProvider:
    """Minimal duck-typed stand-in for the memory provider, mirroring
    ``tests/api/test_memory_rest_routes.py``'s ``StubWrapper`` — only the
    ``add`` path this test drives through ``/api/memory/add``."""

    async def add(
        self,
        *,
        text: str,
        dataset: str,
        tags: list[str],
        source: str | None,
        metadata: dict[str, Any],
        client_id: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        return {"id": document_id or "id-1", "timestamp": "2026-07-19T00:00:00Z"}


def _memory_rest_app() -> FastAPI:
    """A bare app mounting the REAL ``/api/memory`` router behind the REAL
    ``AuthEnforcementMiddleware`` (the same gate ``create_app()`` wires in)
    — enough to exercise the ADMIN-classification decision without needing
    a live Hindsight backend."""
    from hal0.api.auth import AuthEnforcementMiddleware
    from hal0.api.middleware import error_codes
    from hal0.api.routes import memory as memory_routes

    app = FastAPI()
    error_codes.install(app)
    app.add_middleware(AuthEnforcementMiddleware)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = _StubMemoryProvider()
    return app


def _mcp_memory_call_request_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The exact headers a live ``_mcp_memory_call`` would send, reshaped
    into a plain dict for replay against a real HTTP client."""
    req = _capture_mcp_memory_call_headers(monkeypatch)
    headers = {
        "Content-Type": req.get_header("Content-type"),
        "X-hal0-Agent": req.get_header("X-hal0-agent"),
    }
    bearer = req.get_header("Authorization")
    if bearer:
        headers["Authorization"] = bearer
    return {k: v for k, v in headers.items() if v is not None}


def test_mcp_memory_call_headers_clear_real_auth_gate_with_auth_on(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """Golden path: auth ON, the headers ``_mcp_memory_call`` actually
    sends reach the real ``/api/memory/add`` route (behind the real
    ADMIN-classification middleware) and succeed instead of 401ing."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "memory-golden-path-key")
    headers = _mcp_memory_call_request_headers(monkeypatch)
    assert headers["Authorization"] == "Bearer memory-golden-path-key"
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")

    with TestClient(_memory_rest_app()) as client:
        resp = client.post(
            "/api/memory/add",
            headers=headers,
            json={"text": "probe", "dataset": "shared"},
        )
    assert resp.status_code == 200, resp.text


def test_mcp_memory_call_headers_without_bearer_401s_with_auth_on(
    monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """Negative control: the SAME request minus ``Authorization`` is
    exactly the pre-fix 401 this half of the lane closes."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "memory-golden-path-key")
    headers = _mcp_memory_call_request_headers(monkeypatch)
    del headers["Authorization"]
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")

    with TestClient(_memory_rest_app()) as client:
        resp = client.post(
            "/api/memory/add",
            headers=headers,
            json={"text": "probe", "dataset": "shared"},
        )
    assert resp.status_code == 401
