"""#2023 — static slot seeds must go through ``derive_device``, not ship verbatim.

install.sh's "Container slot seeds" loop and its Python twin
(:func:`hal0.install.static_seeds.seed_static_slots`) copy
``installer/etc-hal0/slots/*.toml`` byte-for-byte, and every llama.cpp-backed
seed ships ``device = "gpu-rocm"``. On a kfd-less box the load-time gate
(``_gpu.require_kfd_for_gpu_slot``) then refuses every one of them, so a fresh
install has ZERO loadable LLM slots and the autoloading ``brain`` anchor sits
in ``state=error`` from t=0.

These tests pin the fix: seeding routes the llama.cpp seeds through the same
hardware derivation the rest of the platform uses
(:func:`hal0.install.profile_derive.derive_device`), so the seeded device can
never contradict the host:

* kfd-less render-node box → ``gpu-vulkan`` (VULKAN_CAPABLE_IMAGE_REFS-gated),
* no-GPU box (the ct163 shape) → ``cpu``,
* kfd-present box → still ``gpu-rocm`` (operator ruling: ROCm stays the
  default when the compute node is there).

Non-llama seeds (flm=npu, tts=kokoro, img=comfyui, qwen3tts) have their own
device logic and must keep their shipped device verbatim.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.install.static_seeds import (
    LLAMA_SEED_CAPABILITIES,
    STATIC_SEED_SLOTS,
    apply_derived_seed_devices,
    seed_static_slots,
)

#: The real repo tree — the seeds a fresh install actually receives.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_SRC_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def _seeded_devices(dest: Path) -> dict[str, str | None]:
    return {
        p.stem: tomllib.loads(p.read_text(encoding="utf-8")).get("device")
        for p in sorted(dest.glob("*.toml"))
    }


def _kfd_less_render_node_box(monkeypatch: pytest.MonkeyPatch) -> HardwareInfo:
    """The ct151 shape: AMD render node, no /dev/kfd, Vulkan-capable runner."""
    monkeypatch.setattr("hal0.install.profile_derive.kfd_present", lambda *a, **k: False)
    monkeypatch.setattr(
        "hal0.install.profile_derive.default_image_serves_vulkan_lane",
        lambda *a, **k: True,
    )
    return HardwareInfo(gpus=[GPUInfo(vendor="amd", compute_capable=False, vulkan_capable=True)])


def _kfd_present_rocm_box(monkeypatch: pytest.MonkeyPatch) -> HardwareInfo:
    monkeypatch.setattr("hal0.install.profile_derive.kfd_present", lambda *a, **k: True)
    monkeypatch.setattr(
        "hal0.install.profile_derive.default_image_serves_vulkan_lane",
        lambda *a, **k: True,
    )
    return HardwareInfo(gpus=[GPUInfo(vendor="amd", compute_capable=True, vulkan_capable=True)])


def _no_gpu_box(monkeypatch: pytest.MonkeyPatch) -> HardwareInfo:
    """The ct163 shape: privileged container, zero GPU of any kind."""
    monkeypatch.setattr("hal0.install.profile_derive.kfd_present", lambda *a, **k: False)
    monkeypatch.setattr(
        "hal0.install.profile_derive.default_image_serves_vulkan_lane",
        lambda *a, **k: True,
    )
    return HardwareInfo(gpus=[])


# ── seed_static_slots (the api-lifespan gap-closer) ──────────────────────────


def test_kfd_less_host_seeds_no_gpu_rocm_llama_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #2023 pin: a fresh kfd-less box must not receive a single
    llama.cpp slot labelled ``gpu-rocm`` — that device can only refuse."""
    hw = _kfd_less_render_node_box(monkeypatch)
    dest = tmp_path / "slots"
    seeded = seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=hw)
    assert set(seeded) == set(STATIC_SEED_SLOTS)
    devices = _seeded_devices(dest)
    offenders = [name for name in LLAMA_SEED_CAPABILITIES if devices.get(name) == "gpu-rocm"]
    assert not offenders, (
        f"llama.cpp seeds still ship the un-loadable ROCm lane on a kfd-less box: {offenders}"
    )
    for name in LLAMA_SEED_CAPABILITIES:
        assert devices[name] == "gpu-vulkan", (name, devices[name])


