"""The strix runner: qwen4exp + MTP + strix-halo tuning as a FIRST-CLASS entry.

The variant exists because ../upstream (hal0-combined-upstream:0829) unblocked
the qwen4exp ARCH but carries none of the NextN/MTP speculative-decode wiring,
the --tensor-read-lazy PLE gather, or the gfx1151 Vulkan kernels that
Nathanw1014/llama.cpp's `strix-halo-vulkan` branch ships. Unlike the upstream
variant (pin-only) it is a registry entry — the promptforge shape: optional,
dropdown-selectable, never the AMD default.

Every claim its manifest makes is held here as an executable assertion:
same base digest as the rocmfpx recipe, the DECLARED Vulkan-only cmake flag
divergence (the fork's artifact line is Vulkan-only; GGML_HIP=ON would ship
an untested backend), shared build script and entrypoint — and the registry
entry is held to the manifest (tag lockstep, vulkan lane only, no FPX claim).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE, DEFAULT_STRIX_IMAGE
from hal0.runners import FPX_RUNNER_KEYS, RUNNER_IMAGES

RUNNER = Path(__file__).resolve().parents[2] / "packaging" / "runner"
STRIX = RUNNER / "strix"
ROCMFPX = RUNNER / "rocmfpx"
UPSTREAM = RUNNER / "upstream"

MANIFEST = tomllib.loads((STRIX / "manifest.toml").read_text(encoding="utf-8"))
ROCMFPX_MANIFEST = tomllib.loads((ROCMFPX / "manifest.toml").read_text(encoding="utf-8"))
UPSTREAM_MANIFEST = tomllib.loads((UPSTREAM / "manifest.toml").read_text(encoding="utf-8"))


def test_the_runner_is_not_the_shipped_default() -> None:
    """Same rule as the upstream variant: if this tag ever equals
    DEFAULT_ROCMFPX_IMAGE, the strix runner silently replaced the default and
    every FPX-family model loses its runtime (hal0#1790 SIGSEGV class)."""
    assert MANIFEST["image"]["tag"] != DEFAULT_ROCMFPX_IMAGE
    assert MANIFEST["image"]["tag"] != ROCMFPX_MANIFEST["image"]["tag"]
    assert MANIFEST["image"]["tag"] != UPSTREAM_MANIFEST["image"]["tag"]


def test_the_registry_entry_is_in_lockstep_with_the_recipe() -> None:
    """The manifest names the image the recipe builds; the registry names the
    image the slot pulls. One string, two authorities — this is the pin."""
    assert MANIFEST["image"]["tag"] == DEFAULT_STRIX_IMAGE
    assert RUNNER_IMAGES["strix"].image == DEFAULT_STRIX_IMAGE


def test_the_shipped_pin_matches_the_gate_digest() -> None:
    """The manifest.json entry IS the 2026-08-31 §3-C gate evidence made
    durable (promptforge precedent): tag in lockstep with DEFAULT_STRIX_IMAGE,
    digest exactly the image the ct150+ct151 gate validated."""
    import json

    manifest = json.loads(
        (Path(__file__).resolve().parents[2] / "manifest.json").read_text(encoding="utf-8")
    )
    entry = manifest["toolbox_images"]["strix"]
    assert entry["tag"] == DEFAULT_STRIX_IMAGE
    assert entry["digest"] == (
        "sha256:b50a73487d05bcae146aa54170cec981c7c6a158cc5cd1fa45a6096dfc5fe107"
    )


def test_the_image_is_admitted_to_its_only_lane() -> None:
    """Vulkan is this runner's ONLY lane; without VULKAN_CAPABLE membership
    the launch preflight refuses every launch and the registry entry is a
    dropdown item that can never serve. Membership was earned by the
    2026-08-31 §3-C gate — removing it must be a deliberate revocation."""
    from hal0.config.schema import VULKAN_CAPABLE_IMAGE_REFS

    assert DEFAULT_STRIX_IMAGE in VULKAN_CAPABLE_IMAGE_REFS


def test_the_registry_entry_declares_what_the_manifest_promises() -> None:
    """Vulkan-only build => exactly one GPU lane as fit-check metadata (the
    promptforge single-backend rule, opposite lane); MTP is the headline
    capability; and strix must stay OUT of FPX_RUNNER_KEYS — that exclusion
    is the launch-time guard that refuses FPX-family GGUFs here."""
    runner = RUNNER_IMAGES["strix"]
    assert set(runner.supported_backends) == {"vulkan"}
    assert runner.supports.mtp
    assert runner.format_arch == "gguf"
    assert runner.device_class == "gpu"
    assert "strix" not in FPX_RUNNER_KEYS


def test_the_source_is_the_published_production_snapshot() -> None:
    assert MANIFEST["source"]["repo"] == "https://github.com/myhacsint/llama.cpp.git"


def test_the_source_ref_is_pinned_not_a_branch() -> None:
    ref = MANIFEST["source"]["ref"]
    assert ref not in ("main", "master", "HEAD", "production/strix-halo-qwen4exp-b10669"), ref
    assert len(ref) == 40, f"pin the full 40-char sha, got {ref!r}"
    assert all(c in "0123456789abcdef" for c in ref.lower()), ref


def test_the_base_digest_is_byte_identical_to_the_default_recipe() -> None:
    """Same base digest => same ROCm toolchain lineage across the three GPU
    recipe images; the only intended variable is [source]."""
    assert MANIFEST["base"]["digest"] == ROCMFPX_MANIFEST["base"]["digest"]
    assert MANIFEST["base"]["image"] == ROCMFPX_MANIFEST["base"]["image"]
    assert MANIFEST["base"]["rocm_version"] == ROCMFPX_MANIFEST["base"]["rocm_version"]


def test_the_cmake_flags_are_vulkan_only_by_declaration() -> None:
    """The divergence from rocmfpx is deliberate and exactly this: HIP off,
    Vulkan on, CUDA off, server preserved. A well-meant "restore parity with
    rocmfpx" edit (GGML_HIP=ON) would ship the fork's untested HIP lane —
    see the VULKAN-ONLY manifest block."""
    flags = set(MANIFEST["build"]["cmake_flags"])
    assert "-DGGML_HIP=OFF" in flags
    assert "-DGGML_VULKAN=ON" in flags
    assert "-DGGML_CUDA=OFF" in flags
    assert "-DGGML_HIP=ON" not in flags
    # The entrypoint supervises llama-server; a flag edit that drops the
    # server target would build an image the slot manager cannot launch.
    assert "-DLLAMA_BUILD_SERVER=ON" in flags
    # Reference-build parity (the snapshot's PRODUCTION-SNAPSHOT.md rebuild
    # recipe): Release + static. GGML_NATIVE=ON is the manifest's declared
    # build-host constraint (lxc130, strix-halo-class CPU) — if this
    # assertion is being edited to OFF, edit the manifest note with it.
    assert "-DCMAKE_BUILD_TYPE=Release" in flags
    assert "-DBUILD_SHARED_LIBS=OFF" in flags
    assert "-DGGML_NATIVE=ON" in flags
    assert MANIFEST["build"]["gpu_arch"] == ROCMFPX_MANIFEST["build"]["gpu_arch"]


def test_build_script_and_entrypoint_are_shared_not_copied() -> None:
    """A copy drifts; a symlink cannot. The entrypoint carries the #1936 GPU
    preflight and the #2037/#2126 exit-64 load-death translation the slot
    manager depends on regardless of which llama.cpp is inside."""
    for name in ("build.sh", "entrypoint.sh"):
        link = STRIX / name
        assert link.is_symlink(), f"{name} must be a symlink into ../rocmfpx"
        assert link.resolve() == (ROCMFPX / name).resolve()


def test_the_patch_series_is_deliberately_empty() -> None:
    """Candidates (adaptive MTP draft sizing, skinny prefill) are named in the
    manifest header; an entry appearing here deserves a review with a public
    PR reference, never a blind diff from a field report."""
    assert not MANIFEST.get("patches"), "recipe declares patches — see manifest header"
    assert not list((STRIX / "patches").glob("*.patch"))
