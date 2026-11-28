"""#4006: the NPU software step runs only on a box that has an NPU.

The step "NPU prerequisites (FastFlowLM)" used to run on every apt host: it
added the lemonade-team PPA, installed libxrt-npu2 and libxrt-utils-npu,
downloaded the FastFlowLM .deb and ended with a "flm validate failed" warning
on a box that has no NPU at all. These tests cut the step (and the early
HAL0_NPU_SETUP check) out of ``install.sh`` and run them in real bash on a
PATH that holds only recording stubs and the few tools bash needs, so nothing
can touch the real system. The device node is faked through
``HAL0_NPU_ACCEL_NODE`` (the same override ``preflight_gpu`` reads), never by
creating anything under /dev.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "installer" / "install.sh"

_VALIDATE_START = "# ── HAL0_NPU_SETUP"
_VALIDATE_END = "# Step total."
_STEP_START = "# ── NPU prerequisites (FastFlowLM)"
_STEP_END = "# ── config-convergence migrations"

_STUBS = (
    "apt-get",
    "apt-cache",
    "add-apt-repository",
    "curl",
    "dpkg",
    "dpkg-query",
    "flm",
    "runuser",
    "sudo",
    "mktemp",
)
# What the step's own text needs from the system besides the stubs.
_REAL_TOOLS = ("sort", "head", "grep", "awk", "chmod", "sha256sum", "tr", "cat")

_PRELUDE = r"""
set -euo pipefail
info() { echo "INFO: $*"; }
warn() { echo "WARN: $*"; }
die()  { echo "DIE: $*"; exit 1; }
ui_step() { :; }
ui_spinner_run() { local _label="$1"; shift; "$@"; }
distro_id() { echo ubuntu; }
distro_pretty() { echo "Test OS"; }
_os_release_field() { echo 24.04; }
hal0_lxc_kind() { echo "${TEST_LXC_KIND:-none}"; }
hal0_tmp_track() { :; }
hal0_tmp_remove() { :; }
hal0_run_without_log_fd() { "$@"; }
"""


def _region(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


@pytest.fixture(scope="module")
def regions() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    return (
        _region(text, _VALIDATE_START, _VALIDATE_END) + "\n" + _region(text, _STEP_START, _STEP_END)
    )


class Run:
    def __init__(self, proc: subprocess.CompletedProcess[str], log: Path) -> None:
        self.proc = proc
        # The case directory differs per run; the comparison tests need it out.
        base = str(log.parent)
        self.out = proc.stdout.replace(base, "BASE")
        calls = log.read_text().replace(base, "BASE") if log.exists() else ""
        self.calls = calls.splitlines()

    def called(self, name: str) -> bool:
        return any(c == name or c.startswith(name + " ") for c in self.calls)


@pytest.fixture
def run_step(tmp_path: Path, regions: str):  # type: ignore[no-untyped-def]
    def _run(
        *,
        npu_setup: str | None,
        device: bool,
        dev_mode: int = 0,
        apt: bool = True,
        kind: str = "none",
    ) -> Run:
        base = tmp_path / f"case{len(list(tmp_path.glob('case*')))}"
        stubs = base / "stubs"
        scratch = base / "scratch"
        stubs.mkdir(parents=True)
        scratch.mkdir()
        log = base / "calls.log"
        for name in _STUBS:
            if name == "apt-get" and not apt:
                continue
            body = f'#!/bin/sh\necho "${{0##*/}} $*" >> {shlex.quote(str(log))}\n'
            if name == "mktemp":
                body += (
                    f"mkdir -p {shlex.quote(str(scratch))}/d\necho {shlex.quote(str(scratch))}/d\n"
                )
            if name == "curl":
                body += "exit 1\n"
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        for tool in _REAL_TOOLS:
            real = shutil.which(tool)
            assert real, tool
            (stubs / tool).symlink_to(real)
        node = base / "accel0"
        if device:
            node.write_text("")
        script = _PRELUDE + f"DEV_MODE={dev_mode}\n" + regions + "\necho STEP-FINISHED\n"
        env = {
            "PATH": str(stubs),
            "HAL0_NPU_ACCEL_NODE": str(node),
            "TEST_LXC_KIND": kind,
            "HOME": str(base),
        }
        if npu_setup is not None:
            env["HAL0_NPU_SETUP"] = npu_setup
        bash = shutil.which("bash")
        assert bash
        proc = subprocess.run(
            [bash, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(base),
            timeout=60,
            check=False,
        )
        return Run(proc, log)

    return _run


def test_no_device_unset_does_nothing_and_says_so(run_step) -> None:  # type: ignore[no-untyped-def]
    r = run_step(npu_setup=None, device=False)
    assert r.proc.returncode == 0, r.proc.stderr
    for cmd in ("apt-get", "add-apt-repository", "curl", "dpkg", "dpkg-query", "flm"):
        assert not r.called(cmd), (cmd, r.calls)
    assert r.calls == []
    assert "WARN" not in r.out
    infos = [ln for ln in r.out.splitlines() if ln.startswith("INFO:")]
    assert len(infos) == 1
    assert "no NPU" in infos[0] and "not installed" in infos[0]
    assert "re-run the installer" in infos[0] and "HAL0_NPU_SETUP=on" in infos[0]
    assert "STEP-FINISHED" in r.out


def test_device_present_runs_the_step_as_before(run_step) -> None:  # type: ignore[no-untyped-def]
    r = run_step(npu_setup=None, device=True)
    assert r.proc.returncode == 0, r.proc.stderr
    assert r.calls[0] == "apt-get -o DPkg::Lock::Timeout=120 update -qq"
    assert r.called("curl")
    assert "no NPU" not in r.out


def test_on_without_device_runs_the_step(run_step) -> None:  # type: ignore[no-untyped-def]
    r = run_step(npu_setup="on", device=False)
    assert r.proc.returncode == 0, r.proc.stderr
    assert r.calls[0] == "apt-get -o DPkg::Lock::Timeout=120 update -qq"
    assert "no NPU" not in r.out


def test_off_with_device_does_nothing_and_says_so(run_step) -> None:  # type: ignore[no-untyped-def]
    r = run_step(npu_setup="off", device=True)
    assert r.proc.returncode == 0, r.proc.stderr
    assert r.calls == []
    assert "WARN" not in r.out
    infos = [ln for ln in r.out.splitlines() if ln.startswith("INFO:")]
    assert len(infos) == 1
    assert "HAL0_NPU_SETUP=off" in infos[0]


@pytest.mark.parametrize("device", [False, True])
def test_auto_spelled_out_behaves_as_unset(run_step, device: bool) -> None:  # type: ignore[no-untyped-def]
    unset = run_step(npu_setup=None, device=device)
    auto = run_step(npu_setup="auto", device=device)
    empty = run_step(npu_setup="", device=device)
    assert auto.out == unset.out and auto.calls == unset.calls
    assert empty.out == unset.out and empty.calls == unset.calls


def test_bad_value_stops_before_anything_is_called(run_step) -> None:  # type: ignore[no-untyped-def]
    r = run_step(npu_setup="maybe", device=True)
    assert r.proc.returncode == 1
    assert r.calls == []
    assert "STEP-FINISHED" not in r.out
    assert r.out.strip() == "DIE: HAL0_NPU_SETUP must be auto, on or off (got: maybe)"


def test_dev_mode_keeps_its_lines_when_an_npu_is_present(run_step) -> None:  # type: ignore[no-untyped-def]
    r = run_step(npu_setup=None, device=True, dev_mode=1)
    assert r.calls == []
    assert "dev mode — skipping NPU prereqs" in r.out
    assert "no NPU" not in r.out


def test_no_apt_keeps_its_warning_when_an_npu_is_present(run_step) -> None:  # type: ignore[no-untyped-def]
    r = run_step(npu_setup=None, device=True, apt=False)
    assert r.calls == []
    assert "WARN: Test OS: skipping NPU prereqs" in r.out


def test_no_npu_line_comes_before_dev_mode_and_no_apt(run_step) -> None:  # type: ignore[no-untyped-def]
    dev = run_step(npu_setup=None, device=False, dev_mode=1)
    assert "no NPU" in dev.out and "dev mode" not in dev.out
    non_apt = run_step(npu_setup=None, device=False, apt=False)
    assert "no NPU" in non_apt.out and "WARN" not in non_apt.out


def test_box_kind_is_not_special(run_step) -> None:  # type: ignore[no-untyped-def]
    none = run_step(npu_setup=None, device=False, kind="box")
    assert none.calls == [] and "no NPU" in none.out
    present = run_step(npu_setup=None, device=True, kind="box")
    assert present.calls[0].startswith("apt-get ")
