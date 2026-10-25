"""Phase D task D5 — GpuArbiter ⇄ dispatcher/images-route integration.

Covers:

  * Auto-switch: ``POST /v1/images/generations`` flips the GPU to image
    mode (``ensure_img``) before driving the ComfyUI provider, and holds one
    ``image_job()`` from before the switch until the render returns, so the
    arbiter never restores LLM mode under it.
  * Restore after each render: an LLM request that finds image mode with
    nothing rendering ends it at once and is served.
  * 503 guard: LLM-group dispatch while a render runs is refused with a
    structured ``gpu.image_mode`` envelope + ``Retry-After`` header — fired
    BEFORE the readiness check so the client never sees ``slot.loading``.
  * NPU/CPU isolation: non-llm-group slots dispatch normally in img mode.
  * NON-NEGOTIABLE 1 (D4 review): a dead port on an llm slot while the
    arbiter is in img mode must NOT reload the slot into image mode — the
    dead-port guard surfaces the same ``gpu.image_mode`` 503 instead (the
    dispatcher never retries; systemd Restart= owns process recovery).
  * NON-NEGOTIABLE 2 (D4 review): an in-flight LLM request whose container
    the arbiter force-kills (drain timeout) gets a clean structured 5xx
    JSON envelope, not a hung socket or exception leak.
  * #599 defaults: a request body that omits ``size`` (and
    ``extra_body.steps``) is seeded from the img slot's ``[image]``
    section (``default_size`` / ``default_steps``).

Dispatcher-level tests mirror the _RecordingSlotManager harness in
test_serving_integration.py; app-level tests mirror tests/api/test_v1_images.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import hal0.slots.arbiter as arbiter_mod
from hal0.dispatcher.router import Dispatcher, UpstreamCall, UpstreamUnavailable
from hal0.slots.arbiter import GpuArbiter, GpuImageMode, GpuMode
from hal0.slots.state import SlotState
from hal0.upstreams.registry import Upstream

_DISPATCHABLE_STATES = frozenset({SlotState.READY, SlotState.SERVING, SlotState.IDLE})


@pytest.fixture(autouse=True)
def _no_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """No editor hand-off after a switch: these tests chat right after a render."""
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 0.0)


@pytest.fixture(autouse=True)
def comfyui_queue(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """ComfyUI seams the arbiter reads. By default ``/queue`` cannot be read, so an
    LLM request in image mode is refused at once (a busy queue would make it wait
    for the render), which is what the guard tests below pin. A test that wants an
    idle ComfyUI sets ``rec["queue"] = (0, 0)``."""
    rec: dict[str, Any] = {"queue": "unknown", "free": 0}

    async def _queue() -> tuple[int, int]:
        if rec["queue"] == "unknown":
            raise arbiter_mod.ComfyUIQueueUnknown("scripted")
        return rec["queue"]

    async def _free() -> bool:
        rec["free"] += 1
        return True

    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _queue)
    monkeypatch.setattr(arbiter_mod, "_comfyui_free", _free)
    return rec


@pytest.fixture(autouse=True)
def _sdxl_turbo_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The image tests post ``sdxl-turbo``, which only renders once its weights are on disk."""
    from hal0.comfyui import paths
    from hal0.comfyui.catalog import CATALOG, component_path

    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui-data"))
    for component in CATALOG["sdxl-turbo"].components:
        path = component_path(component, paths.models_root())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


# ── dispatcher-level harness (mirrors test_serving_integration.py) ──────────


