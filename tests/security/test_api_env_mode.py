"""Issue #1466: one mode for ``api.env``, honoured by every writer.

The live box carried ``644 hal0:hal0`` on ``/etc/hal0/api.env`` while that
file held ``HF_TOKEN``, ``MINIMAX_API_KEY``, ``OPENROUTER_API_KEY``,
``HERMES_SESSION_TOKEN`` and ``HAL0_TURNSTONE_TOKEN`` — every local account
could read them.

FOUR writers disagreed:

1. ``installer/install.sh`` — ``chmod 0644`` on the network-block refresh,
   which since 233e305e (#1375) runs on **every** re-run over an existing
   api.env, so any upgrade or repair re-flattened the file.
2. ``hal0.api._env_store`` — the dashboard writer, 0600. Correct, and
   promptly undone by (1) or (4).
3. ``hal0.service_identity`` — key rotation, 0640, with ``routes/auth.py``
   promising "a never-world-readable 0640".
4. ``hal0.install.perms`` — the perms-enforcement table pinned api.env at
   0644 behind a ``FIXME(phase4)``, so the engine whose *job* is converging
   the filesystem independently re-flattened any tightening.

The resolution: a single constant, ``paths.API_ENV_MODE``, and every Python
writer reads it. This file asserts that — including that the constant is
not group- or world-readable, which is the property the FIXME deferred.
``installer/install.sh`` cannot import Python, so it is checked textually:
no ``chmod 0644`` may remain on an api.env path.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from hal0.api._env_store import upsert_env_value
from hal0.config import paths

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "installer" / "install.sh"


# ── 1. the constant ──────────────────────────────────────────────────────────


def test_api_env_mode_is_owner_only() -> None:
    """0600. Not 0640 (a group-readable file is world-readable the moment
    the service group gains a second member), not 0644."""
    assert paths.API_ENV_MODE == 0o600


def test_api_env_mode_grants_nothing_to_group_or_other() -> None:
    """Stated as the property rather than the number, so a future widening
    fails here with a reason attached."""
    assert not paths.API_ENV_MODE & (stat.S_IRWXG | stat.S_IRWXO)


def test_api_env_path_has_one_resolver() -> None:
    assert paths.api_env() == paths.etc() / "api.env"


# ── 2. writer 2 — the dashboard / secrets store ──────────────────────────────


def test_env_store_writes_at_the_shared_mode(tmp_path: Path) -> None:
    target = tmp_path / "api.env"
    upsert_env_value(target, "HF_TOKEN", "hf_secret")
    assert (target.stat().st_mode & 0o777) == paths.API_ENV_MODE


def test_env_store_tightens_a_pre_existing_world_readable_file(tmp_path: Path) -> None:
    """The upgrade path that matters: a box that already has the 0644 file
    must be repaired by the next write, not merely left alone."""
    target = tmp_path / "api.env"
    target.write_text('HF_TOKEN="old"\n', encoding="utf-8")
    os.chmod(target, 0o644)
    upsert_env_value(target, "HF_TOKEN", "new")
    assert (target.stat().st_mode & 0o777) == paths.API_ENV_MODE


# ── 3. writer 3 — key rotation ───────────────────────────────────────────────


def test_service_identity_uses_the_shared_mode() -> None:
    """It was 0640 — closer than 0644, still not the one constant."""
    from hal0 import service_identity

    assert service_identity._API_ENV_MODE == paths.API_ENV_MODE


def test_rotated_key_file_is_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0 import service_identity

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    target = paths.api_env()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("HAL0_LOG_LEVEL=info\n", encoding="utf-8")
    os.chmod(target, 0o644)

    service_identity.rotate_api_env_key("admin")
    assert (target.stat().st_mode & 0o777) == paths.API_ENV_MODE


# ── 4. writer 4 — the perms engine ───────────────────────────────────────────


def test_perms_table_pins_api_env_at_the_shared_mode() -> None:
    """The FIXME(phase4) row. While it said 0644 the engine reverted every
    fix the other three writers made — this is the row that made the bug
    survive repair."""
    from hal0.install import perms

    rows = {r.target.name: r for r in perms.ownership_table(service_user="hal0")}
    assert rows["api.env"].mode == paths.API_ENV_MODE


def test_no_perms_row_leaves_a_secret_bearing_file_world_readable() -> None:
    from hal0.install import perms

    for row in perms.ownership_table(service_user="hal0"):
        if row.target.name in {"api.env", "openwebui.env"}:
            assert not row.mode & (stat.S_IRWXG | stat.S_IRWXO), row.role


# ── 5. writer 1 — the installer ──────────────────────────────────────────────


def test_installer_never_chmods_api_env_world_readable() -> None:
    """install.sh:919 ``chmod 0644 "${API_ENV_TMP}"`` — on the refresh branch
    that #1375 made run on every re-run, so an upgrade downgraded the file."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"chmod\s+0?6[24]4\s+.*API_ENV", line)
    ]
    assert offenders == [], f"installer still widens api.env: {offenders}"


def test_installer_chmods_api_env_to_the_shared_mode() -> None:
    """Both branches — the initial write and the idempotent refresh — must
    land the same mode, or a re-run undoes the fresh install."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    hits = re.findall(r"chmod\s+(0?600)\s+\"\$\{API_ENV(?:_TMP)?\}\"", text)
    assert len(hits) >= 2, "expected api.env chmod 0600 on both the write and refresh branches"


def test_installer_comment_no_longer_advertises_a_world_readable_file() -> None:
    """install.sh warned "api.env is 0644, world-readable" *while* pointing
    the operator at the dashboard Secrets path, which writes that file."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "api.env is 0644" not in text


# ── 6. the doctor backstop ───────────────────────────────────────────────────


def test_doctor_flags_a_world_readable_api_env(tmp_path: Path, monkeypatch) -> None:
    """Independent of the perms table — if a fifth writer appears, or the
    row is widened again, the operator still gets told."""
    from hal0.cli.doctor_all import check_secret_file_modes
    from hal0.cli.doctor_verify import _FAIL, _PASS

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    target = paths.api_env()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('HF_TOKEN="x"\n', encoding="utf-8")

    os.chmod(target, 0o644)
    bad = check_secret_file_modes()
    assert bad.status == _FAIL
    assert bad.critical is True
    assert "api.env" in bad.detail and "644" in bad.detail

    os.chmod(target, paths.API_ENV_MODE)
    assert check_secret_file_modes().status == _PASS


def test_doctor_secret_mode_check_is_clean_when_the_file_is_absent(
    tmp_path: Path, monkeypatch
) -> None:
    from hal0.cli.doctor_all import check_secret_file_modes
    from hal0.cli.doctor_verify import _PASS

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    assert check_secret_file_modes().status == _PASS


def test_doctor_all_includes_the_secret_mode_row() -> None:
    """Wired into the roll-up, not merely defined."""
    import inspect

    from hal0.cli import doctor_all

    assert "check_secret_file_modes()" in inspect.getsource(doctor_all.build_all_checks)
