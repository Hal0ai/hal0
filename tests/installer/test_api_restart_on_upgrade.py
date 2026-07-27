"""An in-place upgrade must put the NEW code into service.

Observed on halo 2026-07-27. The installer ended with ``systemctl enable --now
hal0-api``. On a fresh box that starts the service; on a box where hal0-api is
already **active**, ``--now`` is a no-op — systemd will not restart a running
unit. So the upgrade replaced the venv, swapped ``/usr/lib/hal0/current``,
printed its success banner and exited 0, while the live process kept serving the
old already-imported code. ``hal0 --version`` reported the new version and
``/api/health`` reported the old one; they only agreed after a manual
``systemctl restart hal0-api`` 15 minutes later.

``start_or_restart_api`` closes that: already-active units get an explicit
restart, everything else gets ``enable --now``. Driven here through a fake
``systemctl`` on PATH that records its argv (real systemd isn't available in
CI), the same shim technique ``test_preflight_ports_own_service.py`` uses.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_systemctl(tmp_path: Path, *, is_active: str) -> Path:
    """A systemctl that reports ``is_active`` and logs every invocation."""
    calls = tmp_path / "systemctl.calls"
    _write_exec(
        tmp_path / "systemctl",
        f'echo "$@" >> "{calls}"\n'
        'if [[ "$1" == "is-active" ]]; then\n'
        f'  echo "{is_active}"\n'
        f'  [[ "{is_active}" == "active" ]] && exit 0 || exit 3\n'
        "fi\n"
        "exit 0\n",
    )
    return calls


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Source install.sh's function definitions and call the unit under test.

    ``install.sh`` runs its whole flow at import, so we extract just the
    function body rather than sourcing the script.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("start_or_restart_api()")
    end = text.index("\n}\n", start) + 3
    func = text[start:end]

    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -uo pipefail\n"
        "info() { :; }\nwarn() { :; }\nok() { :; }\n"
        "wait_active() { return 0; }\n"
        f"{func}\n"
        "start_or_restart_api\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
    subprocess.run(["bash", str(script)], env=env, capture_output=True, timeout=30, check=False)
    calls = tmp_path / "systemctl.calls"
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_running_api_is_restarted_onto_the_new_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression: an upgrade over a live box must bounce the service."""
    _fake_systemctl(tmp_path, is_active="active")
    calls = _run(tmp_path, monkeypatch)

    assert "restart hal0-api" in calls, (
        "an already-active hal0-api was not restarted — the upgrade would keep "
        f"serving the old code. systemctl calls:\n{calls}"
    )


def test_stopped_api_is_enabled_and_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh-install path is unchanged: enable + start, no pointless restart."""
    _fake_systemctl(tmp_path, is_active="inactive")
    calls = _run(tmp_path, monkeypatch)

    assert "enable --now hal0-api" in calls
    assert "restart hal0-api" not in calls


def test_api_is_enabled_either_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restarting must not cost the boot-time enable — the unit still has to
    come back after a reboot."""
    _fake_systemctl(tmp_path, is_active="active")
    calls = _run(tmp_path, monkeypatch)

    assert "enable" in calls
