"""Tests for the HMAC session cookie and Origin helpers in ``hal0.api.agents._auth``.

``POST /api/auth/login`` mints this cookie and the auth middleware verifies
it (``hal0.api.auth``); ``_is_same_origin`` is the IP-literal same-origin
fallback the middleware's Origin gate uses (#2277, #2327).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from hal0.api.agents import _auth


@pytest.fixture(autouse=True)
def isolate_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Force the HMAC secret onto a per-test path so secrets don't leak."""
    secret_path = tmp_path / "secret.bin"
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(secret_path))
    yield secret_path


def test_mint_then_verify_roundtrip() -> None:
    """A freshly minted cookie verifies cleanly."""
    cookie = _auth.mint_session_cookie()
    assert _auth.verify_session_cookie(cookie) is True


def test_verify_rejects_garbage() -> None:
    """Random junk that vaguely looks like a cookie is rejected."""
    assert _auth.verify_session_cookie("not-a-cookie") is False
    assert _auth.verify_session_cookie("a.b") is False
    assert _auth.verify_session_cookie("") is False


def test_verify_rejects_tampered_payload() -> None:
    """Mutating the payload (even one bit) invalidates the HMAC."""
    cookie = _auth.mint_session_cookie()
    payload_b64, sig_b64 = cookie.split(".", 1)
    # Append a printable char to the payload portion → b64 still valid,
    # signature mismatches.
    tampered = f"{payload_b64}X.{sig_b64}"
    assert _auth.verify_session_cookie(tampered) is False


def test_verify_rejects_tampered_signature() -> None:
    """Mutating the signature invalidates the cookie."""
    cookie = _auth.mint_session_cookie()
    payload_b64, sig_b64 = cookie.split(".", 1)
    # Flip the first character of the signature (legal b64url).
    new_first = "A" if sig_b64[0] != "A" else "B"
    tampered = f"{payload_b64}.{new_first}{sig_b64[1:]}"
    assert _auth.verify_session_cookie(tampered) is False


def test_verify_rejects_expired_cookie() -> None:
    """A cookie whose ``expires_at`` is in the past is rejected."""
    # Mint with a fake clock far in the past so expiry has elapsed.
    cookie = _auth.mint_session_cookie(now=0)
    # Verify "now" = far future.
    assert _auth.verify_session_cookie(cookie, now=10**12) is False


def test_mint_honours_a_longer_ttl() -> None:
    """Remember-me sessions are the same signed cookie with a later expiry."""
    thirty_days = _auth.SESSION_COOKIE_REMEMBER_TTL_SECONDS
    assert thirty_days == 30 * 24 * 60 * 60
    cookie = _auth.mint_session_cookie(now=1_000, ttl_seconds=thirty_days)
    assert _auth.verify_session_cookie(cookie, now=1_000 + thirty_days - 1) is True
    assert _auth.verify_session_cookie(cookie, now=1_000 + thirty_days) is False
    # ...and the default is still the 8h workday session.
    default = _auth.mint_session_cookie(now=1_000)
    assert _auth.verify_session_cookie(default, now=1_000 + 8 * 3600 - 1) is True
    assert _auth.verify_session_cookie(default, now=1_000 + 8 * 3600) is False


def test_session_cookie_expiry_reads_only_verified_cookies() -> None:
    cookie = _auth.mint_session_cookie(now=1_000, ttl_seconds=600)
    assert _auth.session_cookie_expiry(cookie, now=1_000) == 1_600
    assert _auth.session_cookie_expiry(cookie, now=1_600) is None  # expired
    assert _auth.session_cookie_expiry("garbage") is None
    payload, _, sig = cookie.partition(".")
    assert _auth.session_cookie_expiry(f"{payload}.{sig[:-2]}AA") is None  # forged


def test_secret_file_chmod_0600(isolate_secret: Path) -> None:
    """The on-disk secret is mode 0600 after first creation."""
    _auth.mint_session_cookie()
    mode = isolate_secret.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600 secret perms, got {oct(mode)}"


def test_secret_reused_across_mints(isolate_secret: Path) -> None:
    """The HMAC secret is not regenerated on every call.

    Otherwise every cookie would re-verify against a different secret
    and the whole scheme falls over.
    """
    a = _auth.mint_session_cookie()
    secret_bytes_first = isolate_secret.read_bytes()
    b = _auth.mint_session_cookie()
    secret_bytes_second = isolate_secret.read_bytes()
    assert a != b  # nonces in payload differ
    assert secret_bytes_first == secret_bytes_second
    # Both must still verify.
    assert _auth.verify_session_cookie(a)
    assert _auth.verify_session_cookie(b)


