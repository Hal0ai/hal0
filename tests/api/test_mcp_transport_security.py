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

import pytest

from hal0.api.mcp_mount import _mcp_transport_security


def test_default_is_localhost_only(monkeypatch):
    monkeypatch.delenv("HAL0_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    sec = _mcp_transport_security()

    assert sec.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in sec.allowed_hosts
    assert "localhost:*" in sec.allowed_hosts
    # No operator hosts leaked into the default allowlist.
    assert "192.0.2.142:8080" not in sec.allowed_hosts


def test_extra_hosts_added_and_origins_derived(monkeypatch):
    # Note the stray whitespace — it must be trimmed.
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "192.0.2.142:8080, hal0.example.com")
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    sec = _mcp_transport_security()

    assert sec.enable_dns_rebinding_protection is True
    # Localhost defaults are preserved alongside the operator additions.
    assert "127.0.0.1:*" in sec.allowed_hosts
    # Exact-element checks, written as subsets so they read as list membership
    # rather than URL substring matching.
    assert {"192.0.2.142:8080", "hal0.example.com"} <= set(sec.allowed_hosts)
    # http+https origins are derived from each added host for browser clients.
    assert {
        "http://192.0.2.142:8080",
        "https://192.0.2.142:8080",
        "https://hal0.example.com",
    } <= set(sec.allowed_origins)


def test_wildcard_disables_protection(monkeypatch):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "*")
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    sec = _mcp_transport_security()

    # The fully-open posture some auth-removed LAN deployments want.
    assert sec.enable_dns_rebinding_protection is False


def test_explicit_origins_override_derivation(monkeypatch):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "192.0.2.142:8080")
    monkeypatch.setenv("HAL0_MCP_ALLOWED_ORIGINS", "https://app.example.test")

    sec = _mcp_transport_security()

    assert "https://app.example.test" in sec.allowed_origins
    # When origins are given explicitly, host-derived origins are skipped.
    assert "http://192.0.2.142:8080" not in sec.allowed_origins


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
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "192.0.2.142:8090")
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)

    with _streamable_client(monkeypatch) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Host": "192.0.2.142:8090",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=_initialize_body(),
        )

    # The whole point: a configured non-localhost Host clears the
    # DNS-rebinding gate (it would be a bare 421 without the fix).
    assert resp.status_code != 421


def test_unconfigured_host_still_rejected(monkeypatch):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "192.0.2.142:8090")
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


# -- the box's own names are allowed without any setup (ADR-0043) --------------------------


def _post_initialize(client, host: str, **extra_headers: str):
    return client.post(
        "/mcp",
        headers={
            "Host": host,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **extra_headers,
        },
        json=_initialize_body(),
    )


