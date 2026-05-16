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
    """``slot create --help`` mentions --provider and --hardware."""
    result = runner.invoke(slot_commands.app, ["create", "--help"])
    assert result.exit_code == 0, result.output
    assert "--provider" in result.output
    assert "--hardware" in result.output


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
    """Deprecated ``--backend flm`` is translated to provider=flm + warns."""
    result = runner.invoke(
        slot_commands.app,
        ["create", "primary", "--backend", "flm", "--model", "demo"],
    )
    assert result.exit_code == 0, result.output
    assert captured_post["body"]["provider"] == "flm"
    # The hardware backend must NOT be conflated with the deprecated flag.
    assert captured_post["body"]["backend"] == "vulkan"
    assert "deprecated" in result.output.lower()


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
