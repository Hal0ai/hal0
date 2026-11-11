"""The engine-compatible memory endpoint for coding agents (ADR-0058 decision 4b).

``/api/memory/compat`` is what the Hindsight coding-agents plugin is pointed at
instead of the engine's own port. These tests drive the real router behind the
real ``AuthEnforcementMiddleware``, with a real ``HindsightProvider`` and
``HindsightRestClient`` on top of an ``httpx.MockTransport``, so what is
asserted is what would reach the engine: the envelope on a write, the tag
filter on a read, and nothing the caller was not allowed to send.

The plugin's behaviour that shapes the answers (read from its bundle): it
returns a 404 to its caller without raising on every route, so a write must
never answer 404; every other non-2xx raises and is logged; and on the
knowledge-base routes 404 means "this server has no pages".
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from hal0.api import auth as auth_mod
from hal0.api.auth import AuthEnforcementMiddleware
from hal0.api.middleware import error_codes
from hal0.api.routes import memory_compat
from hal0.bridge.store import BridgeStore
from hal0.errors import Hal0Error
from hal0.memory.hindsight_client import REFLECT_TIMEOUT_S, HindsightRestClient
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.pgvector_provider import PgVectorProvider
from hal0.memory.projects import ProjectRegistry
from hal0.memory.read_scope import (
    project_scope_groups,
    reflect_visibility_scope,
    unresolved_scope_groups,
)
from hal0.security.exposure import COMPAT_MEMORY_PREFIX, AuthClass, classify, match_rule
from tests.memory.test_read_scope import _readable

ADMIN_KEY = "admin-key-for-compat-tests"
CLIENT_KEY = "client-key-for-compat-tests"

#: The platform bank ([memory] bank) every URL bank name is mapped to.
BANK = "hal0:shared"
ENGINE_BANK = f"/v1/default/banks/{BANK}"

#: The plugin's default bank id for a repository checked out as ``hal0-mono``,
#: which the registry below spells ``hal0``.
PLUGIN_BANK = "coding-agent::hal0-mono"
URL_BANK = "/v1/default/banks/coding-agent%3A%3Ahal0-mono"

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hindsight_coding_agents"


# ── the engine double ───────────────────────────────────────────────────────


@dataclass
class Call:
    method: str
    path: str
    params: dict[str, list[str]]
    body: Any
    read_timeout: float | None

    @property
    def line(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class Engine:
    """What sits behind the REST client: records every call, answers like 1.2.0."""

    calls: list[Call] = field(default_factory=list)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    recall_results: list[dict[str, Any]] = field(default_factory=list)
    recall_extras: dict[str, Any] = field(default_factory=dict)
    strategies: tuple[str, ...] = ("conversation",)
    #: ``(method, path)`` -> a response, or an exception to raise.
    overrides: dict[tuple[str, str], Any] = field(default_factory=dict)

    def lines(self) -> list[str]:
        return [call.line for call in self.calls]

    def only(self, method: str, suffix: str) -> Call:
        found = [c for c in self.calls if c.method == method and c.path == ENGINE_BANK + suffix]
        assert len(found) == 1, f"expected one {method} {suffix}, got {self.lines()}"
        return found[0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        params: dict[str, list[str]] = {}
        for key, value in request.url.params.multi_items():
            params.setdefault(key, []).append(value)
        body = json.loads(request.content) if request.content else None
        timeout = request.extensions.get("timeout") or {}
        path = request.url.path
        self.calls.append(Call(request.method, path, params, body, timeout.get("read")))
        override = self.overrides.get((request.method, path))
        if isinstance(override, Exception):
            raise override
        if override is not None:
            return override
        return self._answer(request.method, path, params, body)

    def _answer(self, method: str, path: str, params: dict[str, list[str]], body: Any):
        if (method, path) == ("GET", "/version"):
            return httpx.Response(200, json={"api_version": "1.2.0", "features": {"worker": True}})
        if (method, path) == ("GET", "/health"):
            return httpx.Response(200, json={"status": "healthy"})
        if not path.startswith(ENGINE_BANK):
            return httpx.Response(404, json={"detail": "Not Found"})
        rest = path[len(ENGINE_BANK) :]
        if (method, rest) == ("POST", "/memories"):
            answer = {
                "success": True,
                "bank_id": BANK,
                "items_count": len(body["items"]),
                "async": bool(body.get("async", False)),
            }
            if body.get("async"):
                answer["operation_id"] = body.get("operation_id") or "engine-chosen-id"
            return httpx.Response(200, json=answer)
        if (method, rest) == ("POST", "/reflect"):
            return httpx.Response(200, json={"text": "the lockfile was stale", "usage": {"n": 1}})
        if (method, rest) == ("POST", "/memories/recall"):
            return httpx.Response(200, json={"results": self.recall_results, **self.recall_extras})
        if (method, rest) == ("GET", "/documents"):
            wanted = set(params.get("tags", []))
            items = [d for d in self.documents.values() if wanted <= set(d.get("tags", []))]
            page = {"items": items, "total": len(items), "limit": 100, "offset": 0}
            return httpx.Response(200, json=page)
        if rest.startswith("/documents/"):
            doc_id = rest[len("/documents/") :]
            doc = self.documents.get(doc_id)
            if doc is None:
                return httpx.Response(404, json={"detail": f"Document {doc_id} not found"})
            if method == "GET":
                return httpx.Response(200, json={**doc, "original_text": "the text"})
            if method == "DELETE":
                answer = {
                    "success": True,
                    "message": "deleted",
                    "document_id": doc_id,
                    "memory_units_deleted": 3,
                }
                return httpx.Response(200, json=answer)
        if (method, rest) == ("GET", "/operations"):
            return httpx.Response(200, json=OPERATIONS_PAGE)
        if method == "GET" and rest.startswith("/operations/"):
            return httpx.Response(200, json=OPERATION_STATUS)
        if (method, rest) == ("GET", "/config"):
            resolved = {
                "retain_strategies": {
                    name: {"retain_chunk_size": 3000} for name in self.strategies
                },
                "reflect_mission": "Answer from memory.",
                "llm_api_key": "sk-engine-secret",
                "llm_base_url": "http://127.0.0.1:9/v1",
            }
            overrides = {"reflect_mission": "Answer from memory.", "llm_model": "local"}
            return httpx.Response(
                200, json={"bank_id": BANK, "config": resolved, "overrides": overrides}
            )
        return httpx.Response(404, json={"detail": "Not Found"})


OPERATION_STATUS = {
    "operation_id": "6f1d3b52-8f0e-5c41-a7d2-3e9c4b1f0a66",
    "id": "6f1d3b52-8f0e-5c41-a7d2-3e9c4b1f0a66",
    "status": "failed",
    "operation_type": "retain",
    "task_type": "retain",
    "created_at": "2026-11-10T09:14:02Z",
    "updated_at": "2026-11-10T09:14:09Z",
    "completed_at": "2026-11-10T09:14:09Z",
    "error_message": "extraction model returned no JSON",
    "retry_count": 1,
    "progress": {"stage": "extract", "at": "2026-11-10T09:14:05Z", "detail": "chunk about Alice"},
    "result_metadata": {"document_ids": ["private:hermes:s-9"]},
    "task_payload": {"contents": [{"content": "ANOTHER CLIENT'S TEXT"}]},
    "child_operations": [
        {"operation_id": "c-1", "status": "failed", "items_count": 1, "error_message": "boom"}
    ],
}

OPERATIONS_PAGE = {
    "bank_id": BANK,
    "total": 1,
    "limit": 20,
    "offset": 0,
    "operations": [
        {
            "id": "op-1",
            "task_type": "retain",
            "items_count": 1,
            "document_id": "private:hermes:s-9",
            "filename": "notes.md",
            "created_at": "2026-11-10T09:14:02Z",
            "status": "pending",
            "error_message": "ANOTHER CLIENT'S ERROR",
            "task_payload": {"contents": [{"content": "ANOTHER CLIENT'S TEXT"}]},
        }
    ],
}


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_posture_cache() -> Iterator[None]:
    auth_mod._require_auth_cache = None
    yield
    auth_mod._require_auth_cache = None


@pytest.fixture(autouse=True)
def _box_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A box with both keys set and enforcement off, bound to loopback: the
    posture in which every other class would wave a request through."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.setenv("HAL0_BIND_HOST", "127.0.0.1")


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BridgeStore:
    s = BridgeStore(tmp_path / "bridge.db")
    s.open()
    monkeypatch.setattr(auth_mod, "bridge_store", lambda: s)
    return s


@pytest.fixture
def key(store: BridgeStore) -> str:
    """Key for bridge client ``cc`` with the default scopes (agents, memory)."""
    _row, plain = store.add_client("cc", created_by="t")
    return plain


@pytest.fixture
def engine() -> Engine:
    return Engine()


def build_app(engine_handler: Callable[..., Any], *, unified_bank: bool = True) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_compat.router, prefix=COMPAT_MEMORY_PREFIX)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(engine_handler), base_url="http://engine.test"
    )
    app.state.memory_provider = HindsightProvider(
        client=HindsightRestClient(base_url="http://engine.test", http_client=http),
        unified_bank=unified_bank,
        bank=BANK,
        projects=ProjectRegistry(aliases={"hal0-mono": "hal0"}),
    )
    return app


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    return build_app(engine.handler)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(AuthEnforcementMiddleware(app), client=("127.0.0.1", 50000))


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _url(path: str) -> str:
    return COMPAT_MEMORY_PREFIX + path


def _code(resp: httpx.Response) -> str:
    return resp.json()["error"]["code"]


def _item(**over: Any) -> dict[str, Any]:
    return {"content": "the build fails because the lockfile is stale", **over}


# ── exposure ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path",
    [
        "",
        "/version",
        "/v1/default/banks/b",
        "/v1/default/banks/b/memories",
        "/v1/default/banks/b/documents/d",
        "/v1/default/banks/b/knowledge-base/pages",
        "/anything/else",
    ],
)
def test_every_method_under_the_prefix_is_bridge_class(method: str, path: str) -> None:
    """One rule, all methods. DELETE in particular must not fall to the pinned
    ADMIN row for memory deletes, which sits below the compat rule."""
    assert classify(method, _url(path)) is AuthClass.BRIDGE
    rule = match_rule(method, _url(path))
    assert rule is not None and rule.methods is None


def test_the_rule_stops_at_the_prefix_boundary() -> None:
    assert classify("DELETE", "/api/memory/compat-extra/v1/default/banks/b") is AuthClass.ADMIN
    assert classify("DELETE", "/api/memory/banks/b") is AuthClass.ADMIN
    assert classify("GET", "/api/memory/list") is AuthClass.ADMIN


# ── auth ────────────────────────────────────────────────────────────────────


PROBES = [
    ("GET", "/version"),
    ("POST", URL_BANK + "/memories"),
    ("POST", URL_BANK + "/reflect"),
    ("DELETE", URL_BANK + "/documents/d"),
    ("DELETE", URL_BANK),
    ("GET", URL_BANK + "/knowledge-base/tree"),
    ("GET", "/not/a/route"),
]


@pytest.mark.parametrize(("method", "path"), PROBES)
def test_no_credential_is_refused_on_loopback_with_auth_off(
    client: TestClient, engine: Engine, method: str, path: str
) -> None:
    resp = client.request(method, _url(path), json={})
    assert resp.status_code == 401
    assert _code(resp) == "auth.required"
    assert engine.calls == []


@pytest.mark.parametrize(("method", "path"), PROBES)
def test_a_bridge_key_without_the_memory_scope_is_refused(
    client: TestClient, engine: Engine, store: BridgeStore, method: str, path: str
) -> None:
    """The BRIDGE class admits any bridge key; the handlers require the scope."""
    _row, agents_only = store.add_client("runner", created_by="t", scopes=("agents",))
    resp = client.request(method, _url(path), headers=_auth(agents_only), json={})
    assert resp.status_code == 403
    assert _code(resp) == "memory.compat_scope_required"
    assert resp.json()["error"]["details"] == {"required_scope": "memory"}
    assert engine.calls == []


def test_a_revoked_key_is_refused(client: TestClient, engine: Engine, store: BridgeStore, key: str):
    assert client.get(_url("/version"), headers=_auth(key)).status_code == 200
    store.revoke_client("cc")
    resp = client.get(_url("/version"), headers=_auth(key))
    assert resp.status_code == 401
    assert _code(resp) == "auth.required"
    assert len(engine.calls) == 1


def test_the_box_client_key_is_refused(client: TestClient, engine: Engine) -> None:
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(CLIENT_KEY), json={})
    assert resp.status_code == 403
    assert _code(resp) == "auth.forbidden"
    assert engine.calls == []


def test_the_admin_credential_is_admitted_and_writes_as_admin(client: TestClient, engine: Engine):
    resp = client.post(
        _url(URL_BANK + "/memories"), headers=_auth(ADMIN_KEY), json={"items": [_item()]}
    )
    assert resp.status_code == 200
    tags = engine.only("POST", "/memories").body["items"][0]["tags"]
    assert [t for t in tags if t.startswith("agent:")] == ["agent:admin"]


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({}, 401, "auth.required"),
        ({"authorization": f"Bearer {CLIENT_KEY}"}, 403, "memory.compat_scope_required"),
        ({"authorization": f"Bearer {ADMIN_KEY}"}, None, None),
    ],
)
def test_the_dependency_alone_also_refuses(headers: dict[str, str], status: int, code: str):
    """An app assembled without the middleware still checks the caller."""
    scope = {
        "type": "http",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
    }
    caller = memory_compat.require_memory_bridge()
    if status is None:
        assert caller(Request(scope)) == "admin"
        return
    with pytest.raises(Hal0Error) as raised:
        caller(Request(scope))
    assert (raised.value.status, raised.value.code) == (status, code)


def test_a_degraded_box_answers_503_and_never_the_volatile_fallback(
    client: TestClient, app: FastAPI, key: str
) -> None:
    app.state.memory_provider = PgVectorProvider()
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert resp.status_code == 503
    assert _code(resp) == "memory.unavailable"


# ── retain ──────────────────────────────────────────────────────────────────


def test_retain_goes_through_the_envelope(client: TestClient, engine: Engine, key: str) -> None:
    body = {
        "items": [
            _item(
                context="coding agent session",
                document_id="conversation:s-1",
                tags=[
                    "team:platform",
                    "source:chat",
                    "harness:claude-code",
                    "agent:hermes",
                    "AGENT:evil",
                    "visibility:private",
                    "project:someone-elses",
                    "session:s-1",
                    "source:bridge:other-client",
                ],
                strategy="conversation",
                observation_scopes=[["agent:hermes"]],
                timestamp="2026-11-10T09:14:02.000Z",
                update_mode="append",
                metadata={
                    "ref_id": "conversation:s-1",
                    "harness": "claude-code",
                    "project": "someone-elses",
                    "bridge_client": "hermes",
                },
            )
        ],
        "async": True,
        "operation_id": "6f1d3b52-8f0e-5c41-a7d2-3e9c4b1f0a66",
    }
    # The identity header the rest of the memory API reads is not read here.
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers={**_auth(key), "X-hal0-Agent": "hermes", "X-hal0-Private": "1"},
        json=body,
    )

    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == "hal0"
    # The answer is the engine's own JSON.
    assert resp.json() == {
        "success": True,
        "bank_id": BANK,
        "items_count": 1,
        "async": True,
        "operation_id": "6f1d3b52-8f0e-5c41-a7d2-3e9c4b1f0a66",
    }

    call = engine.only("POST", "/memories")
    (item,) = call.body["items"]
    # Caller agent, visibility and project tags are gone; the client name and
    # the project the bank id resolved to (through the alias) are stamped; the
    # session id left the tag space; ``harness:`` and ``source:`` stayed.
    assert item["tags"] == [
        "team:platform",
        "source:chat",
        "harness:claude-code",
        "agent:cc",
        "kind:durable",
        "project:hal0",
    ]
    # hal0 chose the observation scope; the caller's was ignored.
    assert item["observation_scopes"] == [["project:hal0"]]
    assert item["metadata"] == {
        "ref_id": "conversation:s-1",
        "harness": "claude-code",
        "bridge_client": "cc",
        "source": "cc",
        "project": "hal0",
        "project_source": "bank",
        "session_id": "s-1",
    }
    assert item["document_id"] == "conversation:s-1"
    assert item["update_mode"] == "append"
    assert item["timestamp"] == "2026-11-10T09:14:02.000Z"
    assert item["context"] == "coding agent session"
    assert item["strategy"] == "conversation"
    assert item["content"] == body["items"][0]["content"]


def test_async_and_operation_id_reach_the_engine_unchanged(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The plugin's resend relies on the engine seeing the same id. One engine
    call carries both fields exactly as sent, and neither when not sent."""
    operation_id = "B0C7F5A4-2D61-5E3A-9C18-7F4E2A6D1B90"
    sent = {"items": [_item()], "async": True, "operation_id": operation_id}
    assert (
        client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=sent).status_code == 200
    )
    call = engine.only("POST", "/memories")
    assert call.body["async"] is True
    assert call.body["operation_id"] == operation_id
    assert set(call.body) == {"items", "async", "operation_id"}
    assert call.read_timeout == 30.0

    engine.calls.clear()
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert resp.status_code == 200
    call = engine.only("POST", "/memories")
    assert set(call.body) == {"items"}
    # No ``async`` means the engine's default, a synchronous retain.
    assert call.read_timeout == 300.0

    engine.calls.clear()
    explicit = {"items": [_item()], "async": False}
    assert (
        client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=explicit).status_code
        == 200
    )
    call = engine.only("POST", "/memories")
    assert call.body["async"] is False and call.read_timeout == 300.0


