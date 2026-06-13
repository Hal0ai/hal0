"""npu_swap_status: container slot lifecycle state drives the swap signal.

``fetch_npu_swap_status`` observes the enabled NPU LLM container slot:
transitional lifecycle states (PULLING/STARTING/WARMING/UNLOADING) map to
``in_progress=True``; settled states (READY/SERVING/IDLE/OFFLINE/ERROR)
map to ``in_progress=False``. ``from_model`` is always None (a restarting
container exposes no "previously loaded" signal); ``to_model`` comes from
the slot config's ``model.default``. The helper never raises.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hal0.dispatcher.npu_swap_status import NpuSwapStatus, fetch_npu_swap_status
from hal0.slots.state import SlotState

# ── Helpers ────────────────────────────────────────────────────────────


def _make_slot(state: str) -> MagicMock:
    """Build a Slot-like mock whose .state is a real SlotState enum value."""
    slot = MagicMock()
    slot.state = SlotState(state)
    return slot


def _container_npu_cfg(
    model: str = "gemma3:4b",
    *,
    profile: str = "flm",
    runtime: str | None = None,
    enabled: bool = True,
    name: str = "npu",
) -> dict[str, Any]:
    """Slot config for a containerized NPU LLM slot."""
    cfg: dict[str, Any] = {
        "name": name,
        "device": "npu",
        "type": "llm",
        "enabled": enabled,
        "model": {"default": model},
    }
    if profile:
        cfg["profile"] = profile
    if runtime is not None:
        cfg["runtime"] = runtime
    return cfg


def _noncontainer_npu_cfg(model: str = "llama-3.2-3b-npu") -> dict[str, Any]:
    """Slot config for a legacy/unmigrated (non-container) NPU LLM slot."""
    return {
        "name": "agent",
        "device": "npu",
        "type": "llm",
        "enabled": True,
        "model": {"default": model},
        # no profile, no runtime=container
    }


def _gpu_slot(name: str = "primary", model: str = "phi3") -> dict[str, Any]:
    return {
        "name": name,
        "device": "gpu-vulkan",
        "type": "llm",
        "enabled": True,
        "model": {"default": model},
    }


def _slot_manager(state: str = "ready") -> MagicMock:
    """SlotManager mock returning the given slot state."""
    sm = MagicMock()
    sm.status = AsyncMock(return_value=_make_slot(state))
    return sm


# ── No enabled NPU LLM slot / no slot manager → all-None settled ───────


async def test_no_npu_slot_means_no_swap() -> None:
    """Nothing configured → all-None settled snapshot."""
    status = await fetch_npu_swap_status([_gpu_slot()], slot_manager=_slot_manager())
    assert status == NpuSwapStatus(in_progress=False, from_model=None, to_model=None)


async def test_disabled_npu_slot_ignored() -> None:
    """A disabled NPU LLM slot doesn't drive a swap."""
    status = await fetch_npu_swap_status(
        [_container_npu_cfg(enabled=False)],
        slot_manager=_slot_manager(),
    )
    assert status.in_progress is False
    assert status.to_model is None


async def test_no_slot_manager_means_all_none_settled() -> None:
    """No slot_manager wired → all-None settled snapshot (test bypass)."""
    status = await fetch_npu_swap_status([_container_npu_cfg()])
    assert status == NpuSwapStatus(in_progress=False, from_model=None, to_model=None)


# ── Non-container NPU slot → settled with to_model ─────────────────────


async def test_noncontainer_npu_slot_settled_with_to_model() -> None:
    """Legacy/unmigrated NPU record → no live container to observe.

    The snapshot is settled but still names the configured model so the
    dashboard can render the slot's target.
    """
    sm = MagicMock()
    sm.status = AsyncMock(side_effect=AssertionError("must not probe a non-container slot"))
    status = await fetch_npu_swap_status(
        [_noncontainer_npu_cfg(model="llama-3.2-3b-npu")],
        slot_manager=sm,
    )
    assert status.in_progress is False
    assert status.from_model is None
    assert status.to_model == "llama-3.2-3b-npu"


# ── Container path: transitional states → in_progress=True ─────────────


async def test_container_starting_means_swap_in_progress() -> None:
    """SlotState.STARTING → in_progress=True (container restarting for model swap)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("starting"))
    assert status.in_progress is True
    assert status.to_model == "gemma3:4b"
    # container path has no "from" side (no previously-loaded signal)
    assert status.from_model is None


async def test_container_pulling_means_swap_in_progress() -> None:
    """SlotState.PULLING → in_progress=True."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("pulling"))
    assert status.in_progress is True
    assert status.to_model == "gemma3:4b"


async def test_container_warming_means_swap_in_progress() -> None:
    """SlotState.WARMING → in_progress=True."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("warming"))
    assert status.in_progress is True


async def test_container_unloading_means_swap_in_progress() -> None:
    """SlotState.UNLOADING → in_progress=True (transition still in flight)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("unloading"))
    assert status.in_progress is True


