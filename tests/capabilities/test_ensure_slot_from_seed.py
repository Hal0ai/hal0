"""Switching a job on creates its slot from the shipped seed file.

``CapabilityOrchestrator._ensure_slot_exists`` and an image-model install both go through
``ensure_seed_slot_registered``: the new slot has the seed's port, profile and type, the
device this box can run, and the slot manager knows it.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.api.routes import comfyui as comfyui_routes
from hal0.capabilities.config import CapabilitySelection
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.config.schema import HardwareInfo
from hal0.install.static_seeds import add_seed_tombstone, read_seed_tombstones
from hal0.ports.authority import PortAuthority
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager

_REPO = Path(__file__).resolve().parents[2]
_SEEDS = _REPO / "installer" / "etc-hal0" / "slots"


@pytest.fixture(autouse=True)
def _cpu_only_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """A box with no GPU, so every llama.cpp seed derives ``cpu`` instead of ``gpu-rocm``."""
    monkeypatch.setattr(
        "hal0.install.static_seeds._resolve_hardware_info", lambda: HardwareInfo(gpus=[])
    )
    monkeypatch.setattr("hal0.install.profile_derive.rocm_lane_present", lambda *a, **k: False)
    monkeypatch.setattr(
        "hal0.install.profile_derive.default_image_serves_vulkan_lane", lambda *a, **k: True
    )


def _manager(home: Path) -> SlotManager:
    db = home / "hal0.db"
    return SlotManager(
        identity_store=SlotIdentityStore(db_path=db),
        port_authority=PortAuthority(pool=(8081, 8200), db_path=db),
    )


def _orch(home: Path, manager: SlotManager) -> CapabilityOrchestrator:
    return CapabilityOrchestrator(manager, config_path=home / "etc" / "hal0" / "capabilities.toml")


def _slot_file(home: Path, name: str) -> Path:
    return home / "etc" / "hal0" / "slots" / f"{name}.toml"


def _read(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _seed(name: str) -> dict[str, Any]:
    return _read(_SEEDS / f"{name}.toml")


def _selection(device: str = "cpu") -> CapabilitySelection:
    return CapabilitySelection(device=device, provider="llama-server", model="", enabled=True)


async def test_enabling_embed_creates_slot_from_seed(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    manager = _manager(home)
    assert not _slot_file(home, "embed").exists()

    await _orch(home, manager)._ensure_slot_exists("embed", _selection())

    raw = _read(_slot_file(home, "embed"))
    seed = _seed("embed")
    assert raw["port"] == seed["port"]
    assert raw["profile"] == seed["profile"]
    assert raw["type"] == seed["type"]
    # The seed ships the reference platform's gpu-rocm; this box has no GPU.
    assert seed["device"] == "gpu-rocm"
    assert raw["device"] == "cpu"
    # The manager knows the slot: an identity row exists, it is not a bare file.
    assert "embed" in manager.identity_names()


async def test_enabling_after_delete_clears_tombstone(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    slots = home / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    add_seed_tombstone("rerank", slots_dir=slots)
    add_seed_tombstone("tts", slots_dir=slots)
    manager = _manager(home)

    await _orch(home, manager)._ensure_slot_exists("rerank", _selection())

    assert _slot_file(home, "rerank").is_file()
    assert read_seed_tombstones(slots_dir=slots) == frozenset({"tts"})


async def test_non_template_name_still_synthesised(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    manager = _manager(home)

    await _orch(home, manager)._ensure_slot_exists("stt", _selection())

    raw = _read(_slot_file(home, "stt"))
    assert raw["name"] == "stt"
    assert raw["type"] == "transcription"
    assert raw["device"] == "cpu"


async def test_existing_slot_not_overwritten(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    slots = home / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    path = slots / "embed.toml"
    path.write_bytes(b'name = "embed"\nport = 9123\ndevice = "gpu-vulkan"\n# operator edit\n')
    before = path.read_bytes()
    manager = _manager(home)

    await _orch(home, manager)._ensure_slot_exists("embed", _selection())

    assert path.read_bytes() == before


async def test_image_model_install_creates_img_slot_from_seed(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = Path(tmp_hal0_home)
    manager = _manager(home)
    app = SimpleNamespace(
        state=SimpleNamespace(
            slot_manager=manager,
            image_install_jobs={},
            image_tree_lock=asyncio.Lock(),
        )
    )
    monkeypatch.setattr(comfyui_routes, "_schedule_install", lambda *a, **k: None)
    monkeypatch.setattr(comfyui_routes.comfyui_install, "require_one_filesystem", lambda: None)
    monkeypatch.setattr(
        comfyui_routes.comfyui_install, "require_writable_destinations", lambda m: None
    )
    monkeypatch.setattr(comfyui_routes.comfyui_install, "preflight_disk", lambda m: None)
    assert not _slot_file(home, "img").exists()

    await comfyui_routes.start_image_install(app, "sdxl-lightning")  # type: ignore[arg-type]

    raw = _read(_slot_file(home, "img"))
    seed = _seed("img")
    # img keeps the seed's own device: it is ROCm-only and not derived from the box.
    assert raw["port"] == seed["port"]
    assert raw["profile"] == seed["profile"]
    assert raw["device"] == seed["device"] == "gpu-rocm"
    assert "img" in manager.identity_names()
