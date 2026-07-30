"""``_client_ip`` (GH #1476) — the login/rotate rate-limit key.

Behind a reverse proxy every direct TCP peer is the proxy itself, so the
raw ``request.client.host`` collapses every real caller onto one shared
limiter bucket: one remote guesser exhausts it and 429-locks out the
operator too. ``[security].trust_forwarded_for`` / ``HAL0_TRUST_FORWARDED_FOR``
is an explicit opt-in that reads the leftmost ``X-Forwarded-For`` entry
instead — OFF by default (a client can forge the header) so this only
changes behavior for an operator who has verified their reverse proxy
strips/overwrites it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.requests import Request

from hal0.api import auth as auth_mod
from hal0.api.routes.auth import _client_ip


def _request(
    *, client: tuple[str, int] | None = ("203.0.113.9", 12345), forwarded_for: str | None = None
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope: dict[str, object] = {
        "type": "http",
        "path": "/api/auth/login",
        "headers": headers,
        "client": client,
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    monkeypatch.delenv("HAL0_TRUST_FORWARDED_FOR", raising=False)
    auth_mod._trust_forwarded_for_cache = None


# ---------------------------------------------------------------------------
# trust_forwarded_for_enabled — mirrors require_auth_enabled's precedence


def test_trust_forwarded_for_off_by_default() -> None:
    assert auth_mod.trust_forwarded_for_enabled() is False


def test_trust_forwarded_for_persisted_config_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.config import paths
    from hal0.config.loader import save_hal0_config
    from hal0.config.schema import Hal0Config

    cfg = Hal0Config()
    cfg.security.trust_forwarded_for = True
    paths.hal0_toml().parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(cfg)
    auth_mod._trust_forwarded_for_cache = None
    assert auth_mod.trust_forwarded_for_enabled() is True


def test_trust_forwarded_for_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.config import paths
    from hal0.config.loader import save_hal0_config
    from hal0.config.schema import Hal0Config

    cfg = Hal0Config()
    cfg.security.trust_forwarded_for = True
    paths.hal0_toml().parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(cfg)
    auth_mod._trust_forwarded_for_cache = None

    monkeypatch.setenv("HAL0_TRUST_FORWARDED_FOR", "0")
    assert auth_mod.trust_forwarded_for_enabled() is False


# ---------------------------------------------------------------------------
# _client_ip


def test_client_ip_uses_raw_peer_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Untrusted by default: X-Forwarded-For present but ignored."""
    req = _request(client=("10.0.1.200", 51234), forwarded_for="198.51.100.7")
    assert _client_ip(req) == "10.0.1.200"


def test_client_ip_honours_forwarded_for_when_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_TRUST_FORWARDED_FOR", "1")
    req = _request(client=("10.0.1.200", 51234), forwarded_for="198.51.100.7")
    assert _client_ip(req) == "198.51.100.7"


def test_client_ip_takes_leftmost_forwarded_for_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A multi-hop chain: leftmost is the original client (single trusted hop convention)."""
    monkeypatch.setenv("HAL0_TRUST_FORWARDED_FOR", "1")
    req = _request(client=("10.0.1.200", 51234), forwarded_for="198.51.100.7, 10.0.1.200")
    assert _client_ip(req) == "198.51.100.7"


def test_client_ip_falls_back_to_peer_when_trusted_but_header_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_TRUST_FORWARDED_FOR", "1")
    req = _request(client=("10.0.1.200", 51234), forwarded_for=None)
    assert _client_ip(req) == "10.0.1.200"


def test_client_ip_falls_back_to_unknown_with_no_client_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request(client=None)
    assert _client_ip(req) == "unknown"