class _ArbiterSlotManager:
    """Minimal SlotManager surface + a REAL GpuArbiter wired to it.

    The arbiter persists to a tmp state file; ``_config_file`` points at a
    tmp dir so group lookups never touch host /etc/hal0/slots.
    """

    def __init__(self, tmp_path: Path, state: SlotState = SlotState.READY) -> None:
        self._tmp = tmp_path
        self._state = state
        self.events: list[tuple[str, str]] = []
        self._counts: dict[str, int] = {}
        self._tickets: dict[str, int] = {}
        self.load_calls: list[tuple[str, Any]] = []
        self.unload_calls: list[str] = []
        self.cfgs: list[dict[str, Any]] = []
        self.arbiter = GpuArbiter(
            self,  # type: ignore[arg-type]
            state_path=tmp_path / "gpu_arbiter.json",
        )

    # group lookups must stay inside tmp_path (no host TOML leakage)
    def _config_file(self, name: str) -> Path:
        return self._tmp / "slots" / f"{name}.toml"

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self.cfgs)

    def serving(self, slot_name: str) -> Any:
        mgr = self

        class _Ctx:
            async def __aenter__(self) -> None:
                mgr.events.append(("enter", slot_name))
                mgr._counts[slot_name] = mgr._counts.get(slot_name, 0) + 1

            async def __aexit__(self, *_: Any) -> None:
                mgr.events.append(("exit", slot_name))
                mgr._counts[slot_name] = mgr._counts.get(slot_name, 1) - 1

        return _Ctx()

    def in_flight_count(self, slot_name: str) -> int:
        # Mirrors the real SlotManager (DR-2): serving contexts + committed
        # dispatch tickets, so the arbiter drain sees requests past the guard.
        return self._counts.get(slot_name, 0) + self._tickets.get(slot_name, 0)

    def enter_dispatch(self, slot_name: str) -> None:
        self._tickets[slot_name] = self._tickets.get(slot_name, 0) + 1

    def exit_dispatch(self, slot_name: str) -> None:
        remaining = self._tickets.get(slot_name, 1) - 1
        if remaining > 0:
            self._tickets[slot_name] = remaining
        else:
            self._tickets.pop(slot_name, None)

    def state(self, _slot_name: str) -> SlotState:
        return self._state

    def is_ready_for_dispatch(self, _slot_name: str) -> bool:
        return self._state in _DISPATCHABLE_STATES

    async def load(self, slot_name: str, model: Any = None) -> None:
        self.load_calls.append((slot_name, model))

    async def unload(self, slot_name: str) -> None:
        self.unload_calls.append(slot_name)


def _write_img_mode_state(state_path: Path, saved: list[str]) -> None:
    """Persist an img-mode arbiter state file the lazy loader will pick up."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "mode": "img",
                "pinned": False,
                "saved_llm_slots": saved,
            }
        ),
        encoding="utf-8",
    )


def _make_dispatcher(transport: httpx.MockTransport, sm: _ArbiterSlotManager) -> Dispatcher:
    return Dispatcher(
        http_client=httpx.AsyncClient(transport=transport),
        slot_manager=sm,  # type: ignore[arg-type]
    )


def _slot_call(slot_name: str = "primary") -> UpstreamCall:
    return UpstreamCall(
        upstream_name=slot_name,
        target_url="http://slot/chat/completions",
        headers={"content-type": "application/json"},
        body=b"{}",
        streaming=False,
        method="POST",
        slot_name=slot_name,
    )


def _container_slot_call(slot_name: str = "primary") -> UpstreamCall:
    """UpstreamCall with container_slot_name set and slot_name empty.

    Mirrors the shape produced by ``_container_slot_name_of`` post-#723:
    container-backed slots register as kind="remote" so ``slot_name`` is
    empty and ``container_slot_name`` carries the effective slot key.
    """
    return UpstreamCall(
        upstream_name=slot_name,
        target_url="http://slot/chat/completions",
        headers={"content-type": "application/json"},
        body=b"{}",
        streaming=False,
        method="POST",
        slot_name="",  # container remotes carry no slot_name
        container_slot_name=slot_name,
    )


class _ArbiterContainerSlotManager(_ArbiterSlotManager):
    """Extends _ArbiterSlotManager with a container_readiness_check stub.

    Returns (True, "ready") by default so the gate passes and
    _forward_with_serving (and then _forward_direct) is reached.
    """

    async def container_readiness_check(self, slot_name: str) -> tuple[bool, str]:
        return (True, "ready")


# ── app-level harness (mirrors tests/api/test_v1_images.py) ─────────────────


def _seed_img_upstream(client: TestClient, port: int = 8186) -> None:
    client.app.state.upstreams.upsert(
        Upstream(
            name="img",
            kind="slot",
            url=f"http://127.0.0.1:{port}/v1",
            slot_name="img",
            auth_style="none",
        )
    )


def _seed_chat_upstream(client: TestClient, name: str = "primary") -> None:
    client.app.state.upstreams.upsert(
        Upstream(
            name=name,
            kind="slot",
            url="http://127.0.0.1:8001/v1",
            slot_name=name,
            auth_style="none",
        )
    )


_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64

_FAKE_INFER_RESULT = {
    "images": [
        {"png": _FAKE_PNG, "filename": "hal0-t_00001_.png", "subfolder": "", "type": "output"}
    ],
    "meta": {"template": "sdxl_turbo_simple"},
    "prompt_id": "p1",
}


def _arbitrated_cfgs(
    image_section: dict[str, Any] | None = None, *, llm_slot: str = "chat"
) -> list[dict[str, Any]]:
    img: dict[str, Any] = {"name": "img", "device": "gpu-rocm", "profile": "comfyui", "port": 8186}
    if image_section is not None:
        img["image"] = image_section
    return [
        {"name": llm_slot, "device": "gpu-rocm", "profile": "llama-rocm", "port": 8081},
        img,
    ]


def _wire_image_app(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    image_section: dict[str, Any] | None = None,
    llm_slot: str = "chat",
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Mock manager lifecycle + provider.infer; return (unload, load, infer)."""
    from hal0.providers import get_provider

    manager = client.app.state.slot_manager
    monkeypatch.setattr(
        manager,
        "iter_configs",
        AsyncMock(return_value=_arbitrated_cfgs(image_section, llm_slot=llm_slot)),
    )
    monkeypatch.setattr(manager, "is_ready_for_dispatch", lambda name: name == llm_slot)
    unload = AsyncMock()
    # ensure_img polls state() for readiness after load (#714) — a mocked load
    # must therefore make the slot report READY, as a real load would.
    loaded: set[str] = set()
    load = AsyncMock(side_effect=lambda slot_name, model=None: loaded.add(slot_name))
    real_state = manager.state
    monkeypatch.setattr(
        manager,
        "state",
        lambda name: SlotState.READY if name in loaded else real_state(name),
    )
    monkeypatch.setattr(manager, "unload", unload)
    monkeypatch.setattr(manager, "load", load)
    infer = AsyncMock(return_value=_FAKE_INFER_RESULT)
    monkeypatch.setattr(get_provider("comfyui"), "infer", infer)
    return unload, load, infer


