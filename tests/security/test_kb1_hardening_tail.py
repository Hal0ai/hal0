"""KB-1 hardening tail: WS/SSE api_key log-scrub, origin gate, login throttle.

All three are backend defence-in-depth added on top of the KB-1 auth core:

* the uvicorn ``uvicorn.error`` WebSocket log line must never persist
  ``?api_key=`` (it does by default — access-log scrubbing doesn't cover it),
* state-changing / WebSocket requests get a belt-and-suspenders Origin check
  in the enforcement middleware, and
* ``POST /api/auth/login`` is per-IP rate-limited against brute force.

Kept in ``tests/security`` (fast, does not import the whole tests/api tree).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api import auth as auth_mod
from hal0.api.middleware import log_scrub
from hal0.security.ratelimit import SlidingWindowRateLimiter

# ---------------------------------------------------------------------------
# 1. Query-string scrubber covers the uvicorn.error WebSocket line


def _record(msg: str, args: tuple[object, ...]) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, msg, args, None)


def test_scrubber_strips_ws_accept_line_api_key() -> None:
    scrubber = log_scrub.QueryStringScrubber()
    # Exactly uvicorn's websocket accept format: args = (client_addr, path+qs).
    rec = _record(
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:5000", "/api/board/events?api_key=SUPER-SECRET"),
    )
    assert scrubber.filter(rec) is True
    assert rec.args is not None
    assert rec.args[1] == "/api/board/events"
    assert "SUPER-SECRET" not in (rec.args[1] or "")


def test_scrubber_strips_access_request_line() -> None:
    scrubber = log_scrub.QueryStringScrubber()
    rec = _record(
        '%s - "%s" %d',
        ("127.0.0.1:5000", "GET /api/events/stream?api_key=SECRET HTTP/1.1", 200),
    )
    assert scrubber.filter(rec) is True
    assert rec.args is not None
    assert rec.args[1] == "GET /api/events/stream HTTP/1.1"


def test_scrubber_leaves_non_request_records_untouched() -> None:
    scrubber = log_scrub.QueryStringScrubber()
    # A plain uvicorn.error status line with no request-line arg — must not
    # be mutated (no "/" in args[1]).
    rec = _record("%sWebSocket connection made", ("prefix ",))
    before = rec.args
    assert scrubber.filter(rec) is True
    assert rec.args == before


def test_install_attaches_to_both_loggers() -> None:
    # Start from a clean slate on both loggers so the assertion is meaningful.
    for name in log_scrub.SCRUBBED_LOGGER_NAMES:
        lg = logging.getLogger(name)
        lg.filters = [f for f in lg.filters if not isinstance(f, log_scrub.QueryStringScrubber)]

    log_scrub.install(None)  # type: ignore[arg-type]

    for name in log_scrub.SCRUBBED_LOGGER_NAMES:
        lg = logging.getLogger(name)
        count = sum(1 for f in lg.filters if isinstance(f, log_scrub.QueryStringScrubber))
        assert count == 1, f"{name} should carry exactly one scrubber, got {count}"

    # Idempotent: a second install adds no duplicates.
    log_scrub.install(None)  # type: ignore[arg-type]
    for name in log_scrub.SCRUBBED_LOGGER_NAMES:
        lg = logging.getLogger(name)
        count = sum(1 for f in lg.filters if isinstance(f, log_scrub.QueryStringScrubber))
        assert count == 1


# ---------------------------------------------------------------------------
# 2. Origin defence-in-depth


def _scope(
    *,
    scope_type: str = "http",
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, object]:
    return {
        "type": scope_type,
        "method": method,
        "path": "/api/settings",
        "headers": headers or [],
        "query_string": b"",
    }


def test_origin_allowed_no_origin_is_allowed() -> None:
    assert auth_mod._origin_allowed(_scope()) is True


def test_origin_allowed_allowlisted_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "https://dash.example.com")
    scope = _scope(headers=[(b"origin", b"https://dash.example.com")])
    assert auth_mod._origin_allowed(scope) is True


def test_origin_allowed_same_origin_via_host() -> None:
    # Origin netloc == Host header ⇒ genuine same-origin, allowed even though
    # the host isn't in the static allowlist (dashboard served from any LAN IP).
    scope = _scope(
        headers=[
            (b"origin", b"http://192.168.1.50:8080"),
            (b"host", b"192.168.1.50:8080"),
        ]
    )
    assert auth_mod._origin_allowed(scope) is True


def test_origin_rejected_cross_site() -> None:
    scope = _scope(
        headers=[
            (b"origin", b"https://attacker.example.com"),
            (b"host", b"192.168.1.50:8080"),
        ]
    )
    assert auth_mod._origin_allowed(scope) is False


@pytest.fixture
def hardened_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A fresh app with auth ENFORCED + an admin key, isolated secret/home."""
    from hal0.api import create_app

    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    os.makedirs(tmp_path / "home" / "etc" / "hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-xyz")
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://hal0.local")
    with TestClient(create_app()) as c:
        yield c


def test_middleware_rejects_cross_site_state_change(hardened_app: TestClient) -> None:
    # Bad Origin on a POST → 403 origin_forbidden, BEFORE the key even matters.
    resp = hardened_app.post(
        "/api/settings",
        headers={
            "Origin": "https://evil.example.com",
            "Authorization": "Bearer admin-xyz",
        },
        json={},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth.origin_forbidden"


def test_middleware_allows_no_origin_state_change(hardened_app: TestClient) -> None:
    # No Origin (curl/SDK) with a valid admin key → not an auth/origin block.
    resp = hardened_app.post(
        "/api/settings",
        headers={"Authorization": "Bearer admin-xyz"},
        json={},
    )
    assert resp.status_code not in (401, 403), resp.text


def test_middleware_rejects_cross_site_websocket(hardened_app: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        hardened_app.websocket_connect(
            "/api/board/events",
            headers={"origin": "https://evil.example.com"},
        ),
    ):
        pass
    assert excinfo.value.code == 4403


# ---------------------------------------------------------------------------
# 3. Login rate-limit


def test_limiter_allows_up_to_budget_then_blocks() -> None:
    clock = {"t": 100.0}
    limiter = SlidingWindowRateLimiter(max_events=3, window_s=60.0, clock=lambda: clock["t"])
    assert [limiter.allow("ip") for _ in range(3)] == [True, True, True]
    assert limiter.allow("ip") is False
    # A blocked, unrecorded attempt does not extend the lockout: once the
    # window slides past the first hit, budget frees again.
    clock["t"] = 161.0
    assert limiter.allow("ip") is True


def test_limiter_keys_are_independent() -> None:
    limiter = SlidingWindowRateLimiter(max_events=1, window_s=60.0)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True


def test_login_route_rate_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.api import create_app

    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    os.makedirs(tmp_path / "home" / "etc" / "hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_ADMIN_KEY", "the-real-key")
    monkeypatch.setenv("HAL0_LOGIN_RATELIMIT_MAX", "3")
    monkeypatch.setenv("HAL0_LOGIN_RATELIMIT_WINDOW_S", "60")

    with TestClient(create_app()) as c:
        # 3 wrong attempts are metered (401), the 4th is throttled (429).
        for _ in range(3):
            r = c.post("/api/auth/login", json={"key": "wrong"})
            assert r.status_code == 401, r.text
        r = c.post("/api/auth/login", json={"key": "the-real-key"})
        assert r.status_code == 429, r.text
        assert r.json()["error"]["code"] == "auth.rate_limited"
