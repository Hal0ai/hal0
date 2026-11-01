"""Captured ComfyUI startup journal forms and current-start isolation (#3132)."""

import subprocess
from pathlib import Path

import pytest

from hal0.cli import doctor_all as da
from hal0.config import paths

CAPTURED = """[ERROR] [ComfyUI-Manager] Neither `python -m pip` nor `uv` are available.
   0.3 seconds (PRESTARTUP FAILED): /opt/ComfyUI/custom_nodes/ComfyUI-Manager
   0.0 seconds (IMPORT FAILED): /opt/ComfyUI/custom_nodes/ComfyUI-Crystools
[WARNING] Cannot import /opt/ComfyUI/custom_nodes/ComfyUI-Model-Manager module for custom nodes: Command returned non-zero exit status 1.
[WARNING] Cannot import /opt/ComfyUI/custom_nodes/ComfyUI-Custom-Scripts module for custom nodes: [Errno 13] Permission denied: '/opt/ComfyUI/web'
[WARNING] Cannot import /opt/ComfyUI/custom_nodes/ComfyUI-Crystools module for custom nodes: No module named 'deepdiff'
[WARNING] Cannot import /opt/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper module for custom nodes: No module named 'accelerate'
"""
CLEAN = """Import times for custom nodes:
   0.0 seconds: /opt/ComfyUI/custom_nodes/hal0_gpu_gate
   0.1 seconds: /opt/ComfyUI/custom_nodes/ComfyUI-Crystools
Starting server
"""


@pytest.fixture
def slots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "slots_config_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("stem,identity", [("img", ""), ("42", "id = 42\n")])
@pytest.mark.parametrize("ansi", [False, True])
def test_failures_and_fixed_restart(
    slots: Path, monkeypatch: pytest.MonkeyPatch, stem: str, identity: str, ansi: bool
) -> None:
    (slots / f"{stem}.toml").write_text(f'{identity}[slot]\nname = "img"\n')
    invocation = "a" * 32
    journals = {invocation: CAPTURED, "b" * 32: CLEAN}
    unit = f"hal0-slot@{stem}.service"

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "systemctl":
            assert argv == ["systemctl", "show", unit, "-p", "InvocationID", "--value"]
            return subprocess.CompletedProcess(argv, 0, invocation + "\n", "")
        assert argv == [
            "journalctl",
            "-b",
            "-u",
            unit,
            f"_SYSTEMD_INVOCATION_ID={invocation}",
            "-o",
            "cat",
            "--no-pager",
        ]
        text = journals[invocation]
        if ansi:
            text = text.replace("FAILED", "\x1b[31mFAILED\x1b[0m")
            text = text.replace("Cannot import", "\x1b[33mCannot\x1b[0m import")
        return subprocess.CompletedProcess(argv, 0, text, "")

    monkeypatch.setattr(subprocess, "run", run)
    row = da.check_img_custom_nodes()
    assert row.status == "warn"
    assert row.key == "img-custom-nodes"
    for name in ("Manager", "Model-Manager", "Custom-Scripts", "Crystools", "WanVideoWrapper"):
        assert f"ComfyUI-{name}" in row.detail
    assert row.detail.count("ComfyUI-Crystools") == 1
    assert "manage-runner-images#comfyui-custom-node-dependencies" in row.detail
    invocation = "b" * 32
    assert da.check_img_custom_nodes().status == "pass"


def test_no_img_does_not_query_journal(slots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("no unit should be queried without img")

    monkeypatch.setattr(subprocess, "run", unexpected)
    assert da.check_img_custom_nodes().detail == "no img slot configured"


@pytest.mark.parametrize("line", CAPTURED.splitlines()[1:])
def test_each_captured_failure_form(
    slots: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    (slots / "img.toml").write_text('[slot]\nname = "img"\n')

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, "a" * 32 if argv[0] == "systemctl" else line, ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert da.check_img_custom_nodes().status == "warn"


@pytest.mark.parametrize("failure", ["missing", "denied", "timeout", "no-start", "empty", "hint"])
def test_unavailable_evidence_is_honest(
    slots: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    (slots / "img.toml").write_text('[slot]\nname = "img"\n')

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure == "missing":
            raise FileNotFoundError("command unavailable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 5)
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(
                argv, 0, "" if failure == "no-start" else "a" * 32, ""
            )
        return subprocess.CompletedProcess(
            argv,
            1 if failure == "denied" else 0,
            CLEAN if failure == "hint" else "",
            "not seeing messages from other users" if failure == "hint" else "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    row = da.check_img_custom_nodes()
    assert "not checked" in row.detail or "could not run this check" in row.detail
    assert "no custom-node startup failures" not in row.detail


def test_config_permission_error_is_local(slots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (slots / "img.toml").write_text('[slot]\nname = "img"\n')

    def denied(*args: object, **kwargs: object) -> str:
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "read_text", denied)
    row = da.check_img_custom_nodes()
    assert row.status == "warn"
    assert "sudo hal0 doctor all" in row.detail