@pytest.fixture
def box(monkeypatch):
    """A box with one LAN address, a hostname and no ``[network].public_url``."""
    from hal0.api import mcp_mount

    monkeypatch.delenv("HAL0_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("HAL0_MCP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("HAL0_PORT", raising=False)
    monkeypatch.setenv("HAL0_HOSTNAME", "strix")
    monkeypatch.setattr("hal0.config.network.detect_lan_ips", lambda: ["10.0.0.5"])
    monkeypatch.setattr(mcp_mount, "_box_public_authority", lambda: None)


def test_a_remote_client_reaching_the_lan_address_passes(monkeypatch, box):
    """A bridge client on another machine sends ``Host: <lan ip>:<api port>``."""
    with _streamable_client(monkeypatch) as client:
        assert _post_initialize(client, "10.0.0.5:8080").status_code != 421
        assert _post_initialize(client, "10.0.0.5").status_code != 421


def test_the_hostname_and_mdns_name_pass(monkeypatch, box):
    with _streamable_client(monkeypatch) as client:
        for host in ("strix", "strix:8080", "strix.local", "strix.local:8080"):
            assert _post_initialize(client, host).status_code != 421, host


def test_an_unrelated_host_still_fails(monkeypatch, box):
    with _streamable_client(monkeypatch) as client:
        assert _post_initialize(client, "10.0.0.6:8080").status_code == 421
        assert _post_initialize(client, "attacker.example:8080").status_code == 421


def test_the_api_port_follows_hal0_port(monkeypatch, box):
    monkeypatch.setenv("HAL0_PORT", "9090")
    sec = _mcp_transport_security()
    assert {"10.0.0.5:9090", "strix:9090", "strix.local:9090"} <= set(sec.allowed_hosts)
    assert "10.0.0.5:8080" not in sec.allowed_hosts


def test_box_origins_are_allowed_too(box):
    sec = _mcp_transport_security()
    assert {
        "http://10.0.0.5",
        "http://10.0.0.5:8080",
        "http://strix.local:8080",
        "http://strix:8080",
    } <= set(sec.allowed_origins)
    assert "http://attacker.example:8080" not in sec.allowed_origins


def test_the_public_url_host_is_allowed(monkeypatch, box):
    from hal0.api import mcp_mount

    monkeypatch.setattr(
        mcp_mount, "_box_public_authority", lambda: ("https", "hal0.example.com", None)
    )
    sec = _mcp_transport_security()
    assert {"hal0.example.com", "hal0.example.com:8080"} <= set(sec.allowed_hosts)
    assert {"https://hal0.example.com"} <= set(sec.allowed_origins)


def test_the_public_url_port_is_kept(monkeypatch, box):
    from hal0.api import mcp_mount

    monkeypatch.setattr(
        mcp_mount, "_box_public_authority", lambda: ("https", "hal0.example.com", 8443)
    )
    sec = _mcp_transport_security()
    assert {"hal0.example.com:8443"} <= set(sec.allowed_hosts)
    assert {"https://hal0.example.com:8443"} <= set(sec.allowed_origins)


def test_the_public_url_comes_from_hal0_toml(monkeypatch, tmp_path):
    """``[network].public_url`` is read from config; a broken config reads as unset."""
    from hal0.api import mcp_mount
    from hal0.config.schema import Hal0Config, NetworkConfig

    cfg = Hal0Config(network=NetworkConfig(public_url="https://hal0.example.com:8443/base"))
    monkeypatch.setattr("hal0.config.loader.load_hal0_config", lambda *a, **k: cfg)
    assert mcp_mount._box_public_authority() == ("https", "hal0.example.com", 8443)

    def _broken(*_a, **_k):
        raise PermissionError("hal0.toml")

    monkeypatch.setattr("hal0.config.loader.load_hal0_config", _broken)
    assert mcp_mount._box_public_authority() is None


def test_operator_hosts_are_added_to_the_box_names(monkeypatch, box):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "edge.example:443")
    sec = _mcp_transport_security()
    assert {"edge.example:443", "10.0.0.5:8080", "127.0.0.1:*"} <= set(sec.allowed_hosts)


def test_wildcard_still_disables_protection_with_box_names(monkeypatch, box):
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "*")
    assert _mcp_transport_security().enable_dns_rebinding_protection is False


def test_a_failure_collecting_box_names_keeps_the_configured_list(monkeypatch, box):
    """The box's own names are a convenience: losing them must not fail every mount."""
    import structlog

    def broken() -> list[str]:
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr("hal0.config.network.detect_lan_ips", broken)
    monkeypatch.setenv("HAL0_MCP_ALLOWED_HOSTS", "192.0.2.142:8080")
    with structlog.testing.capture_logs() as logs:
        sec = _mcp_transport_security()
    assert sec.enable_dns_rebinding_protection is True
    # The localhost floor and the operator's additions survive; no box names were added.
    assert {"127.0.0.1:*", "localhost:*", "192.0.2.142:8080"} <= set(sec.allowed_hosts)
    assert "strix:8080" not in sec.allowed_hosts and "10.0.0.5:8080" not in sec.allowed_hosts
    (entry,) = [e for e in logs if e["event"] == "hal0.mcp_mount.box_names_unavailable"]
    assert entry["log_level"] == "warning" and "psutil exploded" in entry["error"]


def test_a_failing_box_name_lookup_does_not_stop_the_mounts(monkeypatch, box):
    """``mount_mcp_servers`` still builds every server when the names cannot be collected."""
    from fastapi import FastAPI

    from hal0.api import mcp_mount
    from hal0.config.schema import BridgeConfig, Hal0Config
    from hal0.mcp import admin
    from hal0.mcp.approval_queue import ApprovalQueue

    def broken() -> list[str]:
        raise OSError("no network")

    monkeypatch.setattr("hal0.config.network.detect_lan_ips", broken)
    monkeypatch.setattr(admin, "install_admin_route_map", lambda _app: None)
    app = FastAPI()
    app.state.hal0_config = Hal0Config(bridge=BridgeConfig(enabled=True))
    queue = ApprovalQueue()
    app.state.approval_queue = queue
    mcp_mount.mount_mcp_servers(app, approval_queue=queue)
    assert {"hal0-admin", "hal0-agents"} <= set(app.state.mcp_servers)
