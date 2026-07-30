"""Install-time brain-model provisioning (v1.0, Stream A).

Covers the three things that can silently break the platform steward:

* the quant choice — a Q8/Q4 build carries custom GGML tensor type ids that
  stock llama.cpp REJECTS, so pulling one onto a CPU-only box means a gigabyte
  downloaded for a container that can never start;
* the pullability of whatever id gets chosen (the old ``brain.toml`` pinned
  ``MiniCPM5-1B-Agentic-Tooluse``, which no catalogue ever defined); and
* the fail-soft contract — a failed pull must leave the slot MODEL-LESS and
  never abort the install.

The pull itself is stubbed: ``run_pull`` is the unit under test elsewhere
(``tests/registry/test_pull.py``) and no test should reach out to
huggingface.co.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.install.brain_model import (
    BRAIN_HF_REPO,
    BRAIN_MODEL_IDS,
    BRAIN_MODEL_PORTABLE,
    BRAIN_MODEL_ROCMFPX,
    BRAIN_MODEL_SMALL,
    BRAIN_SLOT_NAME,
    brain_model_for_hardware,
    main,
    provision_brain_model,
    rocmfpx_capable,
)
from hal0.registry.curated import get_curated

# ── quant choice ────────────────────────────────────────────────────────────


def _hw(*, platform: str = "generic", gpus: list[GPUInfo] | None = None) -> HardwareInfo:
    return HardwareInfo(platform=platform, gpus=gpus or [])


def test_cpu_only_box_gets_the_portable_f16_build() -> None:
    """No GPU → the ONLY variant a stock llama.cpp image can load.

    This is the regression that matters most: the Q8 build is half the size and
    the obvious "default", but its custom tensor type 103 makes it unloadable
    here, so a box with no ROCm/Vulkan device must get F16.
    """
    hw = _hw()
    assert rocmfpx_capable(hw) is False
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_PORTABLE


def test_strix_halo_box_gets_the_rocmfpx_agent_preset() -> None:
    hw = _hw(platform="strix-halo")
    assert rocmfpx_capable(hw) is True
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_ROCMFPX


def test_compute_capable_gpu_box_gets_the_rocmfpx_agent_preset() -> None:
    hw = _hw(gpus=[GPUInfo(vendor="amd", compute_capable=True)])
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_ROCMFPX


def test_vulkan_only_gpu_box_still_gets_the_rocmfpx_agent_preset() -> None:
    """``gpu-vulkan`` resolves to the ``vulkanfpx`` runner row, whose image is
    the same ``DEFAULT_ROCMFPX_IMAGE`` — so Vulkan-only is ROCmFPX-capable."""
    hw = _hw(gpus=[GPUInfo(vendor="amd", vulkan_capable=True)])
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_ROCMFPX


def test_override_selects_a_known_variant() -> None:
    hw = _hw(platform="strix-halo")
    assert brain_model_for_hardware(hw, override=BRAIN_MODEL_SMALL) == BRAIN_MODEL_SMALL
    # Whitespace from an env var is tolerated.
    assert brain_model_for_hardware(hw, override=f"  {BRAIN_MODEL_PORTABLE} ") == (
        BRAIN_MODEL_PORTABLE
    )


def test_unknown_override_falls_back_instead_of_404ing() -> None:
    """A typo'd HAL0_BRAIN_MODEL must not become a doomed pull."""
    hw = _hw()
    assert brain_model_for_hardware(hw, override="MiniCPM5-1B-Agentic-Tooluse") == (
        BRAIN_MODEL_PORTABLE
    )


# ── pullability ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model_id", BRAIN_MODEL_IDS)
def test_every_brain_variant_is_pullable_from_the_public_repo(model_id: str) -> None:
    """Each declared variant must resolve to real HF coordinates in the PUBLIC
    GGUF repo. The base repo (``Hal0ai/hal0-brain-sft``) is private and
    safetensors-only, and no chat runner consumes safetensors."""
    curated = get_curated(model_id)
    assert curated is not None, f"{model_id!r} missing from CURATED_MODELS"
    assert curated.hf_repo == BRAIN_HF_REPO
    assert curated.hf_file.endswith(".gguf")
    assert curated.capability == "chat"


def test_the_three_variants_are_distinct_files() -> None:
    files = {get_curated(m).hf_file for m in BRAIN_MODEL_IDS}  # type: ignore[union-attr]
    assert len(files) == len(BRAIN_MODEL_IDS)


# ── provisioning ────────────────────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self) -> None:
        self.ensured: list[str] = []

    def ensure(self, model_id: str) -> None:
        self.ensured.append(model_id)


class _FakeSlotManager:
    """Records the config writes ``run_pull_and_activate`` performs."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def update_config(self, slot: str, updates: dict[str, Any]) -> None:
        self.updates.append((slot, updates))


def _stub_run_pull(monkeypatch: pytest.MonkeyPatch, *, state: str, exc: Exception | None = None):
    """Replace ``registry.pull.run_pull`` with a no-network stub.

    ``run_pull_and_activate`` imports it lazily from the module, so patching the
    module attribute is what lands.
    """
    import hal0.registry.pull as pull_mod

    async def _fake(job, **kwargs):  # noqa: ANN001, ANN003
        if exc is not None:
            raise exc
        job.state = state

    monkeypatch.setattr(pull_mod, "run_pull", _fake)


def test_successful_pull_binds_the_model_to_the_brain_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_run_pull(monkeypatch, state="completed")
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    landed = asyncio.run(
        provision_brain_model(hw=_hw(platform="strix-halo"), slot_manager=sm, registry=reg)
    )
    assert landed == BRAIN_MODEL_ROCMFPX
    assert reg.ensured == [BRAIN_MODEL_ROCMFPX]
    assert sm.updates == [(BRAIN_SLOT_NAME, {"model": {"default": BRAIN_MODEL_ROCMFPX}})]


def test_failed_pull_leaves_the_slot_model_less_and_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling 7: warn, seed model-less, install still succeeds.

    ``run_pull_and_activate`` must NOT stamp ``[model].default`` — writing an id
    for bytes that never landed is the start-before-model race (#1108), and
    since model-presence IS activation (#1369) withholding it parks the slot.
    """
    _stub_run_pull(monkeypatch, state="failed")
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    with pytest.raises(RuntimeError):
        asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert sm.updates == [(BRAIN_SLOT_NAME, {"meta": {"pull_failed": True}})]
    assert all("model" not in upd for _, upd in sm.updates)


def test_raising_pull_also_leaves_the_slot_model_less(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_run_pull(monkeypatch, state="running", exc=OSError("network is unreachable"))
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    with pytest.raises(OSError, match="unreachable"):
        asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert sm.updates == [(BRAIN_SLOT_NAME, {"meta": {"pull_failed": True}})]


# ── the install.sh entry point never raises ─────────────────────────────────


def test_main_returns_nonzero_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """install.sh runs under ``set -euo pipefail`` behind a ``|| warn``.

    A traceback escaping ``main()`` would abort the whole install over an
    optional model — exactly what ruling 7 forbids. So EVERY failure must come
    back as exit code 1.
    """
    import hal0.install.brain_model as bm

    def _boom() -> None:
        raise RuntimeError("hardware probe exploded")

    monkeypatch.setattr(bm, "_load_hardware", _boom)
    assert main() == 1


def test_main_returns_zero_on_a_successful_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.install.brain_model as bm

    monkeypatch.setattr(bm, "_load_hardware", lambda: _hw(platform="strix-halo"))
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (_FakeSlotManager(), _FakeRegistry()),
    )
    _stub_run_pull(monkeypatch, state="completed")
    assert main() == 0