def test_allowed_origins_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``HAL0_ALLOWED_ORIGINS`` replaces the default tuple."""
    monkeypatch.setenv(
        "HAL0_ALLOWED_ORIGINS",
        "https://demo.example.com, http://other.example.com",
    )
    origins = _auth.allowed_origins()
    assert origins == ("https://demo.example.com", "http://other.example.com")


def test_allowed_origins_empty_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty override falls back to the default allowlist (dev convenience)."""
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "")
    assert _auth.allowed_origins() == _auth.DEFAULT_ALLOWED_ORIGINS


# ---------------------------------------------------------------------------
# _is_same_origin: the "box's LAN IP changed since install" fallback (#2277).
# RFC 5737 / RFC 3849 addresses.


@pytest.mark.parametrize(
    ("origin", "host", "scheme"),
    [
        # Origin == Host on an IP literal, absent from the install-time allowlist.
        ("http://192.0.2.20:8080", "192.0.2.20:8080", "ws"),
        ("http://192.0.2.20:8080", "192.0.2.20:8080", "http"),
        # A TLS-terminating reverse proxy hands hal0 ``ws`` for an ``https`` page.
        ("https://192.0.2.20", "192.0.2.20", "ws"),
        # A wss upgrade hal0 terminated itself, from an https page.
        ("https://192.0.2.20:8443", "192.0.2.20:8443", "wss"),
        # IPv6 literal, compared case-insensitively.
        ("http://[2001:db8::20]:8080", "[2001:DB8::20]:8080", "ws"),
    ],
)
def test_same_origin_ip_literal_accepted(origin: str, host: str, scheme: str) -> None:
    assert _auth._is_same_origin(origin, host, scheme) is True


@pytest.mark.parametrize(
    ("origin", "host", "scheme"),
    [
        # A TLS endpoint hal0 terminates itself never trusts a plain-HTTP page.
        ("http://192.0.2.20:8443", "192.0.2.20:8443", "wss"),
        # Different host entirely: cross-origin.
        ("http://192.0.2.99:8080", "192.0.2.20:8080", "ws"),
        ("https://attacker.example.com", "192.0.2.20:8080", "ws"),
        # Same host, different port: cross-origin.
        ("http://192.0.2.20:9999", "192.0.2.20:8080", "ws"),
        ("http://192.0.2.20", "192.0.2.20:8080", "ws"),
        # A DNS name can be rebound to the box: matching Origin/Host on a
        # name is not proof of same-origin, so only IP literals qualify.
        ("http://rebind.example.com:8080", "rebind.example.com:8080", "ws"),
        ("http://hal0.example.net", "hal0.example.net", "ws"),
        # Non-web schemes and opaque origins never count as same-origin.
        ("chrome-extension://192.0.2.20:8080", "192.0.2.20:8080", "ws"),
        ("null", "null", "ws"),
        # An Origin carrying userinfo or a path is not a browser Origin.
        ("http://user@192.0.2.20:8080", "user@192.0.2.20:8080", "ws"),
        ("http://192.0.2.20:8080/x", "192.0.2.20:8080", "ws"),
        ("http://192.0.2.20:8080/", "192.0.2.20:8080", "ws"),
        # Nor is one carrying a query string or a fragment.
        ("http://192.0.2.20:8080?x=1", "192.0.2.20:8080", "ws"),
        ("http://192.0.2.20:8080#x", "192.0.2.20:8080", "ws"),
        # Same address, different spelling: never normalised into a match.
        ("http://[::ffff:192.0.2.20]:8080", "192.0.2.20:8080", "ws"),
        ("http://192.0.2.20", "192.0.2.20:80", "ws"),
        # A trailing-dot Host is not an IP literal.
        ("http://192.0.2.20.:8080", "192.0.2.20.:8080", "ws"),
        # IPv6 zone ids are rejected even when byte-identical.
        ("http://[fe80::1%25eth0]:8080", "[fe80::1%25eth0]:8080", "ws"),
        # A missing Origin or Host never matches.
        ("", "192.0.2.20:8080", "ws"),
        ("http://192.0.2.20:8080", "", "ws"),
    ],
)
def test_same_origin_rejects_everything_else(origin: str, host: str, scheme: str) -> None:
    assert _auth._is_same_origin(origin, host, scheme) is False
