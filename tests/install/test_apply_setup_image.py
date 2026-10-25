"""``apply_setup`` and image models: an ``img`` selection installs through the image catalog.

Image models are not in the registry (``get_curated`` knows none), so an ``img`` selection
never makes a registry ``PullPlan``. It creates the slot model-less and hands the catalog id
back in ``SetupResult.image_installs`` for the caller to start the catalog install.
"""

from __future__ import annotations

import pytest

from hal0.capabilities import catalog as capability_catalog
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install import orchestrate
from hal0.install.orchestrate import Selections, SlotSelection
from hal0.slots.state import SlotConfigError, SlotNotFound

pytestmark = pytest.mark.anyio

_SDXL = "sdxl-lightning"


class _FakeSlotManager:
    def __init__(self, existing: tuple[str, ...] = ()) -> None:
        self.created: dict[str, dict] = {}
        self._existing = set(existing)

    async def get_config(self, name: str) -> dict:
        if name in self._existing or name in self.created:
            return {"name": name}
        raise SlotNotFound(f"slot {name!r} is not configured")

    async def create(self, name: str, cfg: dict) -> object:
        if name in self._existing or name in self.created:
            raise SlotConfigError(f"slot {name!r} already exists; use update to modify it")
        self.created[name] = cfg
        return object()


def _strix_hw() -> HardwareInfo:
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=98304,
        ram_available_mb=90000,
        unified_memory_mb=98304,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=True),
    )


@pytest.fixture(autouse=True)
def rocm_lane(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A box with the ROCm lane (kfd + render node): derive_device yields ``gpu-rocm``."""
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda *a, **k: True)
    monkeypatch.setattr("hal0.providers._gpu.render_node_present", lambda *a, **k: True)
    monkeypatch.setattr(capability_catalog, "comfyui_backends", lambda: ["gpu-vulkan"])


def _selections(*slots: SlotSelection) -> Selections:
    return Selections(storage_dir="", slots=list(slots), extensions={}, npu_opt_in=False)


def _img(model_id: str | None = _SDXL) -> SlotSelection:
    return SlotSelection(capability="img", slot_name="img", port=8188, model_id=model_id)


async def _apply(sm: _FakeSlotManager, selections: Selections) -> orchestrate.SetupResult:
    return await orchestrate.apply_setup(
        selections,
        hardware=_strix_hw(),
        slot_manager=sm,
        registry={},
        jobs={},
        write_sentinel=False,
    )


async def test_apply_setup_routes_img_to_catalog() -> None:
    sm = _FakeSlotManager()
    jobs: dict = {}
    res = await orchestrate.apply_setup(
        _selections(_img()),
        hardware=_strix_hw(),
        slot_manager=sm,
        registry={},
        jobs=jobs,
        write_sentinel=False,
    )

    assert res.image_installs == [_SDXL]
    # No registry pull: no plan, no pull-job bookkeeping, and the id is not a registry model.
    assert res.pulls == []
    assert res.model_ids == []
    assert jobs == {}
    out = res.slots[0]
    assert out.created is True and out.skipped is None and out.error is None
    assert out.model_id == _SDXL
    assert out.pull_job_id is None
    # Born model-less, on the device the img slot has (the one ROCm-only lane).
    assert sm.created["img"]["model"]["default"] == ""
    assert sm.created["img"]["device"] == "gpu-rocm"
    assert sm.created["img"]["port"] == 8188


async def test_apply_setup_img_existing_slot_still_queues() -> None:
    """The installer seeds ``img`` on every box, so the slot exists; the model installs anyway."""
    sm = _FakeSlotManager(existing=("img",))

    res = await _apply(sm, _selections(_img()))

    assert res.image_installs == [_SDXL]
    out = res.slots[0]
    assert out.created is False
    assert out.error is None and out.skipped is None
    assert sm.created == {}  # the seeded slot was not touched


async def test_apply_setup_img_slot_create_failure_queues_nothing() -> None:
    class _Broken(_FakeSlotManager):
        async def create(self, name: str, cfg: dict) -> object:
            raise SlotConfigError("failed to write slot config")

    res = await _apply(_Broken(), _selections(_img()))

    assert res.image_installs == []
    assert res.slots[0].created is False
    assert "failed to write slot config" in (res.slots[0].error or "")


async def test_apply_setup_img_without_rocm_lane_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability_catalog, "comfyui_backends", lambda: [])
    sm = _FakeSlotManager()

    res = await _apply(sm, _selections(_img()))

    assert res.image_installs == []
    assert res.slots[0].skipped == "image_engine_unavailable"
    assert res.slots[0].created is False
    assert sm.created == {}  # no slot for an image engine this host cannot run


async def test_apply_setup_unknown_img_id_reason() -> None:
    sm = _FakeSlotManager()

    # A dropped GGUF id: no converter, no registry entry, no catalog entry.
    res = await _apply(sm, _selections(_img("SD-Turbo-GGUF")))

    assert res.image_installs == []
    assert res.slots[0].skipped == "image_model_unknown"
    assert sm.created == {}


async def test_apply_setup_img_scaffold_is_unchanged() -> None:
    """An img selection with no model is the pick-free scaffold: a slot, no install."""
    sm = _FakeSlotManager()

    res = await _apply(sm, _selections(_img(None)))

    assert res.image_installs == []
    assert res.slots[0].created is True
    assert sm.created["img"]["model"]["default"] == ""


async def test_apply_setup_img_beside_a_chat_pull() -> None:
    """Image installs and registry pulls are separate lists in one run."""
    sm = _FakeSlotManager()
    chat = SlotSelection(capability="chat", slot_name="agent", port=8081, model_id="qwen3-4b")

    res = await _apply(sm, _selections(chat, _img()))

    assert res.image_installs == [_SDXL]
    assert [p.model_id for p in res.pulls] == ["qwen3-4b"]
    assert res.model_ids == ["qwen3-4b"]
