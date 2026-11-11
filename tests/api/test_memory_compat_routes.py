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
import hashlib
import json
import re
import types
import uuid
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
from hal0.api.routes.memory_compat import _engine_operation_id
from hal0.bridge.store import BridgeStore, BridgeStoreError
from hal0.config.schema import MemoryCompatConfig, MemoryLanesConfig
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
    #: Engine operation id -> fields laid over the default status record.
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Ids the engine names itself, beside the one it was given.
    extra_operation_ids: list[str] = field(default_factory=list)
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
                started = [
                    body.get("operation_id") or "engine-chosen-id",
                    *self.extra_operation_ids,
                ]
                answer["operation_id"] = started[0]
                if len(started) > 1:
                    answer["operation_ids"] = started
                for operation_id in started:
                    self.operations.setdefault(operation_id, {})
            return httpx.Response(200, json=answer)
        if (method, rest) == ("POST", "/reflect"):
            return httpx.Response(200, json={"text": "the lockfile was stale", "usage": {"n": 1}})
        if (method, rest) == ("POST", "/memories/recall"):
            return httpx.Response(200, json={"results": self.recall_results, **self.recall_extras})
        if (method, rest) == ("GET", "/documents"):
            wanted = set(params.get("tags", []))
            items = [d for d in self.documents.values() if wanted <= set(d.get("tags", []))]
            limit = int(params.get("limit", ["100"])[0])
            offset = int(params.get("offset", ["0"])[0])
            page = {
                "items": items[offset : offset + limit],
                "total": len(items),
                "limit": limit,
                "offset": offset,
            }
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
            rows = [
                {**OPERATION_ROW, "id": operation_id, **fields}
                for operation_id, fields in self.operations.items()
            ]
            page = {"bank_id": BANK, "total": len(rows), "limit": 20, "offset": 0}
            return httpx.Response(200, json={**page, "operations": rows})
        if method == "GET" and rest.startswith("/operations/"):
            operation_id = rest[len("/operations/") :]
            if operation_id not in self.operations:
                return httpx.Response(404, json={"detail": f"Operation {operation_id} not found"})
            record = {**OPERATION_STATUS, "operation_id": operation_id, "id": operation_id}
            return httpx.Response(200, json={**record, **self.operations[operation_id]})
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

#: One row of the engine's operations list, before its id.
OPERATION_ROW = {
    "task_type": "retain",
    "items_count": 1,
    "document_id": "hal0::cc::conversation:s-1",
    "filename": "notes.md",
    "created_at": "2026-11-10T09:14:02Z",
    "status": "pending",
    "error_message": None,
    "task_payload": {"contents": [{"content": "ANOTHER CLIENT'S TEXT"}]},
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


def _compat_state(app: FastAPI) -> Any:
    """The endpoint's per-app state, as a handler reaches it."""
    return memory_compat._state(types.SimpleNamespace(app=app))


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
    operation = "6f1d3b52-8f0e-5c41-a7d2-3e9c4b1f0a66"
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
                    # A write here is never global, whatever the caller asks.
                    "scope:global",
                    " Scope:Global ",
                    "scope:team",
                ],
                strategy="conversation",
                observation_scopes=[["agent:hermes"]],
                timestamp="2026-11-10T09:14:02.000Z",
                update_mode="append",
                metadata={
                    "source": "chat",
                    "ref_id": "conversation:s-1",
                    "harness": "claude-code",
                    "project": "someone-elses",
                    "bridge_client": "hermes",
                    "client_source": "forged",
                    "client_document_id": "forged",
                },
            )
        ],
        "async": True,
        "operation_id": operation,
    }
    # The identity header the rest of the memory API reads is not read here.
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers={**_auth(key), "X-hal0-Agent": "hermes", "X-hal0-Private": "1"},
        json=body,
    )

    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == "hal0"
    # The engine's own answer, with the operation under the client's own id.
    assert resp.json() == {
        "success": True,
        "bank_id": BANK,
        "items_count": 1,
        "async": True,
        "operation_id": operation,
    }

    call = engine.only("POST", "/memories")
    (item,) = call.body["items"]
    # Caller agent, visibility, project and scope tags are gone; the client
    # name and the project the bank id resolved to (through the alias) are
    # stamped; the session id left the tag space; ``harness:`` and
    # ``source:`` stayed.
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
    # ``source`` is the client; what the plugin called its source is kept
    # beside it. The keys hal0 owns cannot be set by the caller.
    assert item["metadata"] == {
        "ref_id": "conversation:s-1",
        "harness": "claude-code",
        "client_source": "chat",
        "bridge_client": "cc",
        "source": "cc",
        "project": "hal0",
        "project_source": "bank",
        "session_id": "s-1",
    }
    # Stored in this client's namespace of this project.
    assert item["document_id"] == "hal0::cc::conversation:s-1"
    assert item["update_mode"] == "append"
    assert item["timestamp"] == "2026-11-10T09:14:02.000Z"
    assert item["context"] == "coding agent session"
    assert item["strategy"] == "conversation"
    assert item["content"] == body["items"][0]["content"]
    assert call.body["operation_id"] == _engine_operation_id("cc", operation)


@pytest.mark.parametrize("kind", ["kind:ephemeral", "kind:durable", None])
def test_kind_stays_the_callers_choice(
    client: TestClient, engine: Engine, key: str, kind: str | None
) -> None:
    tags = ["source:chat", *([kind] if kind else [])]
    resp = client.post(
        _url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item(tags=tags)]}
    )
    assert resp.status_code == 200
    sent = engine.only("POST", "/memories").body["items"][0]["tags"]
    assert [t for t in sent if t.startswith("kind:")] == [kind or "kind:durable"]
    # Durable or not, in a project or not, it is never global.
    assert "scope:global" not in sent


def test_an_operation_id_is_the_clients_own_and_the_engines_is_derived(
    client: TestClient, engine: Engine, key: str, store: BridgeStore
) -> None:
    """The plugin's resend relies on one id naming one operation. The engine
    is given an id derived from the client and the id it sent, so that still
    holds for one client, and no id one client picks can name another's."""
    _row, other_key = store.add_client("codex", created_by="t")
    sent = {"items": [_item()], "async": True, "operation_id": "not-even-a-uuid"}

    def engine_id(auth_key: str, body: dict[str, Any]) -> tuple[str | None, httpx.Response]:
        engine.calls.clear()
        resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(auth_key), json=body)
        return engine.only("POST", "/memories").body.get("operation_id"), resp

    first, resp = engine_id(key, sent)
    assert resp.json()["operation_id"] == "not-even-a-uuid"
    assert first == _engine_operation_id("cc", "not-even-a-uuid")
    assert str(uuid.UUID(first)) == first  # the engine requires a UUID
    assert engine.calls[-1].read_timeout == 30.0

    again, _ = engine_id(key, sent)
    assert again == first
    theirs, resp = engine_id(other_key, sent)
    assert theirs != first and resp.json()["operation_id"] == "not-even-a-uuid"

    # No id sent: the caller still gets one it can poll.
    fresh, resp = engine_id(key, {"items": [_item()], "async": True})
    given = resp.json()["operation_id"]
    assert str(uuid.UUID(given)) == given and fresh == _engine_operation_id("cc", given)

    # A synchronous retain has no operation: the engine ignores an id there,
    # and none is sent. No ``async`` means the engine's default, synchronous.
    for body in (
        {"items": [_item()], "operation_id": "x"},
        {"items": [_item()], "async": False, "operation_id": "x"},
    ):
        none, resp = engine_id(key, body)
        assert none is None and resp.status_code == 200
        assert set(engine.calls[-1].body) == {"items", *(["async"] if "async" in body else [])}
        assert engine.calls[-1].read_timeout == 300.0


