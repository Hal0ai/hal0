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


def test_rerank_suggestions_returned():
    out = suggest_models("rerank", _hw(96), limit=3)
    assert out, "expected curated rerank picks"
    assert all(s.capability == "rerank" for s in out)


def test_stt_suggestions_are_empty_without_a_curated_runtime():
    # _CAP_MATCH["stt"] maps to the curated "stt" capability (not "asr"). No
    # installable curated STT row exists any more: the whisper.cpp rows were
    # dropped (#2691, nothing could serve them), Moonshine weights are
    # operator-staged by design, and the NPU lane is the FLM trio shadow, not
    # a pick. An answers-file `stt` slot with model_id "auto" therefore skips
    # cleanly instead of pulling weights no runtime can load.
    from hal0.install.suggest import _CAP_MATCH

    assert _CAP_MATCH["stt"] == ("stt",)
    assert suggest_models("stt", _hw(96), limit=3) == []


def test_embed_suggestions_returned():
    out = suggest_models("embed", _hw(96), limit=3)
    assert out and all(s.capability == "embed" for s in out)


def test_npu_opt_in_threads_into_advertised_device():
    # The device a suggestion advertises is derive_device's answer for the
    # SAME npu_opt_in value apply_setup uses, so the picker never advertises
    # a lane apply_setup would skip (#1109). STT no longer has a curated pick
    # (#2691), so the lane itself is checked through derive_device and the
    # suggestion list stays empty either way.
    from hal0.install.profile_derive import derive_device

    hw = _hw(96, npu=True)
    assert derive_device("stt", hw, npu_opt_in=True) == "npu"
    assert derive_device("stt", hw, npu_opt_in=False) is None
    assert suggest_models("stt", hw, limit=3, npu_opt_in=True) == []
    assert suggest_models("stt", hw, limit=3) == []
