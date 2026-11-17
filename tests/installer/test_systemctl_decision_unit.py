"""#3757 — the ``write-hs-decide-unit`` / ``remove-hs-decide-unit`` seam verbs.

The root side takes four values, never unit text, and renders the unit from
its own template. These tests run the REAL bash wrapper through its
side-effect-free ``check-hs-decide-unit`` verb and pin it byte-for-byte to
:func:`hal0.system.seam.render_decision_unit` (the direct-branch twin), then
check the Python seam sends only those values over sudo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.system.seam import DECISION_UNIT_PATH, SystemCtlSeam, render_decision_unit

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"
REV = "e2c789026dfd1f7608cafb2ef6a9bb31397af802"


def _check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), "check-hs-decide-unit", *args],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )


@pytest.mark.parametrize(
    ("threads", "port", "max_tokens"), [(8, 9178, 4096), (1, 1024, 256), (64, 65535, 32768)]
)
def test_wrapper_renders_exactly_what_python_renders(
    threads: int, port: int, max_tokens: int
) -> None:
    proc = _check(REV, str(threads), str(port), str(max_tokens))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == render_decision_unit(
        revision=REV, threads=threads, port=port, max_tokens=max_tokens
    )


def test_rendered_unit_runs_as_hal0_on_loopback_offline() -> None:
    body = render_decision_unit(revision=REV, threads=8, port=9178, max_tokens=4096)
    assert "User=hal0\n" in body and "Group=hal0\n" in body
    assert "--host 127.0.0.1 " in body
    assert "Environment=HF_HUB_OFFLINE=1\n" in body
    assert "NoNewPrivileges=yes\n" in body
    assert f"/snapshots/{REV} " in body
    assert "ExecStartPre" not in body and "b9a782058d" not in body


@pytest.mark.parametrize(
    "args",
    [
        ("b9a7820", "8", "9178", "4096"),  # short sha
        (REV.upper(), "8", "9178", "4096"),
        (f"{REV};id", "8", "9178", "4096"),
        ("../../etc", "8", "9178", "4096"),
        (REV, "0", "9178", "4096"),
        (REV, "65", "9178", "4096"),
        (REV, "08", "9178", "4096"),  # no octal surprises
        (REV, "8", "80", "4096"),  # below 1024
        (REV, "8", "70000", "4096"),
        (REV, "8", "9178", "100"),
        (REV, "8", "9178 --host 0.0.0.0", "4096"),
        (REV, "8", "9178"),  # missing argument
        (REV, "8", "9178", "4096", "extra"),
    ],
)
def test_wrapper_refuses_anything_but_four_valid_values(args: tuple[str, ...]) -> None:
    proc = _check(*args)
    assert proc.returncode == 64, proc.stdout
    assert proc.stdout == ""


@pytest.mark.parametrize(
    "kw",
    [
        {"revision": "abc", "threads": 8, "port": 9178, "max_tokens": 4096},
        {"revision": REV, "threads": 0, "port": 9178, "max_tokens": 4096},
        {"revision": REV, "threads": 8, "port": 80, "max_tokens": 4096},
        {"revision": REV, "threads": True, "port": 9178, "max_tokens": 4096},
    ],
)
def test_python_render_validates_like_the_wrapper(kw: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        render_decision_unit(**kw)


def _recorder() -> tuple[list[list[str]], Any]:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return calls, run


def test_seam_sends_only_the_four_values(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)
    seam.write_decision_unit(
        revision=REV, threads=8, port=9178, max_tokens=4096, path=tmp_path / "ignored"
    )
    seam.remove_decision_unit()
    assert calls == [
        ["sudo", "-n", "/usr/lib/hal0/bin/hal0-systemctl", "write-hs-decide-unit", REV, "8",
         "9178", "4096"],
        ["sudo", "-n", "/usr/lib/hal0/bin/hal0-systemctl", "remove-hs-decide-unit"],
    ]  # fmt: skip
    assert not (tmp_path / "ignored").exists()


def test_seam_routes_the_decision_unit_through_svc_verbs() -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)
    for verb in ("enable", "start", "restart", "stop", "disable"):
        seam.systemctl("systemctl", verb, DECISION_UNIT_PATH.name)
    assert [c[3:] for c in calls] == [
        [f"svc-{v}", "hs-decide"] for v in ("enable", "start", "restart", "stop", "disable")
    ]


def test_direct_branch_writes_and_removes_the_file(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)
    unit = tmp_path / "hal0-hs-decide.service"
    seam.write_decision_unit(revision=REV, threads=8, port=9178, max_tokens=4096, path=unit)
    assert unit.read_text() == render_decision_unit(
        revision=REV, threads=8, port=9178, max_tokens=4096
    )
    seam.remove_decision_unit(path=unit)
    assert not unit.exists() and calls == []


# ── write-hs-decide-unit keeps a hand-written unit once, root-owned ──────────
#
# Runs a neutrally named copy of the shipped wrapper (the conftest guard matches
# the basename) with UNIT_DIR at a tmp dir and no-op chown/systemctl shims, the
# test_systemctl_repin_owui.py pattern.

_NOOP = "#!/bin/bash\nexit 0\n"


@pytest.fixture
def wrapper(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("chown", "systemctl"):
        shim = bindir / name
        shim.write_text(_NOOP)
        shim.chmod(0o755)
    copy = tmp_path / "seam-under-test"
    shutil.copy2(WRAPPER, copy)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "UNIT_DIR": str(unit_dir)}
    return copy, env, unit_dir


def _write(wrapper: tuple[Path, dict[str, str], Path], port: str = "9178") -> None:
    copy, env, _ = wrapper
    proc = subprocess.run(
        [str(copy), "write-hs-decide-unit", REV, "8", port, "4096"],
        capture_output=True, text=True, check=False, env=env,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr


def test_a_hand_written_unit_is_kept_before_the_first_rewrite(
    wrapper: tuple[Path, dict[str, str], Path],
) -> None:
    _, _, units = wrapper
    hand = "[Service]\nUser=hal0\nExecStart=/opt/x --port 9178\n"
    (units / "hal0-hs-decide.service").write_text(hand)
    _write(wrapper)
    backup = units / "hal0-hs-decide.service.pre-hal0"
    assert backup.read_text() == hand
    assert (units / "hal0-hs-decide.service").read_text() == render_decision_unit(
        revision=REV, threads=8, port=9178, max_tokens=4096
    )
    # Later rewrites never touch the kept copy.
    (units / "hal0-hs-decide.service").write_text("[Service]\nExecStart=/opt/other\n")
    _write(wrapper, port="9300")
    assert backup.read_text() == hand


def test_a_hal0_rendered_unit_or_a_symlink_is_not_kept(
    wrapper: tuple[Path, dict[str, str], Path], tmp_path: Path
) -> None:
    _, _, units = wrapper
    _write(wrapper)  # no unit before: nothing to keep
    _write(wrapper, port="9300")  # hal0's own unit: nothing to keep
    assert not (units / "hal0-hs-decide.service.pre-hal0").exists()
    (units / "hal0-hs-decide.service").unlink()
    secret = tmp_path / "secret"
    secret.write_text("root only\n")
    (units / "hal0-hs-decide.service").symlink_to(secret)
    _write(wrapper)
    assert not (units / "hal0-hs-decide.service.pre-hal0").exists()


def test_the_python_direct_branch_keeps_the_same_copy(tmp_path: Path) -> None:
    unit = tmp_path / "hal0-hs-decide.service"
    unit.write_text("[Service]\nExecStart=/opt/x\n")
    seam = SystemCtlSeam(is_hal0_user=lambda: False)
    seam.write_decision_unit(revision=REV, threads=8, port=9178, max_tokens=4096, path=unit)
    seam.write_decision_unit(revision=REV, threads=4, port=9178, max_tokens=4096, path=unit)
    assert (
        tmp_path / "hal0-hs-decide.service.pre-hal0"
    ).read_text() == "[Service]\nExecStart=/opt/x\n"
