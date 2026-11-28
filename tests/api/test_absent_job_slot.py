"""A job whose slot does not exist says so: 503 ``capability_not_enabled``.

A fresh install holds three slots (``agent``, ``brain``, ``memory``). A request for embeddings,
reranking, speech, transcription or image generation then has no slot to reach, and must answer
which job is not set up and how to turn it on, never a 404, a 500 or a quiet fall back to the
chat slot. Health and doctor treat the same absence as information, not as a fault.

Every command a message prints is run here against a manager that holds just those three slots.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hal0.capabilities.orchestrator import CapabilityOrchestrator, ensure_seed_slot_registered
from hal0.cli import capabilities_commands, comfyui_commands
from hal0.config.schema import HardwareInfo
from hal0.dispatcher.npu_trio import NpuTrioRouter
from hal0.jobs import (
    JOBS,
    CapabilityNotEnabled,
    capability_not_enabled_message,
    configs_serve,
    job_slot_present,
)
from hal0.ports.authority import PortAuthority
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState, SlotStateRecord
from hal0.upstreams.registry import Upstream

_REPO = Path(__file__).resolve().parents[2]
_SEEDS = _REPO / "installer" / "etc-hal0" / "slots"
_THREE = ("agent", "brain", "memory")

#: capability -> the path that asks for the job (multipart is built by the helper)
_JOB_REQUESTS: dict[str, tuple[str, dict[str, Any]]] = {
    "embed": ("/v1/embeddings", {"model": "text-embedding-3-small", "input": "hello"}),
    "rerank": ("/v1/rerankings", {"model": "rerank-1", "query": "q", "documents": ["a"]}),
    "tts": ("/v1/audio/speech", {"model": "kokoro", "input": "hello"}),
    "image": ("/v1/images/generations", {"model": "sdxl-turbo", "prompt": "a cat"}),
}


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _cpu_only_box(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hal0.install.static_seeds._resolve_hardware_info", lambda: HardwareInfo(gpus=[])
    )
    monkeypatch.setattr("hal0.install.profile_derive.rocm_lane_present", lambda *a, **k: False)
    monkeypatch.setattr(
        "hal0.install.profile_derive.default_image_serves_vulkan_lane", lambda *a, **k: True
    )


def _slots_dir(home: str) -> Path:
    path = Path(home) / "etc" / "hal0" / "slots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_three_seeds(home: str) -> None:
    for name in _THREE:
        shutil.copy(_SEEDS / f"{name}.toml", _slots_dir(home) / f"{name}.toml")


class _Calls:
    """Requests that reached the (fake) chat upstream behind the ``agent`` slot."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.ports: list[int | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.ports.append(request.url.port)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
            },
        )


@pytest.fixture
def three_slot_client(client: TestClient, tmp_hal0_home: str) -> tuple[TestClient, _Calls]:
    """The app on a fresh-install box: the three slots, and a live ``agent`` upstream."""
    _write_three_seeds(tmp_hal0_home)
    calls = _Calls()
    dispatcher = client.app.state.dispatcher
    dispatcher._http_client = httpx.AsyncClient(transport=httpx.MockTransport(calls.handler))
    dispatcher._owns_http_client = True
    client.app.state.upstreams.upsert(
        Upstream(
            name="agent",
            kind="slot",
            url="http://127.0.0.1:8099/v1",
            slot_name="agent",
            auth_style="none",
        )
    )
    manager = dispatcher._slot_manager
    manager._states[manager._key("agent")] = SlotStateRecord(
        name="agent",
        state=SlotState.READY,
        model_id="chat-model",
        port=0,
        updated_at=0.0,
        message="pinned by test",
        extra={},
    )
    return client, calls


def _assert_refusal(response: Any, capability: str) -> None:
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "capability_not_enabled"
    assert error["capability"] == capability
    assert error["message"] == capability_not_enabled_message(capability)
    assert "not set up" in error["message"]


# ── the refusal, per job ──────────────────────────────────────────────────────


