"""resolve_runner_image precedence (§7.1b / ML-4).

Precedence under test: ``HAL0_TOOLBOX_IMAGE_<KEY>`` env override >
``manifest.json`` digest pin (only when the runner has a ``manifest_key``) >
the runner's bundled default image. One resolver now backs every provider
(llama-server HW-gated runners, FLM, kokoro, qwen3-tts, comfyui) — this
replaces the old three-inconsistent-chains story (see the module docstring
in ``hal0.runners``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.runners import RUNNER_IMAGES, get_runner, resolve_runner_image


def _write_manifest(home: str, payload: dict[str, object]) -> None:
    manifest_dir = Path(home) / "etc" / "hal0"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_bare_default_when_nothing_else_set(tmp_hal0_home: str) -> None:
    runner = get_runner("kokoro")
    assert resolve_runner_image(runner) == runner.image


def test_manifest_digest_pin_beats_bundled_default(tmp_hal0_home: str) -> None:
    runner = get_runner("flm")
    _write_manifest(
        tmp_hal0_home,
        {
            "toolbox_images": {
                "flm": {
                    "tag": "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44",
                    "digest": "sha256:" + "a" * 64,
                }
            }
        },
    )
    resolved = resolve_runner_image(runner)
    assert resolved == f"ghcr.io/hal0ai/hal0-toolbox-flm@sha256:{'a' * 64}"
    assert resolved != runner.image


def test_env_override_beats_manifest_digest_pin(tmp_hal0_home: str, monkeypatch) -> None:
    runner = get_runner("flm")
    _write_manifest(
        tmp_hal0_home,
        {
            "toolbox_images": {
                "flm": {
                    "tag": "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44",
                    "digest": "sha256:" + "a" * 64,
                }
            }
        },
    )
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_FLM", "ghcr.io/dev/flm-override:test")
    assert resolve_runner_image(runner) == "ghcr.io/dev/flm-override:test"


def test_env_override_beats_bundled_default_with_no_manifest_key(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """rocmfpx/vulkanfpx/cuda/cpu deliberately carry manifest_key=None (see the
    module docstring) — the env tier still applies even though the manifest
    tier is always skipped for them."""
    runner = get_runner("rocmfpx")
    assert runner.manifest_key is None
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", "ghcr.io/dev/rocmfpx-override:test")
    assert resolve_runner_image(runner) == "ghcr.io/dev/rocmfpx-override:test"


def test_blank_env_override_is_ignored(tmp_hal0_home: str, monkeypatch) -> None:
    runner = get_runner("kokoro")
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_KOKORO", "   ")
    assert resolve_runner_image(runner) == runner.image


def test_no_manifest_key_skips_manifest_tier_even_if_manifest_has_a_match(
    tmp_hal0_home: str,
) -> None:
    """rocmfpx has manifest_key=None by design (see hal0.runners module
    docstring: manifest.json's "rocm"/"vulkan" keys are a DIFFERENT, older
    image lineage) — a manifest entry under an unrelated key must not leak
    into the rocmfpx resolution."""
    runner = get_runner("rocmfpx")
    _write_manifest(
        tmp_hal0_home,
        {
            "toolbox_images": {
                "rocm": {
                    "tag": "ghcr.io/hal0ai/hal0-toolbox-rocm:v1",
                    "digest": "sha256:" + "b" * 64,
                }
            }
        },
    )
    assert resolve_runner_image(runner) == runner.image


def test_malformed_manifest_falls_back_to_default(tmp_hal0_home: str) -> None:
    manifest_dir = Path(tmp_hal0_home) / "etc" / "hal0"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text("not json", encoding="utf-8")
    runner = get_runner("comfyui")
    assert resolve_runner_image(runner) == runner.image


@pytest.mark.parametrize("key", sorted(k for k, r in RUNNER_IMAGES.items() if r.manifest_key))
def test_manifest_tier_applies_to_every_manifest_backed_runner(
    tmp_hal0_home: str, key: str
) -> None:
    runner = get_runner(key)
    digest = "sha256:" + "c" * 64
    _write_manifest(
        tmp_hal0_home,
        {"toolbox_images": {runner.manifest_key: {"tag": runner.image, "digest": digest}}},
    )
    resolved = resolve_runner_image(runner)
    assert resolved.endswith(f"@{digest}")
