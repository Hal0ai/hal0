"""#3569: a memory identity comes from a credential, never from ``X-hal0-Agent`` alone.

Before the fix any caller (the box client key holder, or anyone on a box with
auth off) could send ``X-hal0-Agent: hermes`` and read Hermes's private notes.
Now the header is the identity only with that agent's identity key in
``X-hal0-Agent-Key`` (:mod:`hal0.agent_identity`) or, on ``/mcp/memory``, when
a bridge key names it. A reserved name without its key is refused with 403
``bridge.client_name_reserved``; any other unkeyed name is an audit label and
the caller is ``anonymous``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount
from starlette.testclient import TestClient

from hal0 import agent_identity
from hal0.api import mcp_mount
from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.memory.envelope import AGENT_TAG_PREFIX
from hal0.memory.read_scope import (
    VISIBILITY_PRIVATE_TAG,
    reflect_visibility_scope,
    tags_readable,
)

# ── the key itself ───────────────────────────────────────────────────────────


def test_key_is_derived_stable_and_per_agent() -> None:
    first = agent_identity.key_for("hermes")
    assert first is not None and first.startswith(agent_identity.KEY_PREFIX)
    assert agent_identity.key_for("hermes") == first  # a re-run never rotates it
    assert agent_identity.key_for("hermes__coder") != first
    assert agent_identity.verify("hermes", first)
    assert not agent_identity.verify("hermes__coder", first)
    assert not agent_identity.verify("hermes", first[:-1] + ("A" if first[-1] != "A" else "B"))
    assert not agent_identity.verify("hermes", None)


def test_secret_is_owner_only() -> None:
    agent_identity.key_for("hermes")
    mode = stat.S_IMODE(os.stat(agent_identity.secret_path()).st_mode)
    assert mode == 0o600


def test_verify_never_creates_the_secret() -> None:
    assert not agent_identity.secret_path().exists()
    assert not agent_identity.verify("hermes", "hal0ak_" + "x" * 43)
    assert not agent_identity.secret_path().exists()


def test_deleting_the_secret_revokes_every_key() -> None:
    old = agent_identity.key_for("hermes")
    agent_identity.secret_path().unlink()
    assert not agent_identity.verify("hermes", old)
    assert agent_identity.key_for("hermes") != old


def test_judge_claim() -> None:
    key = agent_identity.key_for("hermes")
    assert agent_identity.judge_claim("hermes", key) == agent_identity.CLAIM_VERIFIED
    assert agent_identity.judge_claim("hermes", None) == agent_identity.CLAIM_RESERVED
    assert agent_identity.judge_claim("hermes__coder", key) == agent_identity.CLAIM_RESERVED
    assert agent_identity.judge_claim("pi", None) == agent_identity.CLAIM_RESERVED
    assert agent_identity.judge_claim("anonymous", None) == agent_identity.CLAIM_UNVERIFIED
    assert agent_identity.judge_claim("claude-code", None) == agent_identity.CLAIM_UNVERIFIED


def test_identity_headers_drop_a_reserved_name_it_cannot_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_identity, "key_for", lambda _agent: None)
    assert agent_identity.identity_headers("hal0-cli") == {}
    assert agent_identity.identity_headers("rvskeptic") == {"X-hal0-Agent": "rvskeptic"}


def test_a_key_is_masked_by_the_redactor() -> None:
    from hal0.redaction import redact_secret_shapes as redact

    key = agent_identity.key_for("hermes")
    assert key is not None
    assert key not in redact(f"headers: X-hal0-Agent-Key: {key}")
    assert key not in redact(f"sent {key} to hal0")


# ── REST: /api/memory/* ──────────────────────────────────────────────────────

_PRIVATE_NOTE = {
    "id": "n1",
    "text": "hermes private note",
    "tags": [f"{AGENT_TAG_PREFIX}hermes", VISIBILITY_PRIVATE_TAG],
}
_SHARED_NOTE = {"id": "n2", "text": "shared note", "tags": [f"{AGENT_TAG_PREFIX}pi"]}
# A private note owned by an agent whose name is not reserved: before #3569 a
# bare `X-hal0-Agent: rvskeptic` read it.
_OTHER_PRIVATE_NOTE = {
    "id": "n3",
    "text": "rvskeptic private note",
    "tags": [f"{AGENT_TAG_PREFIX}rvskeptic", VISIBILITY_PRIVATE_TAG],
}


class _Provider:
    """Holds two notes and filters reads with the real visibility rule."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append(kw)
        groups = reflect_visibility_scope(kw.get("client_id"))
        notes = (_PRIVATE_NOTE, _SHARED_NOTE, _OTHER_PRIVATE_NOTE)
        return [n for n in notes if tags_readable(groups, n["tags"])]

    async def add(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"id": "new", "timestamp": "t"}


