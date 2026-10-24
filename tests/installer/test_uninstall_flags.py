"""``installer/uninstall.sh`` flag parsing (#2866).

``--keep-data`` (an alias of the default) and ``--clean-slate`` (an alias of
``--purge``) are gone. Flag parsing runs before the root check and before any
teardown, so these run the real script safely: a refused flag exits 2 having
touched nothing, which is what keeps a stale runbook that spelled ``--purge``
as ``--clean-slate`` from quietly running the conservative teardown instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_UNINSTALL = Path(__file__).resolve().parents[2] / "installer" / "uninstall.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_UNINSTALL), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("flag", ["--keep-data", "--clean-slate", "--nope"])
def test_unknown_and_removed_flags_exit_2_before_any_teardown(flag: str) -> None:
    proc = _run(flag, "--force")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert f"Unknown flag: {flag}" in proc.stderr
    assert "Stopping" not in proc.stdout and "Removed" not in proc.stdout


def test_help_lists_only_the_surviving_flags() -> None:
    proc = _run("--help")
    assert proc.returncode == 0
    assert "--purge" in proc.stdout and "--force" in proc.stdout and "--dev" in proc.stdout
    assert "--keep-data" not in proc.stdout and "--clean-slate" not in proc.stdout
