"""A failing config migration must not abort the install.

``install.sh`` runs under ``set -euo pipefail`` (:17), so a bare python heredoc
in the config-migration phase aborts the WHOLE script on any raise — before
``start_or_restart_api`` is ever reached, which leaves the old daemon serving
while the new tree sits installed and unused.

That is not hypothetical. ``ensure_seed_profiles`` raises ``ConfigParseError``
on a ``profiles.toml`` that fails today's ``extra="forbid"`` ``ProfileConfig``,
which is precisely the pre-v1.0 shape the migration phase exists to repair: the
step that fixes an old box was the step most likely to brick its upgrade.
``Updater.commit()`` has always wrapped the identical calls in
try/except-and-warn; ``hal0_migration_step`` gives install.sh the same posture.

Driven by extracting the function out of install.sh and calling it, the same
technique ``test_api_restart_on_upgrade.py`` uses (install.sh runs its whole
flow at source time, so it cannot simply be sourced).
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def _extract_function(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _drive(tmp_path: Path, python_body: str) -> subprocess.CompletedProcess[str]:
    """Run ``hal0_migration_step`` over ``python_body`` under the real shell opts."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    shim = venv_bin / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    payload = tmp_path / "step.py"
    payload.write_text(python_body, encoding="utf-8")

    script = tmp_path / "drive.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'VENV_DIR="{tmp_path / "venv"}"\n'
        "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
        f"{_extract_function('hal0_migration_step')}\n"
        f'hal0_migration_step "unit under test" < "{payload}"\n'
        'echo "REACHED_START_OR_RESTART_API"\n',
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60, check=False
    )


def test_raising_migration_does_not_abort_the_install(tmp_path: Path) -> None:
    """The regression: this is exactly what a pre-v1.0 profiles.toml does."""
    proc = _drive(
        tmp_path,
        'raise RuntimeError("failed to validate profiles.toml during seed merge")\n',
    )
    assert proc.returncode == 0, proc.stderr
    assert "REACHED_START_OR_RESTART_API" in proc.stdout
    assert "WARN: unit under test: migration step failed" in proc.stdout
    # The operator is told how to retry, not just that something broke.
    assert "sudo bash install.sh" in proc.stdout


def test_nonzero_exit_is_contained_too(tmp_path: Path) -> None:
    proc = _drive(tmp_path, "import sys\nsys.exit(3)\n")
    assert proc.returncode == 0, proc.stderr
    assert "REACHED_START_OR_RESTART_API" in proc.stdout
    assert "migration step failed" in proc.stdout


def test_successful_migration_streams_output_and_stays_quiet(tmp_path: Path) -> None:
    proc = _drive(tmp_path, 'print("  pruned 3 materialised seed profile(s)")\n')
    assert proc.returncode == 0, proc.stderr
    assert "pruned 3 materialised seed profile(s)" in proc.stdout
    assert "migration step failed" not in proc.stdout
    assert "REACHED_START_OR_RESTART_API" in proc.stdout


def test_bare_heredoc_would_have_aborted(tmp_path: Path) -> None:
    """Pins WHY the helper exists — without it, `set -e` kills the install."""
    script = tmp_path / "bare.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'"{sys.executable}" -c \'raise RuntimeError("boom")\'\n'
        'echo "REACHED_START_OR_RESTART_API"\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode != 0
    assert "REACHED_START_OR_RESTART_API" not in proc.stdout


# ── every migration heredoc actually goes through the helper ──────────────────


def test_no_bare_python_heredoc_survives_in_the_migration_phase() -> None:
    """Guard against a future edit reintroducing an uncontained call.

    Scoped to *stdin* invocations — ``"${VENV_DIR}/bin/python" -`` with nothing
    after the dash. ``-c`` and ``-m`` forms are a different shape and are
    separately contained (``|| fallback`` at the network-env read, ``if …; then``
    at the openwebui env write); this test is about the heredoc-fed migration
    steps, every one of which must arrive through ``hal0_migration_step``.
    """
    import re

    text = INSTALL_SH.read_text(encoding="utf-8")
    stdin_call = re.compile(r'"\$\{VENV_DIR\}/bin/python"\s+-(?![\w-])')
    bare = [line for line in text.splitlines() if stdin_call.search(line)]
    # The single legitimate occurrence is the one inside the helper's body.
    assert len(bare) == 1, bare
    assert bare[0].strip().startswith('if "${VENV_DIR}/bin/python" -')
    # …and it really is inside hal0_migration_step, not merely the only one.
    helper = _extract_function("hal0_migration_step")
    assert bare[0] in helper


def test_migration_heredocs_are_valid_python() -> None:
    """A syntax error in an embedded heredoc only surfaces on a real install."""
    import ast
    import re

    text = INSTALL_SH.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PYEOF'\n(.*?)\nPYEOF\n", text, flags=re.S)
    assert len(blocks) >= 7, "expected the migration heredocs to still be present"
    for block in blocks:
        ast.parse(block)


def test_installer_runs_the_same_convergence_steps_as_the_updater() -> None:
    """install.sh convergence must not be weaker than `hal0 update` convergence.

    ``ensure_seed_profiles``, ``clear_stale_mtp_overrides``,
    ``retag_stale_slot_images`` and ``sanitize_model_extra_args`` run through
    the SAME shared ``run_post_activation_migrations`` sequence
    ``Updater.commit()`` calls (GH #1475), rather than being called
    separately here — the two upgrade paths can never drift onto different
    on-disk state, and calling them a second time directly would just re-run
    the same passes.
    """
    import re

    text = INSTALL_SH.read_text(encoding="utf-8")
    for fn in (
        "run_post_activation_migrations",
        "rerender_slot_units",
        "sweep_slot_enabled_keys",
        "reset_profile_catalog",
        "convergence_report",
    ):
        # Match the name anywhere in an import list, not just directly after
        # `import` — several of these are imported alongside a sibling
        # (`import profile_reset_status, reset_profile_catalog`).
        assert re.search(rf"^from hal0\.[\w.]+ import .*\b{fn}\b", text, re.M), (
            f"install.sh no longer imports {fn}"
        )
        assert re.search(rf"^.*\b{fn}\(", text, re.M), f"install.sh imports {fn} but never calls it"