@pytest.mark.parametrize(
    "body",
    [
        {"items": [_item()], "async": "yes"},
        {"items": [_item()], "operation_id": 7},
        {"items": []},
        {"items": "one"},
        {"items": ["just text"]},
        {"items": [{"content": ""}]},
        {"items": [{"content": "x", "entities": "uv"}]},
        ["not", "an", "object"],
    ],
)
def test_a_malformed_retain_is_400_and_reaches_no_engine(
    client: TestClient, engine: Engine, key: str, body: Any
) -> None:
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=body)
    assert resp.status_code == 400
    assert engine.calls == []


@pytest.mark.parametrize(
    ("bank", "tags", "project_tag", "header", "scopes", "source"),
    [
        # The bank id names a project, through an alias: one project tag.
        ("coding-agent%3A%3Ahal0-mono", ["project:other"], "project:hal0", "hal0", None, "bank"),
        ("codex%3A%3Aatlas", [], "project:atlas", "atlas", None, "bank"),
        # A denied directory name is no project, and the caller's own
        # ``project:`` tag is not consulted after a refusal.
        (
            "coding-agent%3A%3Atmp",
            ["project:atlas"],
            "project:unresolved",
            "unresolved",
            "shared",
            "denied",
        ),
        # ``unknown`` is what the plugin reports outside a repository; it is a
        # role word, so the bank id names no project at all.
        ("coding-agent%3A%3Aunknown", [], "project:unresolved", "unresolved", "shared", "none"),
        # A bank id that names no project: the item's own tags decide.
        ("claude-code", ["project:Atlas"], "project:atlas", "atlas", None, "tag"),
        ("claude-code", [], "project:unresolved", "unresolved", "shared", "none"),
        ("Hal0%3A%3AAgents", [], "project:unresolved", "unresolved", "shared", "none"),
    ],
)
def test_the_bank_in_the_url_carries_the_project(
    client: TestClient,
    engine: Engine,
    key: str,
    bank: str,
    tags: list[str],
    project_tag: str,
    header: str,
    scopes: str | None,
    source: str,
) -> None:
    resp = client.post(
        _url(f"/v1/default/banks/{bank}/memories"),
        headers=_auth(key),
        json={"items": [_item(tags=tags)]},
    )
    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == header
    # Whatever the URL named, the write went to the platform bank.
    (item,) = engine.only("POST", "/memories").body["items"]
    assert [t for t in item["tags"] if t.startswith("project:")] == [project_tag]
    assert "scope:global" not in item["tags"]
    assert item["observation_scopes"] == (scopes or [[project_tag]])
    assert item["metadata"]["project"] == header
    assert item["metadata"]["project_source"] == source


