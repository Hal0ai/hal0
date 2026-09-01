"""RUNNER_IMAGES registry completeness (§7.1b / ML-4).

Pins the shape invariants the rest of ML-4 depends on: every runner's
``runtime_family`` is one of the real families, every non-``None``
``manifest_key`` actually exists in the shipped ``manifest.json``, and
``get_runner``/``runner_for_backend`` behave as documented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.errors import NotFound
from hal0.runners import (
    CANONICAL_FAMILY,
    FPX_RUNNER_KEYS,
    RUNNER_IMAGES,
    STALE_RUNNER_IMAGE_REFS,
    Runner,
    RunnerSupports,
    canonical_family,
    canonical_runner_key,
    get_runner,
    runner_for_backend,
    runner_matches,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_REAL_FAMILIES = {"llama-server", "flm", "kokoro", "qwen3tts", "moonshine", "comfyui"}
_REAL_DEVICE_CLASSES = {"gpu", "cpu", "npu", "img"}


def test_registry_has_every_expected_key() -> None:
    assert set(RUNNER_IMAGES) == {
        "rocmfpx",
        "promptforge",
        "strix",
        "cuda",
        "cpu",
        "flm",
        "kokoro",
        "moonshine",
        "qwen3tts",
        "comfyui",
    }


@pytest.mark.parametrize("key", sorted(RUNNER_IMAGES))
def test_every_runner_shape_is_sane(key: str) -> None:
    runner = RUNNER_IMAGES[key]
    assert isinstance(runner, Runner)
    assert runner.key == key
    assert runner.image  # non-empty
    assert runner.runtime_family in _REAL_FAMILIES
    assert runner.device_class in _REAL_DEVICE_CLASSES
    assert isinstance(runner.supports, RunnerSupports)


def test_every_manifest_key_exists_in_manifest_json() -> None:
    manifest = json.loads((_REPO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    toolbox_images = manifest["toolbox_images"]
    for runner in RUNNER_IMAGES.values():
        if runner.manifest_key is not None:
            assert runner.manifest_key in toolbox_images, (
                f"runner {runner.key!r} points at manifest_key "
                f"{runner.manifest_key!r}, which is missing from manifest.json"
            )


def test_get_runner_known_key() -> None:
    assert get_runner("flm") is RUNNER_IMAGES["flm"]


def test_get_runner_unknown_key_raises_not_found() -> None:
    with pytest.raises(NotFound):
        get_runner("does-not-exist")


@pytest.mark.parametrize(
    "backend,device_class,expected_key",
    [
        ("cuda", "gpu", "cuda"),
        ("cuda", None, "cuda"),
        (None, "cpu", "cpu"),
        ("cpu", None, "cpu"),
        ("vulkan", "gpu", "rocmfpx"),
        ("rocm", "gpu", "rocmfpx"),
        (None, None, "rocmfpx"),
        ("", "gpu", "rocmfpx"),
    ],
)
def test_runner_for_backend_hw_gate_parity(
    backend: str | None, device_class: str | None, expected_key: str
) -> None:
    """Same HW-gate table the old resolve_default_image implemented."""
    assert runner_for_backend(backend, device_class).key == expected_key


def test_runner_matches_device_class_gate() -> None:
    cpu_runner = RUNNER_IMAGES["cpu"]
    assert runner_matches(cpu_runner, device_class="cpu", backend=None) is True
    assert runner_matches(cpu_runner, device_class="gpu", backend=None) is False


def test_runner_matches_backend_gate_only_when_both_declare_one() -> None:
    rocm_runner = RUNNER_IMAGES["rocmfpx"]
    # When runner.supported_backends is declared, backend membership is checked.
    assert runner_matches(rocm_runner, device_class="gpu", backend="vulkan") is True
    assert runner_matches(rocm_runner, device_class="gpu", backend="rocm") is True
    assert runner_matches(rocm_runner, device_class="gpu", backend="cuda") is False
    # Caller has no opinion on backend -> never vetoes over it.
    assert runner_matches(rocm_runner, device_class="gpu", backend=None) is True
    # Runner with supported_backends only accepts those backends.
    kokoro_runner = RUNNER_IMAGES["kokoro"]
    assert runner_matches(kokoro_runner, device_class="cpu", backend="cpu") is True
    assert runner_matches(kokoro_runner, device_class="cpu", backend="rocm") is False


def test_stale_runner_image_refs_alias_matches_schema() -> None:
    from hal0.config.schema import STALE_ROCMFPX_IMAGE_REFS

    assert STALE_RUNNER_IMAGE_REFS == STALE_ROCMFPX_IMAGE_REFS


def test_every_runner_carries_fit_check_metadata() -> None:
    """spec-hw-slot-ownership §4: each RUNNER_IMAGES entry exposes a
    ``supported_backends`` tuple + a ``format_arch`` marker for the fit-check."""
    for key, runner in RUNNER_IMAGES.items():
        assert isinstance(runner.supported_backends, tuple), key
        assert all(isinstance(b, str) for b in runner.supported_backends), key
        assert runner.format_arch, f"{key} missing format_arch"
        # A runner that declares a backend must list it among its supported set
        # (metadata is a superset of the entry's own primary backend).
        if runner.backend is not None:
            assert runner.backend in runner.supported_backends, key


def test_llama_server_runners_report_gguf_format() -> None:
    for key, runner in RUNNER_IMAGES.items():
        if runner.runtime_family == "llama-server":
            assert runner.format_arch == "gguf", key


def test_rocm_and_vulkan_fpx_share_supported_backends() -> None:
    """One Vulkan-portable image → both fpx keys resolve to rocmfpx;
    device — not BINARY — disambiguates (spec-hw-slot-ownership §2/§4)."""
    assert RUNNER_IMAGES["rocmfpx"].supported_backends == ("rocm", "vulkan")
    assert get_runner("vulkanfpx") is get_runner("rocmfpx")


def test_vulkanfpx_key_collapsed() -> None:
    """#2127 follow-up: there is no vulkanFPX binary — the key is gone."""
    assert "vulkanfpx" not in RUNNER_IMAGES


