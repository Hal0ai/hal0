"""RUNNER_IMAGES registry completeness (ADR-0028).

Pins the shape invariants the rest of hal0 depends on: the runner set, every
runner's ``runtime_family`` / device class, one pin row per runner in the
shipped ``manifest.json``, the tier sets, the naming rule, and
``get_runner``/``runner_for_backend`` behaving as documented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.errors import NotFound
from hal0.runners import (
    RUNNER_IMAGES,
    RUNNER_TIERS,
    Runner,
    RunnerSupports,
    get_runner,
    llama_build_from_tag,
    runner_draft_kinds,
    runner_for_backend,
    runner_matches,
    runner_supports_reasoning_effort,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_REAL_FAMILIES = {"llama-server", "flm", "kokoro", "qwen3tts", "moonshine", "comfyui"}
_REAL_DEVICE_CLASSES = {"gpu", "cpu", "npu", "img"}


def test_registry_has_every_expected_key() -> None:
    assert set(RUNNER_IMAGES) == {
        "standard",
        "cpu",
        "flm",
        "kokoro",
        "moonshine",
        "comfyui",
        "qwen3tts",
        "cuda",
    }


@pytest.mark.parametrize("key", sorted(RUNNER_IMAGES))
def test_every_runner_shape_is_sane(key: str) -> None:
    runner = RUNNER_IMAGES[key]
    assert isinstance(runner, Runner)
    assert runner.key == key
    assert runner.image  # resolves through the pin table
    assert runner.runtime_family in _REAL_FAMILIES
    assert runner.device_class in _REAL_DEVICE_CLASSES
    assert isinstance(runner.supports, RunnerSupports)


def test_manifest_has_exactly_one_pin_row_per_runner() -> None:
    manifest = json.loads((_REPO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["runners"]) == set(RUNNER_IMAGES)
    assert "toolbox_images" not in manifest
    assert "_legacy_toolboxes" not in manifest


@pytest.mark.parametrize("key", sorted(set(RUNNER_IMAGES) - {"cuda"}))
def test_hal0_built_runners_use_one_package_name_scheme(key: str) -> None:
    manifest = json.loads((_REPO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["runners"][key]["image"] == f"ghcr.io/hal0ai/hal0-runner-{key}"


def test_cuda_pins_the_upstream_ggml_org_image() -> None:
    manifest = json.loads((_REPO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["runners"]["cuda"]["image"] == "ghcr.io/ggml-org/llama.cpp"


def test_get_runner_known_key() -> None:
    assert get_runner("flm") is RUNNER_IMAGES["flm"]


@pytest.mark.parametrize("key", ["does-not-exist", "rocmfpx", "vulkanfpx", "strix", "promptforge"])
def test_get_runner_unknown_or_retired_key_raises_not_found(key: str) -> None:
    with pytest.raises(NotFound):
        get_runner(key)


@pytest.mark.parametrize(
    "backend,device_class,expected_key",
    [
        ("cuda", "gpu", "cuda"),
        ("cuda", None, "cuda"),
        (None, "cpu", "cpu"),
        ("cpu", None, "cpu"),
        ("vulkan", "gpu", "standard"),
        ("rocm", "gpu", "standard"),
        (None, None, "standard"),
        ("", "gpu", "standard"),
    ],
)
def test_runner_for_backend(
    backend: str | None, device_class: str | None, expected_key: str
) -> None:
    """ui/src/dash/hw-cascade.js mirrors this table."""
    assert runner_for_backend(backend, device_class).key == expected_key


def test_runner_matches_device_class_gate() -> None:
    cpu_runner = RUNNER_IMAGES["cpu"]
    assert runner_matches(cpu_runner, device_class="cpu", backend=None) is True
    assert runner_matches(cpu_runner, device_class="gpu", backend=None) is False


def test_runner_matches_consults_supported_backends() -> None:
    standard = RUNNER_IMAGES["standard"]
    assert runner_matches(standard, device_class="gpu", backend="vulkan") is True
    assert runner_matches(standard, device_class="gpu", backend="rocm") is True
    assert runner_matches(standard, device_class="gpu", backend="cuda") is False
    # Caller has no opinion on backend -> never vetoes over it.
    assert runner_matches(standard, device_class="gpu", backend=None) is True
    kokoro = RUNNER_IMAGES["kokoro"]
    assert runner_matches(kokoro, device_class="cpu", backend="cpu") is True
    assert runner_matches(kokoro, device_class="cpu", backend="rocm") is False


def test_every_runner_carries_fit_check_metadata() -> None:
    for key, runner in RUNNER_IMAGES.items():
        assert runner.supported_backends, f"{key} declares no supported_backends"
        assert runner.format_arch, f"{key} declares no format_arch"


def test_llama_server_runners_report_gguf_format() -> None:
    for runner in RUNNER_IMAGES.values():
        if runner.runtime_family == "llama-server":
            assert runner.format_arch == "gguf"


def test_only_the_standard_runner_is_multi_backend() -> None:
    """ADR-0006 decision 3 as amended by ADR-0028: the default AMD runner is
    the one image that serves two lanes; every other runner declares one."""
    for key, runner in RUNNER_IMAGES.items():
        if key == "standard":
            assert runner.supported_backends == ("rocm", "vulkan")
            continue
        assert len(runner.supported_backends) == 1, (
            f"{key} declares {runner.supported_backends}; only standard may be multi-backend"
        )


@pytest.mark.parametrize("key", sorted(RUNNER_IMAGES))
def test_every_runner_has_display_metadata(key: str) -> None:
    runner = RUNNER_IMAGES[key]
    assert runner.title, f"{key} has no title"
    assert runner.blurb, f"{key} has no blurb"
    assert "\n" not in runner.blurb, f"{key} blurb must be one line"


@pytest.mark.parametrize("key", sorted(RUNNER_IMAGES))
def test_runner_naming_rule(key: str) -> None:
    """ADR-0027 naming rule: no title or blurb says how the image was
    assembled, and "Standard" names the default AMD llama.cpp runner only."""
    runner = RUNNER_IMAGES[key]
    for word in ("combined", "toolbox", "fpx"):
        assert word not in runner.title.lower()
        assert word not in runner.blurb.lower()
    assert (runner.title == "Standard") is (key == "standard")


def test_tiers_are_the_operator_decided_sets() -> None:
    """ADR-0027 / ADR-0028: the registry's ``tier`` is the only place the
    default set is written down."""
    assert RUNNER_TIERS == ("default", "optional")
    by_tier: dict[str, set[str]] = {t: set() for t in RUNNER_TIERS}
    for key, runner in RUNNER_IMAGES.items():
        assert runner.tier in RUNNER_TIERS, f"{key}: unknown tier {runner.tier!r}"
        by_tier[runner.tier].add(key)
    assert by_tier["default"] == {"standard", "cpu", "flm", "kokoro", "moonshine", "comfyui"}
    assert by_tier["optional"] == {"qwen3tts", "cuda"}


def test_at_most_one_default_per_family_and_device_class() -> None:
    seen: dict[tuple[str, str], str] = {}
    for key, runner in RUNNER_IMAGES.items():
        if runner.tier != "default":
            continue
        shape = (runner.runtime_family, runner.device_class)
        assert shape not in seen, f"{key} and {seen[shape]} are both default for {shape}"
        seen[shape] = key


@pytest.mark.parametrize(
    ("backend", "device_class"),
    [("rocm", "gpu"), ("vulkan", "gpu"), (None, "gpu"), (None, "cpu"), ("cpu", None)],
)
def test_hw_gate_lands_on_a_default_runtime(backend: str | None, device_class: str | None) -> None:
    """A slot with no ``binary`` on AMD or CPU hardware launches a default-tier
    runtime; only the explicit ``cuda`` backend reaches an optional one."""
    assert runner_for_backend(backend, device_class).tier == "default"


_ALL_DRAFT_KINDS = {"simple", "mtp", "dflash", "eagle3", "lilicorr"}


def test_standard_and_cpu_draft_kinds() -> None:
    for key in ("standard", "cpu"):
        assert RUNNER_IMAGES[key].supports.draft_kinds == _ALL_DRAFT_KINDS


def test_cuda_and_non_llama_have_no_draft_kinds() -> None:
    for key, runner in RUNNER_IMAGES.items():
        if key not in ("standard", "cpu"):
            assert runner.supports.draft_kinds == frozenset(), key


def test_llama_build_from_tag() -> None:
    assert llama_build_from_tag("b11716-r52bc433") == 11716
    assert llama_build_from_tag("b11716") == 11716
    assert llama_build_from_tag("ghcr.io/x/y:b11596-r6d1fb9e") == 11596
    assert llama_build_from_tag("ghcr.io/x/llama:rocm-b11596") == 11596
    assert llama_build_from_tag("ghcr.io/x/b2:latest") is None
    assert llama_build_from_tag("ghcr.io/x/b2") is None
    assert llama_build_from_tag("host:5000/x/b2") is None
    assert llama_build_from_tag("0.9.44-re14329d") is None


def test_llama_build_from_digest_pinned_ref() -> None:
    """The shipped refs are ``repo:tag@sha256:<hex>``: the digest must not hide the tag."""
    digest = "@sha256:" + "ab" * 32
    assert llama_build_from_tag("ghcr.io/x/y:b11700-r52bc433" + digest) == 11700
    assert llama_build_from_tag("localhost:5000/x/y:b11700-r1" + digest) == 11700
    assert llama_build_from_tag("ghcr.io/x/y" + digest) is None
    assert llama_build_from_tag("localhost:5000/x/y" + digest) is None
    assert llama_build_from_tag(None) is None


def test_runner_draft_kinds_drops_lilicorr_below_floor() -> None:
    runner = RUNNER_IMAGES["standard"]
    old = runner_draft_kinds(runner, "b11596-r6d1fb9e")
    assert "lilicorr" not in old
    assert "dflash" in old
    assert "lilicorr" in runner_draft_kinds(runner, "b11716-r52bc433")
    assert "lilicorr" not in runner_draft_kinds(runner, "ghcr.io/x/llama:rocm-b11596")
    assert runner_draft_kinds(runner, None) == _ALL_DRAFT_KINDS
    assert runner_draft_kinds(runner, "0.9.44-re14329d") == _ALL_DRAFT_KINDS


def test_digest_pinned_ref_is_gated_by_build() -> None:
    """A digest-pinned ref (the shipped shape) is gated like its bare tag."""
    runner = RUNNER_IMAGES["standard"]
    digest = "@sha256:" + "ab" * 32
    assert "lilicorr" not in runner_draft_kinds(runner, "ghcr.io/x/y:b11596-r6d1fb9e" + digest)
    assert "lilicorr" in runner_draft_kinds(runner, "ghcr.io/x/y:b11716-r52bc433" + digest)
    assert not runner_supports_reasoning_effort(runner, "ghcr.io/x/y:b11700-r1" + digest)
    assert runner_supports_reasoning_effort(runner, "ghcr.io/x/y:b11840-r1" + digest)
    assert runner_supports_reasoning_effort(runner, "ghcr.io/x/y" + digest)  # unparseable: allowed