def test_a_strategy_is_kept_only_when_the_platform_bank_defines_it(
    client: TestClient, engine: Engine, key: str
) -> None:
    items = [_item(strategy="conversation"), _item(strategy="git"), _item()]
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    assert resp.status_code == 200
    sent = engine.only("POST", "/memories").body["items"]
    assert [item.get("strategy") for item in sent] == ["conversation", None, None]
    assert resp.headers["X-hal0-Ignored-Fields"] == "items.strategy"

    # The bank configuration is read once and cached.
    again = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    assert again.status_code == 200
    assert engine.lines().count(f"GET {ENGINE_BANK}/config") == 1


def test_no_strategy_survives_when_the_bank_configuration_cannot_be_read(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.overrides[("GET", ENGINE_BANK + "/config")] = httpx.Response(500, json={})
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(strategy="conversation")]},
    )
    assert resp.status_code == 200
    assert "strategy" not in engine.only("POST", "/memories").body["items"][0]


def test_a_retain_without_a_strategy_reads_no_configuration(
    client: TestClient, engine: Engine, key: str
) -> None:
    client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert engine.lines() == [f"POST {ENGINE_BANK}/memories"]


def test_several_items_are_one_engine_call(client: TestClient, engine: Engine, key: str) -> None:
    items = [
        _item(content=f"fact {n}", document_id=f"doc-{n}")
        for n in range(memory_compat.MAX_RETAIN_ITEMS)
    ]
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    assert resp.status_code == 200
    assert resp.json()["items_count"] == 32
    sent = engine.only("POST", "/memories").body["items"]
    assert [item["document_id"] for item in sent] == [f"doc-{n}" for n in range(32)]
    assert all("agent:cc" in item["tags"] and "project:hal0" in item["tags"] for item in sent)


def test_more_items_than_the_limit_is_413(client: TestClient, engine: Engine, key: str) -> None:
    items = [_item() for _ in range(memory_compat.MAX_RETAIN_ITEMS + 1)]
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    assert resp.status_code == 413
    assert _code(resp) == "memory.compat_too_many_items"
    assert engine.calls == []


def test_a_body_over_the_cap_is_413_whether_or_not_it_declares_its_size(
    client: TestClient, engine: Engine, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_compat, "MAX_RETAIN_BODY_BYTES", 2048)
    raw = json.dumps({"items": [_item(content="x" * 4096)]}).encode()

    declared = client.post(
        _url(URL_BANK + "/memories"),
        headers={**_auth(key), "Content-Type": "application/json"},
        content=raw,
    )
    assert declared.status_code == 413
    assert _code(declared) == "memory.compat_body_too_large"
    assert declared.json()["error"]["details"] == {"max_bytes": 2048}

    def chunks() -> Iterator[bytes]:
        for start in range(0, len(raw), 512):
            yield raw[start : start + 512]

    streamed = client.post(
        _url(URL_BANK + "/memories"),
        headers={**_auth(key), "Content-Type": "application/json"},
        content=chunks(),
    )
    assert streamed.status_code == 413
    assert _code(streamed) == "memory.compat_body_too_large"
    assert engine.calls == []

    under = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert under.status_code == 200


def test_the_shipped_caps() -> None:
    assert memory_compat.MAX_RETAIN_ITEMS == 32
    assert memory_compat.MAX_RETAIN_BODY_BYTES == 48 * 1024 * 1024


