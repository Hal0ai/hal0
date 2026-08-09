"""#1464 — argv contract of ``installer/wrappers/hal0-update``.

The sudoers grant is pinned to this binary, so the wrapper's ``case`` statement
IS the allow-list: whatever it forwards, the ``hal0`` service account can make
root do. These tests exercise the real script against a stub interpreter, so
they need no sudo, no root and no provisioned box.

The Python side re-validates everything (``hal0.updater.privileged.main``); this
suite pins the *shell* half — that a bad channel / version / directory token
never reaches the interpreter at all, and that a good one is forwarded verbatim.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "installer" / "wrappers" / "hal0-update"

_STUB_PY = """#!/bin/bash
# Records argv so the test can assert exactly what the wrapper forwarded.
printf '%s\\n' "$@" > "${HAL0_TEST_ARGV_SINK}"
# Records the resolved HAL0_RELEASES_URL (if any) so #1690 tests can assert
# on it without needing a real network fetch.
if [[ -n "${HAL0_TEST_ENV_SINK:-}" ]]; then
  printf '%s' "${HAL0_RELEASES_URL:-}" > "${HAL0_TEST_ENV_SINK}"
fi
"""


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """Lay the wrapper out exactly as install.sh does: <lib>/bin + <lib>/venv."""
    lib = tmp_path / "lib" / "hal0"
    (lib / "bin").mkdir(parents=True)
    (lib / "venv" / "bin").mkdir(parents=True)
    shutil.copy2(WRAPPER, lib / "bin" / "hal0-update")
    (lib / "bin" / "hal0-update").chmod(0o755)
    stub = lib / "venv" / "bin" / "python"
    stub.write_text(_STUB_PY)
    stub.chmod(0o755)
    return lib


def _run(
    installed: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[int, list[str], str]:
    sink = installed / "argv.txt"
    env_sink = installed / "env.txt"
    base_env = {
        "PATH": "/usr/bin:/bin",
        "HAL0_TEST_ARGV_SINK": str(sink),
        "HAL0_TEST_ENV_SINK": str(env_sink),
    }
    if env:
        base_env.update(env)
    proc = subprocess.run(
        [str(installed / "bin" / "hal0-update"), *args],
        capture_output=True,
        text=True,
        env=base_env,
        check=False,
    )
    forwarded = sink.read_text().splitlines() if sink.exists() else []
    return proc.returncode, forwarded, proc.stderr


def _released_url(installed: Path) -> str | None:
    """The HAL0_RELEASES_URL the stub interpreter observed, or None if unset."""
    env_sink = installed / "env.txt"
    return env_sink.read_text() if env_sink.exists() and env_sink.stat().st_size else None


def _write_api_env(hal0_home: Path, body: str) -> None:
    """Lay out $HAL0_HOME/etc/hal0/api.env exactly where paths.py's api_env() and
    this wrapper's resolve_releases_url() both look for it."""
    etc = hal0_home / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "api.env").write_text(body)


# ── accepted verbs ─────────────────────────────────────────────────────────────


def test_check_forwards_the_probe_verb(installed: Path) -> None:
    rc, argv, _ = _run(installed, "check")
    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "check"]


def test_stage_forwards_channel_and_optional_version(installed: Path) -> None:
    rc, argv, _ = _run(installed, "stage", "stable", "1.0.0")
    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "stage", "stable", "1.0.0"]

    rc, argv, _ = _run(installed, "stage", "nightly")
    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "stage", "nightly"]


def test_activate_and_discard_forward_the_dir_token(installed: Path) -> None:
    rc, argv, _ = _run(installed, "activate", "hal0-1.0.0")
    assert rc == 0
    assert argv[-2:] == ["activate", "hal0-1.0.0"]

    rc, argv, _ = _run(installed, "discard", "hal0-1.0.0-rc.1")
    assert rc == 0
    assert argv[-2:] == ["discard", "hal0-1.0.0-rc.1"]


def test_isolated_mode_is_always_used(installed: Path) -> None:
    """`-I` drops PYTHON* env vars, user site, and the CWD from sys.path.

    Without it a caller-controlled working directory containing a ``hal0/``
    package would be imported by a ROOT interpreter.
    """
    for args in (("check",), ("stage", "stable"), ("activate", "hal0-1.0.0")):
        _, argv, _ = _run(installed, *args)
        assert argv[0] == "-I"


