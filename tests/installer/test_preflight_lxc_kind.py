"""Contract tests for ``hal0_lxc_kind`` in ``installer/lib/preflight.sh``.

The classifier answers "where is this install running" with one of three
values — ``none``, ``lxc-privileged``, ``lxc-unprivileged`` — because the
answer decides which remedies are even possible from inside the guest:

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
returns one of the three admitted values on the machine running the suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"

_ADMITTED = {"none", "lxc-privileged", "lxc-unprivileged"}


def _run(snippet: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    script = f"source {_PREFLIGHT!s} >/dev/null 2>&1\n{snippet}\n"
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
        ("lxc-privileged", 0),
        ("lxc-unprivileged", 0),
    ):
        result = _run("hal0_in_lxc", {"HAL0_LXC_KIND_OVERRIDE": kind})
        assert result.returncode == expected_rc, f"{kind}: {result.stderr}"


def test_classifier_is_used_not_reimplemented() -> None:
    """The old inline ``grep container=lxc /proc/1/environ`` checks are the
    thing this function replaces — a stray copy would drift from the
    privileged/unprivileged split and resurrect the wrong-remedy bug."""
    preflight = _PREFLIGHT.read_text(encoding="utf-8")
    # One occurrence remains by design: the probe inside hal0_lxc_kind itself.
    assert preflight.count("container=lxc") == 1

    install_sh = (_REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "hal0_lxc_kind" in install_sh
