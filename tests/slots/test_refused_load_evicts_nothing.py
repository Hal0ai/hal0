"""A load or swap the runtime will refuse must not cost the box anything.

Pre-load eviction stops idle, unpinned slots to make room. Every refusal a
provider can give from cheap local facts (wrong host, missing file, a model on
the wrong runtime, a runner of another family) therefore runs first, so a doomed
load changes nothing. A swap goes further: it stops the running slot and makes the
new model durable before it loads, so its refusal runs before either.

The slot manager, the eviction policy and the refusals are real. Only the
systemd seam is the in-memory double, with the real ``check_load`` wired on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.errors import Hal0Error
from hal0.providers.container import ContainerProvider
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager
from hal0.slots.state import (
    RunnerFamilyMismatch,
    SlotHostUnsupported,
    SlotModelRuntimeMismatch,
    SlotState,
)
from tests.slots.conftest import FakeContainerProvider

GFX1151 = 110501
GFX1100 = 110000
CKPT_NAME = "qwen38-flash-next-ht43.hgn"
#: Far more than the host has free, so admission evicts everything it may.
BIG_BYTES = 72 * 2**30


def _write_llama_slot(root: Path, name: str, port: int) -> None:
    (root / f"{name}.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                f"port = {port}",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_halogen_slot(root: Path) -> None:
    (root / "flash.toml").write_text(
        "\n".join(
            [
                'name = "flash"',
                "port = 8099",
                'device = "gpu-rocm"',
                'type = "llm"',
                'profile = "halogen"',
                "[model]",
                'default = "qwen3.8-flash-next-ht43"',
                "",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A complete checkpoint directory: the checkpoint and its tokenizer."""
    (tmp_path / CKPT_NAME).write_bytes(b"HGN1")
    (tmp_path / "tokenizer").mkdir()
    (tmp_path / "tokenizer" / "tokenizer.json").write_text("{}")
    return tmp_path


def _halogen_row(store: Path) -> dict[str, Any]:
    return {
        "_model_key": "qwen3.8-flash-next-ht43",
        "path": str(store / CKPT_NAME),
        "size_bytes": BIG_BYTES,
        "backends": ["halogen"],
        "provider": "halogen",
    }


