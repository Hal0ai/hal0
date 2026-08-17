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


def test_stack_apply_defaults_to_rocm_when_an_entry_omits_device() -> None:
    """The apply path's fallback used to be ``gpu-vulkan`` (#1888)."""
    import inspect

    from hal0.api.routes import stacks as stacks_mod

    src = inspect.getsource(stacks_mod)
    assert 'entry.device or "gpu-rocm"' in src
    assert 'entry.device or "gpu-vulkan"' not in src