def test_help_lists_exactly_the_granted_verbs(installed: Path) -> None:
    proc = subprocess.run(
        [str(installed / "bin" / "hal0-update"), "help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    for verb in ("check", "stage", "activate", "discard"):
        assert verb in proc.stdout


# ── operator releases-URL override (#1690) ──────────────────────────────────


def test_stage_reads_releases_url_from_hal0_home_api_env(installed: Path, tmp_path: Path) -> None:
    """The documented interim mechanism (custom HAL0_RELEASES_URL while
    releases.hal0.dev does not exist) must reach root, or every custom-URL box
    passes /api/updates/check and then always fails stage (#1690)."""
    hal0_home = tmp_path / "sandbox"
    _write_api_env(hal0_home, "HAL0_RELEASES_URL=https://mirror.example/preview.json\n")

    rc, argv, _ = _run(installed, "stage", "preview", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "stage", "preview"]
    assert _released_url(installed) == "https://mirror.example/preview.json"


def test_stage_refuses_a_file_url_from_api_env(installed: Path, tmp_path: Path) -> None:
    """#1750: api.env is hal0-owned under the hardened flip, so a ``file://``
    there is the unprivileged account picking a local path for ROOT to read as
    a release manifest. Only the root-owned update.conf may name one."""
    hal0_home = tmp_path / "sandbox"
    _write_api_env(hal0_home, "HAL0_RELEASES_URL=file:///srv/hal0-releases/stable.json\n")

    rc, argv, stderr = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc != 0
    assert argv == [], "the interpreter must not be reached at all"
    assert "update.conf" in stderr


def test_stage_strips_matching_quotes_around_releases_url(installed: Path, tmp_path: Path) -> None:
    hal0_home = tmp_path / "sandbox"
    _write_api_env(hal0_home, 'HAL0_RELEASES_URL="https://mirror.example/stable.json"\n')

    rc, _, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert _released_url(installed) == "https://mirror.example/stable.json"


def test_stage_uses_the_last_releases_url_line_when_duplicated(
    installed: Path, tmp_path: Path
) -> None:
    hal0_home = tmp_path / "sandbox"
    _write_api_env(
        hal0_home,
        "HAL0_RELEASES_URL=https://stale.example/stable.json\n"
        "HAL0_RELEASES_URL=https://current.example/stable.json\n",
    )

    rc, _, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert _released_url(installed) == "https://current.example/stable.json"


def test_stage_ignores_a_missing_api_env(installed: Path, tmp_path: Path) -> None:
    """No override configured -> unset, falls through to the production default
    (releases.hal0.dev) exactly like before this fix."""
    hal0_home = tmp_path / "sandbox"  # created, but no etc/hal0/api.env inside it
    hal0_home.mkdir()

    rc, argv, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "stage", "stable"]
    assert _released_url(installed) is None


def test_stage_ignores_an_api_env_with_no_releases_url_line(
    installed: Path, tmp_path: Path
) -> None:
    hal0_home = tmp_path / "sandbox"
    _write_api_env(hal0_home, "HF_TOKEN=super-secret\nHAL0_PORT=8000\n")

    rc, _, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert _released_url(installed) is None


def test_stage_never_forwards_unrelated_api_env_secrets(installed: Path, tmp_path: Path) -> None:
    """Only HAL0_RELEASES_URL is parsed out — never a `source`/`eval` of the
    whole file, which also carries provider tokens and HAL0_ADMIN_KEY /
    HAL0_CLIENT_KEY."""
    hal0_home = tmp_path / "sandbox"
    _write_api_env(
        hal0_home,
        "HF_TOKEN=super-secret\n"
        "HAL0_ADMIN_KEY=do-not-leak\n"
        "HAL0_RELEASES_URL=https://mirror.example/stable.json\n",
    )

    proc = subprocess.run(
        [str(installed / "bin" / "hal0-update"), "stage", "stable"],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HAL0_TEST_ARGV_SINK": str(installed / "argv.txt"),
            "HAL0_TEST_ENV_SINK": str(installed / "env.txt"),
            "HAL0_HOME": str(hal0_home),
        },
        check=False,
    )

    assert proc.returncode == 0
    assert "super-secret" not in proc.stdout
    assert "super-secret" not in proc.stderr
    assert "do-not-leak" not in proc.stdout
    assert "do-not-leak" not in proc.stderr


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "ftp://mirror.example/stable.json",
        "not-a-url",
        "http://mirror.example/stable.json",  # https:// only from api.env (#1690, #1750)
    ],
)
def test_stage_refuses_a_releases_url_with_a_disallowed_scheme(
    installed: Path, tmp_path: Path, bad_url: str
) -> None:
    hal0_home = tmp_path / "sandbox"
    _write_api_env(hal0_home, f"HAL0_RELEASES_URL={bad_url}\n")

    rc, argv, stderr = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 64
    assert argv == [], "malformed operator URL must never reach the interpreter"
    assert "hal0-update:" in stderr


def test_check_activate_discard_do_not_resolve_releases_url(
    installed: Path, tmp_path: Path
) -> None:
    """Only `stage` fetches a manifest; keep the blast radius of #1690 narrow."""
    hal0_home = tmp_path / "sandbox"
    _write_api_env(hal0_home, "HAL0_RELEASES_URL=https://mirror.example/stable.json\n")

    rc, _, _ = _run(installed, "check", env={"HAL0_HOME": str(hal0_home)})
    assert rc == 0
    assert _released_url(installed) is None

    rc, _, _ = _run(installed, "activate", "hal0-1.0.0", env={"HAL0_HOME": str(hal0_home)})
    assert rc == 0
    assert _released_url(installed) is None


