"""Tests for ``hal0 system-info`` (§21.3 host/GPU/NPU/runtime evidence).

The assembler ``build_system_info`` is pure — exercised with fixture data so
no real hardware is touched. The command is driven through its injectable
seams (``_probe_hardware`` / ``_command_version``) with a captured console.
"""

from __future__ import annotations

import io
import json as jsonlib
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console

from hal0.cli import system_info_command as si


def _fake_hw() -> SimpleNamespace:
    return SimpleNamespace(
        hostname="halo",
        kernel="Linux 7.0.6-2-pve",
        distro="Debian GNU/Linux 13 (trixie)",
        uptime_s=3600,
        cpu_model="AMD Ryzen AI Max+ 395",
        cpu_cores=16,
        cpu_threads=32,
        ram_mb=131072,
        ram_available_mb=98304,
        swap_mb=8192,
        unified_memory_mb=131072,
        gpus=[
            SimpleNamespace(
                vendor="amd",
                index=0,
                name="Radeon 8060S",
                vram_mb=98304,
                driver="amdgpu",
                compute_capable=True,
                vulkan_capable=True,
            )
        ],
        npu=SimpleNamespace(
            present=True,
            vendor="amd",
            name="AMD XDNA (Strix Halo)",
            driver="amdxdna",
            accel_path="/dev/accel/accel0",
            render_path="/dev/dri/renderD128",
            aie_columns=8,
            validated=None,
        ),
        platform="strix-halo",
    )


# ── build_system_info (pure) ──────────────────────────────────────────────────


def test_build_full_shape() -> None:
    data = si.build_system_info(
        _fake_hw(),
        hal0_version="9.9.9",
        python_version="3.13.1",
        python_executable="/usr/bin/python3",
        podman_version="podman version 5.0.0",
    )
    assert data["hal0_version"] == "9.9.9"
    assert data["platform"] == "strix-halo"
    assert data["hardware_probe_ok"] is True
    assert data["host"]["hostname"] == "halo"
    assert data["cpu"]["cores"] == 16 and data["cpu"]["threads"] == 32
    assert data["memory"]["ram_mb"] == 131072
    assert data["gpus"][0]["name"] == "Radeon 8060S"
    assert data["gpus"][0]["compute_capable"] is True
    assert data["npu"]["present"] is True
    assert data["npu"]["accel_path"] == "/dev/accel/accel0"
    assert data["runtime"]["podman_version"] == "podman version 5.0.0"
    assert data["runtime"]["python_version"] == "3.13.1"


def test_build_runtime_only_when_probe_unavailable() -> None:
    data = si.build_system_info(
        None,
        hal0_version="9.9.9",
        python_version="3.13.1",
        python_executable="/usr/bin/python3",
        podman_version=None,
    )
    assert data["hardware_probe_ok"] is False
    assert data["host"] == {}
    assert data["gpus"] == []
    assert data["npu"] == {"present": False}
    # Runtime facts always present even without a probe.
    assert data["runtime"]["python_version"] == "3.13.1"
    assert data["runtime"]["podman_version"] is None


def test_build_is_json_serialisable() -> None:
    data = si.build_system_info(
        _fake_hw(),
        hal0_version="1.0",
        python_version="3.13.1",
        python_executable="/x",
        podman_version=None,
    )
    # Must round-trip — the --json path relies on this.
    assert jsonlib.loads(jsonlib.dumps(data))["npu"]["aie_columns"] == 8


# ── rendering ─────────────────────────────────────────────────────────────────


def test_render_does_not_crash_on_full_and_empty() -> None:
    con = Console(file=io.StringIO())
    render_full = si.build_system_info(
        _fake_hw(), hal0_version="1", python_version="3", python_executable="/x", podman_version="p"
    )
    si.render_system_info(con, render_full)
    render_empty = si.build_system_info(
        None, hal0_version="1", python_version="3", python_executable="/x", podman_version=None
    )
    si.render_system_info(con, render_empty)
    out = con.file.getvalue()
    assert "system-info" in out


# ── command ───────────────────────────────────────────────────────────────────


def test_command_json_exits_zero_and_emits_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "_probe_hardware", _fake_hw)
    monkeypatch.setattr(si, "_command_version", lambda argv: "podman version 5.0.0")
    buf = io.StringIO()
    monkeypatch.setattr(si, "console", Console(file=buf))
    with pytest.raises(typer.Exit) as exc:
        si.system_info_cmd(json_output=True)
    assert exc.value.exit_code == 0
    payload = jsonlib.loads(buf.getvalue())
    assert payload["host"]["hostname"] == "halo"
    assert payload["runtime"]["podman_version"] == "podman version 5.0.0"


def test_command_human_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "_probe_hardware", lambda: None)
    monkeypatch.setattr(si, "_command_version", lambda argv: None)
    monkeypatch.setattr(si, "console", Console(file=io.StringIO()))
    with pytest.raises(typer.Exit) as exc:
        si.system_info_cmd(json_output=False)
    assert exc.value.exit_code == 0


def test_command_version_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    # shutil.which returns None → _command_version returns None (not found).
    monkeypatch.setattr(si.shutil, "which", lambda _name: None)
    assert si._command_version(("podman", "--version")) is None