# ── Container path: settled states → in_progress=False ─────────────────


async def test_container_ready_means_settled() -> None:
    """SlotState.READY → in_progress=False, to_model populated from config."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("ready"))
    assert status.in_progress is False
    assert status.to_model == "gemma3:4b"
    assert status.from_model is None


async def test_container_serving_means_settled() -> None:
    """SlotState.SERVING → in_progress=False (inference in flight, not a swap)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("serving"))
    assert status.in_progress is False


async def test_container_idle_means_settled() -> None:
    """SlotState.IDLE → in_progress=False (warm but quiet)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("idle"))
    assert status.in_progress is False


async def test_container_offline_means_settled() -> None:
    """SlotState.OFFLINE → in_progress=False (slot is not running)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("offline"))
    assert status.in_progress is False


async def test_container_error_means_settled() -> None:
    """SlotState.ERROR → in_progress=False (swap not in progress)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("error"))
    assert status.in_progress is False


# ── Container detection variants + peers ───────────────────────────────


async def test_container_via_runtime_field_also_uses_container_path() -> None:
    """runtime='container' with no profile also triggers the container path."""
    cfg = _container_npu_cfg(model="gemma3:4b", profile="", runtime="container")
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("starting"))
    assert status.in_progress is True
    assert status.to_model == "gemma3:4b"


async def test_swap_in_progress_with_gpu_peers_present() -> None:
    """Non-NPU peer slots don't affect the swap signal."""
    status = await fetch_npu_swap_status(
        [
            _gpu_slot(name="primary", model="phi3"),
            _container_npu_cfg(name="npu", model="gemma3:4b"),
            _gpu_slot(name="nano", model="qwen3-1b"),
        ],
        slot_manager=_slot_manager("starting"),
    )
    assert status.in_progress is True
    assert status.to_model == "gemma3:4b"


# ── model.default edge cases ───────────────────────────────────────────


async def test_empty_slot_model_default_means_no_to_model() -> None:
    """NPU LLM slot with empty model.default → to_model is None."""
    cfg = _container_npu_cfg()
    cfg["model"] = {"default": ""}
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("ready"))
    assert status.in_progress is False
    assert status.to_model is None


async def test_missing_model_section_means_no_to_model() -> None:
    """NPU LLM slot missing the [model] section entirely → to_model is None."""
    cfg = _container_npu_cfg()
    del cfg["model"]
    status = await fetch_npu_swap_status([cfg], slot_manager=_slot_manager("ready"))
    assert status.in_progress is False
    assert status.to_model is None


# ── Accessor error resilience ──────────────────────────────────────────


async def test_container_status_raises_degrades_to_settled() -> None:
    """slot_manager.status() raising → swallowed, in_progress=False (safe degrade)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = MagicMock()
    sm.status = AsyncMock(side_effect=RuntimeError("state file corrupt"))
    status = await fetch_npu_swap_status([cfg], slot_manager=sm)
    assert status.in_progress is False
    assert status.to_model == "gemma3:4b"


# ── Wire shape ─────────────────────────────────────────────────────────


def test_to_dict_shape() -> None:
    """NpuSwapStatus.to_dict matches the wire shape."""
    status = NpuSwapStatus(
        in_progress=True,
        from_model=None,
        to_model="gemma3:4b",
    )
    assert status.to_dict() == {
        "in_progress": True,
        "from_model": None,
        "to_model": "gemma3:4b",
    }


# ── HTTP route smoke test ──────────────────────────────────────────────


def test_swap_status_endpoint_returns_default_shape(client: Any) -> None:
    """GET /api/npu/swap-status returns the shape even with no NPU configured."""
    resp = client.get("/api/npu/swap-status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"in_progress", "from_model", "to_model"}
    assert body["in_progress"] is False


def test_swap_status_endpoint_observes_npu_slot(
    client: Any,
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a container NPU LLM slot is configured and its lifecycle state is
    transitional, the endpoint surfaces in_progress=True.

    Seeds a slot TOML on disk so SlotManager.iter_configs() picks it up;
    patches the slot manager's status() to report STARTING.
    """
    from pathlib import Path

    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "npu.toml").write_text(
        'name = "npu"\n'
        "port = 8092\n"
        'device = "npu"\n'
        'type = "llm"\n'
        "enabled = true\n"
        'profile = "flm"\n'
        "[model]\n"
        'default = "gemma3:4b"\n',
        encoding="utf-8",
    )

    # The container is mid-restart: status() reports STARTING.
    sm = client.app.state.slot_manager
    monkeypatch.setattr(sm, "status", AsyncMock(return_value=_make_slot("starting")))

    resp = client.get("/api/npu/swap-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["in_progress"] is True
    assert body["from_model"] is None
    assert body["to_model"] == "gemma3:4b"


__all__: list[str] = []
