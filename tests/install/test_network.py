"""Tests for WS-C network-coherence derivation (``hal0.install.network``).

Covers the invariant that closes the 4403 mismatch: whatever host the
operator reaches the dashboard on — and therefore whatever URL
``GET /api/config/urls`` advertises — is present in the derived
``HAL0_ALLOWED_ORIGINS`` that the chat-proxy WS gate checks.
"""

from __future__ import annotations

import pytest

from hal0.install import network


def test_resolve_hostname_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit choice wins over env, env wins over gethostname()."""
    monkeypatch.setenv("HAL0_HOSTNAME", "from-env")
    assert network.resolve_hostname("explicit") == "explicit"
    assert network.resolve_hostname() == "from-env"
    monkeypatch.delenv("HAL0_HOSTNAME", raising=False)
    assert network.resolve_hostname() == __import__("socket").gethostname()


def test_detect_lan_ips_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL0_LAN_IPS (space or comma separated) short-circuits detection."""
    monkeypatch.setenv("HAL0_LAN_IPS", "192.168.1.5 10.0.0.2, 127.0.0.1")
    ips = network.detect_lan_ips()
    assert ips == ["192.168.1.5", "10.0.0.2"]  # loopback filtered, order kept


def test_detect_lan_ips_override_arg_filters_junk() -> None:
    """A garbage/IPv6 entry is dropped; valid IPv4 survive."""
    assert network.detect_lan_ips("::1 not-an-ip 172.16.0.9") == ["172.16.0.9"]


def test_derive_allowed_origins_covers_advertised_url() -> None:
    """The advertised http://<lan-ip>:<port> URL is in the allowlist."""
    origins = network.derive_allowed_origins("myhost", 8080, lan_ips=["192.168.1.20"])
    # This is exactly what /api/config/urls advertises when reached on the
    # LAN IP — it MUST be allowlisted or the WS upgrade 4403s.
    assert "http://192.168.1.20:8080" in origins
    assert "http://myhost:8080" in origins
    assert "http://myhost.local:8080" in origins  # bare name gets .local
    assert "http://localhost:8080" in origins
    assert "http://127.0.0.1:8080" in origins
    assert "http://hal0.local" in origins  # historical port-less default
    assert "http://localhost:5173" in origins  # vite dev preserved


def test_derive_allowed_origins_dotted_hostname_no_local_suffix() -> None:
    """An already-qualified hostname is not given a spurious .local."""
    origins = network.derive_allowed_origins("box.lan", 8080)
    assert "http://box.lan:8080" in origins
    assert "http://box.lan.local:8080" not in origins


def test_derive_allowed_origins_public_url() -> None:
    """A reverse-proxy public_url contributes its bare scheme://host origin."""
    origins = network.derive_allowed_origins("myhost", 8080, public_url="https://hal0.example.com/")
    assert "https://hal0.example.com" in origins
    # bare host public_url is assumed https
    origins2 = network.derive_allowed_origins("h", 8080, public_url="chat.example.com")
    assert "https://chat.example.com" in origins2


def test_derive_allowed_origins_is_deduped() -> None:
    """No duplicate origins even when hostname collides with a default."""
    origins = network.derive_allowed_origins("localhost", 8080, lan_ips=["127.0.0.1"])
    assert len(origins) == len(set(origins))


def test_network_env_triple(monkeypatch: pytest.MonkeyPatch) -> None:
    """network_env returns the three coherent keys with the bind choice."""
    monkeypatch.delenv("HAL0_HOSTNAME", raising=False)
    env = network.network_env(
        bind_host="0.0.0.0",
        hostname="hal0",
        port=8080,
        lan_ips=["192.168.1.50"],
    )
    assert env["HAL0_BIND_HOST"] == "0.0.0.0"
    assert env["HAL0_HOSTNAME"] == "hal0"
    origins = env["HAL0_ALLOWED_ORIGINS"].split(",")
    assert "http://192.168.1.50:8080" in origins
    assert "http://hal0:8080" in origins


def test_network_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """All-unset call still yields a working bind host + origins."""
    for var in ("HAL0_BIND_HOST", "HAL0_PORT", "HAL0_PUBLIC_URL", "HAL0_LAN_IPS"):
        monkeypatch.delenv(var, raising=False)
    env = network.network_env(lan_ips=[])
    assert env["HAL0_BIND_HOST"] == network.DEFAULT_BIND_HOST
    assert env["HAL0_ALLOWED_ORIGINS"]  # non-empty


def test_network_env_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL0_PORT flows into the derived origin ports."""
    monkeypatch.setenv("HAL0_PORT", "9090")
    monkeypatch.delenv("HAL0_LAN_IPS", raising=False)
    env = network.network_env(hostname="hal0", lan_ips=["10.1.1.1"])
    assert "http://10.1.1.1:9090" in env["HAL0_ALLOWED_ORIGINS"].split(",")


def test_main_emits_env_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """main() prints KEY=value lines the installer appends to api.env."""
    rc = network.main()
    assert rc == 0
    out = capsys.readouterr().out
    keys = {line.split("=", 1)[0] for line in out.strip().splitlines()}
    assert keys == {"HAL0_BIND_HOST", "HAL0_HOSTNAME", "HAL0_ALLOWED_ORIGINS"}
