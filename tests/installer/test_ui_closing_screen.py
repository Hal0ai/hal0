"""``ui_closing_screen`` in installer/lib/ui.sh: the first-account line and the admin key.

The admin key is a secret and the installer's stdout and stderr are copied into
a log other users can read, so its value must reach the terminal device only.
In these tests the "terminal" is a file given through ``UI_TTY_OUT``; a missing
terminal is a path whose directory does not exist.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_SH = REPO_ROOT / "installer" / "lib" / "ui.sh"
INSTALL_SH = REPO_ROOT / "installer" / "install.sh"

KEY = "hk_test_1234567890abcdef"


@dataclass
class Run:
    returncode: int
    stdout: str
    stderr: str
    tty: str


def function_body(name: str) -> str:
    """Text of the shell function ``name`` in ui.sh (up to its closing brace at column 0)."""
    text = UI_SH.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def strip_guarded_printf(body: str) -> str:
    """Drop the lines that write to the terminal descriptor (>&5): the only allowed printf."""
    return "\n".join(line for line in body.splitlines() if ">&5" not in line)


@pytest.fixture
def run_closing(tmp_path: Path) -> Callable[..., Run]:
    def _run(
        *,
        minted: int = 0,
        api_env_lines: list[str] | None = None,
        tty: str | None = None,
        dev: int = 0,
        no_start: int = 0,
        upgrade: int = 0,
        owui: str = "off",
    ) -> Run:
        api_env = tmp_path / "api.env"
        api_env.write_text("\n".join(api_env_lines or []) + "\n", encoding="utf-8")
        tty_path = tty if tty is not None else str(tmp_path / "tty")
        script = tmp_path / "run.sh"
        script.write_text(
            "\n".join(
                [
                    "set -euo pipefail",
                    "trap 'echo ERR-TRAP >&2' ERR",
                    f'source "{UI_SH}"',
                    f"DEV_MODE={dev}",
                    f"NO_START={no_start}",
                    f"IS_UPGRADE={upgrade}",
                    "HOST=192.0.2.7",
                    "declare -A COMPONENT_STATES=()",
                    f'COMPONENT_STATES[openwebui]="{owui}"',
                    f'API_ENV="{api_env}"',
                    f"_HAL0_ADMIN_KEY_MINTED={minted}",
                    f'UI_TTY_OUT="{tty_path}"',
                    "ui_closing_screen",
                    "echo AFTER",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HAL0_PLAIN": "1"},
            check=False,
        )
        tty_file = Path(tty_path)
        tty_text = tty_file.read_text(encoding="utf-8") if tty_file.is_file() else ""
        assert "AFTER" in proc.stdout, proc.stderr
        return Run(proc.returncode, proc.stdout, proc.stderr, tty_text)

    return _run


@pytest.fixture
def install_sh_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_fresh_install_shows_the_key_on_the_terminal_only(run_closing: Callable[..., Run]) -> None:
    r = run_closing(minted=1, api_env_lines=[f"HAL0_ADMIN_KEY={KEY}"])
    assert f"Admin key (shown once): {KEY}" in r.tty
    assert KEY not in r.stdout and KEY not in r.stderr
    assert "An admin key was created and shown on the terminal." in r.stdout
    assert r.returncode == 0


def test_no_terminal_prints_no_value_and_does_not_fail(run_closing: Callable[..., Run]) -> None:
    r = run_closing(
        minted=1,
        api_env_lines=[f"HAL0_ADMIN_KEY={KEY}"],
        tty="/nonexistent/dir/tty",
    )
    assert KEY not in r.stdout + r.stderr
    assert r.returncode == 0
    assert "ERR-TRAP" not in r.stderr
    assert "It is stored in" in r.stdout and "(root only)" in r.stdout


def test_marker_unset_says_nothing_about_a_key(run_closing: Callable[..., Run]) -> None:
    r = run_closing(minted=0, api_env_lines=[f"HAL0_ADMIN_KEY={KEY}"])
    assert "dmin key" not in r.stdout + r.tty
    assert KEY not in r.tty


def test_key_line_missing_from_the_file_is_not_an_error(run_closing: Callable[..., Run]) -> None:
    r = run_closing(minted=1, api_env_lines=["OTHER=1"])
    assert r.returncode == 0
    assert "It is stored in" in r.stdout
    assert "Admin key (shown once)" not in r.tty


def test_the_value_is_never_a_program_argument() -> None:
    body = function_body("ui_closing_screen")
    assert not re.search(
        r"\b(grep|sed|awk|cut|cat|echo|printf\s+[^>]*\$\{?_?key)",
        strip_guarded_printf(body),
    )
    # The one allowed printf per terminal line really does target the terminal.
    assert ">&5" in body


@pytest.mark.parametrize(
    ("dev", "no_start", "upgrade", "owui", "shown"),
    [
        (0, 0, 0, "on", True),
        (1, 0, 0, "on", False),
        (0, 1, 0, "on", False),
        (0, 0, 1, "on", False),
        (0, 0, 0, "off", False),
    ],
)
def test_first_account_line_only_when_it_is_true(
    run_closing: Callable[..., Run],
    dev: int,
    no_start: int,
    upgrade: int,
    owui: str,
    shown: bool,
) -> None:
    r = run_closing(minted=0, dev=dev, no_start=no_start, upgrade=upgrade, owui=owui)
    assert ("Create the first chat account now: http://" in r.stdout) is shown


def test_install_sh_calls_it_after_the_totals_and_before_summary_json(install_sh_text: str) -> None:
    a = install_sh_text.index('info "Total install time')
    b = install_sh_text.index("ui_closing_screen", a)
    c = install_sh_text.index("# ── --summary-json", a)
    assert a < b < c
    assert (
        "ui_closing_screen" not in install_sh_text[install_sh_text.rindex("ui_step_finalize\n") : a]
    )