@pytest.mark.parametrize(
    ("upstream", "status", "code"),
    [
        # The plugin does not raise on a 404, so a retain must never answer one.
        (httpx.Response(404, json={"detail": "bank not found"}), 502, "memory.compat_engine_error"),
        (httpx.Response(500, json={"detail": "boom"}), 502, "memory.compat_engine_error"),
        (httpx.Response(503, text="starting"), 502, "memory.compat_engine_error"),
        # hal0's own engine credential being wrong is not the caller's 401.
        (httpx.Response(401, json={"detail": "bad key"}), 502, "memory.compat_engine_error"),
        (httpx.ConnectError("connection refused"), 503, "memory.engine_unavailable"),
        (httpx.ConnectTimeout("no route"), 503, "memory.engine_unavailable"),
        (httpx.ReadTimeout("slow"), 504, "memory.compat_engine_timeout"),
    ],
)
def test_an_engine_failure_on_retain_is_never_a_404(
    client: TestClient, engine: Engine, key: str, upstream: Any, status: int, code: str
) -> None:
    engine.overrides[("POST", ENGINE_BANK + "/memories")] = upstream
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert resp.status_code == status
    assert _code(resp) == code


def test_an_engine_4xx_is_passed_on_with_the_engine_body(
    client: TestClient, engine: Engine, key: str
) -> None:
    conflict = {"detail": "operation_id belongs to a different operation"}
    engine.overrides[("POST", ENGINE_BANK + "/memories")] = httpx.Response(409, json=conflict)
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert resp.status_code == 409
    assert resp.json() == conflict
    assert resp.headers["X-hal0-Project"] == "hal0"

    engine.overrides[("POST", ENGINE_BANK + "/memories")] = httpx.Response(
        429, json={"detail": "slow down"}, headers={"Retry-After": "7"}
    )
    limited = client.post(
        _url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]}
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "7"


def test_a_private_document_id_is_refused(client: TestClient, engine: Engine, key: str) -> None:
    """The unified bank keeps ``private:`` ids for private documents, which
    this endpoint never writes."""
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id="private:hermes:s-9")]},
    )
    assert resp.status_code == 400
    assert _code(resp) == "memory.compat_document_id_reserved"
    assert engine.calls == []


# The engine upserts on document id, per bank, and every bank name here is one
# bank. The plugin picks ids as if it had a bank of its own.


@pytest.mark.parametrize(
    ("bank", "existing", "taken"),
    [
        # Nothing there yet: the id is free.
        ("coding-agent%3A%3Ahal0-mono", None, False),
        # This project's document, whoever wrote it: an append or a rewrite.
        ("coding-agent%3A%3Ahal0-mono", ["agent:cc", "kind:durable", "project:hal0"], False),
        ("coding-agent%3A%3Ahal0-mono", ["agent:codex", "kind:durable", "project:hal0"], False),
        # Another repository retained the same id.
        ("coding-agent%3A%3Ahal0-mono", ["agent:cc", "kind:durable", "project:atlas"], True),
        # A document one of hal0's own agents wrote.
        ("coding-agent%3A%3Ahal0-mono", ["agent:hermes", "kind:durable", "scope:global"], True),
        ("coding-agent%3A%3Ahal0-mono", [], True),
        # No project: only the caller's own unresolved document.
        ("claude-code", ["agent:cc", "kind:durable", "project:unresolved"], False),
        ("claude-code", ["agent:codex", "kind:durable", "project:unresolved"], True),
        ("claude-code", ["agent:cc", "kind:durable", "project:hal0"], True),
    ],
)
def test_a_retain_cannot_replace_a_document_outside_the_callers_project(
    client: TestClient,
    engine: Engine,
    key: str,
    bank: str,
    existing: list[str] | None,
    taken: bool,
) -> None:
    if existing is not None:
        engine.documents["readme"] = _doc("readme", existing)
    resp = client.post(
        _url(f"/v1/default/banks/{bank}/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id="readme", update_mode="append")], "async": True},
    )
    posted = [line for line in engine.lines() if line.startswith("POST")]
    if taken:
        assert resp.status_code == 409
        assert _code(resp) == "memory.compat_document_id_taken"
        assert resp.json()["error"]["details"] == {"document_ids": ["readme"]}
        assert posted == []
    else:
        assert resp.status_code == 200
        assert posted == [f"POST {ENGINE_BANK}/memories"]
    assert engine.lines()[0] == f"GET {ENGINE_BANK}/documents/readme"


def test_one_taken_document_id_refuses_the_whole_retain(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.documents["theirs"] = _doc("theirs", ["agent:cc", "project:atlas"])
    items = [
        _item(document_id="new"),
        _item(document_id="theirs"),
        _item(),
        _item(document_id="new"),
    ]
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == {"document_ids": ["theirs"]}
    # One lookup per distinct id, none for the item that names no id.
    assert sorted(engine.lines()) == [
        f"GET {ENGINE_BANK}/documents/new",
        f"GET {ENGINE_BANK}/documents/theirs",
    ]


def test_a_document_lookup_that_fails_refuses_the_retain(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.overrides[("GET", ENGINE_BANK + "/documents/readme")] = httpx.Response(500, text="boom")
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id="readme")]},
    )
    assert (resp.status_code, _code(resp)) == (502, "memory.compat_engine_error")
    assert [line for line in engine.lines() if line.startswith("POST")] == []


def test_unknown_request_fields_never_reach_the_engine(
    client: TestClient, engine: Engine, key: str
) -> None:
    body = {
        "items": [_item(resolve_entities=False, visibility="private", entities=[{"text": "uv"}])],
        "document_tags": ["agent:hermes"],
        "bank_id": "private__hermes",
    }
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=body)
    assert resp.status_code == 200
    call = engine.only("POST", "/memories")
    assert set(call.body) == {"items"}
    (item,) = call.body["items"]
    assert set(item) == {
        "content",
        "document_id",
        "context",
        "metadata",
        "tags",
        "timestamp",
        "entities",
        "observation_scopes",
    }
    assert item["entities"] == [{"text": "uv"}]
    assert resp.headers["X-hal0-Ignored-Fields"] == (
        "bank_id,document_tags,items.resolve_entities,items.visibility"
    )


class _Paused(Hal0Error):
    code = "memory.writes_paused"
    status = 503


def test_the_write_gate_guards_retain_and_delete_once_it_exists(
    client: TestClient, engine: Engine, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``hal0.memory.write_gate`` pauses writes while the store is rebuilt.
    With that module present the two write routes consult it first; reads do
    not. The module is injected here so the test holds on either side of its
    arrival on main."""

    def refuse() -> None:
        raise _Paused("memory writes are paused")

    gate = types.ModuleType("hal0.memory.write_gate")
    gate.require_writes_allowed = refuse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hal0.memory.write_gate", gate)
    monkeypatch.setattr("hal0.memory.write_gate", gate, raising=False)
    engine.documents["doc-1"] = {"id": "doc-1", "tags": ["agent:cc", "project:hal0"]}

    retain = client.post(
        _url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]}
    )
    delete = client.delete(_url(URL_BANK + "/documents/doc-1"), headers=_auth(key))
    assert (retain.status_code, _code(retain)) == (503, "memory.writes_paused")
    assert (delete.status_code, _code(delete)) == (503, "memory.writes_paused")
    assert engine.calls == []

    reflect = client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "why?"})
    assert reflect.status_code == 200


def test_the_ignored_fields_header_echoes_only_plain_names(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The names are the caller's own JSON keys: one with a newline, or one
    that is not Latin-1, must not break (or inject into) the response."""
    body = {
        "items": [_item()],
        "plain_field": 1,
        "x\r\nSet-Cookie: a=b": 1,
        "naïve": 1,
        "": 1,
        **{f"extra_{n:02d}": 1 for n in range(30)},
    }
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=body)
    assert resp.status_code == 200
    echoed = resp.headers["X-hal0-Ignored-Fields"].split(",")
    assert len(echoed) == 20
    assert all(name.startswith("extra_") for name in echoed)
    assert "set-cookie" not in resp.headers
    assert set(engine.only("POST", "/memories").body) == {"items"}


# ── reflect and recall ──────────────────────────────────────────────────────