async def _rest(path: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = _Provider()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        return await client.post(f"http://test{path}", json=body, headers=headers)


def _ids(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


@pytest.mark.asyncio
async def test_rest_reserved_header_without_key_is_403() -> None:
    for name in ("hermes", "hermes__coder", "pi", "unknown", "hal0-brain"):
        response = await _rest("/api/memory/search", {"query": "q"}, {"X-hal0-Agent": name})
        assert response.status_code == 403, (name, response.text)
        assert response.json()["error"]["code"] == "bridge.client_name_reserved"


@pytest.mark.asyncio
async def test_rest_another_agents_key_does_not_open_hermes() -> None:
    other = agent_identity.key_for("hermes__coder")
    response = await _rest(
        "/api/memory/search",
        {"query": "q"},
        {"X-hal0-Agent": "hermes", "X-hal0-Agent-Key": other or ""},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rest_header_only_caller_cannot_read_a_private_note() -> None:
    response = await _rest("/api/memory/search", {"query": "q"}, {"X-hal0-Agent": "rvskeptic"})
    assert _ids(response) == ["n2"]
    response = await _rest("/api/memory/search", {"query": "q"}, {})
    assert _ids(response) == ["n2"]


@pytest.mark.asyncio
async def test_rest_header_only_caller_cannot_write_private() -> None:
    response = await _rest(
        "/api/memory/add",
        {"text": "x"},
        {"X-hal0-Agent": "rvskeptic", "X-hal0-Private": "1"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "memory.namespace_invalid"


@pytest.mark.asyncio
async def test_rest_keyed_agent_reads_and_writes_its_own_private_notes() -> None:
    headers = agent_identity.identity_headers("hermes")
    assert "X-hal0-Agent-Key" in headers
    response = await _rest("/api/memory/search", {"query": "q"}, headers)
    assert _ids(response) == ["n1", "n2"]
    response = await _rest("/api/memory/add", {"text": "x"}, {**headers, "X-hal0-Private": "1"})
    assert response.status_code == 200, response.text


# ── MCP: /mcp/memory ─────────────────────────────────────────────────────────


@pytest.fixture
def mcp_headers(monkeypatch: pytest.MonkeyPatch):
    def _set(headers: dict[str, str]) -> None:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        request = Request({"type": "http", "headers": raw})
        monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)

    return _set


def test_mcp_header_only_identity_is_anonymous(mcp_headers) -> None:
    mcp_headers({"X-hal0-Agent": "rvskeptic"})
    assert mcp_mount.client_id_resolver() == "anonymous"
    mcp_headers({"X-hal0-Agent": "hermes"})
    assert mcp_mount.client_id_resolver() == "anonymous"


def test_mcp_keyed_identity_is_the_agent(mcp_headers) -> None:
    mcp_headers(agent_identity.identity_headers("hermes__coder"))
    assert mcp_mount.client_id_resolver() == "hermes__coder"


@pytest.mark.asyncio
async def test_mcp_header_only_caller_cannot_read_a_private_note(mcp_headers) -> None:
    from hal0.mcp import memory

    provider = _Provider()
    dispatcher = memory.make_dispatcher(
        provider,
        client_id_resolver=mcp_mount.client_id_resolver,
        private_resolver=mcp_mount.private_resolver,
    )
    mcp_headers({"X-hal0-Agent": "rvskeptic"})
    out = await dispatcher("memory_search", {"query": "q"})
    assert [item["id"] for item in out["results"]] == ["n2"], out
    # Asking for its private bank does not help: an unproven caller has none.
    mcp_headers({"X-hal0-Agent": "rvskeptic", "X-hal0-Private": "1"})
    out = await dispatcher("memory_search", {"query": "q"})
    assert out["status"] == "error", out

    mcp_headers({**agent_identity.identity_headers("hermes"), "X-hal0-Private": "1"})
    out = await dispatcher("memory_search", {"query": "q"})
    assert [item["id"] for item in out["results"]] == ["n1", "n2"], out


def _guarded_client() -> TestClient:
    async def inner(scope: Any, receive: Any, send: Any) -> None:
        await PlainTextResponse("served")(scope, receive, send)

    app = Starlette(routes=[Mount("/mcp/memory", app=mcp_mount.guard_agent_claim(inner))])
    return TestClient(app)


def test_mcp_guard_refuses_a_reserved_header_without_key() -> None:
    client = _guarded_client()
    for name in ("hermes", "hermes__research", "pi", "unknown"):
        response = client.post("/mcp/memory/mcp", headers={"X-hal0-Agent": name})
        assert response.status_code == 403, name
        assert response.json()["error"]["code"] == "bridge.client_name_reserved"


def test_mcp_guard_passes_keyed_unreserved_and_anonymous_callers() -> None:
    client = _guarded_client()
    for headers in (
        agent_identity.identity_headers("hermes"),
        {"X-hal0-Agent": "rvskeptic"},
        {"X-hal0-Agent": "anonymous"},
        {},
    ):
        response = client.post("/mcp/memory/mcp", headers=headers)
        assert (response.status_code, response.text) == (200, "served"), headers


def test_mcp_guard_leaves_a_bridge_principal_alone() -> None:
    """A bridge client's identity is its key's name; the header is ignored, not refused."""
    from hal0.api.auth import BRIDGE_PRINCIPAL_SCOPE_KEY, AuthPrincipal

    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append("served")
        await PlainTextResponse("served")(scope, receive, send)

    guarded = mcp_mount.guard_agent_claim(inner)

    async def with_principal(scope: Any, receive: Any, send: Any) -> None:
        scope[BRIDGE_PRINCIPAL_SCOPE_KEY] = AuthPrincipal(
            tier="bridge", source="bearer", name="cc", scopes=frozenset({"memory"})
        )
        await guarded(scope, receive, send)

    client = TestClient(Starlette(routes=[Mount("/mcp/memory", app=with_principal)]))
    response = client.post(
        "/mcp/memory/mcp",
        headers={"X-hal0-Agent": "hermes", "Authorization": "Bearer hal0bk_cc.token"},
    )
    assert response.status_code == 200 and seen == ["served"]


# ── first-party writers carry the key ────────────────────────────────────────


def test_profile_mcp_servers_carry_the_key() -> None:
    from hal0.agents import hermes_provision as hp

    servers = hp._build_profile_mcp_servers("hermes__coder", private=True)
    key = agent_identity.key_for("hermes__coder")
    for name in ("hal0-admin", "hal0-memory"):
        headers = servers[name]["headers"]
        assert headers["X-hal0-Agent"] == "hermes__coder"
        assert headers["X-hal0-Agent-Key"] == key


def test_default_profile_converges_to_its_key(tmp_path: Path) -> None:
    import yaml

    from hal0.agents import hermes_provision as hp

    home = tmp_path / "hermes-home"
    home.mkdir()
    config = {
        "model": {"default": "x"},
        "mcp_servers": {
            "hal0-memory": {"url": "u", "headers": {"X-hal0-Agent": "hermes"}},
            "hal0-admin": {"url": "u", "headers": {"X-hal0-Agent": "hermes"}},
            "github": {"url": "g", "headers": {"X-hal0-Agent": "hermes"}},
        },
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    first = hp.converge_default_profile_agent_key(home)
    assert first["changed"] is True, first
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    key = agent_identity.key_for("hermes")
    assert data["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Agent-Key"] == key
    assert data["mcp_servers"]["hal0-admin"]["headers"]["X-hal0-Agent-Key"] == key
    # Never put on a server that is not hal0's own.
    assert "X-hal0-Agent-Key" not in data["mcp_servers"]["github"]["headers"]
    assert data["model"] == {"default": "x"}

    before = (home / "config.yaml").read_bytes()
    again = hp.converge_default_profile_agent_key(home)
    assert again["changed"] is False
    assert (home / "config.yaml").read_bytes() == before


def test_default_profile_convergence_without_config_is_a_no_op(tmp_path: Path) -> None:
    from hal0.agents import hermes_provision as hp

    assert hp.converge_default_profile_agent_key(tmp_path / "absent")["changed"] is False


def test_memory_plugin_sends_the_key_from_its_profile_config(tmp_path: Path) -> None:
    import yaml

    from hal0.agents.hermes.plugins.memory_hindsight import provider as plugin

    config = tmp_path / "config.yaml"
    key = agent_identity.key_for("hermes__coder")
    config.write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {
                    "hal0-memory": {
                        "headers": {"X-hal0-Agent": "hermes__coder", "X-hal0-Agent-Key": key}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert plugin._read_agent_key(str(config), "hermes__coder") == key
    # An entry for another identity is never borrowed.
    assert plugin._read_agent_key(str(config), "hermes") is None
    assert plugin._read_agent_key(str(tmp_path / "missing.yaml"), "hermes__coder") is None


def test_memory_client_puts_the_key_on_every_request() -> None:
    from hal0.agents.hermes.plugins.memory_hindsight._client import Hal0MemoryClient

    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json={"items": []})

    keys = iter([None, "hal0ak_" + "k" * 43])
    client = Hal0MemoryClient(
        base_url="http://test",
        agent_id="hermes",
        http_client=httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)),
        agent_key=lambda: next(keys),
    )
    client.search("q")
    client.search("q")
    assert "x-hal0-agent-key" not in seen[0]
    assert seen[1]["x-hal0-agent-key"] == "hal0ak_" + "k" * 43


# ── the secret is trusted only when it is the service user's, 0600 (review S1-S3) ──


def _secret_bytes() -> bytes:
    agent_identity.key_for("hermes")
    return agent_identity.secret_path().read_bytes()


def test_an_unreadable_secret_fails_closed_and_says_so() -> None:
    from structlog.testing import capture_logs

    _secret_bytes()
    path = agent_identity.secret_path()
    os.chmod(path, 0)
    try:
        with capture_logs() as logs:
            assert agent_identity.key_for("hermes") is None  # no new, predictable key
            assert not agent_identity.verify("hermes", "hal0ak_" + "x" * 43)
    finally:
        os.chmod(path, 0o600)
    if os.geteuid() != 0:  # root reads a mode-0 file; the mode check still refuses it
        assert any(e["event"] == "hal0.agent_identity.secret_refused" for e in logs)


def test_a_loose_secret_is_refused() -> None:
    from structlog.testing import capture_logs

    key = agent_identity.key_for("hermes")
    os.chmod(agent_identity.secret_path(), 0o644)
    with capture_logs() as logs:
        assert not agent_identity.verify("hermes", key)
        assert not agent_identity.verify("hermes", key)
    refused = [e for e in logs if e["event"] == "hal0.agent_identity.secret_refused"]
    assert len(refused) == 1 and "0644" in refused[0]["reason"]  # logged once, not per call
    os.chmod(agent_identity.secret_path(), 0o600)
    assert agent_identity.verify("hermes", key)


def test_a_short_or_linked_secret_is_refused(tmp_path: Path) -> None:
    key = agent_identity.key_for("hermes")
    path = agent_identity.secret_path()
    real = tmp_path / "elsewhere.bin"
    real.write_bytes(path.read_bytes())
    os.chmod(real, 0o600)
    path.unlink()
    path.symlink_to(real)
    assert not agent_identity.verify("hermes", key)
    path.unlink()
    path.write_bytes(b"short")
    os.chmod(path, 0o600)
    assert not agent_identity.verify("hermes", key)


def test_a_foreign_owned_secret_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    key = agent_identity.key_for("hermes")
    monkeypatch.setattr(agent_identity, "_trusted_owners", lambda: {0, os.geteuid() + 4242})
    monkeypatch.setattr(agent_identity, "_cache", None)
    assert not agent_identity.verify("hermes", key)


def test_only_root_or_the_service_user_creates_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator in the hal0 group must not mint a file hal0-api cannot read (S3)."""
    if os.geteuid() == 0:
        pytest.skip("root may always create it")
    monkeypatch.setattr(agent_identity, "_service_uid", lambda: os.geteuid() + 4242)
    assert agent_identity.key_for("hermes") is None
    assert not agent_identity.secret_path().exists()
    assert not agent_identity.secret_path().parent.exists()


# ── keys exist only for the names hal0's own agents use (review S5) ──────────


@pytest.mark.parametrize("name", ["openwebui", "claude-code", "rvskeptic", "anonymous"])
def test_no_key_for_a_name_a_bridge_client_may_hold(name: str) -> None:
    assert agent_identity.key_for(name) is None
    agent_identity.key_for("hermes")  # a secret exists
    secret = agent_identity.secret_path().read_bytes()
    forged = agent_identity._derive(secret, name)
    assert not agent_identity.verify(name, forged)
    assert agent_identity.judge_claim(name, forged) == agent_identity.CLAIM_UNVERIFIED