def test_vulkanfpx_alias_resolves_to_rocmfpx() -> None:
    assert canonical_runner_key("vulkanfpx") == "rocmfpx"
    assert canonical_runner_key("rocmfpx") == "rocmfpx"
    assert canonical_runner_key("kokoro") == "kokoro"  # non-aliased passthrough
    assert get_runner("vulkanfpx") is RUNNER_IMAGES["rocmfpx"]


def test_runner_for_backend_vulkan_is_rocmfpx() -> None:
    assert runner_for_backend("vulkan").key == "rocmfpx"


def test_fpx_guard_covers_only_the_canonical_key() -> None:
    assert frozenset({"rocmfpx"}) == FPX_RUNNER_KEYS


def test_runner_matches_consults_supported_backends() -> None:
    """The vulkan lane must match rocmfpx now that the vulkanfpx row is gone.

    The single `backend` field is the default lane, not a veto —
    `supported_backends` is the membership test when declared (§4).
    """
    rocmfpx = RUNNER_IMAGES["rocmfpx"]
    assert runner_matches(rocmfpx, device_class="gpu", backend="vulkan")
    assert runner_matches(rocmfpx, device_class="gpu", backend="rocm")
    assert not runner_matches(rocmfpx, device_class="gpu", backend="cuda")
    # A caller with no opinion on backend (backend=None) never vetoes,
    # regardless of what the runner declares — comfyui actually declares
    # supported_backends=("rocm",) here, so this exercises the `not
    # backend: return True` early return, not the empty-tuple branch.
    assert runner_matches(RUNNER_IMAGES["comfyui"], device_class="img", backend=None)
    # A genuinely backend-agnostic runner (empty supported_backends AND no
    # declared `backend`) never vetoes on backend even when the caller DOES
    # name one — this is the empty-supported_backends branch itself.
    agnostic = Runner(
        "synthetic-agnostic",
        "ghcr.io/example/agnostic:1",
        "flm",
        RunnerSupports(),
        "cpu",
    )
    assert runner_matches(agnostic, device_class="cpu", backend="cuda")


