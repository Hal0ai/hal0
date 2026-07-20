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
    RUNNER_IMAGES,
    STALE_RUNNER_IMAGE_REFS,
    Runner,
    RunnerSupports,
    get_runner,
    runner_for_backend,
    runner_matches,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_REAL_FAMILIES = {"llama-server", "flm", "kokoro", "qwen3tts", "comfyui"}
_REAL_DEVICE_CLASSES = {"gpu", "cpu", "npu", "img"}


def test_registry_has_every_expected_key() -> None:
    assert set(RUNNER_IMAGES) == {
        "rocmfpx",
        "vulkanfpx",
        "cuda",
        "cpu",
        "flm",
        "kokoro",
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
        ("vulkan", "gpu", "vulkanfpx"),
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
    # Both sides declare a backend and disagree -> reject.
    assert runner_matches(rocm_runner, device_class="gpu", backend="vulkan") is False
    # Same backend -> accept.
    assert runner_matches(rocm_runner, device_class="gpu", backend="rocm") is True
    # Caller has no opinion on backend -> never vetoes over it.
    assert runner_matches(rocm_runner, device_class="gpu", backend=None) is True
    # Backend-agnostic runner (kokoro) never vetoes on backend either.
    kokoro_runner = RUNNER_IMAGES["kokoro"]
    assert runner_matches(kokoro_runner, device_class="cpu", backend="rocm") is True


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
    """One Vulkan-portable image → both fpx keys advertise (rocm, vulkan);
    device — not BINARY — disambiguates (spec-hw-slot-ownership §2/§4)."""
    assert RUNNER_IMAGES["rocmfpx"].supported_backends == ("rocm", "vulkan")
    assert RUNNER_IMAGES["vulkanfpx"].supported_backends == ("rocm", "vulkan")