# ── auto-switch on image requests ────────────────────────────────────────────


def test_image_request_triggers_switch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """Image request in LLM mode flips the arbiter: chat unloaded, img loaded, 200."""
    _seed_img_upstream(client)
    unload, load, infer = _wire_image_app(client, monkeypatch)

    r = client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": "a cat in a hat"},
    )
    assert r.status_code == 200, r.text
    unload.assert_awaited_once_with("chat")
    load.assert_awaited_once_with("img", ANY)
    assert client.app.state.slot_manager.arbiter.mode == GpuMode.IMG
    infer.assert_awaited_once()


def test_image_job_spans_the_switch_and_the_render(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """The route holds one image job from before ensure_img until the render
    returns: a restore can never slip in between the switch and the render."""
    _seed_img_upstream(client)
    _, _, infer = _wire_image_app(client, monkeypatch)

    manager = client.app.state.slot_manager
    arbiter = manager.arbiter
    events: list[str] = []
    real_job = arbiter.image_job
    real_ensure_img = arbiter.ensure_img

    @contextlib.asynccontextmanager
    async def _recording_job() -> Any:
        events.append("job_enter")
        try:
            async with real_job():
                yield
        finally:
            events.append("job_exit")

    async def _recording_ensure_img(*a: Any, **kw: Any) -> None:
        events.append("ensure_img")
        await real_ensure_img(*a, **kw)

    monkeypatch.setattr(arbiter, "image_job", _recording_job)
    monkeypatch.setattr(arbiter, "ensure_img", _recording_ensure_img)

    async def _recording_infer(*a: Any, **kw: Any) -> dict[str, Any]:
        events.append(f"infer jobs={arbiter.status()['image_jobs']}")
        return _FAKE_INFER_RESULT

    infer.side_effect = _recording_infer

    r = client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": "long wan video frame"},
    )
    assert r.status_code == 200, r.text
    assert events == ["job_enter", "ensure_img", "infer jobs=1", "job_exit"], events
    assert arbiter.status()["image_jobs"] == 0