@pytest.mark.parametrize(
    "body",
    [
        {"items": [_item()], "async": "yes"},
        {"items": [_item()], "operation_id": 7},
        {"items": [_item()], "operation_id": ""},
        {"items": [_item()], "async": True, "operation_id": "x" * 201},
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
    # A real project is its own observation scope; an unresolved write is
    # scoped to its writer, never to the untagged scope everyone reads.
    unresolved_scope = [["project:unresolved", "agent:cc"]]
    assert item["observation_scopes"] == (unresolved_scope if scopes else [[project_tag]])
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
    assert [item["document_id"] for item in sent] == [f"hal0::cc::doc-{n}" for n in range(32)]
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
    monkeypatch.setattr(memory_compat, "SMALL_RETAIN_BODY_BYTES", 1024)
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


# The engine upserts on document id, per bank, and every bank name here is one
# bank. The plugin picks ids as if it had a bank of its own, so each document
# id is stored inside the caller's namespace.


def _stored_ids(engine: Engine) -> list[str]:
    return [item["document_id"] for item in engine.only("POST", "/memories").body["items"]]


@pytest.mark.parametrize(
    ("bank", "client_id", "stored"),
    [
        # A client's own documents: its namespace inside the project.
        ("coding-agent%3A%3Ahal0-mono", "readme", "hal0::cc::readme"),
        ("coding-agent%3A%3Aatlas", "readme", "atlas::cc::readme"),
        ("coding-agent%3A%3Ahal0-mono", "conversation:0f3c", "hal0::cc::conversation:0f3c"),
        # Ids every agent on a repository derives the same way are shared.
        ("coding-agent%3A%3Ahal0-mono", "git:4f2a9c1", "hal0::shared::git:4f2a9c1"),
        ("coding-agent%3A%3Ahal0-mono", "gitlog:hal0-mono", "hal0::shared::gitlog:hal0-mono"),
        (
            "coding-agent%3A%3Ahal0-mono",
            "survey-baseline:4f2a",
            "hal0::shared::survey-baseline:4f2a",
        ),
        ("coding-agent%3A%3Ahal0-mono", "gitignore-notes", "hal0::cc::gitignore-notes"),
        # No project: a namespace of the client's own, and nothing shared.
        ("claude-code", "readme", "unresolved::cc::readme"),
        ("claude-code", "git:4f2a9c1", "unresolved::cc::git:4f2a9c1"),
        # An id that looks namespaced is the client's id, whole.
        ("coding-agent%3A%3Ahal0-mono", "atlas::codex::readme", "hal0::cc::atlas::codex::readme"),
        ("coding-agent%3A%3Ahal0-mono", "hal0::shared::git:x", "hal0::cc::hal0::shared::git:x"),
        ("coding-agent%3A%3Ahal0-mono", "::x", "hal0::cc::::x"),
        # The unified bank's reserved prefix is no longer at the front.
        ("coding-agent%3A%3Ahal0-mono", "private:hermes:s-9", "hal0::cc::private:hermes:s-9"),
    ],
)
def test_a_document_id_is_stored_in_the_callers_namespace(
    client: TestClient, engine: Engine, key: str, bank: str, client_id: str, stored: str
) -> None:
    resp = client.post(
        _url(f"/v1/default/banks/{bank}/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id=client_id)]},
    )
    assert resp.status_code == 200
    assert _stored_ids(engine) == [stored]
    # Nothing is looked up first: there is no document a client could take.
    assert engine.lines() == [f"POST {ENGINE_BANK}/memories"]
    assert "client_document_id" not in engine.calls[0].body["items"][0]["metadata"]


def test_two_clients_and_two_projects_never_share_a_document(
    client: TestClient, engine: Engine, key: str, store: BridgeStore
) -> None:
    _row, codex = store.add_client("codex", created_by="t")
    seen = set()
    for auth_key in (key, codex, ADMIN_KEY):
        for bank in ("coding-agent%3A%3Ahal0-mono", "coding-agent%3A%3Aatlas", "claude-code"):
            engine.calls.clear()
            client.post(
                _url(f"/v1/default/banks/{bank}/memories"),
                headers=_auth(auth_key),
                json={"items": [_item(document_id="readme", update_mode="replace")]},
            )
            seen.update(_stored_ids(engine))
    assert len(seen) == 9
    assert "hal0::admin::readme" in seen and "unresolved::codex::readme" in seen


def test_an_id_hal0_cannot_address_is_stored_as_a_digest(
    client: TestClient, engine: Engine, key: str
) -> None:
    """hal0's own bank routes take ids of at most 256 characters from
    ``[a-zA-Z0-9._:-]``. Anything else keeps its namespace and carries a
    digest; the client's id goes into metadata so it can be shown back."""
    odd = ["a title with spaces", "é", "x" * 400, "a/../b", "trailing..dots"]
    items = [_item(document_id=client_id) for client_id in odd]
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    assert resp.status_code == 200
    sent = engine.only("POST", "/memories").body["items"]
    for client_id, item in zip(odd, sent, strict=True):
        digest = hashlib.sha256(client_id.encode()).hexdigest()
        assert item["document_id"] == f"hal0::cc::sha256-{digest}"
        assert len(item["document_id"]) <= 256
        assert item["metadata"]["client_document_id"] == client_id


def test_an_item_with_no_document_id_gets_one_in_the_namespace(
    client: TestClient, engine: Engine, key: str
) -> None:
    items = [_item(), _item(metadata={"session_id": "s-1"}), _item(document_id="")]
    client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    stored = _stored_ids(engine)
    assert len(set(stored)) == 3
    assert all(re.fullmatch(r"hal0::cc::[0-9a-f]{32}", doc_id) for doc_id in stored)


def test_an_item_naming_several_projects_is_written_as_unresolved(
    client: TestClient, engine: Engine, key: str
) -> None:
    """A document has one namespace, so a write here has one project or none."""
    resp = client.post(
        _url("/v1/default/banks/claude-code/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id="d", tags=["project:hal0", "project:atlas", "x"])]},
    )
    assert resp.headers["X-hal0-Project"] == "unresolved"
    (item,) = engine.only("POST", "/memories").body["items"]
    assert item["tags"] == ["x", "agent:cc", "kind:durable", "project:unresolved"]
    assert item["document_id"] == "unresolved::cc::d"
    assert item["observation_scopes"] == [["project:unresolved", "agent:cc"]]


def test_the_shared_prefixes_are_the_operators_to_set(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    compat = MemoryCompatConfig(shared_document_prefixes=["adr:"])
    app.state.hal0_config = types.SimpleNamespace(memory=types.SimpleNamespace(compat=compat))
    items = [_item(document_id="adr:0058"), _item(document_id="git:4f2a9c1")]
    client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": items})
    assert _stored_ids(engine) == ["hal0::shared::adr:0058", "hal0::cc::git:4f2a9c1"]


def test_a_project_named_private_cannot_hold_documents(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The unified bank keeps ``private:``-prefixed ids for private documents,
    and a stored id starts with its project."""
    resp = client.post(
        _url("/v1/default/banks/claude-code/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id="d", tags=["project:private"])]},
    )
    assert (resp.status_code, _code(resp)) == (400, "memory.compat_document_id_reserved")
    assert engine.calls == []


def test_an_engine_refusal_never_shows_a_stored_id(
    client: TestClient, engine: Engine, key: str
) -> None:
    detail = {"detail": "document hal0::cc::readme cannot be appended to: hal0::shared::git:x"}
    engine.overrides[("POST", ENGINE_BANK + "/memories")] = httpx.Response(422, json=detail)
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id="readme")]},
    )
    assert resp.status_code == 422
    assert resp.json() == {"detail": "document readme cannot be appended to: git:x"}


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


def test_the_write_gate_refuses_retain_and_delete_with_the_typed_503(
    client: TestClient,
    engine: Engine,
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """While ``hal0 memory embedder switch`` holds the write gate, the two
    write routes refuse before any engine call with the same typed 503 and
    ``Retry-After`` as ``POST /api/memory/add``; reads still answer."""
    from hal0.memory import write_gate

    gate = tmp_path / "memory-writes-paused.json"
    monkeypatch.setattr(write_gate, "gate_path", lambda: gate)
    write_gate.engage_gate(run_id="run-1", path=gate)
    engine.documents["hal0::cc::doc-1"] = {
        "id": "hal0::cc::doc-1",
        "tags": ["agent:cc", "project:hal0"],
    }

    retain = client.post(
        _url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]}
    )
    batch = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(), _item()], "async": True},
    )
    delete = client.delete(_url(URL_BANK + "/documents/doc-1"), headers=_auth(key))
    for resp in (retain, batch, delete):
        assert (resp.status_code, _code(resp)) == (503, "memory.writes_paused")
        assert resp.headers["Retry-After"] == str(write_gate.RETRY_AFTER_S)
        assert resp.json()["error"]["details"]["reason"] == write_gate.REASON_EMBEDDER_SWITCH
    assert engine.calls == []
    assert "hal0::cc::doc-1" in engine.documents

    reflect = client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "why?"})
    assert reflect.status_code == 200

    write_gate.lift_gate(path=gate)
    again = client.delete(_url(URL_BANK + "/documents/doc-1"), headers=_auth(key))
    assert again.status_code < 300


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
    # The abandoned reflect gave both its slots back.
    assert _compat_state(app).in_flight == {}


# ── documents ───────────────────────────────────────────────────────────────


def _doc(doc_id: str, tags: list[str], **extra: Any) -> dict[str, Any]:
    return {"id": doc_id, "bank_id": BANK, "tags": tags, "text_length": 10, **extra}


def _seed(engine: Engine, *docs: dict[str, Any]) -> None:
    engine.documents = {doc["id"]: doc for doc in docs}


def test_the_documents_listing_shows_the_callers_namespaces_under_its_own_ids(
    client: TestClient, engine: Engine, key: str
) -> None:
    upload = "source:upload"
    _seed(
        engine,
        _doc("hal0::cc::readme", [*OWN_PROJECT, upload]),
        # Shared by every client of the project, whoever wrote it.
        _doc("hal0::shared::git:4f2a", [*OTHER_AGENT_PROJECT, upload]),
        # Another client's own namespace, in the same project.
        _doc("hal0::codex::readme", [*OTHER_AGENT_PROJECT, upload]),
        # One of hal0's own agents wrote into the project under its own id.
        _doc("hermes:s-9", ["agent:hermes", "kind:durable", "project:hal0", upload]),
        # Another project, and this client's own unresolved namespace.
        _doc("atlas::cc::readme", [*OTHER_PROJECT, upload]),
        _doc("unresolved::cc::readme", [*OWN_UNRESOLVED, upload]),
        _doc("hal0::cc::other-kind", OWN_PROJECT),
    )
    resp = client.get(
        _url(URL_BANK + "/documents"),
        headers=_auth(key),
        # What the plugin sends, plus a project tag of its own.
        params=[
            ("tags", upload),
            ("tags", "project:someone-elses"),
            ("tags_match", "all"),
            ("limit", "500"),
            ("offset", "0"),
            ("time_field", "created_at"),
        ],
    )
    assert resp.status_code == 200
    assert resp.headers["X-hal0-Project"] == "hal0"
    (call,) = engine.calls
    assert call.params == {
        "tags": ["project:hal0", upload],
        "tags_match": ["all_strict"],
        "limit": ["500"],
        "offset": ["0"],
    }
    assert [d["id"] for d in resp.json()["items"]] == ["readme", "git:4f2a"]
    assert resp.headers["X-hal0-Filtered-Items"] == "2"
    assert "::" not in resp.text


def test_the_documents_listing_hides_another_agents_private_document(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The engine's listing has no ``tag_groups``, so "not someone else's
    private document" is applied to the returned rows in hal0."""
    _seed(
        engine,
        _doc("hal0::cc::mine", OWN_PROJECT),
        _doc("hal0::cc::my-private", OWN_PRIVATE),
        _doc("hal0::shared::git:x", OTHER_AGENT_PRIVATE),
    )
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key))
    page = resp.json()
    assert [d["id"] for d in page["items"]] == ["mine", "my-private"]
    assert resp.headers["X-hal0-Filtered-Items"] == "1"


def test_a_listing_row_is_checked_even_if_the_engine_ignores_the_filter(
    client: TestClient, engine: Engine, key: str
) -> None:
    everything = [
        _doc("hal0::cc::mine", OWN_PROJECT),
        _doc("hal0::cc::mistagged", OTHER_PROJECT),
        {"id": "hal0::cc::no-tags"},
        "not a row",
    ]
    engine.overrides[("GET", ENGINE_BANK + "/documents")] = httpx.Response(
        200, json={"items": everything, "total": 4, "limit": 100, "offset": 0}
    )
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key))
    assert [d["id"] for d in resp.json()["items"]] == ["mine"]


def test_a_digested_id_is_shown_as_the_client_sent_it(
    client: TestClient, engine: Engine, key: str
) -> None:
    title = "a title with spaces"
    digest = hashlib.sha256(title.encode()).hexdigest()
    forged = hashlib.sha256(b"something else").hexdigest()
    _seed(
        engine,
        _doc(
            f"hal0::cc::sha256-{digest}",
            OWN_PROJECT,
            document_metadata={"client_document_id": title},
        ),
        # Metadata that does not map to the stored id is not believed.
        _doc(
            f"hal0::cc::sha256-{forged}",
            OWN_PROJECT,
            document_metadata={"client_document_id": title},
        ),
    )
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key))
    assert [d["id"] for d in resp.json()["items"]] == [title, f"sha256-{forged}"]


