"""resolve_runner_image precedence (ADR-0028).

``HAL0_RUNNER_IMAGE_<KEY>`` env override > the ``manifest.json`` pin. One
resolver backs every provider; there is no alias env name and no bundled
fallback literal.
"""

from __future__ import annotations

import pytest

import hal0.runners.pins as pins_mod
from hal0.runners import (
    RUNNER_IMAGES,
    cpu_lane_has_runner_image,
    get_runner,
    resolve_runner_image,
)
from hal0.runners.pins import RunnerPin

_DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> dict[str, RunnerPin]:
    """Every runner pinned to a published tag + digest."""
    table = {
        key: RunnerPin(
            key=key, image=f"ghcr.io/hal0ai/hal0-runner-{key}", tag="b1-r1", digest=_DIGEST
        )
        for key in RUNNER_IMAGES
    }
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    return table


def test_pin_ref_is_the_default(pinned: dict[str, RunnerPin]) -> None:
    runner = get_runner("kokoro")
    assert resolve_runner_image(runner) == f"ghcr.io/hal0ai/hal0-runner-kokoro:b1-r1@{_DIGEST}"
    assert runner.image == resolve_runner_image(runner)


def test_env_override_beats_the_pin(pinned: dict[str, RunnerPin], monkeypatch) -> None:
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_FLM", "ghcr.io/dev/flm-override:test")
    assert resolve_runner_image(get_runner("flm")) == "ghcr.io/dev/flm-override:test"


def test_blank_env_override_is_ignored(pinned: dict[str, RunnerPin], monkeypatch) -> None:
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_KOKORO", "   ")
    assert resolve_runner_image(get_runner("kokoro")) == pinned["kokoro"].ref


def test_old_toolbox_env_name_is_not_read(pinned: dict[str, RunnerPin], monkeypatch) -> None:
    """No alias: the migration renames HAL0_TOOLBOX_IMAGE_* in api.env."""
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_STANDARD", "ghcr.io/x/old:1")
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", "ghcr.io/x/old:1")
    assert resolve_runner_image(get_runner("standard")) == pinned["standard"].ref


def test_unpublished_pin_resolves_to_the_bare_repo(monkeypatch) -> None:
    table = {
        key: RunnerPin(key=key, image=f"ghcr.io/hal0ai/hal0-runner-{key}") for key in RUNNER_IMAGES
    }
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    assert resolve_runner_image(get_runner("cpu")) == "ghcr.io/hal0ai/hal0-runner-cpu"


def test_cpu_lane_needs_a_published_pin_or_an_override(monkeypatch) -> None:
    table = {
        key: RunnerPin(key=key, image=f"ghcr.io/hal0ai/hal0-runner-{key}") for key in RUNNER_IMAGES
    }
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    assert cpu_lane_has_runner_image() is False
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_CPU", "ghcr.io/me/llama-cpu:1")
    assert cpu_lane_has_runner_image() is True


def test_cpu_lane_with_a_published_pin(pinned: dict[str, RunnerPin]) -> None:
    assert cpu_lane_has_runner_image() is True
