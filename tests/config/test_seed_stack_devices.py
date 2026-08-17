"""#1888 — no shipped seed may label an LLM slot ``gpu-vulkan``.

The seed stacks used to pair a ROCm agent with a ``device = "gpu-vulkan"``
utility. On every box with ``/dev/kfd`` that slot actually executed on ROCm
(the field simply lied); on every box without it, it executed on the runner
image's Vulkan backend and emitted invalid tokens for every model. Either way
the label named a backend the slot was not running, so the day-one catalog
must not ship it.

``gpu-vulkan`` itself stays a valid device id — Kokoro TTS, whisper.cpp and
ComfyUI run genuinely-Vulkan images — this only pins the llama.cpp seeds.
"""

from __future__ import annotations

from hal0.config.seeds import seed_stacks


def test_no_seed_stack_slot_declares_gpu_vulkan() -> None:
    offenders = [
        (stack_id, slot.slot, slot.device)
        for stack_id, stack in seed_stacks().items()
        for slot in stack.slots
        if slot.device == "gpu-vulkan"
    ]
    assert not offenders, f"seed stacks still name the unsupported Vulkan LLM lane: {offenders}"


def test_seed_stacks_still_ship_gpu_slots() -> None:
    """Guard against 'fixing' this by deleting every GPU slot."""
    devices = {slot.device for stack in seed_stacks().values() for slot in stack.slots}
    assert "gpu-rocm" in devices


def test_stack_apply_default_device_is_host_resolved_never_vulkan(monkeypatch) -> None:
    """The apply path's fallback for a device-less entry used to be the
    constant ``gpu-vulkan`` (#1888). It is now resolved from the host, and on
    an AMD box with a reachable compute node that means ROCm.
    """
    from hal0.api.routes import stacks as stacks_mod

    monkeypatch.setattr("hal0.install.profile_derive.kfd_present", lambda *a, **k: True)
    assert stacks_mod._default_stack_slot_device() != "gpu-vulkan"


def test_stack_apply_default_device_falls_back_when_the_probe_is_unreadable(
    monkeypatch,
) -> None:
    from hal0.api.routes import stacks as stacks_mod
    from hal0.model_meta import DEFAULT_DEVICE

    monkeypatch.setattr(
        "hal0.config.loader.load_hardware_info",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no probe")),
    )
    assert stacks_mod._default_stack_slot_device() == DEFAULT_DEVICE


def test_no_static_slot_seed_declares_gpu_vulkan() -> None:
    """The seeds a fresh install ACTUALLY receives (#1888).

    ``installer/etc-hal0/slots/*.toml`` is what the installer copies onto a
    box before auto-selection runs, and six of them declared
    ``device = "gpu-vulkan"`` — the label ct151's garbage-serving slots wore.
    The seed-stack check above does not cover this directory, so a fix that
    only touched the stack catalog would have left the real default slots
    untouched.
    """
    import tomllib
    from pathlib import Path

    slots_dir = Path(__file__).resolve().parents[2] / "installer" / "etc-hal0" / "slots"
    offenders = [
        p.name
        for p in sorted(slots_dir.glob("*.toml"))
        if tomllib.loads(p.read_text()).get("device") == "gpu-vulkan"
    ]
    assert not offenders, (
        f"static slot seeds still name the unsupported Vulkan LLM lane: {offenders}"
    )
