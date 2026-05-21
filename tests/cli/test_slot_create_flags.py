"""Tests for ``hal0 slot create`` flag semantics.

Regression coverage for the hal0 v1 harness finding #2 / task #20:
``--backend`` historically named the *provider* but the slot's actual
hardware backend was hardcoded to "vulkan". Fix split the flag into
``--provider`` (engine) and ``--hardware`` (vulkan / rocm / cpu), with
``--backend`` kept as a hidden deprecated alias for provider.

These tests poke the Typer command via ``CliRunner`` and monkeypatch
the API client + hardware-probe defaults so they don't hit network or
read the host's real ``/etc/hal0/hardware.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import slot_commands

# Click ≥8.2 separates stdout/stderr on the result by default — the
# deprecation warning (emitted via ``typer.echo(..., err=True)``) lands
# in ``result.stderr``. We assert against ``result.stderr`` rather than
# ``result.output`` so a future contributor moving the warning back to
# stdout fails this regression test loudly.
runner = CliRunner()


@pytest.fixture
def captured_post(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the API surface ``slot_create`` touches and capture the POST body."""
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(path: str, **_kw: Any) -> list[dict[str, Any]]:
        # No existing slots → port auto-assign falls to 8081.
        return []

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["path"] = path
        captured["body"] = json or {}
        return {"port": (json or {}).get("port", 8081)}

    monkeypatch.setattr(slot_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(slot_commands, "api_get", fake_get)
    monkeypatch.setattr(slot_commands, "api_post", fake_post)
    # Default hardware detection — pin to "vulkan" so tests don't depend on
    # whatever GPU is on the runner.
    monkeypatch.setattr(slot_commands, "_detect_default_hardware", lambda: "vulkan")
    return captured


def test_help_lists_new_flags() -> None:
    """``slot create --help`` mentions --provider and --hardware.

    Click colors the flag name's leading ``-`` and ``-provider`` with
    separate ANSI sequences, so the raw ``result.output`` won't contain
    a contiguous ``--provider`` substring under a coloring terminal.
    Strip ANSI before checking.
    """
    import re

    result = runner.invoke(slot_commands.app, ["create", "--help"])
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--provider" in plain
    assert "--hardware" in plain


def test_provider_flag_sets_provider_and_default_hardware(
    captured_post: dict[str, Any],
) -> None:
    """``--provider llama-server`` (no --hardware) uses the auto-detected default."""
    result = runner.invoke(
        slot_commands.app,
        ["create", "primary", "--provider", "llama-server", "--model", "demo"],
    )
    assert result.exit_code == 0, result.output
    body = captured_post["body"]
    assert body["provider"] == "llama-server"
    assert body["backend"] == "vulkan"


def test_hardware_flag_overrides_default(captured_post: dict[str, Any]) -> None:
    """``--hardware rocm`` is forwarded as SlotConfig.backend = 'rocm'."""
    result = runner.invoke(
        slot_commands.app,
        [
            "create",
            "primary",
            "--provider",
            "llama-server",
            "--hardware",
            "rocm",
            "--model",
            "demo",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured_post["body"]["backend"] == "rocm"
    assert captured_post["body"]["provider"] == "llama-server"


def test_legacy_backend_flag_translates_to_provider(
    captured_post: dict[str, Any],
) -> None:
    """Deprecated ``--backend flm`` is translated to provider=flm + warns on stderr."""
    result = runner.invoke(
        slot_commands.app,
        ["create", "primary", "--backend", "flm", "--model", "demo"],
    )
    assert result.exit_code == 0, result.output
    assert captured_post["body"]["provider"] == "flm"
    # The hardware backend must NOT be conflated with the deprecated flag.
    assert captured_post["body"]["backend"] == "vulkan"
    # Deprecation warning goes to stderr so stdout stays parseable for
    # scripts piping the success line elsewhere.
    assert "deprecated" in result.stderr.lower()
    assert "--provider" in result.stderr


def test_legacy_backend_with_invalid_value_errors(
    captured_post: dict[str, Any],
) -> None:
    """``--backend vulkan`` (hardware-shaped) is rejected as not-a-provider."""
    result = runner.invoke(
        slot_commands.app,
        ["create", "primary", "--backend", "vulkan", "--model", "demo"],
    )
    # ``die()`` calls typer.Exit(1) — non-zero is the contract.
    assert result.exit_code != 0
    # No API call should have been made.
    assert "body" not in captured_post


def test_default_hardware_reads_probe_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``_detect_default_hardware`` picks rocm for AMD+compute_capable GPUs."""
    probe = tmp_path / "hardware.json"
    probe.write_text(
        json.dumps(
            {
                "gpus": [
                    {
                        "vendor": "amd",
                        "name": "Radeon 890M",
                        "compute_capable": True,
                        "vulkan_capable": True,
                    }
                ]
            }
        )
    )
    from hal0.config import paths as _paths

    monkeypatch.setattr(_paths, "hardware_json", lambda: probe)
    assert slot_commands._detect_default_hardware() == "rocm"


def test_default_hardware_vulkan_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Missing probe → vulkan (safe default that works on most GPUs)."""
    from hal0.config import paths as _paths

    monkeypatch.setattr(_paths, "hardware_json", lambda: tmp_path / "missing.json")
    assert slot_commands._detect_default_hardware() == "vulkan"


def test_default_hardware_cpu_when_no_gpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty gpus[] → cpu."""
    probe = tmp_path / "hardware.json"
    probe.write_text(json.dumps({"gpus": []}))
    from hal0.config import paths as _paths

    monkeypatch.setattr(_paths, "hardware_json", lambda: probe)
    assert slot_commands._detect_default_hardware() == "cpu"


def test_invalid_hardware_value_rejected_by_typer(
    captured_post: dict[str, Any],
) -> None:
    """``--hardware foo`` is rejected at the Typer parsing layer.

    Because ``--hardware`` is a ``SlotHardware`` StrEnum, Typer/Click
    rejects unknown values before the command body runs — the API
    client should never be called.
    """
    result = runner.invoke(
        slot_commands.app,
        [
            "create",
            "primary",
            "--provider",
            "llama-server",
            "--hardware",
            "foo",
            "--model",
            "demo",
        ],
    )
    assert result.exit_code != 0
    # Click's bad-parameter envelope mentions the offending flag.
    assert "hardware" in (result.stderr + result.output).lower()
    # No API call should have been made — the command body never ran.
    assert "body" not in captured_post


def test_bare_create_on_strix_halo_resolves_to_vulkan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A bare ``slot create primary`` on a Strix Halo fixture auto-resolves
    ``--hardware vulkan``.

    Strix Halo presents as an AMD iGPU (vendor=amd) that is Vulkan-capable
    but typically not flagged compute_capable in the probe output — the
    iGPU runs llama.cpp via Vulkan, not ROCm. The auto-detect path must
    pick ``vulkan`` so the user doesn't need to know about hardware flags
    on the platform that hal0 v1 most cares about.
    """
    probe = tmp_path / "hardware.json"
    probe.write_text(
        json.dumps(
            {
                "gpus": [
                    {
                        "vendor": "amd",
                        "name": "Radeon 890M (Strix Halo)",
                        "vram_mb": 512,
                        # iGPU — Vulkan via Mesa, no ROCm.
                        "compute_capable": False,
                        "vulkan_capable": True,
                    }
                ],
                "unified_memory_mb": 102400,
            }
        )
    )
    from hal0.config import paths as _paths

    monkeypatch.setattr(_paths, "hardware_json", lambda: probe)

    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(path: str, **_kw: Any) -> list[dict[str, Any]]:
        return []

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["body"] = json or {}
        return {"port": (json or {}).get("port", 8081)}

    monkeypatch.setattr(slot_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(slot_commands, "api_get", fake_get)
    monkeypatch.setattr(slot_commands, "api_post", fake_post)

    result = runner.invoke(
        slot_commands.app,
        ["create", "primary", "--model", "demo"],
    )
    assert result.exit_code == 0, result.output
    # Bare invocation → provider defaults to llama-server, hardware
    # auto-resolves to vulkan from the Strix Halo fixture.
    assert captured["body"]["provider"] == "llama-server"
    assert captured["body"]["backend"] == "vulkan"
