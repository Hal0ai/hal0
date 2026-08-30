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
import logging
from pathlib import Path

import pytest

import hal0.runners as runners
from hal0.runners import RUNNER_IMAGES, get_runner, resolve_runner_image


@pytest.fixture(autouse=True)
def _reset_legacy_env_warn_dedup() -> None:
    """The legacy-env-override warning dedups once per (surface, key) per
    process (finding 3) — clear the seen-set before each test so caplog
    assertions here stay deterministic regardless of test order/reruns."""
    runners._warned.clear()


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
    """rocmfpx/cuda/cpu deliberately carry manifest_key=None (see the
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


def test_legacy_alias_env_override_still_resolves(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", raising=False)
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_VULKANFPX", "ghcr.io/x/legacy:1")
    with caplog.at_level(logging.WARNING, logger="hal0.runners"):
        assert resolve_runner_image(get_runner("rocmfpx")) == "ghcr.io/x/legacy:1"
    assert any("VULKANFPX" in r.message for r in caplog.records)


def test_canonical_env_override_beats_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", "ghcr.io/x/canon:1")
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_VULKANFPX", "ghcr.io/x/legacy:1")
    assert resolve_runner_image(get_runner("rocmfpx")) == "ghcr.io/x/canon:1"


def test_promptforge_manifest_digest_pin_resolves(tmp_hal0_home: str) -> None:
    """Post-#1891: promptforge carries a REAL manifest_key (unlike
    rocmfpx, whose manifest key would point at the wrong lineage —
    see the module docstring). A shipped manifest entry must therefore win
    over the bundled tag default."""
    runner = get_runner("promptforge")
    assert runner.manifest_key == "promptforge"
    _write_manifest(
        tmp_hal0_home,
        {
            "toolbox_images": {
                "promptforge": {
                    "tag": "ghcr.io/hal0ai/hal0-promptforge:v2.3-qwen38",
                    "digest": "sha256:" + "c" * 64,
                }
            }
        },
    )
    assert resolve_runner_image(runner) == f"ghcr.io/hal0ai/hal0-promptforge@sha256:{'c' * 64}"
