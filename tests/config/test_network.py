"""Tests for hal0.config.network — the HAL0_BIND_HOST-derived network shape.

Covers bind_host()'s env precedence/defaulting, hostname()'s HAL0_HOSTNAME
override + gethostname() fallback + .local-suffix stripping,
detect_lan_ips()'s psutil-then-UDP-trick fallback chain, and
derive_allowed_origins()'s loopback/wildcard/concrete-bind_host branches
(#1099, installer-setup WS-C).

Fully hermetic: an autouse fixture clears HAL0_BIND_HOST/HAL0_HOSTNAME/
HAL0_PORT and pins socket.gethostname() to a fixed value, so nothing here
depends on the host running the suite.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import psutil
import pytest

from hal0.config import network

_FIXED_HOSTNAME = "cibox"


@pytest.fixture(autouse=True)
def _hermetic_network_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the module's env vars and pin gethostname() for every test."""
    for var in ("HAL0_BIND_HOST", "HAL0_HOSTNAME", "HAL0_PORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: _FIXED_HOSTNAME)


# ── module surface ───────────────────────────────────────────────────────────


def test_all_exports_are_callable() -> None:
    assert set(network.__all__) == {
        "bind_host",
        "derive_allowed_origins",
        "detect_lan_ips",
        "hostname",
    }
    for name in network.__all__:
        assert callable(getattr(network, name)), f"network.{name} is not callable"


# ── bind_host() ───────────────────────────────────────────────────────────────


class TestBindHost:
    def test_default_when_unset(self) -> None:
        assert network.bind_host() == "127.0.0.1"

    def test_explicit_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
        assert network.bind_host() == "0.0.0.0"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "  10.0.0.5  ")
        assert network.bind_host() == "10.0.0.5"

    def test_empty_string_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "")
        assert network.bind_host() == "127.0.0.1"

    def test_whitespace_only_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "   ")
        assert network.bind_host() == "127.0.0.1"


# ── hostname() ────────────────────────────────────────────────────────────────


class TestHostname:
    def test_default_uses_gethostname(self) -> None:
        assert network.hostname() == _FIXED_HOSTNAME

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_HOSTNAME", "myhost")
        assert network.hostname() == "myhost"

    def test_env_local_suffix_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_HOSTNAME", "myhost.local")
        assert network.hostname() == "myhost"

    def test_env_trailing_dots_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_HOSTNAME", "myhost.")
        assert network.hostname() == "myhost"

    def test_gethostname_local_suffix_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "gethostname", lambda: "box.local")
        assert network.hostname() == "box"

    def test_dots_only_falls_back_to_hal0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_HOSTNAME", ".")
        assert network.hostname() == "hal0"

    def test_env_whitespace_only_falls_back_to_gethostname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAL0_HOSTNAME", "   ")
        assert network.hostname() == _FIXED_HOSTNAME


# ── detect_lan_ips() ──────────────────────────────────────────────────────────


