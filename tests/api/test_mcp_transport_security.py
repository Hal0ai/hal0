"""Unit tests for the MCP transport DNS-rebinding allowlist.

Regression context: FastMCP auto-locks a mount built with the default
``127.0.0.1`` host to a localhost-only ``TransportSecuritySettings``
(see ``mcp.server.fastmcp.FastMCP.__init__``). That lockdown is invisible
until a non-localhost client — another homelab node, or the Traefik vhost
— hits ``/mcp/*`` and gets a bare ``421 Invalid Host header``. The mount
now honours ``HAL0_MCP_ALLOWED_HOSTS`` / ``HAL0_MCP_ALLOWED_ORIGINS`` so
operators can widen the allowlist without losing the secure default.
"""

from __future__ import annotations

from hal0.api.mcp_mount import _mcp_transport_security


def test_default_is_localhost_only(monkeypatch):
    monkeypatch.delenv("HAL0_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    sec = _mcp_transport_security()

    assert sec.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in sec.allowed_hosts
    assert "localhost:*" in sec.allowed_hosts
    # No operator hosts leaked into the default allowlist.
    assert "10.0.1.142:8080" not in sec.allowed_hosts


def test_extra_hosts_added_and_origins_derived(monkeypatch):
    # Note the stray whitespace — it must be trimmed.
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "10.0.1.142:8080, hal0.thinmint.dev")
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    sec = _mcp_transport_security()

    assert sec.enable_dns_rebinding_protection is True
    # Localhost defaults are preserved alongside the operator additions.
    assert "127.0.0.1:*" in sec.allowed_hosts
    assert "10.0.1.142:8080" in sec.allowed_hosts
    assert "hal0.thinmint.dev" in sec.allowed_hosts
    # http+https origins are derived from each added host for browser clients.
    assert "http://10.0.1.142:8080" in sec.allowed_origins
    assert "https://10.0.1.142:8080" in sec.allowed_origins
    assert "https://hal0.thinmint.dev" in sec.allowed_origins


def test_wildcard_disables_protection(monkeypatch):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "*")
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    sec = _mcp_transport_security()

    # The fully-open posture some auth-removed LAN deployments want.
    assert sec.enable_dns_rebinding_protection is False


def test_explicit_origins_override_derivation(monkeypatch):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "10.0.1.142:8080")
    monkeypatch.setenv("HAL0_MCP_ALLOWED_ORIGINS", "https://app.example.test")

    sec = _mcp_transport_security()

    assert "https://app.example.test" in sec.allowed_origins
    # When origins are given explicitly, host-derived origins are skipped.
    assert "http://10.0.1.142:8080" not in sec.allowed_origins


def _streamable_client(monkeypatch):
    """Mount a minimal FastMCP server through the same ``transport_security``
    wiring ``mount_mcp_servers`` uses, and return a ``TestClient`` for it.

    This exercises the real path: settings are applied *before*
    ``streamable_http_app()`` builds the session manager, so the SDK's
    ``TransportSecurityMiddleware`` enforces the allowlist we computed.
    """
    from mcp.server.fastmcp import FastMCP
    from starlette.testclient import TestClient

    server = FastMCP("transport-security-probe")
    server.settings.transport_security = _mcp_transport_security()
    return TestClient(server.streamable_http_app())


def _initialize_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"},
        },
    }


def test_allowed_host_is_not_rejected_over_http(monkeypatch):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "10.0.1.142:8090")
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    with _streamable_client(monkeypatch) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Host": "10.0.1.142:8090",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=_initialize_body(),
        )

    # The whole point: a configured non-localhost Host clears the
    # DNS-rebinding gate (it would be a bare 421 without the fix).
    assert resp.status_code != 421


def test_unconfigured_host_still_rejected(monkeypatch):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "10.0.1.142:8090")
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    with _streamable_client(monkeypatch) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Host": "attacker.example:8090",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=_initialize_body(),
        )

    # Protection is still live for hosts outside the allowlist.
    assert resp.status_code == 421
