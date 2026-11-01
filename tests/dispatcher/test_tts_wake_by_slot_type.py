"""A speech request wakes an idle-evicted tts slot chosen by TYPE (#2989).

``/v1/audio/speech`` routes to any serving ``type = "tts"`` slot bound to the
requested model (#2784). The idle sweeper evicts a tts slot to OFFLINE and
deregisters its upstream, so a request for a second slot's model (``voice2``)
used to 404: ``_wake_capability_slot`` only ever woke the slot named ``tts``.
The wake target now comes from the same ``select_slot_by_type`` routing uses,
over the slots that are bound to the model and wakeable instead of serving.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.responses import Response

from hal0.api.routes.v1 import _dispatch_and_forward
from hal0.dispatcher.router import Dispatcher, NoRouteFound
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from hal0.upstreams.registry import UpstreamRegistry
from tests.dispatcher.test_capability_wake_on_evict import _make_request
from tests.slots.conftest import FakeContainerProvider

SPEECH = "/v1/audio/speech"


def _write_tts_slot(root: Path, name: str, *, port: int, model: str) -> None:
    """A minimal ``type = "tts"`` container slot bound to ``model``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                'type = "tts"',
                f"port = {port}",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "[model]",
                f'default = "{model}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _load_count(fake: FakeContainerProvider, name: str) -> int:
    return sum(1 for cfg, _mi in fake.load_calls if cfg.get("name") == name)


def _wire(
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
    slots: dict[str, tuple[int, str]],
) -> tuple[SlotManager, Dispatcher, UpstreamRegistry, FakeContainerProvider]:
    """Real SlotManager + Dispatcher sharing ONE registry, one tts slot per entry."""
    fake = FakeContainerProvider()
    monkeypatch.setattr("hal0.providers.container.container_provider", lambda: fake)
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    for name, (port, model) in slots.items():
        _write_tts_slot(root, name, port=port, model=model)
    registry = UpstreamRegistry()
    sm = SlotManager(
        idle_after_s=0.0,
        evict_after_s=0.01,
        idle_monitor_interval_s=10.0,
        upstreams_registry=registry,
    )
    dispatcher = Dispatcher(
        upstream_registry=registry,
        model_registry=None,
        cached_models=lambda _name: [],
        slot_manager=sm,
    )
    return sm, dispatcher, registry, fake


async def _evict(sm: SlotManager, name: str) -> None:
    sm._last_used[sm._key(name)] = 0.0  # ancient — well past the 0.01s TTL
    await sm._sweep_idle_once()


