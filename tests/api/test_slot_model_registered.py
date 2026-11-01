"""A slot only binds a model hal0 can resolve (#2668).

``hal0 slot create`` / ``slot edit --model`` (CLI), ``POST /api/slots`` /
``PUT /api/slots/{name}/config`` (REST) and the ``slot_create`` / ``slot_edit``
MCP tools all end in the same two route handlers. Before this guard they wrote
an unregistered model id to the slot TOML, and the next ``load`` silently served
a same-capability stand-in model (``slot.model_default_fallback``). ``swap`` and
load-with-a-model already refused the same id with a 404; create and edit now
refuse it with ``validation.model_not_registered``.

What is NOT refused: a registered id, a curated catalogue id (``load`` pulls it),
an NPU/FLM tag, a ComfyUI catalogue id, and an id the slot already carries (so an
unrelated edit to a slot that predates the guard still saves).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hal0.api import create_app
from hal0.cli import slot_commands
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue
from hal0.registry.curated import CURATED_MODELS
from hal0.registry.fallback import resolve_servable_model
from tests.fixtures.model_registration import register_model_file
from tests.fixtures.slot_probe import probe_slot_name

CODE = "validation.model_not_registered"
UNKNOWN = "not-a-registered-id"
CURATED_ID = CURATED_MODELS[0].id
runner = CliRunner()


@pytest.fixture
def container_stub() -> Iterator[None]:
    active: set[str] = set()
    provider = MagicMock()
    provider.load_sync = MagicMock(side_effect=lambda cfg, info: active.add(cfg["name"]))
    provider.unload_sync = MagicMock(side_effect=lambda cfg: active.discard(cfg["name"]))
    provider.wait_ready = AsyncMock(return_value=None)
    provider.is_active = MagicMock(side_effect=lambda s: probe_slot_name(s) in active)
    provider.health = AsyncMock(return_value={"ok": True, "status": "healthy"})
    provider.running_image = MagicMock(return_value=None)
    provider.running_argv = MagicMock(return_value=None)
    provider.expected_argv = MagicMock(return_value=None)
    provider.image_present = MagicMock(return_value=True)
    with patch("hal0.providers.container.container_provider", return_value=provider):
        yield


@pytest.fixture
def slots(tmp_hal0_home: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat.toml").write_text(
        'name = "chat"\nport = 8081\ndevice = "gpu-vulkan"\nprovider = "llama-server"\n'
        'runtime = "container"\nprofile = "vulkan-radv"\n[model]\ndefault = "gguf-a"\n',
        encoding="utf-8",
    )
    # A slot written before this guard existed: it already names an unknown id.
    (root / "stale.toml").write_text(
        'name = "stale"\nport = 8083\ndevice = "gpu-vulkan"\nprovider = "llama-server"\n'
        'runtime = "container"\nprofile = "vulkan-radv"\n[model]\ndefault = "gone-model"\n',
        encoding="utf-8",
    )
    return root


@pytest.fixture
def client(tmp_hal0_home: str) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _register(client: TestClient, home: str, mid: str) -> None:
    register_model_file(client, home, mid)


def _code(r: Any) -> str:
    return r.json()["error"]["code"]


def _assert_refusal_names_the_id(message: str, model_id: str = UNKNOWN) -> None:
    assert repr(model_id) in message
    assert "hal0 model" in message  # says how to register one


# ── REST ─────────────────────────────────────────────────────────────────────


def test_create_with_unregistered_model_refused(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    r = client.post("/api/slots", json={"name": "zz-emb", "device": "gpu-vulkan", "model": UNKNOWN})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE
    _assert_refusal_names_the_id(r.json()["error"]["message"])
    assert r.json()["error"]["details"]["model_id"] == UNKNOWN
    assert not (slots / "zz-emb.toml").exists()


def test_create_with_unregistered_model_in_nested_shape_refused(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    r = client.post(
        "/api/slots",
        json={"name": "zz-emb", "device": "gpu-vulkan", "model": {"default": UNKNOWN}},
    )
    assert r.status_code == 400, r.text
    assert _code(r) == CODE


def test_create_with_registered_model_passes(
    slots: Path, container_stub: None, client: TestClient, tmp_hal0_home: str
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    r = client.post("/api/slots", json={"name": "ok", "device": "gpu-vulkan", "model": "gguf-b"})
    assert r.status_code == 201, r.text
    assert 'default = "gguf-b"' in (slots / "ok.toml").read_text()


def test_create_with_curated_unpulled_model_passes(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    """A curated catalogue id is pulled by ``load``; the write must not veto it."""
    assert client.get(f"/api/models/{CURATED_ID}").status_code == 404
    r = client.post(
        "/api/slots", json={"name": "pull", "device": "gpu-vulkan", "model": CURATED_ID}
    )
    assert r.status_code == 201, r.text


def test_create_without_a_model_still_passes(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    r = client.post("/api/slots", json={"name": "bare", "device": "gpu-vulkan"})
    assert r.status_code == 201, r.text


def test_put_config_with_unregistered_model_refused(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    before = (slots / "chat.toml").read_text()
    r = client.put("/api/slots/chat/config", json={"model": {"default": UNKNOWN}})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE
    _assert_refusal_names_the_id(r.json()["error"]["message"])
    assert (slots / "chat.toml").read_text() == before


def test_put_config_with_registered_model_passes(
    slots: Path, container_stub: None, client: TestClient, tmp_hal0_home: str
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    r = client.put("/api/slots/chat/config", json={"model": {"default": "gguf-b"}})
    assert r.status_code == 200, r.text
    assert 'default = "gguf-b"' in (slots / "chat.toml").read_text()


def test_put_config_resending_the_stored_unknown_id_is_not_vetoed(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    """A slot that predates the guard keeps saving: the unchanged id is not a new bind."""
    r = client.put(
        "/api/slots/stale/config", json={"model": {"default": "gone-model"}, "priority": 60}
    )
    assert r.status_code == 200, r.text


def test_put_config_without_a_model_is_not_vetoed_by_a_stale_default(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    r = client.put("/api/slots/stale/config", json={"priority": 60})
    assert r.status_code == 200, r.text


def test_put_config_changing_a_stale_slot_to_another_unknown_id_refused(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    r = client.put("/api/slots/stale/config", json={"model": {"default": UNKNOWN}})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE


def test_npu_slot_is_not_checked_against_the_registry(
    slots: Path, container_stub: None, client: TestClient
) -> None:
    """NPU models are FLM tags served by the engine, never registry rows."""
    r = client.post(
        "/api/slots", json={"name": "npu2", "device": "npu", "type": "llm", "model": "qwen3-4b-FLM"}
    )
    assert r.status_code != 400 or _code(r) != CODE, r.text


# ── CLI → REST ───────────────────────────────────────────────────────────────


@pytest.fixture
def cli_bridge(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the real ``hal0`` CLI HTTP helpers at the in-process app."""
    from hal0.cli import _shared

    class _Bridge:
        def __init__(self, **_kw: Any) -> None:
            pass

        def __enter__(self) -> _Bridge:
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

        def request(self, method: str, url: str, **kw: Any) -> Any:
            return client.request(method, url, json=kw.get("json"))

    monkeypatch.setattr(_shared, "_api_base", lambda: "http://testserver")
    monkeypatch.setattr(_shared.httpx, "Client", _Bridge)
    monkeypatch.setattr(_shared, "_auth_headers", lambda _url: {})
    monkeypatch.setattr(slot_commands, "_api_base", lambda: "http://testserver")
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _u: False)
    monkeypatch.setattr(slot_commands, "_detect_default_hardware", lambda: "vulkan")


