"""A load the runtime will refuse must not cost the box its idle slots.

Pre-load eviction stops idle, unpinned slots to make room. Every refusal a
provider can give from cheap local facts (wrong host, missing file, a model on
the wrong runtime) therefore runs first, so a doomed load changes nothing.

The slot manager, the eviction policy and the refusals are real. Only the
systemd seam is the in-memory double, with the real ``check_load`` wired on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from hal0.errors import Hal0Error
from hal0.providers.container import ContainerProvider
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotHostUnsupported, SlotModelRuntimeMismatch, SlotState
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