def _capture_forward(dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_forward(call: Any) -> Response:
        captured["call"] = call
        return Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(dispatcher, "forward", _fake_forward)
    return captured


async def _speak(sm: SlotManager, dispatcher: Dispatcher, model: str | None) -> Response:
    body: dict[str, Any] = {"input": "hi", "voice": "af_bella"}
    if model is not None:
        body["model"] = model
    return await _dispatch_and_forward(_make_request(SPEECH, sm), dispatcher, body=body)


async def test_evicted_second_tts_slot_wakes_for_its_model(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2989: ``voice2`` evicted to OFFLINE is reloaded by a speech request for its model."""
    sm, dispatcher, registry, fake = _wire(
        tmp_hal0_home, monkeypatch, {"voice2": (8091, "kokoro-v1")}
    )
    await sm.load("voice2")
    await _evict(sm, "voice2")
    assert (await sm.status("voice2")).state == SlotState.OFFLINE
    assert registry.get("voice2") is None
    captured = _capture_forward(dispatcher, monkeypatch)

    resp = await _speak(sm, dispatcher, "kokoro-v1")

    assert resp.status_code == 200
    assert _load_count(fake, "voice2") == 2, "the request must drive a second load"
    assert (await sm.status("voice2")).state == SlotState.READY
    assert captured["call"].upstream_name == "voice2"


async def test_named_tts_slot_still_wakes(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seeded ``tts`` slot wakes exactly as before the type-based pick."""
    sm, dispatcher, _registry, fake = _wire(
        tmp_hal0_home, monkeypatch, {"tts": (8084, "kokoro-v1")}
    )
    await sm.load("tts")
    await _evict(sm, "tts")
    assert (await sm.status("tts")).state == SlotState.OFFLINE
    captured = _capture_forward(dispatcher, monkeypatch)

    resp = await _speak(sm, dispatcher, "kokoro-v1")

    assert resp.status_code == 200
    assert _load_count(fake, "tts") == 2
    assert captured["call"].upstream_name == "tts"


async def test_seeded_tts_wins_the_wake_tie(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both slots evicted and bound to one model: ``tts`` wakes, ``voice2`` stays dark."""
    sm, dispatcher, _registry, fake = _wire(
        tmp_hal0_home,
        monkeypatch,
        {"voice2": (8091, "kokoro-v1"), "tts": (8084, "kokoro-v1")},
    )
    for name in ("voice2", "tts"):
        await sm.load(name)
        await _evict(sm, name)
    captured = _capture_forward(dispatcher, monkeypatch)

    await _speak(sm, dispatcher, "kokoro-v1")

    assert _load_count(fake, "tts") == 2
    assert _load_count(fake, "voice2") == 1, "only the tie-winner is woken"
    assert captured["call"].upstream_name == "tts"


async def test_wake_picks_the_slot_bound_to_the_requested_model(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tts`` holds a different model: the request wakes ``voice2``, not ``tts``."""
    sm, dispatcher, _registry, fake = _wire(
        tmp_hal0_home,
        monkeypatch,
        {"tts": (8084, "qwen3-tts"), "voice2": (8091, "kokoro-v1")},
    )
    for name in ("tts", "voice2"):
        await sm.load(name)
        await _evict(sm, name)
    captured = _capture_forward(dispatcher, monkeypatch)

    await _speak(sm, dispatcher, "kokoro-v1")

    assert _load_count(fake, "voice2") == 2
    assert _load_count(fake, "tts") == 1, "a slot bound to another model stays dark"
    assert captured["call"].upstream_name == "voice2"


async def test_serving_slot_means_no_wake(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A serving ``voice2`` already answers: an evicted ``tts`` for the same model is not loaded."""
    sm, dispatcher, _registry, fake = _wire(
        tmp_hal0_home,
        monkeypatch,
        {"tts": (8084, "kokoro-v1"), "voice2": (8091, "kokoro-v1")},
    )
    await sm.load("tts")
    await _evict(sm, "tts")
    await sm.load("voice2")
    captured = _capture_forward(dispatcher, monkeypatch)

    await _speak(sm, dispatcher, "kokoro-v1")

    assert _load_count(fake, "tts") == 1, "routing reaches voice2; nothing needs waking"
    assert captured["call"].upstream_name == "voice2"


async def test_unknown_model_still_wakes_the_named_tts_slot(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model no slot is bound to pins to ``tts`` in routing, so ``tts`` is the wake too."""
    sm, dispatcher, _registry, fake = _wire(
        tmp_hal0_home,
        monkeypatch,
        {"tts": (8084, "kokoro-v1"), "voice2": (8091, "other")},
    )
    for name in ("tts", "voice2"):
        await sm.load(name)
        await _evict(sm, name)
    captured = _capture_forward(dispatcher, monkeypatch)

    await _speak(sm, dispatcher, "never-heard-of-it")

    assert _load_count(fake, "tts") == 2
    assert _load_count(fake, "voice2") == 1
    assert captured["call"].upstream_name == "tts"


async def test_model_less_second_tts_slot_is_not_woken(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleared (model-less) ``voice2`` stays dark (#1369): the request 404s as before."""
    sm, dispatcher, registry, fake = _wire(tmp_hal0_home, monkeypatch, {"voice2": (8091, "")})
    assert (await sm.status("voice2")).state == SlotState.OFFLINE

    with pytest.raises(NoRouteFound) as exc:
        await _speak(sm, dispatcher, "kokoro-v1")

    assert exc.value.code == "dispatch.no_route"
    assert _load_count(fake, "voice2") == 0
    assert registry.get("voice2") is None