@pytest.fixture
async def box(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> SlotManager:
    """An idle, unpinned ``rerank`` slot, short of memory for anything else."""
    # ``check_load`` is the only real ContainerProvider method here: the double
    # has none, and the refusal under test lives in it.
    monkeypatch.setattr(container_stub, "check_load", ContainerProvider().check_load, raising=False)
    _write_llama_slot(slot_root, "rerank", 8090)
    _write_halogen_slot(slot_root)
    sm = SlotManager(preload_evict_headroom_mb=0.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0  # idle long enough to be evicted
    container_stub.load_calls.clear()
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: 100.0)
    return sm


def _resolves_to(sm: SlotManager, monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> None:
    monkeypatch.setattr(sm, "_resolve_model_info", AsyncMock(return_value=row))


def _gfx(monkeypatch: pytest.MonkeyPatch, targets: list[int]) -> None:
    monkeypatch.setattr("hal0.providers.container.kfd_gfx_target_versions", lambda: targets)


async def _assert_nothing_moved(sm: SlotManager, stub: FakeContainerProvider) -> None:
    assert (await sm.status("rerank")).state in (SlotState.READY, SlotState.IDLE)
    assert stub.unload_calls == []
    assert stub.load_calls == []


async def test_the_wrong_host_evicts_nothing(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolves_to(box, monkeypatch, _halogen_row(store))
    _gfx(monkeypatch, [0, GFX1100])
    with pytest.raises(SlotHostUnsupported):
        await box.load("flash")
    await _assert_nothing_moved(box, container_stub)
    assert (await box.status("flash")).state == SlotState.ERROR


async def test_a_missing_checkpoint_evicts_nothing(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (store / CKPT_NAME).unlink()
    _resolves_to(box, monkeypatch, _halogen_row(store))
    _gfx(monkeypatch, [0, GFX1151])
    with pytest.raises(Hal0Error) as exc:
        await box.load("flash")
    assert exc.value.code == "slot.model_missing"
    await _assert_nothing_moved(box, container_stub)


async def test_a_garbage_checkpoint_evicts_nothing(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file exists but the engine cannot load it: the box must not pay for finding out."""
    (store / CKPT_NAME).write_bytes(b"NOPE" + b"\x00" * 60)
    _resolves_to(box, monkeypatch, _halogen_row(store))
    _gfx(monkeypatch, [0, GFX1151])
    with pytest.raises(Hal0Error) as exc:
        await box.load("flash")
    assert exc.value.code == "slot.model_missing"
    await _assert_nothing_moved(box, container_stub)


async def test_a_missing_tokenizer_evicts_nothing(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (store / "tokenizer" / "tokenizer.json").unlink()
    _resolves_to(box, monkeypatch, _halogen_row(store))
    _gfx(monkeypatch, [0, GFX1151])
    with pytest.raises(Hal0Error) as exc:
        await box.load("flash")
    assert exc.value.code == "slot.model_missing"
    await _assert_nothing_moved(box, container_stub)


async def test_a_model_on_the_wrong_runtime_evicts_nothing(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gguf = {
        "_model_key": "qwen3-4b-q4_k_m",
        "path": str(store / "qwen3-4b-q4_k_m.gguf"),
        "size_bytes": BIG_BYTES,
        "backends": [],
        "provider": "llama-server",
    }
    _resolves_to(box, monkeypatch, gguf)
    _gfx(monkeypatch, [0, GFX1151])
    with pytest.raises(SlotModelRuntimeMismatch):
        await box.load("flash")
    await _assert_nothing_moved(box, container_stub)


async def test_a_refused_load_runs_no_gtt_leak_check(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is the news: no warning about memory for a load that never starts."""
    _resolves_to(box, monkeypatch, _halogen_row(store))
    _gfx(monkeypatch, [0, GFX1100])
    guard = AsyncMock(return_value=None)
    feasibility = AsyncMock(return_value=None)
    monkeypatch.setattr(box, "_guard_gtt_leak", guard)
    monkeypatch.setattr(box, "_warn_gtt_feasibility", feasibility)
    with pytest.raises(SlotHostUnsupported):
        await box.load("flash")
    guard.assert_not_awaited()
    feasibility.assert_not_awaited()


async def test_a_load_that_passes_the_check_still_evicts_to_make_room(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check only moves the refusals: an honest load still gets its room."""
    _resolves_to(box, monkeypatch, _halogen_row(store))
    _gfx(monkeypatch, [0, GFX1151])
    readings = [100.0, 500_000.0]
    monkeypatch.setattr(
        box, "_probe_host_free_mb", lambda: readings.pop(0) if readings else 500_000.0
    )
    # Nothing answers on the slot's port: the real halogen wait polls for 600 s.
    monkeypatch.setattr(box, "_await_ready", AsyncMock(return_value=SlotState.READY))
    await box.load("flash")
    assert (await box.status("rerank")).state == SlotState.OFFLINE
    assert [cfg["name"] for cfg, _ in container_stub.load_calls] == ["flash"]


async def test_a_llama_slot_passes_the_check_untouched(
    box: SlotManager,
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``preflight`` on the default path and no host read: the check is a no-op there."""
    reads: list[int] = []
    monkeypatch.setattr(
        "hal0.providers.container.kfd_gfx_target_versions", lambda: reads.append(1) or []
    )
    monkeypatch.setattr(box, "_probe_host_free_mb", lambda: 500_000.0)
    await box.load("chat")
    assert reads == []
    assert (await box.status("chat")).state in (SlotState.READY, SlotState.IDLE)


def test_load_sync_still_refuses_for_a_caller_that_skipped_the_manager(store: Path) -> None:
    """``spawn()`` and direct callers keep the check: it is the last line of defence."""
    provider = ContainerProvider()
    cfg = {
        "name": "flash",
        "port": 8099,
        "device": "gpu-rocm",
        "type": "llm",
        "profile": "halogen",
        "model": {"default": "qwen3.8-flash-next-ht43"},
    }
    with (
        patch("hal0.providers.container._SYSTEMCTL_SEAM") as seam,
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch("hal0.providers.container.kfd_gfx_target_versions", return_value=[0, GFX1100]),
        pytest.raises(SlotHostUnsupported),
    ):
        provider.load_sync(cfg, _halogen_row(store))
    seam.write_quadlet.assert_not_called()


# ── a runner of another family ──────────────────────────────────────────────


def _foreign_binary_toml(name: str, port: int) -> str:
    """What only a hand edit reaches: the llama path would launch the halogen image."""
    return (
        f'name = "{name}"\ntype = "llm"\ndevice = "gpu-rocm"\nport = {port}\n'
        'profile = "agent"\nbinary = "halogen"\n'
        '[model]\ndefault = "qwen3-4b-q4_k_m"\n'
    )


def _llama_row(store: Path) -> dict[str, Any]:
    return {
        "_model_key": "qwen3-4b-q4_k_m",
        "path": str(store / "qwen3-4b-q4_k_m.gguf"),
        "size_bytes": BIG_BYTES,
        "backends": [],
        "provider": "llama-server",
    }


async def test_a_runner_of_another_family_evicts_nothing(
    box: SlotManager,
    slot_root: Path,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (slot_root / "foreign.toml").write_text(_foreign_binary_toml("foreign", 8092), encoding="utf-8")
    _resolves_to(box, monkeypatch, _llama_row(store))
    with pytest.raises(RunnerFamilyMismatch):
        await box.load("foreign")
    await _assert_nothing_moved(box, container_stub)


def _flash_cfg(**over: Any) -> dict[str, Any]:
    return {
        "name": "flash",
        "port": 8099,
        "device": "gpu-rocm",
        "type": "llm",
        "profile": "halogen",
        "model": {"default": "qwen3.8-flash-next-ht43"},
        **over,
    }


def test_check_load_refuses_a_runner_of_another_family(store: Path) -> None:
    cfg = {**_flash_cfg(profile="agent"), "binary": "halogen"}
    with pytest.raises(RunnerFamilyMismatch):
        ContainerProvider().check_load(cfg, _llama_row(store))


def test_check_load_leaves_a_stale_binary_beside_a_dispatched_provider_alone(
    store: Path,
) -> None:
    """A provider launches its own image, so that slot's ``binary`` is inert."""
    with patch("hal0.providers.container.kfd_gfx_target_versions", return_value=[GFX1151]):
        ContainerProvider().check_load(_flash_cfg(binary="kokoro"), _halogen_row(store))


def test_check_load_does_not_run_the_kfd_guard(store: Path) -> None:
    """Ruled: it costs a sudo and podman round trip, so it stays in ``load_sync``."""
    with (
        patch("hal0.providers.container.kfd_gfx_target_versions", return_value=[GFX1151]),
        patch(
            "hal0.providers.container.require_kfd_for_gpu_slot",
            side_effect=AssertionError("the KFD guard ran"),
        ),
    ):
        ContainerProvider().check_load(_flash_cfg(), _halogen_row(store))


def test_load_sync_still_refuses_another_family_before_the_kfd_guard(store: Path) -> None:
    """The last line of defence, and it does not pay for the guard's round trip first."""
    cfg = {**_flash_cfg(profile="agent"), "binary": "halogen"}
    with (
        patch("hal0.providers.container._SYSTEMCTL_SEAM") as seam,
        patch(
            "hal0.providers.container.require_kfd_for_gpu_slot",
            side_effect=AssertionError("the KFD guard ran"),
        ),
        pytest.raises(RunnerFamilyMismatch),
    ):
        ContainerProvider().load_sync(cfg, _llama_row(store))
    seam.write_quadlet.assert_not_called()


# ── a swap ──────────────────────────────────────────────────────────────────


def _register_halogen(store: Path, model_id: str, file_name: str) -> None:
    ModelRegistry().add(
        Model(
            id=model_id,
            name=model_id,
            path=str(store / file_name),
            size_bytes=BIG_BYTES,
            capabilities=["chat"],
            backends=["halogen"],
            provider="halogen",
        )
    )


@pytest.fixture
async def running(
    box: SlotManager,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> SlotManager:
    """``flash`` up on a good checkpoint, on the right host, with memory to spare."""
    _register_halogen(store, "qwen3.8-flash-next-ht43", CKPT_NAME)
    _gfx(monkeypatch, [0, GFX1151])
    monkeypatch.setattr(box, "_probe_host_free_mb", lambda: 500_000.0)
    # Nothing answers on the slot's port: the real halogen wait polls for 600 s.
    monkeypatch.setattr(box, "_await_ready", AsyncMock(return_value=SlotState.READY))
    await box.load("flash")
    assert (await box.status("flash")).state == SlotState.READY
    container_stub.load_calls.clear()
    return box


async def _assert_swap_refused_and_nothing_moved(
    sm: SlotManager,
    stub: FakeContainerProvider,
    slot_root: Path,
    slot: str,
    model_id: str,
    expected: type[Exception],
) -> Exception:
    stored = (slot_root / f"{slot}.toml").read_bytes()
    with pytest.raises(expected) as exc:
        await sm.swap(slot, model_id)
    assert (await sm.status(slot)).state in (SlotState.READY, SlotState.IDLE)
    assert (slot_root / f"{slot}.toml").read_bytes() == stored
    assert stub.unload_calls == []
    assert stub.load_calls == []
    return exc.value


async def test_a_swap_to_a_garbage_checkpoint_leaves_the_slot_running(
    running: SlotManager,
    slot_root: Path,
    store: Path,
    container_stub: FakeContainerProvider,
) -> None:
    (store / "garbage.hgn").write_bytes(b"NOPE" + b"\x00" * 60)
    _register_halogen(store, "garbage", "garbage.hgn")
    refused = await _assert_swap_refused_and_nothing_moved(
        running, container_stub, slot_root, "flash", "garbage", Hal0Error
    )
    assert refused.code == "slot.model_missing"  # type: ignore[attr-defined]
    assert "HGN1" in str(refused)


async def test_a_swap_to_a_missing_checkpoint_leaves_the_slot_running(
    running: SlotManager,
    slot_root: Path,
    store: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """It names the file the operator asked for. ``load`` alone would quietly fall back
    to the only other halogen row, the one already running, and restart the slot on it."""
    _register_halogen(store, "gone", "gone.hgn")
    refused = await _assert_swap_refused_and_nothing_moved(
        running, container_stub, slot_root, "flash", "gone", Hal0Error
    )
    assert refused.code == "slot.model_missing"  # type: ignore[attr-defined]
    assert str(store / "gone.hgn") in str(refused)


async def test_a_swap_to_an_unregistered_id_on_halogen_leaves_the_slot_running(
    running: SlotManager,
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    refused = await _assert_swap_refused_and_nothing_moved(
        running, container_stub, slot_root, "flash", "no-such-model", Hal0Error
    )
    assert refused.code == "slot.model_missing"  # type: ignore[attr-defined]


async def test_a_swap_on_the_wrong_host_leaves_the_slot_running(
    running: SlotManager,
    slot_root: Path,
    store: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (store / "other.hgn").write_bytes(b"HGN1")
    _register_halogen(store, "other", "other.hgn")
    _gfx(monkeypatch, [0, GFX1100])
    await _assert_swap_refused_and_nothing_moved(
        running, container_stub, slot_root, "flash", "other", SlotHostUnsupported
    )


async def test_a_swap_to_a_gguf_on_halogen_leaves_the_slot_running(
    running: SlotManager,
    slot_root: Path,
    store: Path,
    container_stub: FakeContainerProvider,
) -> None:
    (store / "small.gguf").write_bytes(b"GGUF")
    ModelRegistry().add(
        Model(id="small", name="small", path=str(store / "small.gguf"), capabilities=["chat"])
    )
    await _assert_swap_refused_and_nothing_moved(
        running, container_stub, slot_root, "flash", "small", SlotModelRuntimeMismatch
    )


async def test_a_swap_to_a_checkpoint_on_llama_leaves_the_slot_running(
    box: SlotManager,
    slot_root: Path,
    store: Path,
    container_stub: FakeContainerProvider,
) -> None:
    _register_halogen(store, "qwen3.8-flash-next-ht43", CKPT_NAME)
    await _assert_swap_refused_and_nothing_moved(
        box,
        container_stub,
        slot_root,
        "rerank",
        "qwen3.8-flash-next-ht43",
        SlotModelRuntimeMismatch,
    )


async def test_a_swap_on_a_slot_with_a_foreign_runner_tears_nothing_down(
    box: SlotManager,
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    (slot_root / "rerank.toml").write_text(_foreign_binary_toml("rerank", 8090), encoding="utf-8")
    await _assert_swap_refused_and_nothing_moved(
        box, container_stub, slot_root, "rerank", "another-model", RunnerFamilyMismatch
    )


async def test_an_honest_swap_still_goes_through(
    running: SlotManager,
    slot_root: Path,
    store: Path,
    container_stub: FakeContainerProvider,
) -> None:
    (store / "v2.hgn").write_bytes(b"HGN1")
    _register_halogen(store, "v2", "v2.hgn")
    await running.swap("flash", "v2")
    assert [cfg["name"] for cfg in container_stub.unload_calls] == ["flash"]
    assert [cfg["model"]["default"] for cfg, _ in container_stub.load_calls] == ["v2"]
    assert 'default = "v2"' in (slot_root / "flash.toml").read_text(encoding="utf-8")


async def test_a_swap_to_a_model_still_to_pull_is_judged_after_the_pull(
    running: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its file does not exist yet, so the check would refuse a legitimate download."""
    check = MagicMock()
    monkeypatch.setattr(container_stub, "check_load", check, raising=False)
    monkeypatch.setattr(running, "_needs_pull", lambda _model: True)
    monkeypatch.setattr(running, "unload", AsyncMock())
    load = AsyncMock(return_value=None)
    monkeypatch.setattr(running, "load", load)
    await running.swap("flash", "to-be-pulled")
    check.assert_not_called()
    load.assert_awaited_once_with("flash", model_id="to-be-pulled")