def test_a_short_page_is_filled_from_the_engine_while_it_has_more_rows(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The plugin stops paging at the first page shorter than ``limit``. Rows
    hal0 leaves out must not end its loop early."""
    mine = [_doc(f"hal0::cc::doc-{n:02d}", OWN_PROJECT) for n in range(7)]
    theirs = [_doc(f"hal0::codex::doc-{n:02d}", OTHER_AGENT_PROJECT) for n in range(5)]
    # Engine order: three of theirs, four of mine, two of theirs, three of mine.
    _seed(engine, *theirs[:3], *mine[:4], *theirs[3:], *mine[4:])

    def page(offset: int) -> httpx.Response:
        engine.calls.clear()
        return client.get(
            _url(URL_BANK + "/documents"),
            headers=_auth(key),
            params={"limit": "4", "offset": str(offset)},
        )

    first = page(0)
    assert [d["id"] for d in first.json()["items"]] == ["doc-00", "doc-01", "doc-02", "doc-03"]
    assert [c.params["offset"] for c in engine.calls] == [["0"], ["4"]]
    assert first.headers["X-hal0-Filtered-Items"] == "3"
    # The engine's count for the tag filter: an upper bound, never too low.
    assert first.json()["total"] == 12
    assert (first.json()["limit"], first.json()["offset"]) == (4, 0)
    assert "X-hal0-Listing-Incomplete" not in first.headers

    # Collecting ids page by page, as the plugin does, finds every document.
    ids: set[str] = set()
    offset = 0
    while True:
        items = page(offset).json()["items"]
        ids.update(d["id"] for d in items)
        if len(items) < 4:
            break
        offset += 4
    assert ids == {f"doc-{n:02d}" for n in range(7)}


def test_a_listing_that_cannot_be_filled_says_so(
    client: TestClient, engine: Engine, key: str
) -> None:
    theirs = [_doc(f"hal0::codex::doc-{n:02d}", OTHER_AGENT_PROJECT) for n in range(40)]
    _seed(engine, *theirs, _doc("hal0::cc::last", OWN_PROJECT))
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key), params={"limit": "5"})
    # One request and three more, then it stops reading.
    assert len(engine.calls) == 4
    assert resp.json()["items"] == []
    assert resp.headers["X-hal0-Listing-Incomplete"] == "1"
    assert resp.headers["X-hal0-Filtered-Items"] == "20"


@pytest.mark.parametrize(("asked", "sent"), [("5000", "500"), ("500", "500"), ("7", "7")])
def test_a_documents_page_is_at_most_the_plugins_page(
    client: TestClient, engine: Engine, key: str, asked: str, sent: str
) -> None:
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key), params={"limit": asked})
    assert resp.status_code == 200
    assert engine.calls[0].params["limit"] == [sent]
    assert resp.json()["limit"] == int(sent)


@pytest.mark.parametrize("params", [{"limit": "-1"}, {"limit": "many"}, {"offset": "1.5"}])
def test_a_malformed_page_is_400(
    client: TestClient, engine: Engine, key: str, params: dict[str, str]
) -> None:
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key), params=params)
    assert resp.status_code == 400
    assert engine.calls == []


def test_an_unresolved_caller_lists_only_its_own_unresolved_documents(
    client: TestClient, engine: Engine, key: str
) -> None:
    _seed(
        engine,
        _doc("unresolved::cc::mine", OWN_UNRESOLVED),
        _doc("unresolved::codex::theirs", OTHER_UNRESOLVED),
        _doc("hermes:s-1", GLOBAL),
    )
    resp = client.get(_url("/v1/default/banks/claude-code/documents"), headers=_auth(key))
    assert resp.status_code == 200
    assert engine.calls[0].params == {
        "tags": ["project:unresolved", "agent:cc"],
        "tags_match": ["all_strict"],
        "limit": ["100"],
        "offset": ["0"],
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
        sent = engine.calls[0].params
        assert sent["tags"][0] == "project:hal0" and sent["tags_match"] == ["all_strict"]
    else:
        assert resp.status_code == 400
        assert _code(resp) == "memory.compat_filter_unsupported"
        assert engine.calls == []


def test_a_client_deletes_its_own_document(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    recorded: list[dict[str, Any]] = []

    class Audit:
        async def record(self, **row: Any) -> None:
            recorded.append(row)

    app.state.audit = Audit()
    stored = "hal0::cc::conversation:0f3c1c1e"
    _seed(engine, _doc(stored, [*OWN_PROJECT, "source:chat"]))
    resp = client.delete(
        _url(URL_BANK + "/documents/conversation%3A0f3c1c1e"),
        headers={**_auth(key), "X-hal0-Agent": "hermes"},
    )
    assert resp.status_code == 200
    # The caller's own id comes back, never the stored one.
    assert resp.json() == {
        "success": True,
        "message": "document deleted",
        "document_id": "conversation:0f3c1c1e",
        "memory_units_deleted": 3,
    }
    # Read first, then deleted, both on the platform bank.
    assert engine.lines() == [
        f"GET {ENGINE_BANK}/documents/{stored}",
        f"DELETE {ENGINE_BANK}/documents/{stored}",
    ]
    assert [c.read_timeout for c in engine.calls] == [30.0, 30.0]
    (row,) = recorded
    # The audit row names the credential, not the header, and the stored id.
    assert (row["action"], row["target"], row["actor"]) == (
        "memory.document.delete",
        stored,
        "bridge:cc",
    )


def test_a_shared_document_is_deleted_only_by_the_client_that_wrote_it(
    client: TestClient, engine: Engine, key: str
) -> None:
    _seed(
        engine,
        _doc("hal0::shared::git:mine", OWN_PROJECT),
        _doc("hal0::shared::git:theirs", OTHER_AGENT_PROJECT),
    )
    mine = client.delete(_url(URL_BANK + "/documents/git:mine"), headers=_auth(key))
    theirs = client.delete(_url(URL_BANK + "/documents/git:theirs"), headers=_auth(key))
    assert mine.status_code == 200
    assert (theirs.status_code, _code(theirs)) == (403, "memory.compat_document_not_owned")
    assert [line for line in engine.lines() if line.startswith("DELETE")] == [
        f"DELETE {ENGINE_BANK}/documents/hal0::shared::git:mine"
    ]


@pytest.mark.parametrize(
    "client_id",
    [
        "gone",  # nothing by that id anywhere
        "theirs",  # another client's document in this project
        "hermes:s-9",  # one of hal0's own agents' documents
        "hal0::codex::theirs",  # spelling out someone else's stored id
        "atlas::cc::mine",  # this client's document in another project
        "unresolved::cc::mine",
    ],
)
def test_a_document_outside_the_callers_namespaces_does_not_exist(
    client: TestClient, engine: Engine, key: str, client_id: str
) -> None:
    """One answer for "no such document" and "not yours": nothing the caller
    sends can tell them apart, and nothing outside its namespaces is read."""
    _seed(
        engine,
        _doc("hal0::codex::theirs", OTHER_AGENT_PROJECT),
        _doc("hermes:s-9", GLOBAL),
        _doc("atlas::cc::mine", OTHER_PROJECT),
        _doc("unresolved::cc::mine", OWN_UNRESOLVED),
    )
    resp = client.delete(_url(URL_BANK + f"/documents/{client_id}"), headers=_auth(key))
    assert resp.status_code == 404
    assert resp.json()["error"] == {
        "code": "memory.compat_document_not_found",
        "message": "no such document",
        "details": {"document_id": client_id},
    }
    assert engine.lines() == [f"GET {ENGINE_BANK}/documents/hal0::cc::{client_id}"]
    assert len(engine.documents) == 4


def test_an_unresolved_caller_deletes_only_in_its_own_namespace(
    client: TestClient, engine: Engine, key: str
) -> None:
    _seed(
        engine,
        _doc("unresolved::cc::mine", OWN_UNRESOLVED),
        _doc("unresolved::codex::theirs", OTHER_UNRESOLVED),
        _doc("hal0::cc::in-a-project", OWN_PROJECT),
    )
    bank = "/v1/default/banks/claude-code/documents/"
    assert client.delete(_url(bank + "mine"), headers=_auth(key)).status_code == 200
    assert client.delete(_url(bank + "theirs"), headers=_auth(key)).status_code == 404
    assert client.delete(_url(bank + "in-a-project"), headers=_auth(key)).status_code == 404
    assert [line for line in engine.lines() if line.startswith("DELETE")] == [
        f"DELETE {ENGINE_BANK}/documents/unresolved::cc::mine"
    ]


# ── operations, configuration, the bank ─────────────────────────────────────


def _start(client: TestClient, key: str, operation: str | None = None) -> str:
    """One asynchronous retain; returns the operation id the caller was given."""
    body: dict[str, Any] = {"items": [_item()], "async": True}
    if operation is not None:
        body["operation_id"] = operation
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["operation_id"]


def test_a_client_polls_its_own_operation_under_its_own_id(
    client: TestClient, engine: Engine, key: str
) -> None:
    operation = _start(client, key, "my-op-1")
    engine.calls.clear()
    resp = client.get(
        _url(URL_BANK + "/operations/my-op-1"),
        headers=_auth(key),
        params={"include_payload": "true"},
    )
    assert resp.status_code == 200
    engine_id = _engine_operation_id("cc", "my-op-1")
    (call,) = engine.calls
    assert call.line == f"GET {ENGINE_BANK}/operations/{engine_id}"
    # The submitted payload is never asked for, whatever the caller sends.
    assert call.params == {"include_payload": ["false"]}
    assert call.read_timeout == 15.0
    assert operation == "my-op-1"
    assert resp.json() == {
        "id": "my-op-1",
        "operation_id": "my-op-1",
        "task_type": "retain",
        "operation_type": "retain",
        "status": "failed",
        "created_at": "2026-11-10T09:14:02Z",
        "updated_at": "2026-11-10T09:14:09Z",
        "completed_at": "2026-11-10T09:14:09Z",
        "retry_count": 1,
        "error_message": "extraction model returned no JSON",
        "progress": {"stage": "extract", "at": "2026-11-10T09:14:05Z"},
        # Children are counted, not named: their ids are the engine's.
        "child_operations": [{"status": "failed", "items_count": 1}],
    }
    assert engine_id not in resp.text
    assert "ANOTHER CLIENT" not in resp.text and "private:hermes" not in resp.text


def test_an_operation_error_is_cut_short(client: TestClient, engine: Engine, key: str) -> None:
    _start(client, key, "my-op-1")
    engine.operations[_engine_operation_id("cc", "my-op-1")]["error_message"] = "x" * 5000
    resp = client.get(_url(URL_BANK + "/operations/my-op-1"), headers=_auth(key))
    assert len(resp.json()["error_message"]) == memory_compat.MAX_ERROR_MESSAGE_LENGTH == 500


def test_another_clients_operation_does_not_exist(
    client: TestClient, engine: Engine, key: str, store: BridgeStore
) -> None:
    """Unknown, another client's, and one of hal0's own: one answer, 404."""
    _row, codex = store.add_client("codex", created_by="t")
    _start(client, key, "my-op-1")
    theirs = _engine_operation_id("cc", "my-op-1")
    engine.operations["0d9b6c0a-41f7-4e7c-b3a8-52c1e8f97d24"] = {"status": "pending"}

    answers = []
    for asked in (
        "my-op-1",  # the same caller-chosen id, from another client
        theirs,  # the engine's id for it, if it leaked
        "0d9b6c0a-41f7-4e7c-b3a8-52c1e8f97d24",  # an operation hal0's own agents started
        "never-started",
    ):
        resp = client.get(_url(URL_BANK + f"/operations/{asked}"), headers=_auth(codex))
        assert resp.status_code == 404, asked
        error = resp.json()["error"]
        assert error["code"] == "memory.compat_operation_not_found"
        answers.append((error["message"], sorted(error["details"])))
    assert len(set(map(str, answers))) == 1
    # The owner still reads it, and cannot read it by the engine's id either.
    assert client.get(_url(URL_BANK + "/operations/my-op-1"), headers=_auth(key)).status_code == 200
    assert (
        client.get(_url(URL_BANK + f"/operations/{theirs}"), headers=_auth(key)).status_code == 404
    )


def test_an_engine_that_answers_not_found_in_the_body_is_404(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine_id = _engine_operation_id("cc", "gone")
    engine.overrides[("GET", f"{ENGINE_BANK}/operations/{engine_id}")] = httpx.Response(
        200, json={"operation_id": engine_id, "status": "not_found"}
    )
    resp = client.get(_url(URL_BANK + "/operations/gone"), headers=_auth(key))
    assert (resp.status_code, _code(resp)) == (404, "memory.compat_operation_not_found")


def test_a_poll_needs_no_memory_of_who_started_what(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    """After a hal0-api restart the caller's own recent operations still
    answer: the engine id is derived from the client and its id, not looked
    up. The listing is what a restart empties."""
    _start(client, key, "before-restart")
    app.state.memory_compat = None  # what a restart leaves
    assert (
        client.get(_url(URL_BANK + "/operations/before-restart"), headers=_auth(key)).status_code
        == 200
    )
    listing = client.get(_url(URL_BANK + "/operations"), headers=_auth(key))
    assert listing.json()["operations"] == []


def test_the_operations_listing_is_the_callers_own(
    client: TestClient, engine: Engine, key: str, store: BridgeStore
) -> None:
    _row, codex = store.add_client("codex", created_by="t")
    _start(client, key, "mine-1")
    _start(client, codex, "theirs-1")
    given = _start(client, key)  # no id sent: the caller is handed one
    engine.operations["0d9b6c0a-41f7-4e7c-b3a8-52c1e8f97d24"] = {
        "status": "pending",
        "error_message": "ANOTHER CLIENT'S ERROR",
    }
    engine.calls.clear()
    resp = client.get(
        _url(URL_BANK + "/operations"),
        headers=_auth(key),
        params={"limit": "50", "include_payload": "true"},
    )
    assert resp.status_code == 200
    # The engine's list, which covers every client, is never read: each of
    # the caller's own unfinished operations is read by its id.
    assert sorted(engine.lines()) == sorted(
        f"GET {ENGINE_BANK}/operations/{_engine_operation_id('cc', op)}" for op in ("mine-1", given)
    )
    assert all(c.params == {"include_payload": ["false"]} for c in engine.calls)
    page = resp.json()
    assert set(page) == {"bank_id", "total", "limit", "offset", "operations"}
    assert (page["total"], page["limit"], page["offset"]) == (2, 50, 0)
    # Newest first, under the caller's own ids.
    assert [op["id"] for op in page["operations"]] == [given, "mine-1"]
    assert "theirs-1" not in resp.text and "ANOTHER CLIENT" not in resp.text
    assert _engine_operation_id("cc", "mine-1") not in resp.text
    assert "hal0::" not in resp.text and "private:hermes" not in resp.text

    theirs = client.get(_url(URL_BANK + "/operations"), headers=_auth(codex)).json()
    assert [op["id"] for op in theirs["operations"]] == ["theirs-1"]


def test_the_operations_listing_pages_over_the_callers_own_operations_only(
    client: TestClient, engine: Engine, key: str, store: BridgeStore
) -> None:
    """A caller's ``offset`` and ``limit`` index into its own operations. They
    are never sent to the engine, where they would index into everyone's, and
    the caller's operations are found however many others there are."""
    _row, codex = store.add_client("codex", created_by="t")
    for n in range(3):
        _start(client, key, f"mine-{n}")
    for n in range(150):  # more than one engine page of someone else's
        engine.operations[f"00000000-0000-4000-8000-{n:012d}"] = {"status": "pending"}
    _start(client, codex, "theirs")
    for n in range(3, 6):
        _start(client, key, f"mine-{n}")
    engine.operations[_engine_operation_id("cc", "mine-4")]["status"] = "completed"

    def listing(**params: str) -> dict[str, Any]:
        engine.calls.clear()
        resp = client.get(_url(URL_BANK + "/operations"), headers=_auth(key), params=params)
        assert resp.status_code == 200
        assert all("offset" not in c.params and "limit" not in c.params for c in engine.calls)
        return resp.json()

    everything = listing()
    assert [op["id"] for op in everything["operations"]] == [
        f"mine-{n}" for n in (5, 4, 3, 2, 1, 0)
    ]
    assert everything["total"] == 6
    second = listing(limit="2", offset="2")
    assert [op["id"] for op in second["operations"]] == ["mine-3", "mine-2"]
    assert (second["total"], second["limit"], second["offset"]) == (6, 2, 2)
    assert [op["id"] for op in listing(status="completed")["operations"]] == ["mine-4"]
    assert listing(type="consolidation")["operations"] == []
    # A finished operation is not read again.
    assert _engine_operation_id("cc", "mine-4") not in "".join(engine.lines())


def test_one_clients_operations_cannot_push_out_anothers(
    client: TestClient,
    engine: Engine,
    key: str,
    store: BridgeStore,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What is remembered is bounded per client. A client that churns through
    operations loses its own oldest finished ones, and says so; another
    client's are untouched."""
    monkeypatch.setattr(memory_compat, "OPERATIONS_REMEMBERED_PER_CLIENT", 3)
    _row, codex = store.add_client("codex", created_by="t")
    _start(client, key, "mine")
    for n in range(8):
        operation = _start(client, codex, f"churn-{n}")
        engine.operations[_engine_operation_id("codex", operation)]["status"] = "completed"
        client.get(_url(URL_BANK + f"/operations/{operation}"), headers=_auth(codex))
    state = _compat_state(app)
    assert len(state.operations_of("codex")) == 3 and len(state.operations_of("cc")) == 1
    mine = client.get(_url(URL_BANK + "/operations"), headers=_auth(key))
    assert [op["id"] for op in mine.json()["operations"]] == ["mine"]
    assert "X-hal0-Listing-Incomplete" not in mine.headers
    theirs = client.get(_url(URL_BANK + "/operations"), headers=_auth(codex))
    assert [op["id"] for op in theirs.json()["operations"]] == ["churn-7", "churn-6", "churn-5"]
    assert theirs.headers["X-hal0-Listing-Incomplete"] == "1"


def test_an_operation_error_never_shows_a_stored_id(
    client: TestClient, engine: Engine, key: str
) -> None:
    _start(client, key, "my-op-1")
    engine.operations[_engine_operation_id("cc", "my-op-1")]["error_message"] = (
        "document hal0::cc::conversation:s-1 failed; see atlas::shared::git:4f2a too"
    )
    for path in ("/operations/my-op-1", "/operations"):
        resp = client.get(_url(URL_BANK + path), headers=_auth(key))
        assert "document conversation:s-1 failed; see git:4f2a too" in resp.text
        assert "::" not in resp.text.replace("hal0:shared", "")


def test_an_operation_the_engine_named_itself_is_pollable_by_the_client_it_was_handed_to(
    client: TestClient, engine: Engine, key: str, store: BridgeStore
) -> None:
    """Items with different strategies start one operation each; the engine
    names the extra ones. They are returned as they are and remembered."""
    _row, codex = store.add_client("codex", created_by="t")
    extra = "5a0c2f3e-91b7-4c1d-8e55-0f6a7d2b9c41"
    engine.extra_operation_ids = [extra]
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(), _item()], "async": True, "operation_id": "batch-1"},
    )
    assert resp.json()["operation_id"] == "batch-1"
    assert resp.json()["operation_ids"] == ["batch-1", extra]
    assert (
        client.get(_url(URL_BANK + f"/operations/{extra}"), headers=_auth(key)).status_code == 200
    )
    assert (
        client.get(_url(URL_BANK + f"/operations/{extra}"), headers=_auth(codex)).status_code == 404
    )


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
    _seed(engine, _doc("hal0::cc::d", OWN_PROJECT))
    engine.operations[_engine_operation_id("cc", "op-1")] = {}
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