def test_embeddings_without_embed_slot_is_503_capability_not_enabled(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    path, body = _JOB_REQUESTS["embed"]
    _assert_refusal(client.post(path, json=body), "embed")


@pytest.mark.parametrize("path", ["/v1/rerankings", "/v1/rerank"])
def test_rerank_without_rerank_slot_is_503_capability_not_enabled(
    three_slot_client: tuple[TestClient, _Calls], path: str
) -> None:
    client, _ = three_slot_client
    _, body = _JOB_REQUESTS["rerank"]
    _assert_refusal(client.post(path, json=body), "rerank")


def test_speech_without_tts_slot_is_503_capability_not_enabled(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    path, body = _JOB_REQUESTS["tts"]
    _assert_refusal(client.post(path, json=body), "tts")


def test_transcription_without_stt_slot_is_503_capability_not_enabled(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"RIFF0000WAVE", "audio/wav")},
        data={"model": "whisper-1"},
    )
    _assert_refusal(response, "stt")


def _register_ready_slot(client: TestClient, name: str, port: int) -> None:
    """Register ``name`` as a live slot upstream on ``port`` and pin it READY."""
    client.app.state.upstreams.upsert(
        Upstream(
            name=name,
            kind="slot",
            url=f"http://127.0.0.1:{port}/v1",
            slot_name=name,
            auth_style="none",
        )
    )
    manager = client.app.state.dispatcher._slot_manager
    manager._states[manager._key(name)] = SlotStateRecord(
        name=name,
        state=SlotState.READY,
        model_id="m",
        port=0,
        updated_at=0.0,
        message="pinned by test",
        extra={},
    )


def _transcribe(client: TestClient, model: str) -> Any:
    return client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"RIFF0000WAVE", "audio/wav")},
        data={"model": model},
    )


def test_flm_tag_model_transcription_still_reaches_the_flm_slot(
    three_slot_client: tuple[TestClient, _Calls], tmp_hal0_home: str
) -> None:
    """A box with an ``flm`` slot but no ``stt``/transcription slot: ``name:tag`` goes to flm.

    FLM transcribes natively; the dispatcher's ``name:tag`` rule sends the model there, so
    the "not set up" refusal must not run ahead of it.
    """
    client, calls = three_slot_client
    shutil.copy(_SEEDS / "flm.toml", _slots_dir(tmp_hal0_home) / "flm.toml")
    _register_ready_slot(client, "flm", 8098)
    response = _transcribe(client, "whisper-v3:turbo")
    assert response.status_code == 200, response.text
    assert calls.paths == ["/v1/audio/transcriptions"]
    assert calls.ports == [8098]


def test_flm_tag_model_transcription_on_a_three_slot_box_is_refused(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    """The same request with no ``flm`` slot is the 503, and ``agent`` is never called."""
    client, calls = three_slot_client
    _assert_refusal(_transcribe(client, "whisper-v3:turbo"), "stt")
    assert calls.paths == []


def test_transcription_slot_keeps_its_path(
    three_slot_client: tuple[TestClient, _Calls], tmp_hal0_home: str
) -> None:
    """With a transcription-typed slot the request is routed to it, as before."""
    client, calls = three_slot_client
    (_slots_dir(tmp_hal0_home) / "stt-x.toml").write_text(
        'name = "stt-x"\nport = 8097\ntype = "transcription"\nruntime = "container"\n'
    )
    _register_ready_slot(client, "stt-x", 8097)
    response = _transcribe(client, "stt-x")
    assert response.status_code == 200, response.text
    assert calls.ports == [8097]


def test_embed_named_transcription_model_does_not_reach_an_embed_slot(
    three_slot_client: tuple[TestClient, _Calls], tmp_hal0_home: str
) -> None:
    """No transcription slot: a model named ``embed`` is not sent audio-side to the embed slot."""
    client, calls = three_slot_client
    shutil.copy(_SEEDS / "embed.toml", _slots_dir(tmp_hal0_home) / "embed.toml")
    _register_ready_slot(client, "embed", 8096)
    _assert_refusal(_transcribe(client, "my-embed-model"), "stt")
    assert calls.paths == []


def test_image_generation_without_img_slot_is_503_capability_not_enabled(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    path, body = _JOB_REQUESTS["image"]
    _assert_refusal(client.post(path, json=body), "image")
    # Not even a model is needed to be told the job is off.
    _assert_refusal(client.post(path, json={"prompt": "a cat"}), "image")


def test_no_fallback_to_a_chat_slot(three_slot_client: tuple[TestClient, _Calls]) -> None:
    """No job request reaches the ``agent`` slot's upstream when its own slot is missing."""
    client, calls = three_slot_client
    for path, body in _JOB_REQUESTS.values():
        assert client.post(path, json=body).status_code == 503
    client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"RIFF0000WAVE", "audio/wav")},
        data={"model": "whisper-1"},
    )
    assert calls.paths == []


