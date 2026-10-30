"""Open WebUI provenance on memory writes: ``user:<name>`` tag + ``session_id``.

Identity never comes from the headers; they only annotate a write made by the
bridge client named ``openwebui``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hal0.api import mcp_mount
from hal0.api.auth import AuthPrincipal
from hal0.mcp import memory
from tests.api.test_memory_extraction_ctx_preflight import StubHindsightWrapper

OWUI = AuthPrincipal(tier="bridge", name="openwebui", scopes=frozenset({"memory"}))
OTHER = AuthPrincipal(tier="bridge", name="cc", scopes=frozenset({"memory"}))


def _request(headers: dict[str, str]) -> Any:
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()})


def _resolve(monkeypatch: pytest.MonkeyPatch, principal: AuthPrincipal, headers: dict[str, str]):
    req = _request(headers)
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: req)
    monkeypatch.setattr(mcp_mount, "judged_principal", lambda _r: principal)
    return mcp_mount.provenance_resolver()


@pytest.mark.asyncio
async def test_memory_add_tags_user_and_session_from_owui_headers() -> None:
    w = StubHindsightWrapper()
    await memory._memory_add(
        w,
        {"text": "x"},
        client_id="openwebui",
        private=False,
        provenance={"user": "alexander-smith", "session_id": "abc-123"},
    )
    call = w.add_calls[0]
    assert "user:alexander-smith" in call["tags"]
    assert call["metadata"]["session_id"] == "abc-123"


@pytest.mark.asyncio
async def test_memory_add_caller_session_id_wins() -> None:
    w = StubHindsightWrapper()
    await memory._memory_add(
        w,
        {"text": "x", "metadata": {"session_id": "mine"}},
        client_id="openwebui",
        private=False,
        provenance={"session_id": "abc"},
    )
    assert w.add_calls[0]["metadata"]["session_id"] == "mine"


@pytest.mark.asyncio
async def test_memory_add_without_provenance_is_unchanged() -> None:
    w = StubHindsightWrapper()
    await memory._memory_add(w, {"text": "x"}, client_id="a", private=False)
    assert not any(t.startswith("user:") for t in w.add_calls[0]["tags"])
    assert "session_id" not in w.add_calls[0]["metadata"]


@pytest.mark.asyncio
async def test_dispatcher_threads_the_resolver_into_memory_add() -> None:
    w = StubHindsightWrapper()
    dispatch = memory.make_dispatcher(
        w,
        client_id_resolver=lambda: "openwebui",
        provenance_resolver=lambda: {"user": "bob", "session_id": "s1"},
    )
    out = await dispatch("memory_add", {"text": "x"})
    assert out["status"] == "ok"
    assert "user:bob" in w.add_calls[0]["tags"]


def test_resolver_reads_headers_for_openwebui(monkeypatch: pytest.MonkeyPatch) -> None:
    got = _resolve(
        monkeypatch,
        OWUI,
        {"X-OpenWebUI-User-Name": "Alexander%20Smith", "X-OpenWebUI-Chat-Id": "abc-123"},
    )
    assert got == {"user": "alexander-smith", "session_id": "abc-123"}


def test_resolver_omits_absent_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _resolve(monkeypatch, OWUI, {"X-OpenWebUI-Chat-Id": "c"}) == {"session_id": "c"}
    assert _resolve(monkeypatch, OWUI, {}) == {}


def test_resolver_ignores_non_openwebui_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    hdrs = {"X-OpenWebUI-User-Name": "A"}
    assert _resolve(monkeypatch, OTHER, hdrs) == {}
    assert _resolve(monkeypatch, AuthPrincipal(tier="admin"), hdrs) == {}


def test_resolver_empty_outside_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: None)
    assert mcp_mount.provenance_resolver() == {}


def test_normalise_user_tag() -> None:
    assert mcp_mount.normalise_user_tag("Alexander Smith!") == "alexander-smith"
    assert mcp_mount.normalise_user_tag("a" * 100) == "a" * 64
    assert mcp_mount.normalise_user_tag("!!!") == ""