# ── one client's unresolved writes stay its own ─────────────────────────────


@pytest.mark.parametrize("route", ["/memories/recall", "/reflect"])
def test_an_unresolved_write_is_not_readable_by_another_client(
    client: TestClient, engine: Engine, key: str, store: BridgeStore, route: str
) -> None:
    """Key A writes with no project. Neither the fact nor the observation the
    engine builds from it (which carries exactly the scope's tags) is inside
    key B's read, in a project or out of one; both are inside key A's."""
    _row, other = store.add_client("codex", created_by="t")
    client.post(
        _url("/v1/default/banks/claude-code/memories"),
        headers=_auth(key),
        json={"items": [_item(tags=["source:chat"])]},
    )
    (item,) = engine.only("POST", "/memories").body["items"]
    fact = item["tags"]
    (observation,) = item["observation_scopes"]
    assert observation == ["project:unresolved", "agent:cc"]

    def groups(auth_key: str, bank: str) -> list[dict[str, Any]]:
        engine.calls.clear()
        resp = client.post(
            _url(f"/v1/default/banks/{bank}{route}"), headers=_auth(auth_key), json={"query": "q"}
        )
        assert resp.status_code == 200
        return engine.only("POST", route).body["tag_groups"]

    for bank in ("claude-code", "coding-agent%3A%3Ahal0-mono", "coding-agent%3A%3Atmp"):
        theirs = groups(other, bank)
        assert not _readable(theirs, fact) and not _readable(theirs, observation), bank
    assert not _readable(groups(ADMIN_KEY, "claude-code"), observation)
    mine = groups(key, "claude-code")
    assert _readable(mine, fact) and _readable(mine, observation)
    # Inside a project the writer does not read its unresolved writes either.
    in_project = groups(key, "coding-agent%3A%3Ahal0-mono")
    assert not _readable(in_project, fact) and not _readable(in_project, observation)


