"""Hermes drop-in ships as 10-hal0.conf; legacy override.conf migrates safely (#2391).

hal0 used to ship the drop-in as ``override.conf`` (the name ``systemctl edit``
hands the operator). The installer now writes ``10-hal0.conf`` and removes a
legacy ``override.conf`` only when its sha256 matches a version hal0 shipped.
Driven by extracting the drop-in block from install.sh and running it against
a tmp ``UNIT_DIR`` (same technique as ``test_api_unit_journal_group.py``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "installer" / "install.sh"
SHIPPED = REPO / "installer" / "systemd" / "hal0-agent@hermes.service.d" / "10-hal0.conf"


def _run(tmp_path: Path, legacy: str | None) -> tuple[Path, str]:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index('    AGENT_DROPIN_SRC="')
    end = text.index("    # Session-start hook: inject-system-state.sh", start)
    unit_dir = tmp_path / "units"
    dropin_dir = unit_dir / "hal0-agent@hermes.service.d"
    dropin_dir.mkdir(parents=True)
    if legacy is not None:
        (dropin_dir / "override.conf").write_text(legacy, encoding="utf-8")
    script = (
        'info() { echo "INFO: $*"; }\nwarn() { echo "WARN: $*"; }\n'
        f'REPO_ROOT="{REPO}"\nUNIT_DIR="{unit_dir}"\n' + text[start:end]
    )
    proc = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script], capture_output=True, text=True, check=True
    )
    return dropin_dir, proc.stdout + proc.stderr


def test_fresh_install_writes_10_hal0_conf(tmp_path: Path) -> None:
    d, _ = _run(tmp_path, None)
    assert (d / "10-hal0.conf").read_text() == SHIPPED.read_text()
    assert not (d / "override.conf").exists()


def test_hash_matched_override_conf_is_removed(tmp_path: Path) -> None:
    # Current-HEAD content of the legacy file == the version hal0 last shipped
    # as override.conf (commit 1cc5cb8f); that is byte-identical to 10-hal0.conf.
    d, out = _run(tmp_path, SHIPPED.read_text())
    assert not (d / "override.conf").exists()
    assert (d / "10-hal0.conf").exists()
    assert "WARN" not in out


def test_operator_edited_override_conf_is_kept_with_warning(tmp_path: Path) -> None:
    edited = SHIPPED.read_text() + "\nEnvironment=OPERATOR_TWEAK=1\n"
    d, out = _run(tmp_path, edited)
    assert (d / "override.conf").read_text() == edited
    assert (d / "10-hal0.conf").exists()
    assert out.count("WARN") == 1 and "operator-owned" in out