OWN_PRIVATE = ["agent:cc", "kind:durable", "project:hal0", "visibility:private"]
OWN_PROJECT = ["agent:cc", "kind:durable", "project:hal0"]
OTHER_AGENT_PROJECT = ["agent:codex", "kind:durable", "project:hal0"]
OTHER_AGENT_PRIVATE = ["agent:hermes", "kind:durable", "project:hal0", "visibility:private"]
OTHER_AGENT_PRIVATE_GLOBAL = ["agent:hermes", "kind:durable", "scope:global", "visibility:private"]
OTHER_PROJECT = ["agent:cc", "kind:durable", "project:atlas"]
GLOBAL = ["agent:hermes", "kind:durable", "scope:global"]
UNTAGGED: list[str] = []
OWN_UNRESOLVED = ["agent:cc", "kind:durable", "project:unresolved"]
OTHER_UNRESOLVED = ["agent:codex", "kind:durable", "project:unresolved"]
EVERY_FACT = [
    OWN_PRIVATE,
    OWN_PROJECT,
    OTHER_AGENT_PROJECT,
    OTHER_AGENT_PRIVATE,
    OTHER_AGENT_PRIVATE_GLOBAL,
    OTHER_PROJECT,
    GLOBAL,
    UNTAGGED,
    OWN_UNRESOLVED,
    OTHER_UNRESOLVED,
]


@pytest.mark.parametrize("route", ["/reflect", "/memories/recall"])
def test_a_read_is_held_to_visibility_and_the_project(
    client: TestClient, engine: Engine, key: str, route: str
) -> None:
    resp = client.post(_url(URL_BANK + route), headers=_auth(key), json={"query": "why?"})
    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == "hal0"
    body = engine.only("POST", route).body
    groups = body["tag_groups"]
    assert groups == [*reflect_visibility_scope("cc"), *project_scope_groups("hal0")]
    # The engine refuses ``tags`` beside ``tag_groups``: neither key is sent.
    assert "tags" not in body and "tags_match" not in body
    assert [tags for tags in EVERY_FACT if _readable(groups, tags)] == [
        OWN_PRIVATE,
        OWN_PROJECT,
        OTHER_AGENT_PROJECT,
        GLOBAL,
        UNTAGGED,
    ]
    # Another agent's private fact is outside the read, in this project or not.
    assert not _readable(groups, OTHER_AGENT_PRIVATE)
    assert not _readable(groups, OTHER_AGENT_PRIVATE_GLOBAL)


@pytest.mark.parametrize("route", ["/reflect", "/memories/recall"])
def test_the_callers_tags_are_folded_in_as_one_more_group(
    client: TestClient, engine: Engine, key: str, route: str
) -> None:
    sent = {
        "query": "why?",
        "tags": ["source:chat", "visibility:private"],
        "tags_match": "all_strict",
        "tag_groups": [{"tags": ["harness:claude-code"], "match": "any_strict"}],
    }
    resp = client.post(_url(URL_BANK + route), headers=_auth(key), json=sent)
    assert resp.status_code == 200
    body = engine.only("POST", route).body
    assert body["tag_groups"] == [
        *reflect_visibility_scope("cc"),
        {"tags": ["source:chat", "visibility:private"], "match": "all_strict"},
        *project_scope_groups("hal0"),
        {"tags": ["harness:claude-code"], "match": "any_strict"},
    ]
    assert "tags" not in body and "tags_match" not in body
    # Groups are AND-ed, so the caller's can only narrow: asking for private
    # memories still returns nobody else's.
    theirs = [*OTHER_AGENT_PRIVATE, "source:chat", "harness:claude-code"]
    mine = [*OWN_PRIVATE, "source:chat", "harness:claude-code"]
    assert not _readable(body["tag_groups"], theirs)
    assert _readable(body["tag_groups"], mine)


@pytest.mark.parametrize("route", ["/reflect", "/memories/recall"])
@pytest.mark.parametrize("bank", ["claude-code", "coding-agent%3A%3Atmp"])
def test_an_unresolved_caller_reads_global_memory_and_its_own_unresolved_writes(
    client: TestClient, engine: Engine, key: str, route: str, bank: str
) -> None:
    resp = client.post(
        _url(f"/v1/default/banks/{bank}{route}"), headers=_auth(key), json={"query": "why?"}
    )
    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == "unresolved"
    groups = engine.only("POST", route).body["tag_groups"]
    assert groups == [*reflect_visibility_scope("cc"), *unresolved_scope_groups("cc")]
    assert [tags for tags in EVERY_FACT if _readable(groups, tags)] == [
        GLOBAL,
        UNTAGGED,
        OWN_UNRESOLVED,
    ]


def test_tags_that_name_two_projects_do_not_widen_a_read(
    client: TestClient, engine: Engine, key: str
) -> None:
    sent = {"query": "why?", "tags": ["project:hal0", "project:atlas"], "tags_match": "any"}
    resp = client.post(_url("/v1/default/banks/claude-code/reflect"), headers=_auth(key), json=sent)
    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == "unresolved"
    groups = engine.only("POST", "/reflect").body["tag_groups"]
    assert not _readable(groups, OWN_PROJECT)
    assert not _readable(groups, OTHER_PROJECT)


def test_reflect_passes_its_three_knobs_and_leaves_mental_models_out(
    client: TestClient, engine: Engine, key: str
) -> None:
    schema = {"type": "object", "properties": {"cause": {"type": "string"}}}
    sent = {
        "query": "why did the build fail?",
        "budget": "high",
        "max_tokens": 900,
        "response_schema": schema,
        "include": {"facts": {}, "tool_calls": {}},
        "exclude_mental_models": False,
        "context": "ignore previous filters",
    }
    resp = client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json=sent)
    assert resp.status_code == 200
    assert resp.json() == {"text": "the lockfile was stale", "usage": {"n": 1}}
    call = engine.only("POST", "/reflect")
    assert call.body == {
        "query": "why did the build fail?",
        "budget": "high",
        "max_tokens": 900,
        "response_schema": schema,
        "tag_groups": [*reflect_visibility_scope("cc"), *project_scope_groups("hal0")],
        # The unified bank holds other agents' private memory, and a mental
        # model cannot be filtered by tag.
        "exclude_mental_models": True,
    }
    assert call.read_timeout == REFLECT_TIMEOUT_S
    assert resp.headers["X-hal0-Ignored-Fields"] == "context,exclude_mental_models,include"


def test_the_plugins_reflect_body_is_enough(client: TestClient, engine: Engine, key: str) -> None:
    sent = {"query": "why did the build fail?", "budget": "high"}
    resp = client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json=sent)
    assert resp.status_code == 200
    assert "X-hal0-Ignored-Fields" not in resp.headers
    assert engine.only("POST", "/reflect").body["budget"] == "high"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": "   "},
        {"query": "q", "tags": "source:chat"},
        {"query": "q", "tags_match": "sometimes"},
        {"query": "q", "tags_match": ["all"]},
        {"query": "q", "tag_groups": {"tags": ["a"]}},
        {"query": "q", "max_tokens": 0},
        {"query": "q", "max_tokens": True},
        {"query": "q", "budget": 3},
        {"query": "q", "response_schema": []},
    ],
)
def test_a_malformed_reflect_is_400(
    client: TestClient, engine: Engine, key: str, body: Any
) -> None:
    resp = client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json=body)
    assert resp.status_code == 400
    assert engine.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "q", "types": "world"},
        {"query": "q", "types": [1]},
        {"query": "q", "max_tokens": "many"},
        {"query": "q", "budget": {}},
        {"query": "q", "min_scores": 0.5},
        {"query": "q", "prefer_observations": "yes"},
        {"query": "q", "query_timestamp": 1731230000},
        {"query": "q", "tags": [1, 2]},
    ],
)
def test_a_malformed_recall_is_400(client: TestClient, engine: Engine, key: str, body: Any) -> None:
    resp = client.post(_url(URL_BANK + "/memories/recall"), headers=_auth(key), json=body)
    assert resp.status_code == 400
    assert engine.calls == []


@pytest.mark.parametrize(
    ("route", "upstream", "status", "code"),
    [
        ("/reflect", httpx.ReadTimeout("slow"), 504, "memory.compat_engine_timeout"),
        ("/memories/recall", httpx.ReadTimeout("slow"), 504, "memory.compat_engine_timeout"),
        ("/reflect", httpx.ConnectError("refused"), 503, "memory.engine_unavailable"),
        ("/reflect", httpx.Response(500, text="boom"), 502, "memory.compat_engine_error"),
    ],
)
def test_a_read_that_fails_in_the_engine(
    client: TestClient, engine: Engine, key: str, route: str, upstream: Any, status: int, code: str
) -> None:
    engine.overrides[("POST", ENGINE_BANK + route)] = upstream
    resp = client.post(_url(URL_BANK + route), headers=_auth(key), json={"query": "why?"})
    assert resp.status_code == status
    assert _code(resp) == code


