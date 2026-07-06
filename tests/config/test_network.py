"""Tests for ``hal0.config.network`` — the #1099 WS-C bind-host/allowed-
origins/hostname derivation the systemd unit, ``hal0 serve``, ``GET
/api/config/urls``, and the WS chat-proxy origin gate all share.
"""

from __future__ import annotations

import pytest

from hal0.config import network


def test_bind_host_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)
    assert network.bind_host() == "127.0.0.1"


def test_bind_host_honours_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    assert network.bind_host() == "0.0.0.0"


def test_bind_host_strips_and_ignores_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_BIND_HOST", "   ")
    assert network.bind_host() == "127.0.0.1"


def test_hostname_defaults_to_system_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_HOSTNAME", raising=False)
    monkeypatch.setattr(network.socket, "gethostname", lambda: "some-box.local")
    assert network.hostname() == "some-box"


def test_hostname_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOSTNAME", "mybox.local")
    assert network.hostname() == "mybox"
    monkeypatch.setenv("HAL0_HOSTNAME", "mybox")
    assert network.hostname() == "mybox"


def test_hostname_falls_back_to_hal0_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_HOSTNAME", raising=False)
    monkeypatch.setattr(network.socket, "gethostname", lambda: "")
    assert network.hostname() == "hal0"


def test_derive_allowed_origins_loopback_bind_has_no_lan_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loopback-bound API never leaks LAN IPs into the allowlist."""
    monkeypatch.setenv("HAL0_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("HAL0_HOSTNAME", "hal0")
    monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.5"])
    origins = network.derive_allowed_origins(port=8080)
    assert origins == (
        "http://127.0.0.1:8080",
        "http://hal0.local:8080",
        "http://localhost:8080",
    )


def test_derive_allowed_origins_wildcard_bind_adds_lan_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.0.0.0 (the installer default) pulls in detected LAN IPs."""
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("HAL0_HOSTNAME", "hal0")
    monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.5", "10.0.0.6"])
    origins = network.derive_allowed_origins(port=8080)
    assert "http://10.0.0.5:8080" in origins
    assert "http://10.0.0.6:8080" in origins
    # The wildcard bind host itself is never added as a literal origin.
    assert "http://0.0.0.0:8080" not in origins


def test_derive_allowed_origins_concrete_lan_bind_adds_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concrete LAN bind address (not a wildcard) is added as its own origin."""
    monkeypatch.setenv("HAL0_BIND_HOST", "192.0.2.50")
    monkeypatch.setenv("HAL0_HOSTNAME", "hal0")
    monkeypatch.setattr(network, "detect_lan_ips", lambda: [])
    origins = network.derive_allowed_origins(port=8080)
    assert "http://192.0.2.50:8080" in origins


def test_derive_allowed_origins_uses_hal0_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("HAL0_HOSTNAME", "hal0")
    monkeypatch.setenv("HAL0_PORT", "9090")
    origins = network.derive_allowed_origins()
    assert "http://127.0.0.1:9090" in origins
    assert "http://hal0.local:9090" in origins


def test_derive_allowed_origins_explicit_port_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_PORT", "9090")
    origins = network.derive_allowed_origins(port=1234)
    assert any(o.endswith(":1234") for o in origins)
    assert not any(o.endswith(":9090") for o in origins)


def test_detect_lan_ips_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Best-effort — a fully broken network stack yields an empty list, not a crash."""
    import psutil

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("no network")

    monkeypatch.setattr(psutil, "net_if_addrs", _boom)
    monkeypatch.setattr(network.socket, "socket", _boom)
    assert network.detect_lan_ips() == []
