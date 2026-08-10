"""``preflight_all``'s closing summary line must reflect warnings (#1796).

Before this fix, ``hal0 doctor`` printed six ``!!`` warning lines and then
an unconditional "OK all pre-flight checks passed" (exit 0) — the summary
contradicted the body it was summarizing. ``preflight_all`` now sources the
real ``UI_WARN_COUNT`` counter ui.sh's ``warn()`` bumps and folds it into
the closing line; these tests exercise both real files together (subprocess,
real bash — same technique as the other ``tests/installer/test_preflight_*``
suites) rather than stubbing ui.sh away, since the counter IS the fix.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UI = _REPO_ROOT / "installer" / "lib" / "ui.sh"
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"

#: Every sub-check preflight_all calls, stubbed to a controllable outcome.
_ALL_CHECKS = (
    "preflight_bootstrap_prereqs",
    "preflight_arch",
    "preflight_systemd",
    "preflight_python",
    "preflight_venv",
    "preflight_hindsight_python",
    "preflight_writable",
    "preflight_network",
    "preflight_container_runtime",
    "preflight_git",
    "preflight_podman_network_backend",
    "preflight_podman_forward",
    "preflight_gpu",
    "preflight_node",
    "preflight_disk",
    "preflight_ports",
)


def _run(overrides: str) -> subprocess.CompletedProcess[str]:
    """Source the real ui.sh + preflight.sh, stub every sub-check per
    *overrides* (bash function bodies, redefined after sourcing — bash
    allows this), then invoke preflight_all and capture its output."""
    stub_all_clean = "\n".join(f"{name}() {{ return 0; }}" for name in _ALL_CHECKS)
    script = (
        "set -uo pipefail\n"
        f"source {_UI!s}\n"
        f"source {_PREFLIGHT!s}\n"
        f"{stub_all_clean}\n"
        f"{overrides}\n"
        "preflight_all; rc=$?\n"
        'echo "EXIT:${rc}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
    )


def test_all_clean_says_all_passed() -> None:
    result = _run("")
    combined = result.stdout + result.stderr
    assert "all pre-flight checks passed" in combined
    assert "warning" not in combined
    assert "EXIT:0" in result.stdout


def test_soft_warning_reflected_in_summary_not_hidden() -> None:
    """A soft check (e.g. no GPU) warns without flipping rc — the summary
    must say so instead of claiming an unconditional clean pass."""
    result = _run(
        'preflight_gpu() { warn "no GPU detected — CPU-only install"; return 0; }\n'
        'preflight_git() { warn "git not found"; return 0; }\n'
    )
    combined = result.stdout + result.stderr
    assert "EXIT:0" in result.stdout
    assert "all pre-flight checks passed" not in combined
    assert "passed with 2 warning(s)" in combined


def test_hard_failure_still_reports_failed() -> None:
    result = _run('preflight_python() { err "python too old"; return 1; }\n')
    combined = result.stdout + result.stderr
    assert "EXIT:1" in result.stdout
    assert "one or more pre-flight checks failed" in combined