def test_recall_is_the_engines_answer_in_the_engines_shape(
    client: TestClient, engine: Engine, key: str
) -> None:
    mine = {
        "id": "f-1",
        "text": "the lockfile was stale",
        "type": "world",
        "tags": OWN_PROJECT,
        "document_id": "conversation:s-1",
        "chunk_id": "c-1",
        "source_fact_ids": ["sf-1", "sf-2"],
        "scores": {"final": 0.91},
    }
    untagged = {"id": "f-2", "text": "from before tags existed"}
    engine.recall_results = [mine, untagged]
    engine.recall_extras = {
        "entities": {"uv": {"canonical_name": "uv", "observations": ["used on every project"]}},
        "chunks": {"c-1": {"text": "chunk one"}, "c-9": {"text": "someone else's chunk"}},
        "source_facts": {
            "sf-1": {"id": "sf-1", "text": "kept", "tags": OWN_PROJECT, "chunk_id": "c-1"},
            "sf-2": {"id": "sf-2", "text": "private to hermes", "tags": OTHER_AGENT_PRIVATE},
            "sf-3": {"id": "sf-3", "text": "nobody asked for this", "tags": OWN_PROJECT},
        },
        "trace": {"candidates": ["everything the engine looked at"]},
    }
    sent = {
        "query": "why did the build fail?",
        "types": ["world", "observation"],
        "budget": "mid",
        "max_tokens": 2048,
        "include": {"chunks": {"max_tokens": 500}, "entities": {"max_tokens": 500}},
        "trace": True,
    }
    resp = client.post(_url(URL_BANK + "/memories/recall"), headers=_auth(key), json=sent)

    assert resp.status_code == 200
    call = engine.only("POST", "/memories/recall")
    assert call.body == {
        "query": "why did the build fail?",
        "types": ["world", "observation"],
        "budget": "mid",
        "max_tokens": 2048,
        # Entities are bank-wide state: switched off, whatever was asked for.
        "include": {"chunks": {"max_tokens": 500}, "entities": None},
        "tag_groups": [*reflect_visibility_scope("cc"), *project_scope_groups("hal0")],
    }
    assert call.read_timeout == 60.0
    assert resp.headers["X-hal0-Ignored-Fields"] == "trace"
    # Results are the engine's objects, untouched.
    assert resp.json() == {
        "results": [mine, untagged],
        "entities": None,
        "chunks": {"c-1": {"text": "chunk one"}},
        "source_facts": {
            "sf-1": {"id": "sf-1", "text": "kept", "tags": OWN_PROJECT, "chunk_id": "c-1"}
        },
    }


def test_recall_drops_a_result_the_filter_should_never_have_returned(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The engine is given the filter. If it ever returned a memory outside
    it, the answer is checked against the same groups before it leaves."""
    engine.recall_results = [
        {"id": "f-1", "text": "mine", "tags": OWN_PROJECT},
        {"id": "f-2", "text": "private to hermes", "tags": OTHER_AGENT_PRIVATE},
        {"id": "f-3", "text": "another repository", "tags": OTHER_PROJECT},
        {"id": "f-4", "text": "global", "tags": GLOBAL},
    ]
    resp = client.post(_url(URL_BANK + "/memories/recall"), headers=_auth(key), json={"query": "q"})
    assert [r["id"] for r in resp.json()["results"]] == ["f-1", "f-4"]


def _reflect_scope(path: str, body: bytes) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", f"Bearer {ADMIN_KEY}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


async def test_a_reflect_is_cancelled_upstream_when_the_client_disconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plugin gives up on its own timer. The engine stops a reflect whose
    connection closes, so hal0 must close its side instead of waiting out a
    fifteen-minute budget for an answer nobody will read."""
    monkeypatch.setattr(memory_compat, "_DISCONNECT_POLL_S", 0.01)
    started = asyncio.Event()
    outcome: list[str] = []

    async def hung_engine(request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            outcome.append("cancelled")
            raise
        return httpx.Response(200, json={"text": "too late"})

    app = build_app(hung_engine)
    body = json.dumps({"query": "why?", "budget": "high"}).encode()
    gone = asyncio.Event()
    delivered = False
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    path = _url("/v1/default/banks/coding-agent::hal0-mono/reflect")
    call = asyncio.create_task(app(_reflect_scope(path, body), receive, send))
    await asyncio.wait_for(started.wait(), timeout=5)
    assert not call.done(), "the handler must still be waiting on the engine"

    gone.set()
    await asyncio.wait_for(call, timeout=5)
    assert outcome == ["cancelled"]
    assert sent[0]["status"] == 499


# ── documents ───────────────────────────────────────────────────────────────


def _doc(doc_id: str, tags: list[str]) -> dict[str, Any]:
    return {"id": doc_id, "bank_id": BANK, "tags": tags, "text_length": 10}


def test_the_documents_listing_is_narrowed_to_the_project(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.documents = {
        "mine": _doc("mine", [*OWN_PROJECT, "source:upload"]),
        "codex": _doc("codex", [*OTHER_AGENT_PROJECT, "source:upload"]),
        "atlas": _doc("atlas", [*OTHER_PROJECT, "source:upload"]),
        "global": _doc("global", [*GLOBAL, "source:upload"]),
        "other-kind": _doc("other-kind", OWN_PROJECT),
    }
    resp = client.get(
        _url(URL_BANK + "/documents"),
        headers=_auth(key),
        # What the plugin sends, plus a project tag of its own.
        params=[
            ("tags", "source:upload"),
            ("tags", "project:someone-elses"),
            ("tags_match", "all"),
            ("limit", "500"),
            ("offset", "0"),
            ("time_field", "created_at"),
        ],
    )
    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == "hal0"
    call = engine.only("GET", "/documents")
    assert call.params == {
        "tags": ["project:hal0", "source:upload"],
        "tags_match": ["all_strict"],
        "limit": ["500"],
        "offset": ["0"],
    }
    assert [d["id"] for d in resp.json()["items"]] == ["mine", "codex"]
    assert "X-hal0-Filtered-Items" not in resp.headers


def test_the_documents_listing_hides_another_agents_private_document(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The engine's listing has no ``tag_groups``, so "not someone else's
    private document" is applied to the returned page in hal0."""
    engine.documents = {
        "mine": _doc("mine", OWN_PROJECT),
        "my-private": _doc("my-private", OWN_PRIVATE),
        "private:hermes:s-9": _doc("private:hermes:s-9", OTHER_AGENT_PRIVATE),
    }
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key))
    assert resp.status_code == 200
    page = resp.json()
    assert [d["id"] for d in page["items"]] == ["mine", "my-private"]
    assert resp.headers["X-hal0-Filtered-Items"] == "1"
    # ``total`` is the engine's count, before that step.
    assert page["total"] == 3


def test_a_listing_page_is_checked_even_if_the_engine_ignores_the_filter(
    client: TestClient, engine: Engine, key: str
) -> None:
    everything = [_doc("mine", OWN_PROJECT), _doc("atlas", OTHER_PROJECT), {"id": "no-tags"}]
    engine.overrides[("GET", ENGINE_BANK + "/documents")] = httpx.Response(
        200, json={"items": everything, "total": 3, "limit": 100, "offset": 0}
    )
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key))
    assert [d["id"] for d in resp.json()["items"]] == ["mine"]
    assert resp.headers["X-hal0-Filtered-Items"] == "2"


