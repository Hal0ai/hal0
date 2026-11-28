"""Contract tests for ``hal0_lxc_kind`` in ``installer/lib/preflight.sh``.

The classifier answers "where is this install running" with one of five
values — ``none``, ``box``, ``lxc-privileged``, ``lxc-unprivileged`` and
``container`` (any non-LXC container, see test_preflight_container_kind.py) —
because the answer decides which remedies are even possible from inside the guest:

* a PRIVILEGED LXC can ``chgrp`` a forwarded device node and repair a
  mis-mapped ``/dev/kfd`` itself (#1953),
* an UNPRIVILEGED one gets EPERM on the same call, so the only real fix is
  the Proxmox host's ``devN:`` entry.

Before this existed, install.sh's kfd-gid fallback and the container-runtime
gate both narrated the unprivileged remedy unconditionally, sending operators
of privileged containers to change something that was never the cause.

The classifier reads ``/proc/1/environ`` and ``/proc/self/uid_map``, neither
of which a test can fake, so the shape under test is driven through the
``HAL0_LXC_KIND_OVERRIDE`` seam plus a direct check that the real probe
returns one of the admitted values on the machine running the suite.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"

_ADMITTED = {"none", "box", "lxc-privileged", "lxc-unprivileged", "container"}


def _run(snippet: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    script = f"source {shlex.quote(str(_PREFLIGHT))} >/dev/null 2>&1\n{snippet}\n"
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/tmp"}
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_real_probe_returns_an_admitted_value() -> None:
    """Whatever this suite runs on, the classifier must answer in-vocabulary."""
    result = _run("hal0_lxc_kind")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in _ADMITTED


@pytest.mark.parametrize("kind", sorted(_ADMITTED))
def test_override_seam_is_honoured(kind: str) -> None:
    result = _run("hal0_lxc_kind", {"HAL0_LXC_KIND_OVERRIDE": kind})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == kind


def test_in_lxc_predicate_agrees_with_the_classifier() -> None:
    for kind, expected_rc in (
        ("none", 1),
        ("box", 1),
        ("lxc-privileged", 0),
        ("lxc-unprivileged", 0),
        ("container", 1),
    ):
        result = _run("hal0_in_lxc", {"HAL0_LXC_KIND_OVERRIDE": kind})
        assert result.returncode == expected_rc, f"{kind}: {result.stderr}"


def _classify(tmp_path: Path, marker: str | None, pid1: str) -> str:
    run_dir = tmp_path / "run"
    (run_dir / "systemd").mkdir(parents=True)
    if marker is not None:
        (run_dir / "systemd" / "container").write_text(marker, encoding="utf-8")
    environ = tmp_path / "pid1-environ"
    environ.write_bytes(pid1.encode())
    uid_map = tmp_path / "uid_map"
    uid_map.write_text("         0     100000      65536\n", encoding="utf-8")
    result = _run(
        "hal0_lxc_kind",
        {
            "HAL0_CONTAINER_RUN_DIR": str(run_dir),
            "HAL0_BOX_RELEASE_FILE": str(tmp_path / "no-box-release"),
            "HAL0_PID1_ENVIRON_FILE": str(environ),
            "HAL0_UID_MAP_FILE": str(uid_map),
        },
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_pid1_lxc_without_a_marker_is_lxc(tmp_path: Path) -> None:
    assert _classify(tmp_path, None, "PATH=/bin\0container=lxc\0") == "lxc-unprivileged"


def test_pid1_lxc_libvirt_without_a_marker_is_a_plain_container(tmp_path: Path) -> None:
    assert _classify(tmp_path, None, "PATH=/bin\0container=lxc-libvirt\0") == "container"


def test_marker_lxc_libvirt_is_a_plain_container(tmp_path: Path) -> None:
    assert _classify(tmp_path, "lxc-libvirt\n", "") == "container"


def test_classifier_is_used_not_reimplemented() -> None:
    """The old inline ``grep container=lxc /proc/1/environ`` checks are the
    thing this function replaces — a stray copy would drift from the
    privileged/unprivileged split and resurrect the wrong-remedy bug."""
    preflight = _PREFLIGHT.read_text(encoding="utf-8")
    # One occurrence remains by design: the exact-match probe inside
    # hal0_lxc_kind itself.
    assert preflight.count("container=lxc") == 1
    assert "grep -qx 'container=lxc'" in preflight

    install_sh = (_REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "hal0_lxc_kind" in install_sh