def test_promptforge_shipped_pin_matches_the_gate_digest() -> None:
    """The shipped manifest entry IS the #1891 gate evidence made durable:
    tag must stay in lockstep with DEFAULT_PROMPTFORGE_IMAGE, and the digest
    must be the exact image the ct150 gate validated (report
    2026-08-30, hal0-runner-images images.json pins the same digest)."""
    from hal0.config.schema import DEFAULT_PROMPTFORGE_IMAGE

    manifest = json.loads((_REPO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["toolbox_images"]["promptforge"]
    assert entry["tag"] == DEFAULT_PROMPTFORGE_IMAGE
    assert entry["digest"] == (
        "sha256:370af6e9717e8c96742198a4b920888e6a248d447e21e3432de4d2c913703aea"
    )


# --- canonical family keys (runner-image-catalogue v3, task 11) ------------ #


def test_canonical_family_folds_vulkanfpx() -> None:
    assert canonical_family("vulkanfpx") == "rocmfpx"
    assert canonical_family("rocmfpx") == "rocmfpx"
    assert canonical_family("comfyui") == "comfyui"


def test_canonical_family_map_only_folds_the_fpx_twin() -> None:
    assert CANONICAL_FAMILY == {"vulkanfpx": "rocmfpx"}


# --- arch denylist (model↔runner arch fit-check, hal0#2118) ---------------- #


def test_rocmfpx_denylists_qwen4exp() -> None:
    """hal0#2118: the default ROCmFPX fork build (charlie12345/ROCmFPX @
    c49ebdbd, 2026-08-22) predates upstream's qwen4exp merge (2026-08-27/28),
    so Qwen3.8-Flash-Next fails at load with `unknown model architecture:
    'qwen4exp'`. The denylist entry retires with the fork sync (#2118's
    checklist)."""
    assert "qwen4exp" in RUNNER_IMAGES["rocmfpx"].unsupported_archs


def test_runner_supports_arch_semantics() -> None:
    from hal0.runners import runner_supports_arch

    rocmfpx = RUNNER_IMAGES["rocmfpx"]
    assert not runner_supports_arch(rocmfpx, "qwen4exp")
    # Not on the denylist = not known-broken (never a guarantee).
    assert runner_supports_arch(rocmfpx, "llama")
    # Unknown / unset arch never vetoes — the fit-check stays silent.
    assert runner_supports_arch(rocmfpx, None)
    assert runner_supports_arch(rocmfpx, "")
    # Empty denylist (stock builds) refuses nothing.
    assert runner_supports_arch(RUNNER_IMAGES["cpu"], "qwen4exp")


def test_arch_alternative_images_pair_with_a_denylist_entry() -> None:
    """Every alternative-image hint must correspond to an arch some runner
    actually denylists — a dangling hint is dead weight that outlived its
    retirement (the two tables retire together, see ARCH_ALTERNATIVE_IMAGES)."""
    from hal0.runners import ARCH_ALTERNATIVE_IMAGES

    denylisted = {a for r in RUNNER_IMAGES.values() for a in r.unsupported_archs}
    for arch in ARCH_ALTERNATIVE_IMAGES:
        assert arch in denylisted
    # And the #2118 pairing concretely: qwen4exp → the combined-upstream id.
    assert ARCH_ALTERNATIVE_IMAGES["qwen4exp"] == "hal0ai/hal0-combined-upstream"
# --- display metadata + single-backend invariant (runtime-cascade D2/D5) --- #


def test_single_backend_invariant():
    """D2: the dual-lane privilege belongs solely to the combined default."""
    for key, runner in RUNNER_IMAGES.items():
        if key == "rocmfpx":
            assert len(runner.supported_backends) == 2
            continue
        assert len(runner.supported_backends) <= 1, (
            f"{key} declares {runner.supported_backends}; only rocmfpx may be multi-backend"
        )


def test_llama_server_entries_have_display_metadata():
    """D5: every llama-server runtime needs an operator-facing name + blurb."""
    for key, runner in RUNNER_IMAGES.items():
        if runner.runtime_family != "llama-server":
            continue
        assert runner.title, f"{key} has no title"
        assert runner.blurb, f"{key} has no blurb"


def test_exactly_one_default_gpu_runtime():
    defaults = [k for k, r in RUNNER_IMAGES.items() if r.is_default]
    assert defaults == ["rocmfpx"]