def test_an_unresolved_caller_lists_only_its_own_unresolved_documents(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.documents = {
        "mine": _doc("mine", OWN_UNRESOLVED),
        "codex": _doc("codex", OTHER_UNRESOLVED),
        "global": _doc("global", GLOBAL),
    }
    resp = client.get(_url("/v1/default/banks/claude-code/documents"), headers=_auth(key))
    assert resp.status_code == 200
    assert engine.only("GET", "/documents").params == {
        "tags": ["project:unresolved", "agent:cc"],
        "tags_match": ["all_strict"],
    }
    assert [d["id"] for d in resp.json()["items"]] == ["mine"]


@pytest.mark.parametrize(
    ("params", "ok"),
    [
        ([("tags", "a"), ("tags", "b"), ("tags_match", "all")], True),
        ([("tags", "a"), ("tags", "b"), ("tags_match", "all_strict")], True),
        ([("tags", "a"), ("tags_match", "any")], True),
        ([("tags", "a")], True),
        ([("tags", "a"), ("tags", "b"), ("tags_match", "any")], False),
        ([("tags", "a"), ("tags", "b")], False),
        ([("tags", "a"), ("tags_match", "exact")], False),
    ],
)
def test_a_filter_that_would_turn_the_project_into_an_alternative_is_refused(
    client: TestClient, engine: Engine, key: str, params: list[tuple[str, str]], ok: bool
) -> None:
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key), params=params)
    if ok:
        assert resp.status_code == 200
        sent = engine.only("GET", "/documents").params
        assert sent["tags"][0] == "project:hal0" and sent["tags_match"] == ["all_strict"]
    else:
        assert resp.status_code == 400
        assert _code(resp) == "memory.compat_filter_unsupported"
        assert engine.calls == []


def test_a_client_deletes_its_own_document_in_its_project(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    recorded: list[dict[str, Any]] = []

    class Audit:
        async def record(self, **row: Any) -> None:
            recorded.append(row)

    app.state.audit = Audit()
    doc_id = "conversation:0f3c1c1e/part 2"
    engine.documents[doc_id] = _doc(doc_id, [*OWN_PROJECT, "source:chat"])
    resp = client.delete(
        _url(URL_BANK + "/documents/conversation%3A0f3c1c1e%2Fpart%202"),
        headers={**_auth(key), "X-hal0-Agent": "hermes"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "message": "deleted",
        "document_id": doc_id,
        "memory_units_deleted": 3,
    }
    # Read first, then deleted, both on the platform bank.
    assert engine.lines() == [
        f"GET {ENGINE_BANK}/documents/{doc_id}",
        f"DELETE {ENGINE_BANK}/documents/{doc_id}",
    ]
    assert [c.read_timeout for c in engine.calls] == [30.0, 30.0]
    (row,) = recorded
    assert row["action"] == "memory.document.delete" and row["target"] == doc_id
    # The audit row names the credential, not the header.
    assert row["actor"] == "bridge:cc"


@pytest.mark.parametrize(
    "tags",
    [
        OTHER_AGENT_PROJECT,  # this project, another client
        OTHER_PROJECT,  # this client, another project
        OTHER_AGENT_PRIVATE,
        GLOBAL,
        OWN_UNRESOLVED,  # this client, but not this project
        [],
    ],
)
def test_a_document_that_is_not_the_callers_is_not_deleted(
    client: TestClient, engine: Engine, key: str, tags: list[str]
) -> None:
    engine.documents["doc-1"] = _doc("doc-1", tags)
    resp = client.delete(_url(URL_BANK + "/documents/doc-1"), headers=_auth(key))
    assert resp.status_code == 403
    assert _code(resp) == "memory.compat_document_not_owned"
    assert engine.lines() == [f"GET {ENGINE_BANK}/documents/doc-1"]


def test_an_unresolved_caller_deletes_only_its_own_unresolved_documents(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.documents = {
        "mine": _doc("mine", OWN_UNRESOLVED),
        "codex": _doc("codex", OTHER_UNRESOLVED),
        "in-a-project": _doc("in-a-project", OWN_PROJECT),
    }
    bank = "/v1/default/banks/claude-code/documents/"
    assert client.delete(_url(bank + "mine"), headers=_auth(key)).status_code == 200
    assert client.delete(_url(bank + "codex"), headers=_auth(key)).status_code == 403
    assert client.delete(_url(bank + "in-a-project"), headers=_auth(key)).status_code == 403
    assert [line for line in engine.lines() if line.startswith("DELETE")] == [
        f"DELETE {ENGINE_BANK}/documents/mine"
    ]


def test_deleting_a_document_the_engine_does_not_have_is_the_engines_404(
    client: TestClient, engine: Engine, key: str
) -> None:
    resp = client.delete(_url(URL_BANK + "/documents/gone"), headers=_auth(key))
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Document gone not found"}
    assert engine.lines() == [f"GET {ENGINE_BANK}/documents/gone"]


# ── operations, configuration, the bank ─────────────────────────────────────


def test_an_operation_read_never_asks_for_or_returns_a_payload(
    client: TestClient, engine: Engine, key: str
) -> None:
    op = "6f1d3b52-8f0e-5c41-a7d2-3e9c4b1f0a66"
    resp = client.get(
        _url(URL_BANK + f"/operations/{op}"), headers=_auth(key), params={"include_payload": "true"}
    )
    assert resp.status_code == 200
    call = engine.only("GET", f"/operations/{op}")
    assert call.params == {"include_payload": ["false"]}
    assert call.read_timeout == 15.0
    assert resp.json() == {
        "id": op,
        "operation_id": op,
        "task_type": "retain",
        "operation_type": "retain",
        "status": "failed",
        "created_at": "2026-11-10T09:14:02Z",
        "updated_at": "2026-11-10T09:14:09Z",
        "completed_at": "2026-11-10T09:14:09Z",
        "retry_count": 1,
        "error_message": "extraction model returned no JSON",
        "progress": {"stage": "extract", "at": "2026-11-10T09:14:05Z"},
        "child_operations": [{"operation_id": "c-1", "status": "failed", "items_count": 1}],
    }
    assert "ANOTHER CLIENT" not in resp.text and "private:hermes" not in resp.text


def test_the_operations_listing_is_statuses_only(client: TestClient, engine: Engine, key: str):
    resp = client.get(
        _url(URL_BANK + "/operations"),
        headers=_auth(key),
        params={"status": "pending", "limit": "50", "include_payload": "true"},
    )
    assert resp.status_code == 200
    assert engine.only("GET", "/operations").params == {"status": ["pending"], "limit": ["50"]}
    assert resp.json() == {
        "bank_id": BANK,
        "total": 1,
        "limit": 20,
        "offset": 0,
        "operations": [
            {
                "id": "op-1",
                "task_type": "retain",
                "status": "pending",
                "items_count": 1,
                "created_at": "2026-11-10T09:14:02Z",
            }
        ],
    }
    assert "ANOTHER CLIENT" not in resp.text and "private:hermes" not in resp.text


def test_the_bank_configuration_read_returns_missions_and_nothing_else(
    client: TestClient, engine: Engine, key: str
) -> None:
    resp = client.get(_url(URL_BANK + "/config"), headers=_auth(key))
    assert resp.status_code == 200
    assert engine.only("GET", "/config").read_timeout == 15.0
    assert resp.json() == {
        "bank_id": BANK,
        "config": {"reflect_mission": "Answer from memory."},
        "overrides": {"reflect_mission": "Answer from memory."},
    }
    assert "sk-engine-secret" not in resp.text and "llm" not in resp.text


def test_a_bank_template_is_accepted_and_ignored(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    template = {"version": "1", "bank": {"reflect_mission": "be someone else"}}
    for _ in range(2):
        resp = client.post(_url(URL_BANK + "/import"), headers=_auth(key), json=template)
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ignored",
            "reason": "bank configuration is managed by hal0",
        }
    assert engine.calls == []
    # Noted once per client, not once per configure pass.
    assert app.state.memory_compat.import_noted == {"cc"}


def test_a_bank_cannot_be_deleted(client: TestClient, engine: Engine, key: str) -> None:
    for bank in (URL_BANK, "/v1/default/banks/hal0%3Ashared", "/v1/default/banks/anything"):
        resp = client.delete(_url(bank), headers=_auth(key))
        assert resp.status_code == 403
        assert _code(resp) == "memory.compat_bank_delete_refused"
    admin = client.delete(_url(URL_BANK), headers=_auth(ADMIN_KEY))
    assert admin.status_code == 403
    assert engine.calls == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/knowledge-base/tree"),
        ("GET", "/knowledge-base/pages/kp-1"),
        ("GET", "/knowledge-base/search?q=release&limit=3"),
        ("POST", "/knowledge-base/pages"),
        ("PATCH", "/knowledge-base/nodes/kp-1"),
        ("POST", "/knowledge-base/folders"),
        ("DELETE", "/knowledge-base/nodes/kp-1"),
        ("GET", "/knowledge-base"),
    ],
)
def test_knowledge_base_routes_answer_pages_unsupported(
    client: TestClient, engine: Engine, key: str, method: str, path: str
) -> None:
    """404 on reads AND writes: the plugin takes it as "no pages here"."""
    resp = client.request(method, _url(URL_BANK + path), headers=_auth(key), json={"name": "x"})
    assert resp.status_code == 404
    assert _code(resp) == "memory.compat_knowledge_base_unsupported"
    assert engine.calls == []


def test_version_and_health_are_the_engines(client: TestClient, engine: Engine, key: str) -> None:
    version = client.get(_url("/version"), headers=_auth(key))
    assert version.json() == {"api_version": "1.2.0", "features": {"worker": True}}
    assert client.get(_url("/health"), headers=_auth(key)).json() == {"status": "healthy"}
    assert engine.lines() == ["GET /version", "GET /health"]
    assert [c.read_timeout for c in engine.calls] == [3.0, 3.0]


# ── everything else ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/v1/default/banks",
        URL_BANK + "/memories/list",
        URL_BANK + "/documents/doc-1",
        URL_BANK + "/documents/doc-1/chunks",
        URL_BANK + "/stats",
        URL_BANK + "/tags",
        URL_BANK + "/mental-models",
        URL_BANK + "/transfer/export",
        "/v1/bank-template-schema",
        "/openapi.json",
        "",
    ],
)
def test_an_unknown_read_is_404_and_lists_what_is_served(
    client: TestClient, engine: Engine, key: str, path: str
) -> None:
    resp = client.get(_url(path), headers=_auth(key))
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "memory.compat_route_unsupported"
    assert error["details"]["supported"] == list(memory_compat.SUPPORTED_ROUTES)
    assert engine.calls == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # The engine's own destructive and configuring routes.
        ("DELETE", URL_BANK + "/memories"),
        ("DELETE", URL_BANK + "/config"),
        ("PATCH", URL_BANK + "/config"),
        ("PUT", URL_BANK),
        ("PATCH", URL_BANK),
        ("DELETE", URL_BANK + "/operations/op-1"),
        ("POST", URL_BANK + "/operations/op-1/retry"),
        ("PATCH", URL_BANK + "/documents/doc-1"),
        ("POST", URL_BANK + "/documents/doc-1/reprocess"),
        ("POST", URL_BANK + "/transfer/import"),
        ("POST", URL_BANK + "/mental-models"),
        ("POST", URL_BANK + "/consolidate"),
        ("POST", URL_BANK + "/directives"),
        # A bank id with a slash in it is not a bank this endpoint knows: the
        # write is refused loudly, never answered 404.
        ("POST", "/v1/default/banks/team%2Frepo/memories"),
        ("POST", "/v1/default/banks"),
        ("POST", "/version"),
    ],
)
def test_an_unknown_mutation_is_405(
    client: TestClient, engine: Engine, key: str, method: str, path: str
) -> None:
    resp = client.request(method, _url(path), headers=_auth(key), json={"confirm": "shared"})
    assert resp.status_code == 405
    assert _code(resp) == "memory.compat_mutation_unsupported"
    assert engine.calls == []


