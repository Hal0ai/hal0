"""cli-auth-streamtest — the Phase-0 tail folded in here per §5.1/§5.2.

Phase 0 fixed four CLI transport sites that bypassed ``_auth_headers()``
and 401'd (or silently printed the JSON error envelope as log output) on
auth-enabled boxes: ``slot logs --follow``, ``doctor logs --follow``,
``hal0 chat``, and ``hal0 setup``'s apply/probe path — by routing every
streaming call through the shared ``auth_client``/``api_stream`` helpers
in :mod:`hal0.cli._shared`. The spec called for "an auth-on smoke tier
running every verb against a keyed TestClient" as the regression net for
that fix class; this is it.

Approach: build a REAL ``hal0`` app (:func:`hal0.api.create_app`) with
``require_auth=True`` and a known admin key, then monkeypatch every
``httpx.Client`` the CLI's transport helpers construct to instead build a
:class:`fastapi.testclient.TestClient` bound to that app — the same
sync-to-ASGI bridge every other API test in this suite uses, so real
middleware (exposure classification, auth) runs with no live socket. Each
streaming verb is asserted twice: WITH a discoverable key (must NOT
401/403 — the honest failure mode past the auth gate is a 404 or a
validation error, never an auth error) and WITHOUT one (the SAME route
MUST 401 — the control that proves the first assertion was really the
bearer working, not the route being open).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.cli import _shared

ADMIN_KEY = "cli-streamtest-admin-key-0123456789"


@pytest.fixture
def auth_on_app(monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str) -> FastAPI:
    """A real hal0 app with auth enforcement ON and a known admin key.

    ``HAL0_REQUIRE_AUTH`` is the highest-precedence posture input
    (:func:`hal0.api.auth.require_auth_enabled`) — setting it here, after
    the suite-wide ``_auth_dev_open_by_default`` autouse fixture already
    ran (and pinned it to ``0``), wins per that fixture's own documented
    contract.
    """
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    return create_app()


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch, auth_on_app: FastAPI) -> Iterator[None]:
    """Route every ``httpx.Client`` the CLI's transport helpers construct to
    a fresh :class:`~fastapi.testclient.TestClient` bound to ``auth_on_app``
    instead — one per call, matching production's "one client per request"
    lifecycle (``_shared``'s helpers each open-use-close their own client).
    Only ``headers=`` is meaningful to forward; the other kwargs the real
    ``httpx.Client`` accepts (``timeout=``, bare positional none) have no
    ``TestClient`` equivalent and are safe to drop for this in-process rig.
    """

    def _routed_client(*_args: Any, **kwargs: Any) -> TestClient:
        return TestClient(auth_on_app, headers=kwargs.get("headers"))

    monkeypatch.setattr(_shared.httpx, "Client", _routed_client)
    yield


def _strip_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)


# ── slot logs --follow / doctor logs --follow (api_stream) ──────────────────
#
# Both `slot_commands.slot_logs --follow` and `doctor_commands.doctor_logs
# --follow` are line-buffered passthroughs over `_shared.follow_sse_logs`,
# which is a thin wrapper over `api_stream` — exercise api_stream directly
# against the exact paths those two verbs hit, the Phase-0-owned chokepoint
# every streaming CLI verb funnels through.

_STREAM_PATHS = [
    "/api/slots/nonexistent-slot/logs/stream",  # `hal0 slot logs --follow`
    "/api/logs/stream",  # `hal0 doctor logs --follow`
]


@pytest.mark.usefixtures("routed")
@pytest.mark.parametrize("path", _STREAM_PATHS)
def test_streaming_verb_authenticates_with_key(path: str) -> None:
    with _shared.api_stream("GET", path, timeout=5.0) as resp:
        assert resp.status_code not in (401, 403), resp.status_code


@pytest.mark.usefixtures("routed")
@pytest.mark.parametrize("path", _STREAM_PATHS)
def test_streaming_verb_401s_without_key(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    _strip_keys(monkeypatch)
    with _shared.api_stream("GET", path, timeout=5.0) as resp:
        assert resp.status_code == 401


# ── hal0 chat / hal0 chat --brain (auth_client) ──────────────────────────────


@pytest.mark.usefixtures("routed")
def test_chat_v1_authenticates_with_key() -> None:
    with _shared.auth_client(timeout=5.0) as client:
        resp = client.post(
            "http://testserver/v1/chat/completions",
            json={
                "model": "agent",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )
    assert resp.status_code not in (401, 403), resp.status_code


@pytest.mark.usefixtures("routed")
def test_chat_v1_401s_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _strip_keys(monkeypatch)
    with _shared.auth_client(timeout=5.0) as client:
        resp = client.post(
            "http://testserver/v1/chat/completions",
            json={
                "model": "agent",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )
    assert resp.status_code == 401


@pytest.mark.usefixtures("routed")
def test_chat_brain_authenticates_with_key() -> None:
    with _shared.auth_client(timeout=5.0) as client:
        resp = client.post(
            "http://testserver/api/brain/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code not in (401, 403), resp.status_code


@pytest.mark.usefixtures("routed")
def test_chat_brain_401s_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _strip_keys(monkeypatch)
    with _shared.auth_client(timeout=5.0) as client:
        resp = client.post(
            "http://testserver/api/brain/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 401


# ── every non-streaming api_* helper too, for one belt-and-suspenders pass ──
#
# The spec asks for "every streaming verb"; the same routed-transport rig
# also covers the plain request/response helpers every OTHER CLI verb this
# lane added (auth/board/ports/model) is built on, so one extra parametrized
# pass pins that the non-streaming path never regressed either.

_JSON_GET_PATHS = [
    "/api/auth/status",  # OPEN, but must still resolve through the CLI helper
    "/api/slots",
    "/api/ports",
    "/api/board/board",
    "/api/models",
]


@pytest.mark.usefixtures("routed")
@pytest.mark.parametrize("path", _JSON_GET_PATHS)
def test_json_get_helper_authenticates_with_key(path: str) -> None:
    data = _shared.api_get(path)
    assert data is not None or path == "/api/auth/status"


@pytest.mark.usefixtures("routed")
@pytest.mark.parametrize("path", ["/api/slots", "/api/ports", "/api/board/board", "/api/models"])
def test_json_get_helper_401s_without_key(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    _strip_keys(monkeypatch)
    with pytest.raises(_shared.CliApiError, match="401"):
        _shared.api_get(path)