def test_cli_slot_create_refuses_an_unregistered_model(
    slots: Path, container_stub: None, cli_bridge: None
) -> None:
    result = runner.invoke(
        slot_commands.app,
        ["create", "zz-emb", "--type", "embedding", "--model", UNKNOWN, "--port", "8087"],
    )
    assert result.exit_code != 0, result.output
    assert UNKNOWN in result.output + result.stderr
    assert "hal0 model" in result.output + result.stderr
    assert not (slots / "zz-emb.toml").exists()


def test_cli_slot_create_accepts_a_registered_model(
    slots: Path, container_stub: None, cli_bridge: None, client: TestClient, tmp_hal0_home: str
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    result = runner.invoke(
        slot_commands.app, ["create", "ok", "--model", "gguf-b", "--port", "8087"]
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert (slots / "ok.toml").exists()


def test_cli_slot_edit_refuses_an_unregistered_model(
    slots: Path, container_stub: None, cli_bridge: None
) -> None:
    before = (slots / "chat.toml").read_text()
    result = runner.invoke(slot_commands.app, ["edit", "chat", "--model", UNKNOWN])
    assert result.exit_code != 0, result.output
    assert UNKNOWN in result.output + result.stderr
    assert (slots / "chat.toml").read_text() == before


def test_cli_slot_edit_accepts_a_registered_model(
    slots: Path, container_stub: None, cli_bridge: None, client: TestClient, tmp_hal0_home: str
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    result = runner.invoke(slot_commands.app, ["edit", "chat", "--model", "gguf-b"])
    assert result.exit_code == 0, result.output + result.stderr
    assert 'default = "gguf-b"' in (slots / "chat.toml").read_text()


# ── MCP → REST ───────────────────────────────────────────────────────────────


@pytest.fixture
def mcp_call(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Any]:
    """Run ``hal0-admin`` tools for real, forwarding the REST hop into the app."""

    async def _forward(
        *,
        base_url: str,
        bearer: str | None,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        response = client.request(method, url, json=payload or {})
        if response.status_code >= 400:
            return {
                "status": "error",
                "http_status": response.status_code,
                "error": admin._rest_error_payload(response.json(), text=response.text),
            }
        return response.json()  # type: ignore[no-any-return]

    monkeypatch.setattr(admin, "_call_rest", _forward)
    queue = ApprovalQueue()
    policy = admin.ToolPolicy(auto_approve=("slot_create", "slot_edit"))

    async def _call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return await admin.dispatch(
            tool=tool,
            args=args,
            client_id="pi",
            bearer="t",
            base_url="http://testserver",
            approval_queue=queue,
            policy=policy,
        )

    return _call


@pytest.mark.asyncio
async def test_mcp_slot_create_refuses_an_unregistered_model(
    slots: Path, container_stub: None, mcp_call: Callable[..., Any]
) -> None:
    result = await mcp_call("slot_create", {"name": "zz-emb", "model": UNKNOWN})
    assert result["status"] == "error", result
    assert result["error"]["code"] == CODE
    _assert_refusal_names_the_id(result["error"]["message"])
    assert not (slots / "zz-emb.toml").exists()


@pytest.mark.asyncio
async def test_mcp_slot_create_accepts_a_registered_model(
    slots: Path,
    container_stub: None,
    mcp_call: Callable[..., Any],
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    result = await mcp_call("slot_create", {"name": "ok", "model": "gguf-b"})
    assert result.get("status") != "error", result
    assert (slots / "ok.toml").exists()


@pytest.mark.asyncio
async def test_mcp_slot_edit_refuses_an_unregistered_model(
    slots: Path, container_stub: None, mcp_call: Callable[..., Any]
) -> None:
    before = (slots / "chat.toml").read_text()
    result = await mcp_call("slot_edit", {"name": "chat", "model": {"default": UNKNOWN}})
    assert result["status"] == "error", result
    assert result["error"]["code"] == CODE
    assert (slots / "chat.toml").read_text() == before


@pytest.mark.asyncio
async def test_mcp_slot_edit_accepts_a_registered_model(
    slots: Path,
    container_stub: None,
    mcp_call: Callable[..., Any],
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    result = await mcp_call("slot_edit", {"name": "chat", "model": {"default": "gguf-b"}})
    assert result.get("status") != "error", result
    assert 'default = "gguf-b"' in (slots / "chat.toml").read_text()


# ── load-time substitution stays, but is loud ────────────────────────────────


def test_load_time_substitution_names_both_models_in_the_log_line(
    tmp_hal0_home: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Slot files that already name an unknown id keep loading (a stand-in model);
    the WARNING must now name the configured and the substituted id in its text,
    because ``extra=`` fields never reach the journal record."""
    from hal0.registry.model import Model

    stand_in = Model(id="stand-in-emb", name="stand-in-emb", path=__file__, capabilities=["embed"])
    with (
        patch("hal0.registry.fallback.default_model_cache_check", return_value=False),
        patch("hal0.registry.fallback.fallback_local_model", return_value=stand_in),
        caplog.at_level(logging.WARNING, logger="hal0.registry.fallback"),
    ):
        served = resolve_servable_model("typo-embedding-id", {"type": "embedding"})
    assert served == "stand-in-emb"
    records = [r for r in caplog.records if "slot.model_default_fallback" in r.getMessage()]
    assert len(records) == 1
    text = records[0].getMessage()
    assert "typo-embedding-id" in text
    assert "stand-in-emb" in text