def test_recall_shows_ids_as_the_caller_knows_them(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.recall_results = [
        {
            "id": "f-1",
            "text": "mine",
            "tags": OWN_PROJECT,
            "document_id": "hal0::cc::conversation:s_1",
            "chunk_id": "hal0:shared_hal0::cc::conversation:s~5F1_0",
            "metadata": {"bridge_client": "cc", "session_id": "s_1"},
            "context": "coding agent session",
        },
        {
            "id": "f-2",
            "text": "a shared document this client wrote",
            "tags": OWN_PROJECT,
            "document_id": "hal0::shared::git:4f2a",
            "chunk_id": "hal0:shared_hal0::shared::git:4f2a_2",
        },
        {
            "id": "f-3",
            "text": "another client's, readable because it is this project's",
            "tags": OTHER_AGENT_PROJECT,
            "document_id": "hal0::codex::readme",
            "chunk_id": "hal0:shared_hal0::codex::readme_0",
        },
    ]
    engine.recall_extras = {
        "chunks": {
            "hal0:shared_hal0::cc::conversation:s~5F1_0": {"text": "chunk of mine"},
            "hal0:shared_hal0::codex::readme_0": {"text": "chunk of theirs"},
        }
    }
    resp = client.post(
        _url(URL_BANK + "/memories/recall"),
        headers=_auth(key),
        json={"query": "q", "include": {"chunks": {}}},
    )
    results = resp.json()["results"]
    assert [r["document_id"] for r in results] == ["conversation:s_1", "git:4f2a", None]
    assert [r["chunk_id"] for r in results[:2]] == [
        "hal0:shared_conversation:s~5F1_0",
        "hal0:shared_git:4f2a_2",
    ]
    assert results[2]["chunk_id"].startswith("chunk-")
    # The caller's own row is whole.
    assert results[0]["metadata"] == {"bridge_client": "cc", "session_id": "s_1"}
    assert results[0]["context"] == "coding agent session"
    # Only the caller's own chunk comes back.
    assert resp.json()["chunks"] == {"hal0:shared_conversation:s~5F1_0": {"text": "chunk of mine"}}
    assert "::cc::" not in resp.text and "::codex::" not in resp.text
    assert "::shared::" not in resp.text


# ── what a client learns about another client ───────────────────────────────

# Client A, with a name, a session and document ids that appear nowhere else.
PEER = "alpha-agent"
PEER_SESSION = "0f3c1c1e-5b7a-4a52-9d0e-6c2f1a9b8e77"
PEER_TAGS = [
    "team:their-own-word",
    "source:chat",
    "harness:claude-code",
    f"agent:{PEER}",
    "kind:durable",
    "project:hal0",
    "knowledge:decision",
    f"source:bridge:{PEER}",
]
PEER_METADATA = {
    "bridge_client": PEER,
    "source": PEER,
    "client_source": "chat",
    "client_document_id": "their title with spaces",
    "session_id": PEER_SESSION,
    "ref_id": f"conversation:{PEER_SESSION}",
    "project": "hal0",
}
PEER_STRINGS = (
    PEER,
    PEER_SESSION,
    "their-own-word",
    "their title with spaces",
    "sha256-",
    "::",
    "claude-code",
)


def _no_trace_of_the_peer(resp: httpx.Response) -> None:
    text = resp.text.replace('"bank_id":"hal0:shared"', "")
    for needle in PEER_STRINGS:
        assert needle not in text, needle


def test_a_recall_says_nothing_about_who_wrote_another_clients_memory(
    client: TestClient, engine: Engine, key: str
) -> None:
    """Key B reads project memory key A wrote. It gets the content, what kind
    of memory it is and when; not A's name, document id, session id, context
    or vocabulary. An allow-list: a field hal0 does not name is not returned."""
    digest = hashlib.sha256(b"their title with spaces").hexdigest()
    peer_fact = {
        "id": "f-1",
        "text": "the release is cut from main after the nightly passes",
        "type": "world",
        "tags": PEER_TAGS,
        "document_id": f"hal0::{PEER}::conversation:{PEER_SESSION}",
        "chunk_id": f"hal0:shared_hal0::{PEER}::conversation:{PEER_SESSION}_0",
        "metadata": PEER_METADATA,
        "context": f"{PEER} conversation turn",
        "entities": [PEER, "release"],
        "occurred_start": "2026-11-10T09:14:02Z",
        "mentioned_at": "2026-11-10T09:14:02Z",
        "scores": {"final": 0.9},
        "source_fact_ids": ["sf-1"],
        "some_future_field": PEER,
    }
    engine.recall_results = [
        peer_fact,
        {**peer_fact, "id": "f-2", "document_id": f"hal0::{PEER}::sha256-{digest}"},
        {**peer_fact, "id": "f-3", "document_id": "hal0::shared::git:4f2a"},
        # One of hal0's own agents, by an id of its own.
        {**peer_fact, "id": "f-4", "document_id": f"{PEER}:{PEER_SESSION}", "tags": [*GLOBAL]},
    ]
    engine.recall_extras = {
        "chunks": {peer_fact["chunk_id"]: {"text": f'{{"ref":"conversation:{PEER_SESSION}"}}'}},
        "source_facts": {"sf-1": {**peer_fact, "id": "sf-1", "text": "an earlier fact"}},
        "entities": {PEER: {"observations": ["wrote a lot"]}},
    }
    resp = client.post(
        _url(URL_BANK + "/memories/recall"),
        headers=_auth(key),
        json={"query": "q", "include": {"chunks": {}, "source_facts": {}}},
    )
    assert resp.status_code == 200
    _no_trace_of_the_peer(resp)
    body = resp.json()
    assert len(body["results"]) == 4
    first = body["results"][0]
    assert set(first) == {
        "id",
        "text",
        "type",
        "tags",
        "document_id",
        "chunk_id",
        "occurred_start",
        "mentioned_at",
    }
    assert first["text"] == peer_fact["text"]
    assert first["document_id"] is None and first["chunk_id"].startswith("chunk-")
    assert first["tags"] == ["source:chat", "kind:durable", "project:hal0", "knowledge:decision"]
    assert body["results"][3]["tags"] == ["kind:durable", "scope:global"]
    assert body["chunks"] == {} and body["entities"] is None
    # A source fact is named by ``source_fact_ids``, which is not returned for
    # a result that is not the caller's, so nothing names this one.
    assert body["source_facts"] == {}

    # The same fact under one of the caller's own results: also allow-listed.
    own = {"id": "f-9", "text": "mine", "tags": OWN_PROJECT, "source_fact_ids": ["sf-1"]}
    engine.recall_results = [own]
    resp = client.post(
        _url(URL_BANK + "/memories/recall"),
        headers=_auth(key),
        json={"query": "q", "include": {"source_facts": {}}},
    )
    _no_trace_of_the_peer(resp)
    assert resp.json()["results"] == [own]
    assert set(resp.json()["source_facts"]["sf-1"]) == set(first)


def test_a_listing_says_nothing_about_who_last_wrote_a_shared_document(
    client: TestClient, engine: Engine, key: str
) -> None:
    long_id = "git:" + "f" * 300
    digest = hashlib.sha256(long_id.encode()).hexdigest()
    row = {
        "tags": PEER_TAGS,
        "document_metadata": PEER_METADATA,
        "retain_params": {"context": f"{PEER} conversation turn"},
        "content_hash": "abc",
        "created_at": "2026-11-10T09:14:02Z",
        "updated_at": "2026-11-10T09:15:00Z",
        "text_length": 120,
        "memory_unit_count": 4,
        "some_future_field": PEER,
    }
    _seed(
        engine,
        {**_doc("hal0::shared::git:4f2a", PEER_TAGS), **row},
        # A long shared id: its digest form keeps the shared prefix, and the
        # writer's own spelling of it is not read from the writer's metadata.
        {
            **_doc(f"hal0::shared::git:sha256-{digest}", PEER_TAGS),
            **row,
            "document_metadata": {**PEER_METADATA, "client_document_id": "their title with spaces"},
        },
        {**_doc(f"hal0::{PEER}::readme", PEER_TAGS), **row},
        _doc(
            "hal0::cc::mine", [*OWN_PROJECT, "team:mine"], document_metadata={"bridge_client": "cc"}
        ),
    )
    resp = client.get(_url(URL_BANK + "/documents"), headers=_auth(key))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [d["id"] for d in items] == ["git:4f2a", f"git:sha256-{digest}", "mine"]
    shared = items[0]
    assert shared == {
        "id": "git:4f2a",
        "created_at": "2026-11-10T09:14:02Z",
        "updated_at": "2026-11-10T09:15:00Z",
        "tags": ["source:chat", "kind:durable", "project:hal0", "knowledge:decision"],
    }
    # The caller's own document is whole.
    assert items[2]["tags"] == [*OWN_PROJECT, "team:mine"]
    assert items[2]["document_metadata"] == {"bridge_client": "cc"}
    text = resp.text
    for needle in (PEER, PEER_SESSION, "their-own-word", "their title with spaces", "::"):
        assert needle not in text.replace('"bank_id":"hal0:shared"', ""), needle


def test_a_long_shared_id_round_trips_through_the_shared_namespace(
    client: TestClient, engine: Engine, key: str, store: BridgeStore
) -> None:
    """A shared id too long to store is kept as ``<shared prefix>sha256-...``
    in the SHARED namespace, so the form a listing shows another client maps
    back to the same document. Without the kept prefix it would map into the
    sender's own namespace: a second document, and a delete that finds none."""
    _row, codex = store.add_client("codex", created_by="t")
    long_id = "git:" + "f" * 300
    digest = hashlib.sha256(long_id.encode()).hexdigest()
    stored = f"hal0::shared::git:sha256-{digest}"

    client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id=long_id)]},
    )
    (item,) = engine.only("POST", "/memories").body["items"]
    assert item["document_id"] == stored
    _seed(engine, _doc(stored, item["tags"], document_metadata=item["metadata"]))

    # The writer sees its own id back; another client sees the digest form.
    mine = client.get(_url(URL_BANK + "/documents"), headers=_auth(key)).json()["items"]
    theirs = client.get(_url(URL_BANK + "/documents"), headers=_auth(codex)).json()["items"]
    assert [d["id"] for d in mine] == [long_id]
    assert [d["id"] for d in theirs] == [f"git:sha256-{digest}"]

    # Either spelling, from either client, is the one shared document.
    for auth_key, spelling in ((codex, f"git:sha256-{digest}"), (codex, long_id), (key, long_id)):
        engine.calls.clear()
        client.post(
            _url(URL_BANK + "/memories"),
            headers=_auth(auth_key),
            json={"items": [_item(document_id=spelling)]},
        )
        assert engine.only("POST", "/memories").body["items"][0]["document_id"] == stored
    engine.calls.clear()
    deleted = client.delete(_url(URL_BANK + f"/documents/git:sha256-{digest}"), headers=_auth(key))
    assert deleted.status_code == 200
    assert engine.lines()[-1] == f"DELETE {ENGINE_BANK}/documents/{stored}"


