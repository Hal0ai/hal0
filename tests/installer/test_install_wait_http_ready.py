"""Post-install probes must wait for hal0-api to answer HTTP (#2804).

``wait_active`` only checks ``systemctl is-active``; after a restart the
port can stay closed for several seconds, so the memory-graph and
structured-output probes raced it and reported false failures.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
_TEXT = INSTALL_SH.read_text(encoding="utf-8")


def _extract(func_name: str) -> str:
    start = _TEXT.index(f"{func_name}()")
    end = _TEXT.index("\n}\n", start) + 3
    return _TEXT[start:end]


def _drive(tmp_path: Path, curl_body: str, call: str) -> subprocess.CompletedProcess[str]:
    curl = tmp_path / "curl"
    curl.write_text(f"#!/usr/bin/env bash\n{curl_body}\n", encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "drive.sh"
    script.write_text(
        f"#!/usr/bin/env bash\nset -uo pipefail\n{_extract('wait_http_ready')}\n{call}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(script)],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_waits_through_connection_refused_then_succeeds(tmp_path: Path) -> None:
    """Port closed for the first calls (curl exit 7), then it answers."""
    count = tmp_path / "n"
    body = (
        f'n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{count}"\n'
        "[ $n -ge 3 ] && exit 0 || exit 7"
    )
    res = _drive(tmp_path, body, "wait_http_ready http://127.0.0.1:8080/api/health 10")
    assert res.returncode == 0, res.stderr
    assert int(count.read_text()) == 3


def test_times_out_when_never_ready(tmp_path: Path) -> None:
    res = _drive(tmp_path, "exit 7", "wait_http_ready http://127.0.0.1:8080/api/health 1")
    assert res.returncode == 1


def test_install_gates_probes_on_health_after_wait_active() -> None:
    """The restart path must poll /api/health right after the systemd wait."""
    a = _TEXT.index("if wait_active hal0-api 15; then")
    window = _TEXT[a : a + 900]
    assert "wait_http_ready" in window
    assert "/api/health" in window