def test_kfd_present_host_still_derives_gpu_rocm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator ruling: ROCm stays the default wherever /dev/kfd is usable."""
    hw = _kfd_present_rocm_box(monkeypatch)
    dest = tmp_path / "slots"
    seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=hw)
    devices = _seeded_devices(dest)
    for name in LLAMA_SEED_CAPABILITIES:
        assert devices[name] == "gpu-rocm", (name, devices[name])


def test_no_gpu_host_derives_cpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hw = _no_gpu_box(monkeypatch)
    dest = tmp_path / "slots"
    seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=hw)
    devices = _seeded_devices(dest)
    for name in LLAMA_SEED_CAPABILITIES:
        assert devices[name] == "cpu", (name, devices[name])


def test_non_llama_seeds_keep_their_shipped_device_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """flm (npu), tts (kokoro/cpu), img (comfyui) and qwen3tts have their own
    device logic — derivation must not touch them, whatever the host."""
    hw = _kfd_less_render_node_box(monkeypatch)
    dest = tmp_path / "slots"
    seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=hw)
    devices = _seeded_devices(dest)
    for name in STATIC_SEED_SLOTS:
        if name in LLAMA_SEED_CAPABILITIES:
            continue
        shipped = tomllib.loads((_SEED_SRC_DIR / f"{name}.toml").read_text(encoding="utf-8")).get(
            "device"
        )
        assert devices[name] == shipped, (name, devices[name], shipped)


def test_unresolvable_hardware_keeps_verbatim_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-soft: when no hardware fact can be resolved the copy still lands
    (today's verbatim behaviour) rather than aborting the seed pass."""
    monkeypatch.setattr("hal0.install.static_seeds._resolve_hardware_info", lambda: None)
    dest = tmp_path / "slots"
    seeded = seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest)
    assert set(seeded) == set(STATIC_SEED_SLOTS)
    devices = _seeded_devices(dest)
    shipped = tomllib.loads((_SEED_SRC_DIR / "agent.toml").read_text(encoding="utf-8")).get(
        "device"
    )
    assert devices["agent"] == shipped


def test_llama_seed_capabilities_avoid_the_npu_only_lanes() -> None:
    """``derive_device("agent", ...)`` is the NPU chat lane and returns None on
    every non-NPU box; the seeded ``agent``/``brain`` slots are the CHAT
    capability's slots (ADR-0023 / setup_command._SETUP_SLOTS). Pin the mapping
    so nobody 'simplifies' it back to the seed names."""
    assert LLAMA_SEED_CAPABILITIES["agent"] == "chat"
    assert LLAMA_SEED_CAPABILITIES["brain"] == "chat"
    assert set(LLAMA_SEED_CAPABILITIES) == {
        "agent",
        "brain",
        "coder",
        "embed",
        "rerank",
        "utility",
    }


# ── apply_derived_seed_devices (the install.sh bash-loop follow-up pass) ─────


def test_apply_derived_seed_devices_rewrites_verbatim_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.sh's bash loop copies verbatim, then calls this pass over the
    freshly seeded names — the result must match what seed_static_slots
    derives, so both seeding paths agree."""
    hw = _kfd_less_render_node_box(monkeypatch)
    dest = tmp_path / "slots"
    dest.mkdir()
    for name in STATIC_SEED_SLOTS:
        shutil.copyfile(_SEED_SRC_DIR / f"{name}.toml", dest / f"{name}.toml")
    rewritten = apply_derived_seed_devices(STATIC_SEED_SLOTS, slots_dir=dest, hw=hw)
    assert rewritten == {name: "gpu-vulkan" for name in LLAMA_SEED_CAPABILITIES}
    devices = _seeded_devices(dest)
    for name in LLAMA_SEED_CAPABILITIES:
        assert devices[name] == "gpu-vulkan", (name, devices[name])
    assert devices["flm"] == "npu"
    assert devices["tts"] == "cpu"


def test_apply_derived_seed_devices_noop_when_device_already_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hw = _kfd_present_rocm_box(monkeypatch)
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "agent.toml", dest / "agent.toml")
    before = (dest / "agent.toml").read_text(encoding="utf-8")
    rewritten = apply_derived_seed_devices(("agent",), slots_dir=dest, hw=hw)
    assert rewritten == {}
    assert (dest / "agent.toml").read_text(encoding="utf-8") == before


def test_apply_derived_seed_devices_ignores_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tombstoned / already-existing slot is never copied by the bash loop,
    so its name may reach the pass with no file present — skip, don't raise."""
    hw = _kfd_less_render_node_box(monkeypatch)
    dest = tmp_path / "slots"
    dest.mkdir()
    rewritten = apply_derived_seed_devices(("agent",), slots_dir=dest, hw=hw)
    assert rewritten == {}
