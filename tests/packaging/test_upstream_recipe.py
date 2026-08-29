"""The upstream runner variant must ADD qwen4exp without LOSING anything.

The variant exists because Qwen3.8-Flash-Next's `qwen4exp` architecture
landed upstream (ggml-org/llama.cpp #27742, 2026-08-27) after the fork the
default runner builds from last synced (charlie12345/ROCmFPX @ c49ebdbd,
2026-08-22). The safe shape is a SECOND image from pristine upstream, pinned
to a slot via `image_pin` — never a replacement of the default.

Every preservation claim the variant's manifest makes is held here as an
executable assertion against the rocmfpx recipe it claims to mirror:
same base digest, same cmake flag set, shared build script and entrypoint.
If either recipe drifts, the diff shows up as a red test naming the exact
property lost — not as a slot that stops loading three weeks later.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE

RUNNER = Path(__file__).resolve().parents[2] / "packaging" / "runner"
UPSTREAM = RUNNER / "upstream"
ROCMFPX = RUNNER / "rocmfpx"

MANIFEST = tomllib.loads((UPSTREAM / "manifest.toml").read_text(encoding="utf-8"))
ROCMFPX_MANIFEST = tomllib.loads(
    (ROCMFPX / "manifest.toml").read_text(encoding="utf-8")
)


def test_the_variant_is_not_the_shipped_default() -> None:
    """The whole safety story: the default pin keeps the fork build with the
    FPX quant formats and the minicpm5/lfm2.5 patches. If this tag ever equals
    DEFAULT_ROCMFPX_IMAGE, the variant silently replaced the default and every
    FPX-family model loses its runtime."""
    assert MANIFEST["image"]["tag"] != DEFAULT_ROCMFPX_IMAGE
    assert MANIFEST["image"]["tag"] != ROCMFPX_MANIFEST["image"]["tag"]


def test_the_source_is_pristine_upstream_not_a_fork() -> None:
    assert MANIFEST["source"]["repo"] == "https://github.com/ggml-org/llama.cpp.git"


def test_the_source_ref_is_pinned_not_a_branch() -> None:
    ref = MANIFEST["source"]["ref"]
    assert ref not in ("main", "master", "HEAD"), ref
    assert len(ref) == 40, f"pin the full 40-char sha, got {ref!r}"
    assert all(c in "0123456789abcdef" for c in ref.lower()), ref


def test_the_base_digest_is_byte_identical_to_the_default_recipe() -> None:
    """Same base digest => same ROCm toolchain lineage in both images. The
    variant's only intended variable is [source]; a base drift would mean two
    toolchains on one box with one recipe claiming otherwise."""
    assert MANIFEST["base"]["digest"] == ROCMFPX_MANIFEST["base"]["digest"]
    assert MANIFEST["base"]["image"] == ROCMFPX_MANIFEST["base"]["image"]
    assert (
        MANIFEST["base"]["rocm_version"] == ROCMFPX_MANIFEST["base"]["rocm_version"]
    )


def test_the_cmake_flags_preserve_combined_hip_plus_vulkan() -> None:
    """Flag-set identity IS the preservation claim for the combined image
    property (both the rocmfpx and vulkanfpx lanes run one tag). Comparing as
    sets against the default recipe means a flag dropped from either manifest
    names itself in the diff."""
    assert set(MANIFEST["build"]["cmake_flags"]) == set(
        ROCMFPX_MANIFEST["build"]["cmake_flags"]
    )
    assert MANIFEST["build"]["gpu_arch"] == ROCMFPX_MANIFEST["build"]["gpu_arch"]


def test_build_script_and_entrypoint_are_shared_not_copied() -> None:
    """A copy drifts; a symlink cannot. The entrypoint carries the #1936 GPU
    preflight and the #2037 exit-64 load-death translation — supervision
    features the slot manager depends on regardless of which llama.cpp is
    inside."""
    for name in ("build.sh", "entrypoint.sh"):
        link = UPSTREAM / name
        assert link.is_symlink(), f"{name} must be a symlink into ../rocmfpx"
        assert link.resolve() == (ROCMFPX / name).resolve()


def test_the_patch_series_is_deliberately_empty() -> None:
    """The fork patches target fork-only gaps (minicpm5, lfm2.5, the fork's
    glslc race). Applying them to a tree they no longer match is how ports go
    wrong; an entry appearing here deserves a review, not an auto-apply."""
    assert not MANIFEST.get("patches"), "variant declares patches — see manifest header"
    assert not list((UPSTREAM / "patches").glob("*.patch"))
