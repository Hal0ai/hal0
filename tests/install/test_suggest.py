from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.suggest import Suggestion, suggest_models


def _hw(ram_gb, *, amd=True, npu=True, compute=True):
    return HardwareInfo(
        platform="strix-halo" if amd else "generic",
        ram_mb=int(ram_gb * 1024),
        ram_available_mb=int(ram_gb * 1024 * 0.9),
        unified_memory_mb=int(ram_gb * 1024) if amd else 0,
        gpus=[
            GPUInfo(
                vendor="amd" if amd else "intel",
                vram_mb=512,
                compute_capable=compute,
                vulkan_capable=True,
            )
        ],
        npu=NPUInfo(present=npu),
    )


def test_chat_suggestions_fit_ram_and_rank():
    out = suggest_models("chat", _hw(96), limit=3)
    assert out and isinstance(out[0], Suggestion)
    assert all(s.vram_gb_min <= 96 for s in out)  # only fitting picks
    assert sum(1 for s in out if s.recommended) == 1  # exactly one starred
    assert out[0].recommended  # largest-that-fits starred


def test_low_ram_box_excludes_big_models():
    out = suggest_models("chat", _hw(8), limit=5)
    assert all(s.vram_gb_min <= 8 for s in out)


def test_coder_capability_filters_to_coder_models():
    out = suggest_models("coder", _hw(96), limit=3)
    assert out, "expected at least one coder pick"
    assert all(s.capability in ("coder", "chat") for s in out)


def test_excludes_bundle_only_entries():
    out = suggest_models("chat", _hw(96), limit=20)
    assert all(not s.bundle_only for s in out)
