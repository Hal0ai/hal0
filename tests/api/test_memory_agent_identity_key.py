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


# ── convergence keys only the identity the location implies (review S4, S6) ──


def _write_config(home: Path, config: dict[str, Any]) -> Path:
    import yaml

    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _read_config(path: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_default_profile_convergence_keys_only_hermes(tmp_path: Path) -> None:
    """A default-profile entry naming another identity never gets that identity's key."""
    from hal0.agents import hermes_provision as hp

    path = _write_config(
        tmp_path / "home",
        {
            "mcp_servers": {
                "hal0-memory": {"url": "u", "headers": {"X-hal0-Agent": "pi"}},
                "hal0-admin": {"url": "u", "headers": {"X-hal0-Agent": "hermes__coder"}},
            }
        },
    )
    hp.converge_default_profile_agent_key(tmp_path / "home")
    servers = _read_config(path)["mcp_servers"]
    assert "X-hal0-Agent-Key" not in servers["hal0-memory"]["headers"]
    assert "X-hal0-Agent-Key" not in servers["hal0-admin"]["headers"]


def test_agent_key_desired_ignores_a_foreign_name() -> None:
    from hal0.agents import hermes_provision as hp

    base = {"mcp_servers": {"hal0-memory": {"headers": {"X-hal0-Agent": "pi"}}}}
    assert hp._agent_key_desired(base, "hermes__coder") == {}


def test_an_opted_out_profile_that_uses_hal0_memory_keeps_its_key(tmp_path: Path) -> None:
    """``hal0: {wire_mcp: false}`` opts out of the wiring, not out of the profile's own memory."""
    from hal0.agents import hermes_provision as hp
    from hal0.agents.hermes.plugins.memory_hindsight import provider as plugin

    home = tmp_path / "hermes"
    path = _write_config(
        home / "profiles" / "lab",
        {
            "hal0": {"wire_mcp": False},
            "memory": {"provider": "hal0-memory"},
            "mcp_servers": {
                "hal0-memory": {"url": "u", "headers": {"X-hal0-Agent": "hermes__lab"}},
                "mine": {"url": "m", "headers": {"X-hal0-Agent": "hermes__lab"}},
            },
        },
    )
    report = hp.wire_named_profiles(home, terminal_enabled=None)
    assert "lab" not in report.get("errors", {}), report
    data = _read_config(path)
    key = agent_identity.key_for("hermes__lab")
    assert data["hal0"]["agent_key"] == {"agent": "hermes__lab", "key": key}
    assert data["hal0"]["wire_mcp"] is False
    assert data["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Agent-Key"] == key
    assert "X-hal0-Agent-Key" not in data["mcp_servers"]["mine"]["headers"]  # not hal0's
    assert set(data["mcp_servers"]) == {"hal0-memory", "mine"}  # wiring still opted out
    assert plugin._read_agent_key(str(path), "hermes__lab") == key


def test_an_opted_out_profile_without_hal0_memory_gets_no_key(tmp_path: Path) -> None:
    from hal0.agents import hermes_provision as hp

    home = tmp_path / "hermes"
    path = _write_config(
        home / "profiles" / "discord",
        {"hal0": {"wire_mcp": False}, "memory": {"provider": "other"}},
    )
    hp.wire_named_profiles(home, terminal_enabled=None)
    assert "agent_key" not in (_read_config(path).get("hal0") or {})


def test_the_plugin_reads_the_hal0_record_first(tmp_path: Path) -> None:
    from hal0.agents.hermes.plugins.memory_hindsight import provider as plugin

    path = _write_config(
        tmp_path / "p",
        {"hal0": {"agent_key": {"agent": "hermes__x", "key": "hal0ak_" + "r" * 43}}},
    )
    assert plugin._read_agent_key(str(path), "hermes__x") == "hal0ak_" + "r" * 43
    assert plugin._read_agent_key(str(path), "hermes") is None


# ── the key never rides argv; a failed restart is a warning (review S7, S8) ──


def test_the_overlay_never_puts_the_key_on_the_command_line() -> None:
    from hal0.agents import hermes_provision as hp

    agent_identity.key_for("hermes")  # a secret exists, so a key could be derived
    pairs = hp._build_config_overlay(
        primary={
            "model_id": "m",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 16384,
        },
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=hp._default_mcp_servers(),
        agent_id="hermes",
        live_resolve_enabled=False,
    )
    assert pairs
    assert not any("X-hal0-Agent-Key" in key for key, _value in pairs)
    assert not any(str(value).startswith("hal0ak_") for _key, value in pairs)


def test_config_set_errors_are_redacted(tmp_path: Path) -> None:
    import subprocess

    from hal0.agents import hermes_provision as hp

    secret = "hal0ak_" + "s" * 43

    def run(argv: list[str], **_kw: Any) -> None:
        raise subprocess.CalledProcessError(1, argv)

    home = tmp_path / "hermes"
    home.mkdir()
    _applied, errors = hp._apply_config_set(
        [("x.headers.Authorization", f"Bearer {secret}")],
        hermes_bin=tmp_path / "hermes-bin",
        hermes_home=home,
        run=run,
    )
    assert errors and secret not in errors[0]


def test_a_failed_restart_after_writing_the_key_is_a_warning(tmp_path: Path) -> None:
    from structlog.testing import capture_logs

    from hal0.agents import hermes_provision as hp

    _write_config(
        tmp_path / "home",
        {"mcp_servers": {"hal0-memory": {"url": "u", "headers": {"X-hal0-Agent": "hermes"}}}},
    )
    with capture_logs() as logs:
        result = hp.converge_default_profile_agent_key_on_boot(
            tmp_path / "home", restart=lambda: ["hermes-gateway.service"]
        )
    assert result["changed"] is True
    warned = [e for e in logs if e["event"] == "hermes.default_profile_agent_key_restart_failed"]
    assert warned and warned[0]["log_level"] == "warning"
    assert warned[0]["units"] == ["hermes-gateway.service"]
    # Converged: the next boot restarts nothing.
    calls: list[int] = []

    def restart() -> list[str]:
        calls.append(1)
        return []

    again = hp.converge_default_profile_agent_key_on_boot(tmp_path / "home", restart=restart)
    assert again["changed"] is False and calls == []


# ── a root-created secret belongs to hal0, even under a root-owned dir (verification) ──


def test_pi_first_root_hands_the_secret_and_its_dir_to_hal0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``installer/agents/pi.sh`` used to ``mkdir -p`` ``/var/lib/hal0/agents`` as root,
    so on a box that installs Pi before Hermes the directory was root's. Root
    must give the secret, and that directory, to the hal0 account, never to
    the directory's owner, or hal0-api refuses the secret on its next start."""
    parent = agent_identity.secret_path().parent
    parent.mkdir(parents=True)
    me = (os.getuid(), os.getgid())
    real_stat = os.stat
    chowned: list[tuple[str, int, int]] = []

    class _RootOwned:
        def __init__(self, st: os.stat_result) -> None:
            self._st = st

        def __getattr__(self, name: str) -> Any:
            if name == "st_uid":
                return 0
            if name == "st_gid":
                return 0
            return getattr(self._st, name)

    def fake_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        st = real_stat(path, *args, **kwargs)
        return _RootOwned(st) if Path(path) == parent else st

    def fake_chown(path: Any, uid: int, gid: int, **_kw: Any) -> None:
        chowned.append((str(path), uid, gid))

    monkeypatch.setattr(agent_identity.os, "geteuid", lambda: 0)
    monkeypatch.setattr(agent_identity, "_service_ids", lambda: me)
    monkeypatch.setattr(agent_identity, "_service_uid", lambda: me[0])
    monkeypatch.setattr(agent_identity.os, "stat", fake_stat)
    monkeypatch.setattr(agent_identity.os, "chown", fake_chown)

    key = agent_identity.key_for("pi")

    assert key is not None
    assert chowned == [(str(parent), *me)]  # the root-owned directory goes to hal0
    st = real_stat(agent_identity.secret_path())
    assert (st.st_uid, st.st_gid) == me  # and so does the secret
    assert stat.S_IMODE(st.st_mode) == 0o600


def test_pi_installer_creates_the_agents_dir_as_hal0_before_its_own_dir() -> None:
    """The installer side of the same ordering bug: pi.sh hands the agents root to hal0
    before its ``mkdir -p`` can create it as root."""
    script = (Path(__file__).resolve().parents[2] / "installer" / "agents" / "pi.sh").read_text()
    create = script.index('install -d -o hal0 -g "$hal0_group" -m 0711 "$AGENTS_ROOT"')
    handback = script.index('chown hal0:"$hal0_group" "$AGENTS_ROOT"')
    mkdir = script.index('mkdir -p "$HAL0_AGENT_DATA_DIR"')
    assert create < mkdir and handback < mkdir


def test_the_perms_table_declares_the_secret() -> None:
    from hal0.install import perms

    rows = [r for r in perms.ownership_table() if str(r.target).endswith("agents/identity.bin")]
    assert len(rows) == 1
    assert rows[0].mode == 0o600 and rows[0].optional


# ── the doctor row ───────────────────────────────────────────────────────────


def test_doctor_identity_secret_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.cli import doctor_all as da

    monkeypatch.setattr(da, "_resolve_uid", lambda _user: os.getuid())
    secret = tmp_path / "identity.bin"

    missing = da.check_agent_identity_secret(path=secret)
    assert (missing.key, missing.status) == ("agent-identity-secret", "pass")
    assert "not created yet" in missing.detail

    secret.write_bytes(b"x" * 32)
    os.chmod(secret, 0o600)
    ok = da.check_agent_identity_secret(path=secret)
    assert ok.status == "pass" and "0600 OK" in ok.detail

    os.chmod(secret, 0o644)
    loose = da.check_agent_identity_secret(path=secret)
    assert loose.status == "warn" and "mode 644" in loose.detail
    assert "hal0 doctor perms --fix" in loose.detail

    os.chmod(secret, 0o600)
    secret.write_bytes(b"short")
    short = da.check_agent_identity_secret(path=secret)
    assert short.status == "warn" and "fewer than 32" in short.detail

    link = tmp_path / "link.bin"
    link.symlink_to(secret)
    linked = da.check_agent_identity_secret(path=link)
    assert linked.status == "warn" and "symlink" in linked.detail

    monkeypatch.setattr(da, "_resolve_uid", lambda _user: os.getuid() + 4242)
    secret.write_bytes(b"x" * 32)
    foreign = da.check_agent_identity_secret(path=secret)
    assert foreign.status == "warn" and "expected hal0" in foreign.detail
