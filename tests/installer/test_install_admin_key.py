"""install.sh mints an admin and a client key into api.env when missing.

Every install leaves the box with ``HAL0_ADMIN_KEY`` so enabling auth never
needs a manual key step, and ``HAL0_CLIENT_KEY`` so on-box companions keep
reaching ``/v1`` once auth is on. The block must never replace an existing key, must
keep api.env owner-only, and must never write the value to stdout/stderr:
both are tee'd into the 0644 install log. The value is shown only on
``/dev/tty`` at the end of the install.

Driven by extracting the "Admin key" block and running it against a tmp
api.env with a fake ``${VENV_DIR}/bin/python``, the same technique
``test_avahi_hostname.py`` uses.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"

FAKE_KEY = "Zq3-fake_generated_key_value_0123456789abcdef"
# The fake generator appends a call counter, so the two keys differ.
ADMIN_KEY = f"{FAKE_KEY}1"
CLIENT_KEY = f"{FAKE_KEY}2"


def _extract_block() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("# ── Admin + client keys ─")
    end = text.index("# ── avahi mDNS host-name sync", start)
    return text[start:end]


def _drive(tmp_path: Path, api_env_content: str | None, *, python_ok: bool = True, extra: str = ""):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python"
    py.write_text(
        "#!/usr/bin/env bash\n"
        + (
            f'n=$(( $(cat "{tmp_path}/n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{tmp_path}/n"\n'
            f'echo "{FAKE_KEY}$n"\n'
            if python_ok
            else "exit 1\n"
        ),
        encoding="utf-8",
    )
    py.chmod(py.stat().st_mode | stat.S_IEXEC)

    api_env = tmp_path / "api.env"
    if api_env_content is not None:
        api_env.write_text(api_env_content, encoding="utf-8")
        api_env.chmod(0o600)

    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'info() { echo "INFO $*"; }\nwarn() { echo "WARN $*" >&2; }\n'
        f"VENV_DIR={tmp_path / 'venv'}\nAPI_ENV={api_env}\n"
        f"{_extract_block()}\n"
        'echo "CREATED=${ADMIN_KEY_CREATED}"\n' + extra,
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc, api_env


def test_mints_both_keys_when_api_env_has_none(tmp_path: Path) -> None:
    proc, api_env = _drive(tmp_path, "HAL0_BIND_HOST=0.0.0.0\n")
    text = api_env.read_text()
    assert f"HAL0_ADMIN_KEY={ADMIN_KEY}\n" in text
    assert f"HAL0_CLIENT_KEY={CLIENT_KEY}\n" in text
    assert "CREATED=1" in proc.stdout
    assert stat.S_IMODE(api_env.stat().st_mode) == 0o600


def test_never_echoes_the_key_to_the_logged_streams(tmp_path: Path) -> None:
    proc, _ = _drive(tmp_path, "HAL0_BIND_HOST=0.0.0.0\n")
    assert FAKE_KEY not in proc.stdout
    assert FAKE_KEY not in proc.stderr


def test_admin_key_value_is_kept_for_the_terminal_display(tmp_path: Path) -> None:
    """The end-of-install /dev/tty display reads new_admin_key; it must hold
    the admin value, never the client one."""
    script_tail = 'echo "SHOWN=${new_admin_key:-}"\n'
    proc, _ = _drive(tmp_path, "HAL0_BIND_HOST=0.0.0.0\n", extra=script_tail)
    assert f"SHOWN={ADMIN_KEY}" in proc.stdout


def test_existing_keys_are_never_replaced(tmp_path: Path) -> None:
    content = "HAL0_ADMIN_KEY=operator-kept-this\nHAL0_CLIENT_KEY=and-this\n"
    proc, api_env = _drive(tmp_path, content)
    assert api_env.read_text() == content
    assert "CREATED=0" in proc.stdout


def test_only_the_missing_key_is_minted(tmp_path: Path) -> None:
    proc, api_env = _drive(tmp_path, "HAL0_ADMIN_KEY=operator-kept-this\n")
    assert api_env.read_text() == (
        f"HAL0_ADMIN_KEY=operator-kept-this\nHAL0_CLIENT_KEY={ADMIN_KEY}\n"
    )
    assert "CREATED=0" in proc.stdout


def test_commented_or_empty_key_line_still_gets_a_key(tmp_path: Path) -> None:
    proc, api_env = _drive(tmp_path, "# HAL0_ADMIN_KEY=\nHAL0_ADMIN_KEY=\n")
    assert f"HAL0_ADMIN_KEY={ADMIN_KEY}\n" in api_env.read_text()
    assert "CREATED=1" in proc.stdout


def test_appends_on_its_own_line_when_file_lacks_trailing_newline(tmp_path: Path) -> None:
    _, api_env = _drive(tmp_path, "HAL0_BIND_HOST=0.0.0.0")
    assert api_env.read_text() == (
        f"HAL0_BIND_HOST=0.0.0.0\nHAL0_ADMIN_KEY={ADMIN_KEY}\nHAL0_CLIENT_KEY={CLIENT_KEY}\n"
    )


def test_generator_failure_warns_and_does_not_abort(tmp_path: Path) -> None:
    proc, api_env = _drive(tmp_path, "HAL0_BIND_HOST=0.0.0.0\n", python_ok=False)
    assert "HAL0_ADMIN_KEY" not in api_env.read_text()
    assert "HAL0_CLIENT_KEY" not in api_env.read_text()
    assert "CREATED=0" in proc.stdout
    assert "sudo hal0 auth reset-key" in proc.stderr
    assert "sudo hal0 auth rotate client" in proc.stderr
