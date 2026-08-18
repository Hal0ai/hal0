"""Tests for the `hal0 config show [hal0|upstreams|providers]` and `config
edit [...]` file selector (CLI consolidation, 2026-07).

Previously both commands were hardcoded to hal0.toml, even though `hal0
config validate` already checks three files — when validate reported an
error in upstreams.toml, there was no `edit`/`show` target for it.
"""

from __future__ import annotations

import stat
from pathlib import Path

from typer.testing import CliRunner

from hal0.cli import config_commands

runner = CliRunner()


def _simulate_real_etc_hal0(monkeypatch) -> None:
    """Make ``_fchown_to_service_owner``'s "is this the real /etc/hal0?"
    guard say yes, while file I/O still happens in the test's HAL0_HOME
    sandbox.

    Only the tests that specifically exercise the privileged/chown-denied
    ownership paths need this — everything else should exercise the
    ordinary "we're in a sandbox, skip ownership entirely" branch exactly
    as production code does outside a real install, regardless of whether
    this particular host happens to have a system ``hal0`` account (hal0's
    own dev/CI boxes do, since hal0 also runs on them for real).
    """
    monkeypatch.setattr(config_commands._config_paths, "etc", lambda: Path("/etc/hal0"))


def _set_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_HOME", str(home))
    return home / "etc" / "hal0"


def test_config_show_defaults_to_hal0_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    (cfg_dir / "hal0.toml").write_text("[meta]\nschema_version = 1\n")

    result = runner.invoke(config_commands.app, ["show"])
    assert result.exit_code == 0, result.output
    assert "schema_version" in result.output


def test_config_show_upstreams_selects_upstreams_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    (cfg_dir / "upstreams.toml").write_text("[[upstream]]\nname = 'demo'\n")

    result = runner.invoke(config_commands.app, ["show", "upstreams"])
    assert result.exit_code == 0, result.output
    assert "demo" in result.output


def test_config_show_providers_selects_providers_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    (cfg_dir / "providers.toml").write_text("[[provider]]\nname = 'demo-provider'\n")

    result = runner.invoke(config_commands.app, ["show", "providers"])
    assert result.exit_code == 0, result.output
    assert "demo-provider" in result.output