class TestDetectLanIps:
    def test_psutil_finds_non_loopback_ipv4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            psutil,
            "net_if_addrs",
            lambda: {
                "eth0": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.5")],
                "lo": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
                "eth1": [SimpleNamespace(family=socket.AF_INET6, address="fe80::1")],
            },
        )
        assert network.detect_lan_ips() == ["10.0.0.5"]

    def test_dedupes_and_sorts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            psutil,
            "net_if_addrs",
            lambda: {
                "eth0": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.5")],
                "eth1": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.5")],
                "eth2": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.1")],
            },
        )
        assert network.detect_lan_ips() == ["10.0.0.1", "10.0.0.5"]

    def test_falls_back_to_udp_trick_when_psutil_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            psutil,
            "net_if_addrs",
            lambda: {"lo": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")]},
        )

        class _FakeSocket:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def __enter__(self) -> _FakeSocket:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

            def connect(self, addr: tuple[str, int]) -> None:
                pass

            def getsockname(self) -> tuple[str, int]:
                return ("192.168.1.50", 0)

        monkeypatch.setattr(socket, "socket", lambda *a, **kw: _FakeSocket())
        assert network.detect_lan_ips() == ["192.168.1.50"]

    def test_udp_fallback_filters_loopback_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(psutil, "net_if_addrs", dict)

        class _LoopbackSocket:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def __enter__(self) -> _LoopbackSocket:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

            def connect(self, addr: tuple[str, int]) -> None:
                pass

            def getsockname(self) -> tuple[str, int]:
                return ("127.0.0.1", 0)

        monkeypatch.setattr(socket, "socket", lambda *a, **kw: _LoopbackSocket())
        assert network.detect_lan_ips() == []

    def test_returns_empty_when_all_sources_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom_psutil() -> dict[str, object]:
            raise RuntimeError("psutil unavailable")

        def _boom_socket(*args: object, **kwargs: object) -> socket.socket:
            raise OSError("no route to host")

        monkeypatch.setattr(psutil, "net_if_addrs", _boom_psutil)
        monkeypatch.setattr(socket, "socket", _boom_socket)
        assert network.detect_lan_ips() == []


# ── derive_allowed_origins() ──────────────────────────────────────────────────


class TestDeriveAllowedOrigins:
    def test_default_loopback_bind_excludes_lan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.9"])
        origins = network.derive_allowed_origins()
        assert origins == (
            "http://127.0.0.1:8080",
            f"http://{_FIXED_HOSTNAME}.local:8080",
            "http://localhost:8080",
        )

    def test_hal0_port_env_used_when_no_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_PORT", "9090")
        origins = network.derive_allowed_origins()
        assert all(o.endswith(":9090") for o in origins)

    def test_explicit_port_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_PORT", "9090")
        origins = network.derive_allowed_origins(port=1234)
        assert all(o.endswith(":1234") for o in origins)

    def test_invalid_port_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_PORT", "not-a-number")
        origins = network.derive_allowed_origins()
        assert all(o.endswith(":8080") for o in origins)

    def test_localhost_bind_host_excludes_lan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "localhost")
        monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.9"])
        origins = network.derive_allowed_origins()
        assert not any("10.0.0.9" in o for o in origins)

    def test_ipv6_loopback_bind_host_excludes_lan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "::1")
        monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.9"])
        origins = network.derive_allowed_origins()
        assert not any("10.0.0.9" in o for o in origins)

    def test_wildcard_bind_adds_lan_but_not_bind_host_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
        monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.9", "10.0.0.10"])
        origins = network.derive_allowed_origins()
        assert "http://10.0.0.9:8080" in origins
        assert "http://10.0.0.10:8080" in origins
        assert not any("0.0.0.0" in o for o in origins)

    def test_ipv6_wildcard_bind_adds_lan_but_not_bind_host_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "::")
        monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.9"])
        origins = network.derive_allowed_origins()
        assert "http://10.0.0.9:8080" in origins
        assert not any(o.startswith("http://:") for o in origins)

    def test_concrete_lan_bind_host_adds_lan_and_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "192.168.1.20")
        monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.9"])
        origins = network.derive_allowed_origins()
        assert "http://10.0.0.9:8080" in origins
        assert "http://192.168.1.20:8080" in origins

    def test_always_includes_loopback_and_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "192.168.1.20")
        monkeypatch.setattr(network, "detect_lan_ips", lambda: [])
        origins = network.derive_allowed_origins()
        assert "http://localhost:8080" in origins
        assert "http://127.0.0.1:8080" in origins
        assert f"http://{_FIXED_HOSTNAME}.local:8080" in origins

    def test_result_is_sorted_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_BIND_HOST", "192.168.1.20")
        monkeypatch.setattr(network, "detect_lan_ips", lambda: ["10.0.0.9"])
        origins = network.derive_allowed_origins()
        assert isinstance(origins, tuple)
        assert origins == tuple(sorted(origins))
