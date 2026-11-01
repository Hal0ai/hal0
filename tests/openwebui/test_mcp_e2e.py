"""Open WebUI's MCP wiring end to end on the real app.

``create_app()`` with its lifespan and auth on, a real ``bridge.db`` and a real
rendered ``openwebui.env``; only the Hindsight engine (a recording stub on
``app.state.memory_provider``) and ``systemctl`` (a fake runner) are replaced.
It follows the one key from mint to use: the reconcile mints the ``openwebui``
client and renders its key, podman's parser reads it back, that key verifies against
the store, opens ``/mcp/memory`` and ``/mcp/agents``, is refused on ``/mcp/admin``
(scope not granted) and on REST, and a ``memory_add`` made with Open WebUI's headers
is stored tagged ``agent:openwebui`` and ``user:alexander``.

The plan's fake Hermes is not needed on this path: a delegated run would need a
gateway, and ``tests/api/test_mcp_agents_mount.py`` and ``tests/bridge`` already cover
that half with ``FakeHermes``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hal0.api import auth as auth_mod
from hal0.api import create_app
from hal0.components import openwebui_arm
from hal0.config import paths
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.openwebui import image_pin
from tests.memory.test_unified_bank import RecordingClient
from tests.openwebui._podman_env import parse_podman_env

ADMIN_KEY = "admin-key-for-owui-mcp-e2e"
MCP_HEADERS = {
    "Host": "127.0.0.1:8080",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_INIT = {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "open-webui", "version": "0.12.5"},
}


@pytest.fixture
def box(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    auth_mod._require_auth_cache = None
    auth_mod._bridge_store_instance = None
    etc = Path(os.environ["HAL0_HOME"]) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text("")
    unit = image_pin.installed_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Service]\nExecStart=/usr/bin/podman run \\\n"
        "  --add-host=host.docker.internal:host-gateway \\\n"
        "  ghcr.io/open-webui/open-webui@sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )
    with TestClient(create_app(), base_url="http://127.0.0.1:8080") as c:
        # The real provider over a recording client: it, not the mount, stamps agent: tags.
        provider = HindsightProvider(client=RecordingClient(), unified_bank=True)
        c.app.state.memory_provider = provider  # type: ignore[attr-defined]
        yield c
    auth_mod._require_auth_cache = None
    auth_mod._bridge_store_instance = None


def _reconcile() -> dict[str, Any]:
    runner = MagicMock()
    runner.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return openwebui_arm.reconcile_openwebui_env(runner=runner, is_hal0_user=lambda: True)


def _rpc(
    client: TestClient,
    mount: str,
    key: str,
    method: str,
    params: dict[str, Any],
    *,
    rid: int = 1,
    session: str | None = None,
    extra: dict[str, str] | None = None,
) -> Any:
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {key}", **(extra or {})}
    if session is not None:
        headers["mcp-session-id"] = session
    return client.post(
        f"/mcp/{mount}/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
    )


def _result(resp: Any) -> dict[str, Any]:
    text = resp.text
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        text = next(line[6:] for line in text.splitlines() if line.startswith("data: "))
    body: dict[str, Any] = json.loads(text)
    assert "error" not in body, body
    result: dict[str, Any] = body["result"]
    return result


def _rendered_connections() -> list[dict[str, Any]]:
    env = parse_podman_env(paths.openwebui_env().read_text(encoding="utf-8"))
    conns: list[dict[str, Any]] = json.loads(env["TOOL_SERVER_CONNECTIONS"])
    return conns


def test_one_key_from_mint_to_a_tagged_memory_write(box: TestClient) -> None:
    # 1. The reconcile mints the client and renders its key where podman will read it.
    assert _reconcile()["status"] == "converged"
    conns = _rendered_connections()
    assert [c["info"]["id"] for c in conns] == ["hal0-memory", "hal0-agents"]  # no admin
    assert {c["key"] for c in conns} and len({c["key"] for c in conns}) == 1
    key = conns[0]["key"]
    assert key.startswith("hal0bk_openwebui.")
    assert conns[0]["url"] == "http://host.docker.internal:8080/mcp/memory/mcp"
    assert conns[0]["config"]["function_name_filter_list"] == (
        "memory_add,memory_search,memory_recall,memory_list"
    )

    # 2. The rendered key is the store's, bound to the managed client and its scopes.
    store = auth_mod.bridge_store()
    row = store.verify_key(key)
    assert row is not None and row.name == "openwebui" and row.created_by == "system:openwebui"
    assert row.scopes == {"agents", "memory"}
    assert paths.openwebui_env().stat().st_mode & 0o077 == 0  # owner-only

    # 3. It opens the memory mount and a memory_add made with Open WebUI's headers is
    #    stored as shared memory from agent:openwebui, annotated with the user and chat.
    init = _rpc(box, "memory", key, "initialize", _INIT)
    assert init.status_code == 200, init.text
    session = init.headers["mcp-session-id"]
    call = _rpc(
        box,
        "memory",
        key,
        "tools/call",
        {"name": "memory_add", "arguments": {"text": "CT151 is the utility box"}},
        rid=2,
        session=session,
        extra={"X-OpenWebUI-User-Name": "Alexander", "X-OpenWebUI-Chat-Id": "c1"},
    )
    assert call.status_code == 200, call.text
    _result(call)
    retained = box.app.state.memory_provider._client.retained  # type: ignore[attr-defined]
    assert len(retained) == 1
    tags = retained[0]["tags"]
    assert "agent:openwebui" in tags and "user:alexander" in tags
    assert "visibility:private" not in tags
    assert [t for t in tags if t.startswith("agent:")] == ["agent:openwebui"]
    assert retained[0]["metadata"]["session_id"] == "c1"

    # 4. The same key opens the Hermes delegate mount...
    a_init = _rpc(box, "agents", key, "initialize", _INIT)
    assert a_init.status_code == 200, a_init.text
    listed = _rpc(
        box,
        "agents",
        key,
        "tools/list",
        {},
        rid=3,
        session=a_init.headers["mcp-session-id"],
    )
    names = {t["name"] for t in _result(listed)["tools"]}
    assert {"agent_run", "agent_run_wait", "agents_list"} <= names

    # 5. ...is refused on admin (the scope is an opt-in) and on REST.
    assert _rpc(box, "admin", key, "initialize", _INIT).status_code in (401, 403)
    assert box.get("/api/slots", headers={"Authorization": f"Bearer {key}"}).status_code in (
        401,
        403,
    )


def test_rotate_re_renders_a_key_that_works_and_kills_the_old_one(
    box: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcile()
    old = _rendered_connections()[0]["key"]
    rerender: list[str | None] = []
    real = openwebui_arm.reconcile_openwebui_env

    def _spy(**kw: Any) -> dict[str, Any]:
        rerender.append(kw.get("bridge_key"))
        runner = MagicMock()
        runner.return_value = MagicMock(returncode=0, stdout="", stderr="")
        return real(runner=runner, is_hal0_user=lambda: True, **kw)

    monkeypatch.setattr(openwebui_arm, "reconcile_openwebui_env", _spy)
    resp = box.post(
        "/api/bridge/clients/openwebui/rotate", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
    )
    assert resp.status_code == 200, resp.text
    new = resp.json()["key"]
    assert new != old and rerender == [new]

    store = auth_mod.bridge_store()
    assert store.verify_key(old) is None
    assert store.verify_key(new) is not None
    assert {c["key"] for c in _rendered_connections()} == {new}


def test_a_client_the_operator_made_is_left_alone(box: TestClient) -> None:
    box.post(
        "/api/bridge/clients",
        json={"name": "openwebui"},
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )
    _reconcile()
    env = parse_podman_env(paths.openwebui_env().read_text(encoding="utf-8"))
    assert "TOOL_SERVER_CONNECTIONS" not in env