def test_a_reflect_answer_names_no_stored_document(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.overrides[("POST", ENGINE_BANK + "/reflect")] = httpx.Response(
        200,
        json={
            "text": "the lockfile was stale",
            "structured_output": {"cause": "lockfile"},
            "usage": {"n": 1},
            "based_on": {"memories": [{"document_id": "hal0::codex::readme"}]},
            "trace": {"tool_calls": ["search hal0::codex::readme"]},
        },
    )
    resp = client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "why?"})
    assert resp.json() == {
        "text": "the lockfile was stale",
        "structured_output": {"cause": "lockfile"},
        "usage": {"n": 1},
    }


# ── what one request, and one client, may cost ──────────────────────────────


def test_an_item_larger_than_the_cap_is_413(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    assert MemoryCompatConfig().max_item_bytes == 16 * 1024 * 1024
    compat = MemoryCompatConfig(max_item_bytes=2048)
    app.state.hal0_config = types.SimpleNamespace(memory=types.SimpleNamespace(compat=compat))
    for content in ("x" * 2049, "é" * 1025):
        resp = client.post(
            _url(URL_BANK + "/memories"),
            headers=_auth(key),
            json={"items": [_item(), _item(content=content)]},
        )
        assert (resp.status_code, _code(resp)) == (413, "memory.compat_item_too_large")
        assert resp.json()["error"]["details"] == {"index": 1, "max_bytes": 2048}
    assert engine.calls == []
    fits = {"items": [_item(content="x" * 2048), _item(content="é" * 1024)]}
    assert (
        client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=fits).status_code == 200
    )


@pytest.mark.parametrize("route", ["/reflect", "/memories/recall"])
@pytest.mark.parametrize("budget", ["max", "HIGH", "", 3, ["high"]])
def test_a_budget_outside_the_engines_enum_is_400(
    client: TestClient, engine: Engine, key: str, route: str, budget: Any
) -> None:
    resp = client.post(
        _url(URL_BANK + route), headers=_auth(key), json={"query": "q", "budget": budget}
    )
    assert resp.status_code == 400
    assert engine.calls == []


@pytest.mark.parametrize(
    ("route", "cap"),
    [
        ("/reflect", memory_compat.MAX_REFLECT_TOKENS),
        ("/memories/recall", memory_compat.MAX_RECALL_TOKENS),
    ],
)
def test_max_tokens_is_lowered_to_the_cap(
    client: TestClient, engine: Engine, key: str, route: str, cap: int
) -> None:
    for asked, sent in ((10**9, cap), (cap, cap), (512, 512)):
        engine.calls.clear()
        resp = client.post(
            _url(URL_BANK + route),
            headers=_auth(key),
            json={"query": "q", "max_tokens": asked, "budget": "high"},
        )
        assert resp.status_code == 200
        assert engine.only("POST", route).body["max_tokens"] == sent
    assert (memory_compat.MAX_REFLECT_TOKENS, memory_compat.MAX_RECALL_TOKENS) == (8192, 16384)