# ── rejected input (never reaches the interpreter) ─────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        ("rm-rf",),
        ("write-unit", "x"),
        ("stage",),
        ("stage", "evil"),
        ("stage", "stable; rm -rf /"),
        ("stage", "stable", "../../etc"),
        ("stage", "stable", "1.0.0; whoami"),
        ("stage", "stable", "-rf"),
        ("activate",),
        ("activate", "hal0-1.0.0/../../etc"),
        ("activate", "../hal0-1.0.0"),
        ("activate", "hal0-.."),
        ("activate", "/usr/lib/hal0/hal0-1.0.0"),
        ("activate", "venv"),
        ("activate", "current"),
        ("discard",),
        ("discard", "hal0-1.0.0 ; reboot"),
        ("discard", "hal0-" + "a" * 200),
    ],
)
def test_bad_input_is_refused_before_the_interpreter_runs(
    installed: Path, args: tuple[str, ...]
) -> None:
    rc, forwarded, stderr = _run(installed, *args)
    assert rc == 64, f"expected refusal for {args!r}"
    assert forwarded == [], f"{args!r} reached the interpreter"
    assert "hal0-update:" in stderr


def test_missing_interpreter_fails_loudly(installed: Path) -> None:
    (installed / "venv" / "bin" / "python").unlink()
    rc, _, stderr = _run(installed, "check")
    assert rc == 64
    assert "interpreter not found" in stderr


def test_wrapper_resolves_the_venv_from_its_own_location(tmp_path: Path) -> None:
    """A non-default HAL0_PREFIX install must work — no hardcoded /usr/lib."""
    lib = tmp_path / "opt" / "custom" / "hal0"
    (lib / "bin").mkdir(parents=True)
    (lib / "venv" / "bin").mkdir(parents=True)
    shutil.copy2(WRAPPER, lib / "bin" / "hal0-update")
    (lib / "bin" / "hal0-update").chmod(0o755)
    stub = lib / "venv" / "bin" / "python"
    stub.write_text(_STUB_PY)
    stub.chmod(0o755)

    rc, argv, _ = _run(lib, "check")
    assert rc == 0
    assert argv[-1] == "check"


# ── root-owned override file (#1750) ───────────────────────────────────────────


def _write_update_conf(hal0_home: Path, body: str) -> None:
    """Lay out $HAL0_HOME/etc/hal0/update.conf — the root:root override file."""
    etc = hal0_home / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "update.conf").write_text(body)


def test_stage_reads_releases_url_from_root_owned_update_conf(
    installed: Path, tmp_path: Path
) -> None:
    hal0_home = tmp_path / "sandbox"
    _write_update_conf(hal0_home, "HAL0_RELEASES_URL=https://mirror.example/stable.json\n")

    rc, argv, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "stage", "stable"]
    assert _released_url(installed) == "https://mirror.example/stable.json"


def test_update_conf_may_name_a_file_url(installed: Path, tmp_path: Path) -> None:
    """`file://` stays available for the LXC smoke / release-prototype flow —
    but only from the file the unprivileged account cannot write."""
    hal0_home = tmp_path / "sandbox"
    _write_update_conf(hal0_home, "HAL0_RELEASES_URL=file:///srv/hal0-releases/stable.json\n")

    rc, _, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert _released_url(installed) == "file:///srv/hal0-releases/stable.json"


def test_update_conf_wins_over_api_env(installed: Path, tmp_path: Path) -> None:
    """The root-owned file is authoritative; the service-owned one cannot
    override an operator's explicit root-side choice."""
    hal0_home = tmp_path / "sandbox"
    _write_update_conf(hal0_home, "HAL0_RELEASES_URL=https://trusted.example/stable.json\n")
    _write_api_env(hal0_home, "HAL0_RELEASES_URL=https://attacker.example/stable.json\n")

    rc, _, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert _released_url(installed) == "https://trusted.example/stable.json"


def test_api_env_is_still_honoured_for_https(installed: Path, tmp_path: Path) -> None:
    """#1690's interim mechanism keeps working on boxes with no update.conf."""
    hal0_home = tmp_path / "sandbox"
    _write_api_env(hal0_home, "HAL0_RELEASES_URL=https://mirror.example/stable.json\n")

    rc, _, _ = _run(installed, "stage", "stable", env={"HAL0_HOME": str(hal0_home)})

    assert rc == 0
    assert _released_url(installed) == "https://mirror.example/stable.json"


def test_wrapper_does_not_claim_api_env_is_root_owned() -> None:
    """#1750: the comment asserting api.env is 'root-owned trusted config' was
    false under the hardened perms flip and must not come back."""
    text = WRAPPER.read_text()
    assert "SAME root-owned trusted config" not in text
    assert "update.conf" in text


def test_wrapper_and_python_dir_regexes_agree() -> None:
    """The shell copy is a fail-fast convenience; drift makes it a lie."""
    from hal0.updater.updater import RELEASE_DIR_RE

    text = WRAPPER.read_text()
    assert RELEASE_DIR_RE.pattern.strip("^$") in text


def test_wrapper_channel_list_matches_python() -> None:
    from hal0.updater.privileged import CHANNELS

    text = WRAPPER.read_text()
    assert (
        "|".join(sorted(CHANNELS, key=lambda c: ("stable", "preview", "nightly").index(c))) in text
    )
