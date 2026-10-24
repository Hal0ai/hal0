"""A slot only accepts models its engine can run (FLM <-> NPU, GGUF <-> GPU/CPU).

FLM (``<tag>-FLM``) models run only on the NPU. The GPU/CPU llama.cpp runners
cannot load them, and an NPU slot cannot serve a GGUF. Before this guard the
slot routes accepted the bind and the slot crash-looped at load. The guard sits
on every route that binds a model: create, PUT /config, load and swap.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.slots.config_write import check_model_fits_slot, model_is_flm, slot_is_flm
from hal0.slots.state import SlotConfigError
from tests.fixtures.slot_probe import probe_slot_name

FLM_ID = "qwen3-4b-FLM"
CODE = "slot.model_engine_mismatch"


@pytest.fixture
def container_stub() -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"active": set()}
    provider = MagicMock()
    provider.load_sync = MagicMock(side_effect=lambda cfg, info: state["active"].add(cfg["name"]))
    provider.unload_sync = MagicMock(side_effect=lambda cfg: state["active"].discard(cfg["name"]))
    provider.wait_ready = AsyncMock(return_value=None)
    provider.is_active = MagicMock(side_effect=lambda s: probe_slot_name(s) in state["active"])
    provider.health = AsyncMock(return_value={"ok": True, "status": "healthy"})
    provider.running_image = MagicMock(return_value=None)
    provider.running_argv = MagicMock(return_value=None)
    provider.expected_argv = MagicMock(return_value=None)
    provider.image_present = MagicMock(return_value=True)
    with patch("hal0.providers.container.container_provider", return_value=provider):
        yield state


@pytest.fixture
def slots(tmp_hal0_home: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat.toml").write_text(
        'name = "chat"\nport = 8081\ndevice = "gpu-vulkan"\nprovider = "llama-server"\n'
        'runtime = "container"\nprofile = "vulkan-radv"\n[model]\ndefault = "gguf-a"\n',
        encoding="utf-8",
    )
    (root / "npu.toml").write_text(
        'name = "npu"\nport = 8082\ndevice = "npu"\ntype = "llm"\n'
        '[model]\ndefault = "stale-gguf"\n',
        encoding="utf-8",
    )
    return root


@pytest.fixture
def client(tmp_hal0_home: str) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _flm_installed() -> Iterator[None]:
    with patch("hal0.providers.flm.is_installed_flm_id", side_effect=lambda i: i == FLM_ID):
        yield


def _register(client: TestClient, home: str, mid: str) -> None:
    f = Path(home) / "models" / f"{mid}.gguf"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x00" * 16)
    assert client.post("/api/models", json={"id": mid, "path": str(f)}).status_code == 201


def _code(r: Any) -> str:
    return r.json()["error"]["code"]


# ── pure guard ───────────────────────────────────────────────────────────────


def test_slot_is_flm() -> None:
    assert slot_is_flm({"device": "npu"})
    assert slot_is_flm({"slot": {"device": "npu"}})
    assert slot_is_flm({"provider": "flm"})
    assert not slot_is_flm({"device": "gpu-rocm"})
    assert not slot_is_flm({})


def test_slot_is_flm_via_profile_runtime_family() -> None:
    # Launch dispatch picks FLM from the profile family with no device/provider.
    assert slot_is_flm({"profile": "flm"})
    assert not slot_is_flm({"profile": "no-such-profile-xyz"})
    check_model_fits_slot("flm", {"profile": "flm"}, FLM_ID, None)


def test_model_is_flm_unknown_is_none() -> None:
    assert model_is_flm(FLM_ID, None) is True
    assert model_is_flm("who-knows", None) is None


def test_guard_refuses_only_positive_mismatch() -> None:
    gpu, npu = {"device": "gpu-rocm"}, {"device": "npu"}
    with pytest.raises(SlotConfigError) as ei:
        check_model_fits_slot("chat", gpu, FLM_ID, None)
    assert ei.value.code == CODE
    check_model_fits_slot("chat", gpu, "who-knows", None)
    check_model_fits_slot("flm", npu, FLM_ID, None)
    check_model_fits_slot("flm", npu, "who-knows", None)
    check_model_fits_slot("chat", gpu, None, None)


# ── routes ───────────────────────────────────────────────────────────────────


def test_swap_flm_model_onto_gpu_slot_refused(
    slots: Path, container_stub: dict[str, Any], client: TestClient
) -> None:
    r = client.post("/api/slots/chat/swap", json={"model_id": FLM_ID})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE
    assert 'default = "gguf-a"' in (slots / "chat.toml").read_text()


def test_load_flm_model_onto_gpu_slot_refused(
    slots: Path, container_stub: dict[str, Any], client: TestClient
) -> None:
    r = client.post("/api/slots/chat/load", json={"model_id": FLM_ID})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE


def test_put_config_flm_model_onto_gpu_slot_refused(
    slots: Path, container_stub: dict[str, Any], client: TestClient
) -> None:
    r = client.put("/api/slots/chat/config", json={"model": {"default": FLM_ID}})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE
    assert FLM_ID not in (slots / "chat.toml").read_text()


def test_put_config_moving_slot_to_npu_with_flm_model_passes_guard(
    slots: Path, container_stub: dict[str, Any], client: TestClient
) -> None:
    """Judged on the post-write pair: device and model change together."""
    r = client.put("/api/slots/chat/config", json={"device": "npu", "model": {"default": FLM_ID}})
    assert r.status_code != 400 or _code(r) != CODE, r.text


def test_create_gpu_slot_with_flm_model_refused(
    slots: Path, container_stub: dict[str, Any], client: TestClient
) -> None:
    r = client.post("/api/slots", json={"name": "newchat", "device": "gpu-vulkan", "model": FLM_ID})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE


def test_gguf_model_onto_npu_slot_refused(
    slots: Path,
    container_stub: dict[str, Any],
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    r = client.post("/api/slots/npu/swap", json={"model_id": "gguf-b"})
    assert r.status_code == 400, r.text
    assert _code(r) == CODE


def test_modality_toggle_not_vetoed_by_legacy_gguf_default(
    slots: Path, container_stub: dict[str, Any], client: TestClient
) -> None:
    """A write that carries no model never trips the guard, so a stale GGUF
    default already on an NPU slot cannot block an [npu] toggle."""
    r = client.put("/api/slots/npu/config", json={"npu": {"asr": True}})
    assert r.status_code == 200, r.text


def test_gpu_slot_still_swaps_to_gguf(
    slots: Path,
    container_stub: dict[str, Any],
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    _register(client, tmp_hal0_home, "gguf-b")
    client.post("/api/slots/chat/load")
    r = client.post("/api/slots/chat/swap", json={"model_id": "gguf-b"})
    assert r.status_code == 200, r.text