def _busy(resp: httpx.Response, retry_after: str) -> None:
    assert resp.status_code == 429, resp.text
    assert _code(resp) == "memory.compat_busy"
    # The plugin honours 429 with Retry-After.
    assert resp.headers["Retry-After"] == retry_after
    assert resp.json()["error"]["details"] == {"retry_after_s": int(retry_after)}


def test_one_client_runs_one_reflect_at_a_time(
    client: TestClient, engine: Engine, key: str, store: BridgeStore, app: FastAPI
) -> None:
    _row, other = store.add_client("codex", created_by="t")
    state = _compat_state(app)
    state.in_flight[("reflect", "cc")] = 1
    _busy(client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "q"}), "15")
    assert engine.calls == []
    # Another client is not held up by it, and the slot is given back.
    assert (
        client.post(
            _url(URL_BANK + "/reflect"), headers=_auth(other), json={"query": "q"}
        ).status_code
        == 200
    )
    assert state.in_flight == {("reflect", "cc"): 1}


def test_all_clients_together_run_a_configured_number_of_reflects(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    assert MemoryCompatConfig().max_concurrent_reflects == 2
    roomy = types.SimpleNamespace(llm_max_concurrent=8, reflect=None)
    _configure(app, roomy)
    state = _compat_state(app)
    state.in_flight[("reflect", "*")] = 2
    _busy(client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "q"}), "15")
    assert engine.calls == []
    # The refused request holds no slot of its own afterwards.
    assert state.in_flight == {("reflect", "*"): 2}

    _configure(app, roomy, max_concurrent_reflects=3)
    assert (
        client.post(
            _url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "q"}
        ).status_code
        == 200
    )


def test_one_client_runs_a_few_retains_at_a_time(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    state = _compat_state(app)
    state.in_flight[("retain", "cc")] = memory_compat.RETAINS_PER_CLIENT
    body = {"items": [_item()]}
    _busy(client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=body), "2")
    assert engine.calls == []
    state.in_flight[("retain", "cc")] -= 1
    assert (
        client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=body).status_code == 200
    )
    assert state.in_flight == {("retain", "cc"): memory_compat.RETAINS_PER_CLIENT - 1}
    assert (memory_compat.REFLECTS_PER_CLIENT, memory_compat.RETAINS_PER_CLIENT) == (1, 4)


def test_a_failed_request_gives_its_slot_back(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    engine.overrides[("POST", ENGINE_BANK + "/reflect")] = httpx.ReadTimeout("slow")
    engine.overrides[("POST", ENGINE_BANK + "/memories")] = httpx.Response(500, text="boom")
    client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "q"})
    client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": "bad"})
    assert _compat_state(app).in_flight == {}


def _configure(app: FastAPI, lanes: Any = None, **compat: Any) -> None:
    memory = types.SimpleNamespace(compat=MemoryCompatConfig(**compat), lanes=lanes)
    app.state.hal0_config = types.SimpleNamespace(memory=memory)


def test_one_client_runs_two_recalls_at_a_time(
    client: TestClient, engine: Engine, key: str, store: BridgeStore, app: FastAPI
) -> None:
    """A recall costs an embedding and a search each time; without a limit one
    key could run as many as it can open connections."""
    _row, other = store.add_client("codex", created_by="t")
    state = _compat_state(app)
    recall = {"query": "q"}
    state.in_flight[("recall", "cc")] = memory_compat.RECALLS_PER_CLIENT
    _busy(client.post(_url(URL_BANK + "/memories/recall"), headers=_auth(key), json=recall), "5")
    assert engine.calls == []
    assert (
        client.post(
            _url(URL_BANK + "/memories/recall"), headers=_auth(other), json=recall
        ).status_code
        == 200
    )
    state.in_flight[("recall", "cc")] -= 1
    assert (
        client.post(
            _url(URL_BANK + "/memories/recall"), headers=_auth(key), json=recall
        ).status_code
        == 200
    )
    assert state.in_flight == {("recall", "cc"): 1}
    assert memory_compat.RECALLS_PER_CLIENT == 2


def test_all_clients_together_run_a_configured_number_of_recalls(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    assert MemoryCompatConfig().max_concurrent_recalls == 4
    state = _compat_state(app)
    state.in_flight[("recall", "*")] = 4
    _busy(
        client.post(_url(URL_BANK + "/memories/recall"), headers=_auth(key), json={"query": "q"}),
        "5",
    )
    assert engine.calls == [] and state.in_flight == {("recall", "*"): 4}
    _configure(app, max_concurrent_recalls=5)
    resp = client.post(_url(URL_BANK + "/memories/recall"), headers=_auth(key), json={"query": "q"})
    assert resp.status_code == 200
    # A recall that fails gives both slots back.
    engine.overrides[("POST", ENGINE_BANK + "/memories/recall")] = httpx.ReadTimeout("slow")
    client.post(_url(URL_BANK + "/memories/recall"), headers=_auth(key), json={"query": "q"})
    assert state.in_flight == {("recall", "*"): 4}


@pytest.mark.parametrize(
    ("configured", "engine_slots", "reflect_slots", "allowed"),
    [
        # The shipped engine runs one model call at a time: one reflect here.
        (2, 1, None, 1),
        (2, 2, None, 1),
        (2, 3, None, 2),
        (8, 4, None, 3),
        # The reflect lane's own setting counts when it is the smaller one;
        # the engine's overall limit holds whatever the lane says.
        (8, 4, 2, 1),
        (8, 2, 8, 1),
        (1, 8, None, 1),
    ],
)
def test_reflects_here_always_leave_an_engine_slot_for_everyone_else(
    client: TestClient,
    engine: Engine,
    key: str,
    app: FastAPI,
    configured: int,
    engine_slots: int,
    reflect_slots: int | None,
    allowed: int,
) -> None:
    """Two keys held by one operator must not be able to take every model
    slot the engine has: hal0's own agents reflect through other routes and
    are not counted here, so one slot is kept back for them."""
    lanes = types.SimpleNamespace(
        llm_max_concurrent=engine_slots,
        reflect=types.SimpleNamespace(max_concurrent=reflect_slots),
    )
    _configure(app, lanes, max_concurrent_reflects=configured)
    state = _compat_state(app)
    state.in_flight[("reflect", "*")] = allowed
    _busy(client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "q"}), "15")
    state.in_flight[("reflect", "*")] = allowed - 1
    resp = client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "q"})
    assert resp.status_code == 200


def test_lanes_hal0_cannot_read_are_taken_to_be_the_shipped_single_slot(
    client: TestClient, key: str, app: FastAPI
) -> None:
    assert MemoryLanesConfig().llm_max_concurrent == 1
    _configure(app, None, max_concurrent_reflects=8)
    _compat_state(app).in_flight[("reflect", "*")] = 1
    _busy(client.post(_url(URL_BANK + "/reflect"), headers=_auth(key), json={"query": "q"}), "15")


def _async_retain(client: TestClient, key: str, operation: str, **item: Any) -> httpx.Response:
    return client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(**item)], "async": True, "operation_id": operation},
    )