def test_every_listed_route_is_really_served(client: TestClient, engine: Engine, key: str) -> None:
    engine.documents["d"] = _doc("d", OWN_PROJECT)
    unsupported = {"memory.compat_route_unsupported", "memory.compat_mutation_unsupported"}
    for line in memory_compat.SUPPORTED_ROUTES:
        method, template = line.split(" ", 1)
        path = (
            template.replace("{bank}", "coding-agent%3A%3Ahal0-mono")
            .replace("{document_id}", "d")
            .replace("{operation_id}", "op-1")
        )
        body = {"items": [_item()], "query": "why?"}
        resp = client.request(method, _url(path), headers=_auth(key), json=body)
        assert resp.status_code == 200, (line, resp.text)
        assert resp.json().get("error", {}).get("code") not in unsupported


def test_the_shipped_app_serves_the_endpoint(
    engine: Engine, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create_app()`` itself, with its whole middleware stack: the router is
    mounted where the exposure rule says, behind the catch-all nothing else
    under ``/api/memory`` is shadowed, and a retain reaches the engine."""
    from hal0.api import create_app

    monkeypatch.setattr("hal0.api._mount_dashboard", lambda _app: None)
    shipped = create_app()
    shipped.state.memory_provider = build_app(engine.handler).state.memory_provider
    box = TestClient(shipped, client=("127.0.0.1", 50000))

    assert box.get(_url("/version")).status_code == 401
    retain = box.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(tags=["source:chat"])], "async": True},
    )
    assert retain.status_code == 200, retain.text
    assert retain.headers["X-hal0-Project"] == "hal0"
    (item,) = engine.only("POST", "/memories").body["items"]
    assert item["tags"] == ["source:chat", "agent:cc", "kind:durable", "project:hal0"]

    unknown = box.get(_url("/v1/default/banks"), headers=_auth(key))
    assert (unknown.status_code, _code(unknown)) == (404, "memory.compat_route_unsupported")
    # A bridge key opens nothing else under /api/memory, and the endpoint's
    # catch-all did not swallow the routes beside it.
    assert box.get("/api/memory/list", headers=_auth(key)).status_code == 403
    listing = box.get("/api/memory/list", headers=_auth(ADMIN_KEY))
    assert listing.status_code != 404, listing.text


# ── the plugin's own requests ───────────────────────────────────────────────


def _contract_cases() -> list[Any]:
    cases = []
    for name in ("plugin-0.8.4.json", "plugin-0.8.6.json"):
        suite = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        for request in suite["requests"]:
            cases.append(pytest.param(request, id=f"{suite['version']}-{request['name']}"))
    return cases


@pytest.mark.parametrize("case", _contract_cases())
def test_the_plugins_requests_are_served(
    client: TestClient, engine: Engine, key: str, case: dict[str, Any]
) -> None:
    """Each request the plugin makes, as it makes it: only a bearer header, the
    bank in the URL, the engine's own paths under the mount prefix."""
    engine.strategies = ("conversation", "document")
    engine.documents["release-process"] = _doc("release-process", [*OWN_PROJECT, "source:upload"])
    resp = client.request(
        case["method"],
        _url(case["path"]),
        headers=_auth(key),
        json=case.get("body"),
    )
    expect = case["expect"]
    assert resp.status_code == expect["status"], resp.text

    served = [c for c in engine.calls if c.line != f"GET {ENGINE_BANK}/config"]
    if expect["engine"] is None:
        assert engine.calls == []
    else:
        assert engine.calls[-1].line == expect["engine"]
    # No engine call ever names the bank the plugin named.
    for call in served:
        assert call.path in ("/version", "/health") or call.path.startswith(ENGINE_BANK)
        assert "coding-agent" not in call.path
    if case["path"].endswith("/memories") and expect["status"] == 200:
        for item in engine.calls[-1].body["items"]:
            assert [t for t in item["tags"] if t.startswith("agent:")] == ["agent:cc"]
            assert [t for t in item["tags"] if t.startswith("project:")] == ["project:hal0"]
            assert item["metadata"]["bridge_client"] == "cc"
            assert item["observation_scopes"] == [["project:hal0"]]
            # The session id the plugin puts in metadata stays there, and the
            # document id is the plugin's own.
            sent = case["body"]["items"][0]
            assert item["document_id"] == sent["document_id"]
            assert item.get("strategy") == sent["strategy"]
        assert engine.calls[-1].body.get("operation_id") == case["body"].get("operation_id")
        assert engine.calls[-1].body["async"] is True