def test_config_show_missing_file_reports_dim_notice(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    assert not (cfg_dir / "upstreams.toml").exists()

    result = runner.invoke(config_commands.app, ["show", "upstreams"])
    assert result.exit_code == 0, result.output
    assert "No config at" in result.output


def test_config_edit_upstreams_seeds_and_opens_upstreams_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    monkeypatch.setenv("EDITOR", "true")  # no-op editor available on any *nix box

    result = runner.invoke(config_commands.app, ["edit", "upstreams"])
    assert result.exit_code == 0, result.output
    seeded = cfg_dir / "upstreams.toml"
    assert seeded.exists()
    # hal0.toml's bespoke seed content must NOT leak into other files.
    assert "port_range_start" not in seeded.read_text()


def test_config_edit_upstreams_seeds_at_canonical_mode(monkeypatch, tmp_path: Path) -> None:
    """A freshly-seeded upstreams.toml must land at 0640, not the 0644 that a
    bare ``Path.write_text`` produces.

    ADR-0002 tightened upstreams.toml to 0640 (UPSTREAMS_TOML_MODE) precisely
    because its provider/endpoint inventory is not public information —
    `save_upstreams_config`'s atomic rewrite already honours that mode, but
    `hal0 config edit upstreams` seeding a *missing* file went through a bare
    `write_text`, which carries the process umask (typically 0644) instead.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(config_commands.app, ["edit", "upstreams"])
    assert result.exit_code == 0, result.output
    seeded = cfg_dir / "upstreams.toml"
    assert seeded.exists()
    mode = stat.S_IMODE(seeded.stat().st_mode)
    assert mode == 0o640, f"expected upstreams.toml seeded at 0640, got {oct(mode)}"


def test_config_edit_hal0_seeds_at_canonical_mode(monkeypatch, tmp_path: Path) -> None:
    """A freshly-seeded hal0.toml must land at 0600, matching perms.py's
    canonical mode for the file — the same bare-``write_text`` gap as
    upstreams.toml, pre-existing but fixed by the same change.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(config_commands.app, ["edit", "hal0"])
    assert result.exit_code == 0, result.output
    seeded = cfg_dir / "hal0.toml"
    assert seeded.exists()
    mode = stat.S_IMODE(seeded.stat().st_mode)
    assert mode == 0o600, f"expected hal0.toml seeded at 0600, got {oct(mode)}"


def test_config_edit_seed_chowns_to_service_owner_when_privileged(
    monkeypatch, tmp_path: Path
) -> None:
    """A seed written while running as root (e.g. ``sudo hal0 config edit``)
    must land owned by the ``hal0`` service account, not root.

    hal0-api.service runs ``User=hal0``; combined with the seed's new
    canonical 0600/0640 mode, a root-owned (or third-user-owned) seed is one
    the API's own service account cannot open — ``_config_require_auth()``
    swallows that ``PermissionError`` into "unset" and falls back to
    ``require_auth`` OFF (Codex P2 on a5f4fe5d, the mode-only fix). Faking
    "root" here via monkeypatched ``pwd``/``grp``/``os.fchown`` so the test
    doesn't depend on the runner actually being root or this box actually
    having a system ``hal0`` account.
    """
    _set_home(monkeypatch, tmp_path)
    _simulate_real_etc_hal0(monkeypatch)
    monkeypatch.setenv("EDITOR", "true")

    class _FakePasswd:
        pw_uid = 4242

    class _FakeGroup:
        gr_gid = 4343

    chown_calls: list[tuple[int, int, int]] = []

    def _fake_fchown(fd: int, uid: int, gid: int) -> None:
        chown_calls.append((fd, uid, gid))

    monkeypatch.setattr(config_commands.pwd, "getpwnam", lambda name: _FakePasswd())
    monkeypatch.setattr(config_commands.grp, "getgrnam", lambda name: _FakeGroup())
    monkeypatch.setattr(config_commands.os, "fchown", _fake_fchown)

    result = runner.invoke(config_commands.app, ["edit", "hal0"])
    assert result.exit_code == 0, result.output
    assert len(chown_calls) == 1, f"expected exactly one fchown attempt, got {chown_calls}"
    _fd, uid, gid = chown_calls[0]
    assert (uid, gid) == (4242, 4343), f"chowned to the wrong target: {chown_calls}"


def test_config_edit_seed_rejects_seed_when_chown_denied(monkeypatch, tmp_path: Path) -> None:
    """An unprivileged third-user invocation cannot chown to the service
    account (POSIX: only root may chown to an arbitrary uid). Publishing the
    seed anyway would hand the operator a self-owned hal0.toml at 0600 that
    hal0-api (User=hal0) cannot read — and _config_require_auth() turns that
    unreadable-config case into a silent fall-back to require_auth OFF. The
    command must instead abort loudly: nonzero exit, no file left behind,
    and a message that tells the operator to re-run under sudo.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    _simulate_real_etc_hal0(monkeypatch)
    monkeypatch.setenv("EDITOR", "true")

    class _FakePasswd:
        pw_uid = 4242

    class _FakeGroup:
        gr_gid = 4343

    def _denied_fchown(fd: int, uid: int, gid: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(config_commands.pwd, "getpwnam", lambda name: _FakePasswd())
    monkeypatch.setattr(config_commands.grp, "getgrnam", lambda name: _FakeGroup())
    monkeypatch.setattr(config_commands.os, "fchown", _denied_fchown)

    result = runner.invoke(config_commands.app, ["edit", "hal0"])
    assert result.exit_code != 0, "a chown-denied seed must abort, not silently succeed"
    seeded = cfg_dir / "hal0.toml"
    assert not seeded.exists(), "the unreadable-to-hal0-api seed must not be left on disk"
    assert "sudo" in result.output, f"error must point the operator at sudo: {result.output}"


def test_config_edit_seed_rejects_seed_on_other_oserror(monkeypatch, tmp_path: Path) -> None:
    """Widened from bare PermissionError to OSError on review (e.g. EROFS on
    a read-only-remounted /etc) — any chown failure must hit the same loud
    abort, not an unhandled traceback.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    _simulate_real_etc_hal0(monkeypatch)
    monkeypatch.setenv("EDITOR", "true")

    class _FakePasswd:
        pw_uid = 4242

    class _FakeGroup:
        gr_gid = 4343

    def _erofs_fchown(fd: int, uid: int, gid: int) -> None:
        raise OSError(30, "Read-only file system")  # errno.EROFS

    monkeypatch.setattr(config_commands.pwd, "getpwnam", lambda name: _FakePasswd())
    monkeypatch.setattr(config_commands.grp, "getgrnam", lambda name: _FakeGroup())
    monkeypatch.setattr(config_commands.os, "fchown", _erofs_fchown)

    result = runner.invoke(config_commands.app, ["edit", "upstreams"])
    assert result.exit_code != 0
    assert not (cfg_dir / "upstreams.toml").exists()
    assert "sudo" in result.output


def test_config_edit_seed_hints_sudo_when_mkstemp_denied(monkeypatch, tmp_path: Path) -> None:
    """/etc/hal0 itself (mode 2775 hal0:hal0) is not writable by an ordinary
    user outside the ``hal0`` group, so ``tempfile.mkstemp`` raises
    ``PermissionError`` before ``_fchown_to_service_owner`` is ever reached —
    a failure point the ownership-focused ``ConfigSeedOwnershipError`` catch
    doesn't cover. Must abort with the same sudo hint, not an unhandled
    traceback (#1885).
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    _simulate_real_etc_hal0(monkeypatch)
    monkeypatch.setenv("EDITOR", "true")

    def _denied_mkstemp(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(config_commands.tempfile, "mkstemp", _denied_mkstemp)

    result = runner.invoke(config_commands.app, ["edit", "hal0"])
    assert result.exit_code != 0, "a permission-denied seed dir must abort, not traceback"
    assert not (cfg_dir / "hal0.toml").exists()
    assert "sudo" in result.output, f"error must point the operator at sudo: {result.output}"


def test_config_edit_seed_ignores_host_hal0_account_in_sandbox(monkeypatch, tmp_path: Path) -> None:
    """A HAL0_HOME sandbox seed must succeed regardless of whether the real
    host machine happens to have a system ``hal0`` account.

    Deliberately does NOT monkeypatch ``pwd``/``grp``/``os.fchown``/``etc()``
    — this exercises the actual ``pwd.getpwnam("hal0")`` lookup against
    whatever this box really has. hal0's own dev/CI boxes genuinely install
    a system ``hal0`` account (hal0 also runs on them for real), so on such
    a box this is also a regression test for keying the ownership
    requirement off "does a hal0 account exist" instead of "is this the
    real /etc/hal0" (review finding on commit ca3295ff): that version
    resolved the real ``hal0`` uid/gid, tried to ``fchown`` a HAL0_HOME
    sandbox file to it, failed (the test process isn't root or ``hal0``),
    and made this command die with the sudo hint for a throwaway temp tree
    ``hal0-api`` will never read.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(config_commands.app, ["edit", "upstreams"])
    assert result.exit_code == 0, result.output
    seeded = cfg_dir / "upstreams.toml"
    assert seeded.exists()
    mode = stat.S_IMODE(seeded.stat().st_mode)
    assert mode == 0o640, f"expected upstreams.toml seeded at 0640, got {oct(mode)}"


def test_permission_denied_hint_does_not_recommend_widening_the_file(
    monkeypatch, tmp_path: Path
) -> None:
    """The remedy is sudo or group membership — never `chmod 0644`.

    upstreams.toml is 0640 as of ADR-0002 (its provider inventory is not public
    information). The old hint told an operator hitting PermissionError to
    `sudo chmod 0644` the selected file, i.e. to undo that tightening by hand —
    and `hal0 doctor perms` would then converge it back, so the advice was both
    harmful and wrong. Asserts the rendered hint directly.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    target = cfg_dir / "upstreams.toml"
    target.write_text("[[upstream]]\nname = 'demo'\n")
    target.chmod(0o640)

    def _boom(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)

    result = runner.invoke(config_commands.app, ["show", "upstreams"])

    assert result.exit_code == 1, result.output
    # Rich wraps the panel, so compare with all whitespace removed — a
    # recommendation split across two lines is still a recommendation.
    out = "".join(result.output.split())
    assert "Permissiondenied" in out
    assert "0644" not in out, f"hint still names a world-readable mode: {result.output}"
    assert "sudochmod" not in out, f"hint still recommends a chmod: {result.output}"
    assert "sudo" in out
    assert "usermod-aGhal0" in out


def test_permission_denied_hint_omits_group_advice_for_owner_only_files(
    monkeypatch, tmp_path: Path
) -> None:
    """hal0.toml is 0600 by design — "join the hal0 group" would not help.

    Sending an operator through `usermod -aG` and a re-login only to hit the
    identical error is worse than no advice, so the remedy is mode-aware.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    target = cfg_dir / "hal0.toml"
    target.write_text("[meta]\nschema_version = 1\n")
    target.chmod(0o600)

    def _boom(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)

    result = runner.invoke(config_commands.app, ["show"])

    assert result.exit_code == 1, result.output
    out = "".join(result.output.split())
    assert "usermod" not in out, f"group advice on an owner-only file: {result.output}"
    assert "sudo" in out
    assert "owner-only" in out