def test_a_client_is_held_to_a_number_of_unfinished_async_retains(
    client: TestClient,
    engine: Engine,
    key: str,
    store: BridgeStore,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An async retain is accepted at once and extracted later, so the
    in-flight limit says nothing about how much one key has queued in the
    engine. The quota does."""
    assert MemoryCompatConfig().max_outstanding_operations == 64
    assert MemoryCompatConfig().max_outstanding_bytes == 256 * 1024 * 1024
    monkeypatch.setattr(memory_compat, "_QUOTA_RECHECK_S", 0.0)
    _configure(app, max_outstanding_operations=3)
    _row, other = store.add_client("codex", created_by="t")
    for n in range(5):
        engine.operations[_engine_operation_id("cc", f"op-{n}")] = {"status": "pending"}
    for n in range(3):
        assert _async_retain(client, key, f"op-{n}").status_code == 200

    engine.calls.clear()
    refused = _async_retain(client, key, "op-3")
    _busy(refused, "30")
    # Before refusing, the oldest were read from the engine: still pending.
    assert len(engine.calls) == 3 and all(c.method == "GET" for c in engine.calls)
    # A resend of one already counted starts nothing new, and is let through.
    assert _async_retain(client, key, "op-1").status_code == 200
    # Another client, and a synchronous retain, are not held up by it.
    assert _async_retain(client, other, "op-3").status_code == 200
    sync = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert sync.status_code == 200

    # One finishes; the next retain finds that out by itself.
    engine.operations[_engine_operation_id("cc", "op-0")]["status"] = "completed"
    assert _async_retain(client, key, "op-3").status_code == 200
    _busy(_async_retain(client, key, "op-4"), "30")
    # The plugin's own poll also frees a place.
    engine.operations[_engine_operation_id("cc", "op-2")]["status"] = "failed"
    assert client.get(_url(URL_BANK + "/operations/op-2"), headers=_auth(key)).status_code == 200
    engine.calls.clear()
    assert _async_retain(client, key, "op-4").status_code == 200
    assert engine.lines() == [f"POST {ENGINE_BANK}/memories"]


def test_a_client_is_held_to_a_size_of_unfinished_async_retains(
    client: TestClient, engine: Engine, key: str, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_compat, "_QUOTA_RECHECK_S", 0.0)
    _configure(app, max_outstanding_bytes=1024 * 1024)
    big = "x" * (700 * 1024)
    engine.operations[_engine_operation_id("cc", "op-0")] = {"status": "pending"}
    # A single retain is accepted whatever its size while nothing is outstanding.
    assert _async_retain(client, key, "op-0", content=big + big).status_code == 200
    _busy(_async_retain(client, key, "op-1", content="small"), "30")
    engine.operations[_engine_operation_id("cc", "op-0")]["status"] = "completed"
    assert _async_retain(client, key, "op-1", content=big).status_code == 200
    engine.operations[_engine_operation_id("cc", "op-1")]["status"] = "pending"
    _busy(_async_retain(client, key, "op-2", content=big), "30")


def test_the_quota_fails_open_for_operations_from_before_a_restart(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    """What is outstanding is what this process saw start. After a restart
    the earlier operations are unknown and are not counted."""
    _configure(app, max_outstanding_operations=1)
    engine.operations[_engine_operation_id("cc", "op-0")] = {"status": "pending"}
    assert _async_retain(client, key, "op-0").status_code == 200
    _busy(_async_retain(client, key, "op-1"), "30")
    app.state.memory_compat = None
    assert _async_retain(client, key, "op-1").status_code == 200


def test_a_client_at_its_quota_cannot_make_hal0_poll_the_engine_on_every_request(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    _configure(app, max_outstanding_operations=1)
    engine.operations[_engine_operation_id("cc", "op-0")] = {"status": "pending"}
    assert _async_retain(client, key, "op-0").status_code == 200
    engine.calls.clear()
    _busy(_async_retain(client, key, "op-1"), "30")
    assert len(engine.calls) == 1
    for _ in range(5):
        _busy(_async_retain(client, key, "op-1"), "30")
    assert len(engine.calls) == 1
    assert memory_compat._QUOTA_RECHECK_S == 5.0


def test_a_retain_the_engine_refuses_does_not_count_toward_the_quota(
    client: TestClient, engine: Engine, key: str, app: FastAPI
) -> None:
    """An operation is counted when it is admitted, before the engine call, so
    that side-by-side retains cannot each find the quota free. One the engine
    never accepted is given back."""
    _configure(app, max_outstanding_operations=1)
    engine.overrides[("POST", ENGINE_BANK + "/memories")] = httpx.Response(500, json={})
    assert _async_retain(client, key, "op-0").status_code == 502
    state = _compat_state(app)
    assert state.outstanding("cc") == []
    del engine.overrides[("POST", ENGINE_BANK + "/memories")]

    # While the engine is still answering, the operation is already counted.
    seen: list[int] = []
    real = app.state.memory_provider.retain_items

    async def watched(*args: Any, **kwargs: Any) -> Any:
        seen.append(len(state.outstanding("cc")))
        return await real(*args, **kwargs)

    app.state.memory_provider.retain_items = watched
    engine.operations[_engine_operation_id("cc", "op-1")] = {"status": "pending"}
    assert _async_retain(client, key, "op-1").status_code == 200
    assert seen == [1]
    assert [op.accepted for _id, op in state.outstanding("cc")] == [True]


def test_only_a_few_large_retain_bodies_are_held_at_once(
    client: TestClient,
    engine: Engine,
    key: str,
    store: BridgeStore,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two large bodies at once, and one per client, bound what retains hold
    in hal0-api's memory. A small body does not wait behind them."""
    monkeypatch.setattr(memory_compat, "SMALL_RETAIN_BODY_BYTES", 1024)
    _row, other = store.add_client("codex", created_by="t")
    state = _compat_state(app)
    state.in_flight[("large retain", "cc")] = memory_compat.LARGE_RETAIN_BODIES_PER_CLIENT
    busy = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(content="x" * 4096)]},
    )
    _busy(busy, "2")
    assert (
        client.post(
            _url(URL_BANK + "/memories"),
            headers=_auth(other),
            json={"items": [_item(content="x" * 4096)]},
        ).status_code
        == 200
    )
    assert memory_compat.LARGE_RETAIN_BODIES_PER_CLIENT == 1
    state.in_flight.clear()
    engine.calls.clear()
    state.in_flight[("large retain", "*")] = memory_compat.LARGE_RETAIN_BODIES
    large = {"items": [_item(content="x" * 4096)]}
    small = {"items": [_item()]}
    _busy(client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=large), "2")
    assert engine.calls == []
    assert (
        client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=small).status_code == 200
    )
    # A body that does not say how large it is counts as large.
    unsized = client.post(
        _url(URL_BANK + "/memories"),
        headers={**_auth(key), "Content-Type": "application/json"},
        content=iter([json.dumps(small).encode()]),
    )
    _busy(unsized, "2")
    state.in_flight[("large retain", "*")] -= 1
    assert (
        client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json=large).status_code == 200
    )
    assert state.in_flight == {("large retain", "*"): 1}
    assert memory_compat.LARGE_RETAIN_BODIES == 2


def test_a_body_that_says_it_is_small_is_read_as_small(
    client: TestClient, engine: Engine, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The small path has the small cap, so a declared size cannot be used to
    get a large body past the large-body limit."""

    async def lying_stream(self: Request) -> Any:
        yield b'{"items": [{"content": "'
        yield b"x" * (memory_compat.SMALL_RETAIN_BODY_BYTES + 1)
        yield b'"}]}'

    monkeypatch.setattr(Request, "stream", lying_stream)
    resp = client.post(_url(URL_BANK + "/memories"), headers=_auth(key), json={"items": [_item()]})
    assert (resp.status_code, _code(resp)) == (413, "memory.compat_body_too_large")
    assert resp.json()["error"]["details"] == {"max_bytes": memory_compat.SMALL_RETAIN_BODY_BYTES}
    assert engine.calls == []


def test_a_retain_answer_never_shows_a_stored_id(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.overrides[("POST", ENGINE_BANK + "/memories")] = httpx.Response(
        200,
        json={
            "success": True,
            "items_count": 1,
            "async": False,
            "warnings": ["document hal0::cc::readme replaced", {"id": "hal0::shared::git:x"}],
        },
    )
    resp = client.post(
        _url(URL_BANK + "/memories"),
        headers=_auth(key),
        json={"items": [_item(document_id="readme")]},
    )
    assert resp.json()["warnings"] == ["document readme replaced", {"id": "git:x"}]


# ── a key whose name hal0 reserves ──────────────────────────────────────────


@pytest.mark.parametrize("name", ["unknown", "unresolved"])
def test_a_key_minted_under_a_name_since_reserved_is_refused(
    client: TestClient,
    engine: Engine,
    store: BridgeStore,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """``unknown`` is the identity of a memory write with no caller: a client
    of that name would read those writers' private notes. No such key can be
    minted now; one that already exists is refused here."""
    from hal0.bridge import keys as bridge_keys

    with pytest.raises(BridgeStoreError):
        store.add_client(name, created_by="t")
    with monkeypatch.context() as earlier:
        earlier.setattr(bridge_keys, "RESERVED_CLIENT_NAMES", frozenset())
        _row, old_key = store.add_client(name, created_by="t")

    for method, path in (("POST", URL_BANK + "/memories"), ("POST", URL_BANK + "/reflect")):
        resp = client.request(
            method, _url(path), headers=_auth(old_key), json={"items": [_item()], "query": "q"}
        )
        assert (resp.status_code, _code(resp)) == (403, "memory.compat_client_name_reserved")
    assert engine.calls == []


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
    _seed(engine, _doc("hal0::cc::release-process", [*OWN_PROJECT, "source:upload"]))
    for started in ("6f1d3b52-8f0e-5c41-a7d2-3e9c4b1f0a66", "0d9b6c0a-41f7-4e7c-b3a8-52c1e8f97d24"):
        engine.operations[_engine_operation_id("cc", started)] = {}
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
        polled = case["path"].rsplit("/", 1)[-1]
        expected = expect["engine"].replace(
            "{engine operation id}", _engine_operation_id("cc", polled)
        )
        assert engine.calls[-1].line == expected
    # No engine call ever names the bank the plugin named.
    for call in served:
        assert call.path in ("/version", "/health") or call.path.startswith(ENGINE_BANK)
        assert "coding-agent" not in call.path
    # And nothing the plugin gets back names a stored id.
    assert "hal0::" not in resp.text
    if case["path"].endswith("/memories") and expect["status"] == 200:
        sent = case["body"]["items"][0]
        (item,) = engine.calls[-1].body["items"]
        assert [t for t in item["tags"] if t.startswith("agent:")] == ["agent:cc"]
        assert [t for t in item["tags"] if t.startswith("project:")] == ["project:hal0"]
        assert item["metadata"]["bridge_client"] == "cc"
        assert item["metadata"].get("client_source") == sent["metadata"].get("source")
        assert item["observation_scopes"] == [["project:hal0"]]
        # The plugin's id, inside this client's namespace of the project.
        assert item["document_id"] == f"hal0::cc::{sent['document_id']}"
        assert item.get("strategy") == sent["strategy"]
        assert engine.calls[-1].body["async"] is True
        # The operation is the client's own: its id comes back, the engine
        # was given the derived one.
        given = resp.json()["operation_id"]
        assert given == case["body"].get("operation_id", given)
        assert engine.calls[-1].body["operation_id"] == _engine_operation_id("cc", given)
