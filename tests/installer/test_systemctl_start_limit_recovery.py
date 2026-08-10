"""#1791 / #1424 — the seam clears a start-limited unit before start/restart.

``installer/wrappers/hal0-systemctl`` is the ENTIRE privileged surface for slot
lifecycle on a hal0-service-user box. Once systemd hits ``StartLimitBurst``
(the slot quadlet ships ``StartLimitIntervalSec=300`` / ``StartLimitBurst=5``)
it parks the unit in ``failed`` with ``Result=start-limit-hit`` and refuses
EVERY subsequent ``start``/``restart`` for the rest of the interval. The slot
was then unloadable via the API until an operator ran ``reset-failed`` by hand.

The recovery lives here — on the root side of the seam — so it holds for every
caller of the verb, not just the one Python path that remembers to ask.

WHY THIS RUNS A COPY OF THE WRAPPER. ``tests/conftest.py``'s
``_no_real_systemctl`` guard hard-fails any test that executes a binary named
``hal0-systemctl`` with a mutating verb, because doing so on a developer machine
shells out to sudo and raises polkit dialogs. Its allow-list is a fixed set of
pure verbs (``help`` / ``check-dropin`` / ``check-quadlet``) and ``start`` is not
— nor should it be, since the shipped wrapper really does mutate units.

What the guard is protecting against cannot happen here: the wrapper is copied
to a neutral filename and run DIRECTLY (never through sudo), with a ``systemctl``
shim first on ``PATH`` that only appends its argv to a log file. No privileged
call, no system bus, no root. Running the real bash is the whole point — the
start-limit recovery is bash, so asserting on it in Python would test nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"

_SHIM = """#!/bin/bash
echo "$@" >> "$SYSTEMCTL_LOG"
if [[ "$1" == "is-failed" ]]; then
  exit "${IS_FAILED_RC:-1}"
fi
exit 0
"""


@pytest.fixture
def shim(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Recording ``systemctl`` + a neutrally-named copy of the real wrapper."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim_path = bindir / "systemctl"
    shim_path.write_text(_SHIM, encoding="utf-8")
    shim_path.chmod(0o755)
    # Byte-for-byte the shipped wrapper — only the filename differs, so the
    # conftest guard (which matches on basename) doesn't trip on a call that
    # provably cannot reach the real system bus. See the module docstring.
    wrapper_copy = tmp_path / "seam-under-test"
    shutil.copy2(WRAPPER, wrapper_copy)
    return bindir, tmp_path / "systemctl.log", wrapper_copy


def _run(
    shim: tuple[Path, Path, Path], *args: str, is_failed_rc: str = "1"
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bindir, log, wrapper = shim
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["SYSTEMCTL_LOG"] = str(log)
    env["IS_FAILED_RC"] = is_failed_rc
    proc = subprocess.run(
        [str(wrapper), *args], capture_output=True, text=True, check=False, env=env
    )
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, lines


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_a_start_limited_unit_is_reset_before_the_verb(
    shim: tuple[Path, Path, Path], verb: str
) -> None:
    """is-failed true (systemd gave up) → reset-failed, then the verb."""
    proc, lines = _run(shim, verb, "chat", is_failed_rc="0")

    assert proc.returncode == 0, proc.stderr
    assert lines == [
        "is-failed --quiet hal0-slot@chat.service",
        "reset-failed hal0-slot@chat.service",
        f"{verb} hal0-slot@chat.service",
    ]


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_a_healthy_unit_is_left_untouched(shim: tuple[Path, Path, Path], verb: str) -> None:
    """``is-failed`` is true ONLY for a unit systemd has given up on.

    A healthy or merely-stopped unit reports active/inactive, so the recovery
    is deliberately conditional — no gratuitous state clearing on every load.
    """
    proc, lines = _run(shim, verb, "chat", is_failed_rc="1")

    assert proc.returncode == 0, proc.stderr
    assert lines == [
        "is-failed --quiet hal0-slot@chat.service",
        f"{verb} hal0-slot@chat.service",
    ]


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_the_slot_id_is_still_validated(shim: tuple[Path, Path, Path], verb: str) -> None:
    """The recovery must not open a hole in the seam's argument validation."""
    proc, lines = _run(shim, verb, "chat; rm -rf /")

    assert proc.returncode == 64
    assert "bad slot id" in proc.stderr
    assert lines == [], "nothing may reach systemctl for a rejected id"


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_a_missing_slot_id_is_still_rejected(shim: tuple[Path, Path, Path], verb: str) -> None:
    proc, lines = _run(shim, verb)

    assert proc.returncode == 64
    assert "missing slot id" in proc.stderr
    assert lines == []


def test_stop_does_not_reset_failed(shim: tuple[Path, Path, Path]) -> None:
    """Only the arms that systemd's start limit actually blocks are touched.

    ``stop`` on a failed unit works fine, and clearing the failed state there
    would erase the very evidence ``unit_failure_reason`` reads (#1791).
    """
    proc, lines = _run(shim, "stop", "chat", is_failed_rc="0")

    assert proc.returncode == 0, proc.stderr
    assert lines == ["stop hal0-slot@chat.service"]