def test_chat_for_a_missing_slot_name_still_reaches_agent(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    """``hal0/coder`` is a chat alias: with no ``coder`` slot it ends at ``agent`` (ADR-0023)."""
    client, calls = three_slot_client
    response = client.post(
        "/v1/chat/completions",
        json={"model": "hal0/coder", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200, response.text
    assert calls.paths == ["/v1/chat/completions"]


def test_a_slot_that_exists_but_is_off_keeps_the_old_answer(
    three_slot_client: tuple[TestClient, _Calls], tmp_hal0_home: str
) -> None:
    """An ``embed`` slot on disk that is not serving is not "not set up": same 404 as before."""
    client, _ = three_slot_client
    shutil.copy(_SEEDS / "embed.toml", _slots_dir(tmp_hal0_home) / "embed.toml")
    path, body = _JOB_REQUESTS["embed"]
    response = client.post(path, json=body)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dispatch.no_route"


def test_envelope_of_other_errors_is_unchanged(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    response = client.post("/v1/audio/speech", json={"input": "no model"})
    assert response.status_code == 400
    assert "capability" not in response.json()["error"]


# ── NPU and the image engine's own routes ─────────────────────────────────────


async def test_npu_trio_without_flm_slot_says_npu_is_not_set_up() -> None:
    class _Manager:
        async def get_config(self, name: str) -> dict[str, Any]:
            from hal0.slots.state import SlotNotFound

            raise SlotNotFound(f"no slot {name}")

        async def iter_configs(self) -> list[dict[str, Any]]:
            return [{"name": n, "type": "llm"} for n in _THREE]

    router = NpuTrioRouter(slot_manager=_Manager())  # type: ignore[arg-type]
    with pytest.raises(CapabilityNotEnabled) as caught:
        await router.dispatch_embed_npu(body={"model": "m", "input": "x"})
    assert caught.value.status == 503
    assert caught.value.capability == "npu"
    with pytest.raises(CapabilityNotEnabled):
        await router.dispatch_stt_npu(body=b"", content_type="multipart/form-data")


def test_comfyui_restart_without_img_slot_is_503(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    _assert_refusal(client.post("/api/comfyui/restart"), "image")


def test_comfyui_switchover_to_generation_without_img_slot_is_503(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    _assert_refusal(client.post("/api/comfyui/switchover", json={"mode": "generation"}), "image")


# ── health and doctor: informational ──────────────────────────────────────────


def test_health_row_is_informational_when_img_absent(
    three_slot_client: tuple[TestClient, _Calls],
) -> None:
    client, _ = three_slot_client
    with (
        patch(
            "hal0.api.routes.services_health._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable", None, None),
        ),
        patch(
            "hal0.api.routes.services_health._unit_active_state",
            new_callable=AsyncMock,
            return_value="unknown",
        ),
    ):
        rows = {s["id"]: s for s in client.get("/api/services/health").json()["services"]}
    row = rows["comfyui"]
    # "stopped" is the grey, deliberate state; "down" is red and means a fault.
    assert row["state"] == "stopped"
    assert row["detail"].startswith("not set up")
    assert row["up"] is False


def test_health_row_of_an_img_slot_that_exists_is_unchanged(
    three_slot_client: tuple[TestClient, _Calls], tmp_hal0_home: str
) -> None:
    client, _ = three_slot_client
    shutil.copy(_SEEDS / "img.toml", _slots_dir(tmp_hal0_home) / "img.toml")
    with (
        patch(
            "hal0.api.routes.services_health._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable", None, None),
        ),
        patch(
            "hal0.api.routes.services_health._unit_active_state",
            new_callable=AsyncMock,
            return_value="unknown",
        ),
    ):
        rows = {s["id"]: s for s in client.get("/api/services/health").json()["services"]}
    assert rows["comfyui"]["state"] == "down"
    assert rows["comfyui"]["detail"] == "unreachable"


def test_doctor_ok_on_three_slot_box(tmp_hal0_home: str) -> None:
    """Seven absent slots cause no warn, no fail and no non-zero exit."""
    from hal0.cli import doctor_all
    from hal0.cli.doctor_verify import _FAIL, _WARN, build_checks

    _write_three_seeds(tmp_hal0_home)
    slots = [
        {"name": n, "port": 8081 + i, "state": "offline", "type": "llm", "device": "cpu"}
        for i, n in enumerate(_THREE)
    ]
    capabilities = {
        "selections": {
            "embed": {
                "embed": {"enabled": False, "slot": "embed", "status": "offline"},
                "rerank": {"enabled": False, "slot": "rerank", "status": "offline"},
            },
            "voice": {
                "stt": {"enabled": False, "slot": "stt", "status": "offline"},
                "tts": {"enabled": False, "slot": "tts", "status": "offline"},
            },
            "img": {"img": {"enabled": False, "slot": "img", "status": "offline"}},
        }
    }
    rows = build_checks(
        health={"version": "1"},
        urls={"api": "http://192.0.2.1:8080"},
        system={"checks": {"slot_manager": {"slots": 3, "errored": []}}},
        capabilities=capabilities,
        memory={"engine": None, "enabled": False},
        services={"services": [{"id": "openwebui", "up": True}, {"id": "hermes", "up": True}]},
    )
    rows += [
        doctor_all.check_ports(slots),
        doctor_all.check_npu_liveness(slots, dev_root=tmp_hal0_home),
        doctor_all.check_openwebui_task_model(slots),
        doctor_all.check_hermes_delegation_slot(slots),
        doctor_all.check_img_custom_nodes(),
        doctor_all.check_gtt_leak(slots),
        doctor_all.check_fabric_clock(slots),
    ]
    caused = [r for r in rows if r.key in {"capabilities"} or "img" in r.key]
    assert caused, "the rows that look at the absent slots must be part of this check"
    assert [(r.key, r.detail) for r in rows if r.status in (_WARN, _FAIL) and r.key != "dns"] == []
    assert doctor_all.overall_verdict(rows) == "ok"
    assert doctor_all._exit_code(rows) == 0


# ── the messages and the commands they print ──────────────────────────────────


def test_message_names_the_dashboard_and_a_model_placeholder() -> None:
    for capability in ("embed", "rerank", "tts", "stt", "image"):
        message = capability_not_enabled_message(capability)
        assert message.startswith(f"{JOBS[capability].label} is not set up on this box. ")
        assert "dashboard under Slots" in message
        assert "<model>" in message
    npu = capability_not_enabled_message("npu")
    assert "run:" not in npu and "<model>" not in npu


def _three_slot_manager(home: str) -> SlotManager:
    db = Path(home) / "hal0.db"
    return SlotManager(
        identity_store=SlotIdentityStore(db_path=db),
        port_authority=PortAuthority(pool=(8081, 8200), db_path=db),
    )


async def _manager_with_three(home: str) -> SlotManager:
    manager = _three_slot_manager(home)
    for name in _THREE:
        await ensure_seed_slot_registered(manager, name)
    assert {c["name"] for c in await manager.iter_configs()} == set(_THREE)
    return manager


def _printed_command(capability: str) -> list[str]:
    """The CLI words after ``hal0`` of the command the message prints, with a real model id."""
    message = capability_not_enabled_message(capability)
    command = message.split("run: ", 1)[1].replace("<model>", "test-model")
    words = shlex.split(command)
    assert words[0] == "hal0"
    return words[1:]


@pytest.mark.parametrize("capability", ["embed", "rerank", "tts", "stt"])
async def test_printed_capabilities_command_switches_the_job_on(
    capability: str, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = await _manager_with_three(tmp_hal0_home)
    assert await job_slot_present(manager, capability) is False

    posted: dict[str, Any] = {}

    def _capture(path: str, json: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        posted.update(path=path, body=json)
        return {"selection": json}

    monkeypatch.setattr(capabilities_commands, "api_post", _capture)
    monkeypatch.setattr(capabilities_commands, "_api_unreachable", lambda url: False)
    words = _printed_command(capability)
    assert words[0] == "capabilities"
    result = CliRunner().invoke(capabilities_commands.app, words[1:])
    assert result.exit_code == 0, result.output

    # What the CLI posted is what the API route hands to the orchestrator.
    _, slot, child = posted["path"].rsplit("/", 2)[0], *posted["path"].split("/")[-2:]
    monkeypatch.setattr(CapabilityOrchestrator, "_validate_model_in_catalog", lambda *a, **k: None)
    monkeypatch.setattr("hal0.capabilities.orchestrator._warm_flm_catalog", AsyncMock())
    monkeypatch.setattr("hal0.agents.hermes_refresh.spawn_context_refresh", lambda *a, **k: None)
    monkeypatch.setattr(
        "hal0.capabilities.catalog.available_backends",
        lambda: [{"id": "cpu", "label": "CPU", "provider": "llama-server", "multiplex": False}],
    )
    manager.load = AsyncMock()  # type: ignore[method-assign]
    orch = CapabilityOrchestrator(
        manager, config_path=Path(tmp_hal0_home) / "etc" / "hal0" / "capabilities.toml"
    )
    await orch.apply(slot, child, posted["body"])

    assert await job_slot_present(manager, capability) is True
    assert JOBS[capability].stock_name in {c["name"] for c in await manager.iter_configs()}


async def test_printed_image_command_switches_the_job_on(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.api.routes import comfyui as comfyui_routes

    manager = await _manager_with_three(tmp_hal0_home)
    assert await job_slot_present(manager, "image") is False

    posted: list[str] = []
    monkeypatch.setattr(comfyui_commands, "_require_api", lambda: None)
    monkeypatch.setattr(comfyui_commands, "api_post", lambda path, **_: posted.append(path) or {})
    monkeypatch.setattr(comfyui_commands, "_follow", lambda *a, **k: False)
    words = _printed_command("image")
    assert words[:2] == ["comfyui", "models"]
    result = CliRunner().invoke(comfyui_commands.app, words[1:])
    assert result.exit_code == 0, result.output
    assert posted == ["/api/comfyui/models/test-model/install"]

    # The API route behind that path starts the install through this function.
    app = SimpleNamespace(
        state=SimpleNamespace(
            slot_manager=manager, image_install_jobs={}, image_tree_lock=asyncio.Lock()
        )
    )
    monkeypatch.setattr(
        comfyui_routes,
        "_require_model",
        lambda model_id: SimpleNamespace(id=model_id, workflow_filename="w.json"),
    )
    monkeypatch.setattr(comfyui_routes, "_schedule_install", lambda *a, **k: None)
    monkeypatch.setattr(comfyui_routes.comfyui_install, "require_one_filesystem", lambda: None)
    monkeypatch.setattr(
        comfyui_routes.comfyui_install, "require_writable_destinations", lambda m: None
    )
    monkeypatch.setattr(comfyui_routes.comfyui_install, "preflight_disk", lambda m: None)
    monkeypatch.setattr(
        comfyui_routes,
        "install_state",
        lambda model: SimpleNamespace(state="missing", missing=["w"]),
    )
    await comfyui_routes.start_image_install(app, "test-model")  # type: ignore[arg-type]

    assert await job_slot_present(manager, "image") is True


def test_npu_has_no_command_to_print() -> None:
    assert JOBS["npu"].switch_on is None


def test_configs_serve_matches_by_type_and_by_seed_name() -> None:
    assert configs_serve("embed", [{"name": "my-embedder", "type": "embedding"}])
    assert configs_serve("rerank", [{"name": "rerank"}])  # an old config with no type
    assert not configs_serve("tts", [{"name": n, "type": "llm"} for n in _THREE])
    assert configs_serve("npu", [{"name": "x", "device": "npu"}])