def test_chat_after_a_v1_render_is_not_refused_for_image_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
    comfyui_queue: dict[str, Any],
) -> None:
    """Open WebUI's reply in the same turn as an image: the chat request that
    follows the render ends image mode and is not refused with gpu.image_mode."""
    comfyui_queue["queue"] = (0, 0)
    _seed_img_upstream(client)
    # ADR-0023: model "primary" falls through to the rule-9 `agent` anchor.
    _seed_chat_upstream(client, "agent")
    _, load, _ = _wire_image_app(client, monkeypatch, llm_slot="agent")
    arbiter = client.app.state.slot_manager.arbiter

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "a cat"})
    assert r.status_code == 200, r.text
    assert arbiter.mode is GpuMode.IMG

    r = client.post(
        "/v1/chat/completions",
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    # The upstream itself is unreachable, so the request may end in any error
    # but the image-mode refusal.
    assert r.json().get("error", {}).get("code") != "gpu.image_mode", r.text
    assert arbiter.mode is GpuMode.LLM
    load.assert_any_await("agent", None)  # the restore reloaded the saved set


def test_a_request_cancelled_during_the_switch_leaves_the_gpu_to_the_restore(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
    comfyui_queue: dict[str, Any],
) -> None:
    """The dashboard's Cancel aborts the request while the GPU switches: nothing is
    submitted to ComfyUI, the image job still closes (so the quiet clock starts), and the
    next LLM request restores the GPU exactly as it does after a finished render."""
    from starlette.requests import Request

    from hal0.api.routes import v1

    comfyui_queue["queue"] = (0, 0)
    _seed_img_upstream(client)
    _seed_chat_upstream(client, "agent")
    unload, load, infer = _wire_image_app(client, monkeypatch, llm_slot="agent")
    arbiter = client.app.state.slot_manager.arbiter

    async def _client_has_gone(request: Request) -> bool:
        # The image request's client is gone once the switch has landed, not before it.
        return request.url.path == "/v1/images/generations" and arbiter.mode is GpuMode.IMG

    monkeypatch.setattr(v1, "_client_has_gone", _client_has_gone)

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "a cat"})

    assert r.status_code == 499
    unload.assert_awaited_once_with("agent")
    load.assert_awaited_once_with("img", ANY)  # the switch ran to its end
    infer.assert_not_awaited()
    assert arbiter.status()["image_jobs"] == 0
    assert arbiter._quiet_since is not None  # the job closed normally: the restore clock runs
    assert arbiter.mode is GpuMode.IMG  # restored by the next LLM request or the loop

    r = client.post(
        "/v1/chat/completions",
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.json().get("error", {}).get("code") != "gpu.image_mode", r.text
    assert arbiter.mode is GpuMode.LLM
    load.assert_any_await("agent", None)


def test_image_defaults_filled_from_image_section(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """#599 — body omits size/steps → seeded from [image] default_size/default_steps."""
    _seed_img_upstream(client)
    _, _, infer = _wire_image_app(
        client,
        monkeypatch,
        image_section={"default_size": "512x512", "default_steps": 6},
    )

    r = client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": "defaults please"},
    )
    assert r.status_code == 200, r.text
    sent_body = infer.await_args.args[1]
    assert sent_body["size"] == "512x512"
    assert sent_body["extra_body"]["steps"] == 6


def test_image_defaults_do_not_override_explicit_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """Caller-supplied size / extra_body.steps win over [image] defaults."""
    _seed_img_upstream(client)
    _, _, infer = _wire_image_app(
        client,
        monkeypatch,
        image_section={"default_size": "512x512", "default_steps": 6},
    )

    r = client.post(
        "/v1/images/generations",
        json={
            "model": "sdxl-turbo",
            "prompt": "explicit",
            "size": "1024x1024",
            "extra_body": {"steps": 12},
        },
    )
    assert r.status_code == 200, r.text
    sent_body = infer.await_args.args[1]
    assert sent_body["size"] == "1024x1024"
    assert sent_body["extra_body"]["steps"] == 12


# ── 503 guard for LLM dispatch in img mode ───────────────────────────────────


def test_llm_request_in_img_mode_503_retry_after(client: TestClient, tmp_hal0_home: str) -> None:
    """Chat request while mode==img → 503 gpu.image_mode + Retry-After ≥ 15."""
    # ADR-0023: model "primary" has no slot and is no longer an alias, so it
    # falls through to the rule-9 `agent` anchor — seed the `agent` slot.
    _seed_chat_upstream(client, "agent")
    _write_img_mode_state(
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "gpu_arbiter.json",
        saved=["agent"],
    )

    r = client.post(
        "/v1/chat/completions",
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "gpu.image_mode"
    assert body["error"]["details"]["retry_after_s"] >= 15
    assert int(r.headers["Retry-After"]) >= 15


@pytest.mark.asyncio
async def test_llm_forward_right_after_a_render_restores_and_dispatches(
    tmp_path: Path, comfyui_queue: dict[str, Any]
) -> None:
    """Restore after each render: an LLM request that finds the render done ends
    image mode at once and is served, instead of a 503 for the idle window."""
    sm = _ArbiterSlotManager(tmp_path)
    _write_img_mode_state(tmp_path / "gpu_arbiter.json", saved=["primary"])
    comfyui_queue["queue"] = (0, 0)
    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})), sm
    )
    try:
        resp = await dispatcher.forward(_slot_call("primary"))
        assert resp.status_code == 200
        assert sm.load_calls == [("primary", None)]
        assert sm.arbiter.mode is GpuMode.LLM
        assert comfyui_queue["free"] == 1
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_a_chat_restored_for_on_the_route_keeps_the_gpu_until_it_dispatches(
    tmp_path: Path, comfyui_queue: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The /v1 chat route asks for room before its own awaits (slot load,
    upstream resolution) and takes its dispatch ticket later, in forward. A
    render queued behind the restore must not switch in that gap: the chat is
    served on the reloaded slot, and the render switches after it."""
    sm = _ArbiterSlotManager(tmp_path)
    sm.cfgs = _dr2_cfgs()
    _write_img_mode_state(tmp_path / "gpu_arbiter.json", saved=["primary"])
    comfyui_queue["queue"] = (0, 0)
    arbiter = sm.arbiter
    release = asyncio.Event()
    events: list[str] = []
    real_load, real_unload = sm.load, sm.unload

    async def _gated_load(slot_name: str, model: Any = None) -> None:
        await release.wait()
        await real_load(slot_name, model)

    async def _recording_unload(slot_name: str) -> None:
        events.append(f"unload {slot_name}")
        await real_unload(slot_name)

    monkeypatch.setattr(sm, "load", _gated_load)
    monkeypatch.setattr(sm, "unload", _recording_unload)

    def _upstream(req: httpx.Request) -> httpx.Response:
        events.append("chat served")
        return httpx.Response(200, json={"ok": True})

    dispatcher = _make_dispatcher(httpx.MockTransport(_upstream), sm)

    async def _chat_route() -> int:
        # _ensure_backend_for_model: the demand step and the guard ...
        await arbiter.make_room_for_llm("primary")
        arbiter.guard_dispatch("primary")
        await asyncio.sleep(0.05)  # ... then the route's own awaits ...
        resp = await dispatcher.forward(_slot_call("primary"))  # ... then the ticket
        return resp.status_code

    async def _render() -> None:
        async with arbiter.image_job():
            await arbiter.ensure_img()
        events.append("render switched")

    try:
        chat = asyncio.create_task(_chat_route())
        await asyncio.sleep(0.01)  # the demand restore holds the lock, reloading
        render = asyncio.create_task(_render())
        await asyncio.sleep(0.01)  # the render waits on the lock behind it
        release.set()

        assert await asyncio.wait_for(chat, timeout=2.0) == 200
        await asyncio.wait_for(render, timeout=2.0)
        assert events == ["chat served", "unload primary", "render switched"]
        assert arbiter.mode is GpuMode.IMG
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_npu_and_cpu_slots_unaffected_in_img_mode(tmp_path: Path) -> None:
    """Non-llm-group slots (tts/npu/cpu) dispatch normally while mode==img."""
    sm = _ArbiterSlotManager(tmp_path)
    _write_img_mode_state(tmp_path / "gpu_arbiter.json", saved=["primary"])

    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})), sm
    )
    try:
        for slot in ("tts", "npu", "embed"):
            resp = await dispatcher.forward(_slot_call(slot))
            assert resp.status_code == 200, slot
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_llm_slot_forward_guarded_in_img_mode(tmp_path: Path) -> None:
    """Dispatcher-level: llm slot forward in img mode raises GpuImageMode pre-readiness."""
    sm = _ArbiterSlotManager(tmp_path, state=SlotState.OFFLINE)  # NOT slot.loading
    _write_img_mode_state(tmp_path / "gpu_arbiter.json", saved=["primary"])

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach upstream in img mode")

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm)
    try:
        with pytest.raises(GpuImageMode) as ei:
            await dispatcher.forward(_slot_call("primary"))
        assert ei.value.code == "gpu.image_mode"
        assert ei.value.status == 503
        assert ei.value.details["retry_after_s"] >= 15
        # Guard fires BEFORE readiness/lazy-load: no load, no serving entry.
        assert sm.load_calls == []
        assert sm.events == []
    finally:
        await dispatcher.aclose()


# ── NON-NEGOTIABLE 1: dead-port image-mode guard ─────────────────────────────


@pytest.mark.asyncio
async def test_dead_port_in_img_mode_raises_gpu_image_mode(tmp_path: Path) -> None:
    """ConnectError on an llm slot while mode==img → NO reload, 503 gpu.image_mode.

    Simulates the race: the request passed the guard in LLM mode, then the
    arbiter flipped to img and force-killed the container mid-flight. The
    dead-port guard must surface the structured gpu.image_mode envelope and
    must NOT reload the llm slot into image mode.
    """
    sm = _ArbiterSlotManager(tmp_path)  # starts in LLM mode (no state file)

    def handler(req: httpx.Request) -> httpx.Response:
        # Arbiter flips to img between the guard and the upstream connect.
        st = sm.arbiter._load_state()
        st["mode"] = "img"
        st["saved_llm_slots"] = ["primary"]
        raise httpx.ConnectError("container force-killed", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm)
    try:
        with pytest.raises(GpuImageMode) as ei:
            await dispatcher.forward(_slot_call("primary"))
        assert ei.value.code == "gpu.image_mode"
        assert ei.value.status == 503
        assert sm.load_calls == [], "no slot load may fight the arbiter"
        # serving counter must still balance (no stuck SERVING).
        assert sm.in_flight_count("primary") == 0
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dead_port_in_llm_mode_raises_upstream_unavailable(tmp_path: Path) -> None:
    """Outside image mode a dead port is a plain UpstreamUnavailable — no retry.

    systemd ``Restart=`` policy owns process recovery; the dispatcher makes
    exactly one upstream attempt and never reloads the slot itself.
    """
    sm = _ArbiterSlotManager(tmp_path)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm)
    try:
        with pytest.raises(UpstreamUnavailable):
            await dispatcher.forward(_slot_call("primary"))
        assert calls["n"] == 1, "dead port must not be retried"
        assert sm.load_calls == []
    finally:
        await dispatcher.aclose()


# ── NON-NEGOTIABLE 1b: dead-port guard fires for container slots (#997) ──────


@pytest.mark.asyncio
async def test_container_dead_port_in_img_mode_raises_gpu_image_mode(
    tmp_path: Path,
) -> None:
    """ConnectError on a container-slot while mode==img → 503 gpu.image_mode.

    Regression test for #997: ``_guard_dead_port_image_mode`` was checking
    ``call.slot_name`` only, which is always empty for container-backed slots
    (post-#723 they carry ``container_slot_name``, not ``slot_name``).  The
    guard was therefore silently dead for every real inference slot.

    This test MUST FAIL on the unfixed code (guard returns early at the
    ``if not (call.slot_name …)`` check) and PASS after the fix
    (``call.slot_name or call.container_slot_name``).
    """
    sm = _ArbiterContainerSlotManager(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        # Arbiter flips to img between the guard and the upstream connect.
        st = sm.arbiter._load_state()
        st["mode"] = "img"
        st["saved_llm_slots"] = ["primary"]
        raise httpx.ConnectError("container force-killed", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm)
    try:
        with pytest.raises(GpuImageMode) as ei:
            await dispatcher.forward(_container_slot_call("primary"))
        assert ei.value.code == "gpu.image_mode"
        assert ei.value.status == 503
        assert sm.load_calls == [], "no slot load may fight the arbiter"
        assert sm.in_flight_count("primary") == 0
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_container_dead_port_in_llm_mode_raises_upstream_unavailable(
    tmp_path: Path,
) -> None:
    """Outside image mode a dead container-slot port → plain UpstreamUnavailable.

    Guard must NOT raise GpuImageMode when the arbiter is in LLM mode;
    the caller sees a regular UpstreamUnavailable with no retry.
    """
    sm = _ArbiterContainerSlotManager(tmp_path)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm)
    try:
        with pytest.raises(UpstreamUnavailable):
            await dispatcher.forward(_container_slot_call("primary"))
        assert calls["n"] == 1, "dead port must not be retried"
        assert sm.load_calls == []
    finally:
        await dispatcher.aclose()


# ── NON-NEGOTIABLE 2: clean 5xx on force-kill mid-request ───────────────────


def test_force_killed_inflight_gets_clean_5xx(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """In-flight chat request whose container the arbiter kills → structured 5xx JSON.

    Full-stack pin: the connect error surfaces through the error middleware
    as a clean gpu.image_mode 503 envelope (dead-port guard path), not a
    hung socket or an exception leak.
    """
    # ADR-0023: model "primary" falls through to the rule-9 `agent` anchor.
    _seed_chat_upstream(client, "agent")
    manager = client.app.state.slot_manager
    monkeypatch.setattr(manager, "is_ready_for_dispatch", lambda _n: True)
    arbiter = manager.arbiter  # construct in LLM mode

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": []})
        # Mid-flight force-kill: arbiter persisted img mode, container gone.
        st = arbiter._load_state()
        st["mode"] = "img"
        st["saved_llm_slots"] = ["agent"]
        raise httpx.ConnectError("container force-killed", request=req)

    client.app.state.dispatcher._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    r = client.post(
        "/v1/chat/completions",
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "gpu.image_mode"
    assert int(r.headers["Retry-After"]) >= 15


# ── DR-2: drain must wait for a request already past the image-mode guard ────


def _dr2_cfgs() -> list[dict[str, Any]]:
    """An llm-group slot (``primary``) plus an img-group slot for group derivation."""
    return [
        {
            "name": "primary",
            "device": "gpu-rocm",
            "runtime": "container",
            "provider": "llama-server",
            "type": "llm",
        },
        {
            "name": "img",
            "device": "gpu-rocm",
            "runtime": "container",
            "provider": "comfyui",
            "type": "image",
        },
    ]


@pytest.mark.asyncio
async def test_drain_waits_for_request_past_guard_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DR-2: a request parked between the guard and serving() blocks the drain.

    Regression for the racy GpuArbiter drain: ``in_flight_count`` used to count
    only active ``serving()`` contexts, so a request that had passed
    ``_guard_gpu_image_mode`` but not yet entered ``serving()`` was invisible —
    ``ensure_img`` unloaded the llm slot out from under it. With the synchronous
    dispatch ticket taken in ``Dispatcher.forward`` (before the first await),
    ``in_flight_count`` reflects the committed request and the drain blocks
    until it finishes.

    Pre-fix this FAILS: the ticket is never taken, ``in_flight_count`` stays 0,
    the drain immediately unloads ``primary`` while the request is still parked.
    """
    monkeypatch.setattr("hal0.slots.arbiter._DRAIN_POLL_S", 0.01)

    sm = _ArbiterSlotManager(tmp_path)  # LLM mode (no state file), slot READY
    sm.cfgs = _dr2_cfgs()

    gate = asyncio.Event()
    entered = asyncio.Event()

    async def _blocked_ensure_loaded(_call: UpstreamCall) -> None:
        # Park the forward AFTER the guard + dispatch ticket (router.py) and
        # BEFORE serving() increments its own counter — exactly the DR-2 window.
        entered.set()
        await gate.wait()

    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})), sm
    )
    monkeypatch.setattr(dispatcher, "_ensure_slot_loaded_backend_aware", _blocked_ensure_loaded)

    try:
        req_task = asyncio.create_task(dispatcher.forward(_slot_call("primary")))
        await asyncio.wait_for(entered.wait(), timeout=2.0)

        # The request is parked in the window: serving() has NOT entered, yet
        # the committed dispatch ticket makes it visible to the drain.
        assert not req_task.done()
        assert sm.events == [], "serving() must not have entered yet"
        assert sm.in_flight_count("primary") == 1, "committed dispatch ticket must be visible"

        # Flip to image mode concurrently; its drain must block on the ticket.
        img_task = asyncio.create_task(sm.arbiter.ensure_img())
        await asyncio.sleep(0.1)
        assert not img_task.done(), "drain must wait for the in-flight request"
        assert sm.unload_calls == [], "slot must not be unloaded under an in-flight request"

        # Release the request: it finishes 200 on the still-loaded slot; ONLY
        # then does the drain observe in_flight_count==0 and unload the slot.
        gate.set()
        resp = await asyncio.wait_for(req_task, timeout=2.0)
        assert resp.status_code == 200
        assert sm.in_flight_count("primary") == 0, "ticket + serving balanced on exit"

        await asyncio.wait_for(img_task, timeout=2.0)
        assert sm.unload_calls == ["primary"], "unload only after the request finished"
        assert sm.arbiter.mode == GpuMode.IMG
    finally:
        await dispatcher.aclose()
