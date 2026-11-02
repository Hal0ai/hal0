"""hal0-api.service single-source template (#3160)."""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import hal0.updater.privileged as privileged
import hal0.updater.updater as updater_mod
from hal0.cli import doctor_all as da
from hal0.install import api_unit
from hal0.install.api_unit import ApiUnitParams, desired_api_unit, render_api_unit

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / api_unit.TEMPLATE_RELPATH
SCRIPT = REPO / "src" / "hal0" / "install" / "api_unit.py"

PARAMS = ApiUnitParams(
    bin="/usr/lib/hal0/venv/bin/hal0",
    workdir="/usr/lib/hal0/current",
    api_env="/etc/hal0/api.env",
    etc_dir="/etc/hal0",
    hf_secrets_env="/var/lib/hal0/secrets/hal0-api.env",
    journal_group=True,
)


def _tmpl() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_template_ships_under_installer_systemd() -> None:
    # release.yml copies installer/ whole into the tarball.
    assert TEMPLATE.is_file()
    assert TEMPLATE.parent.name == "systemd" and TEMPLATE.parent.parent.name == "installer"


def test_render_has_no_leftover_tokens_and_keeps_port_expansion() -> None:
    out = render_api_unit(_tmpl(), PARAMS)
    assert "@HAL0_" not in out
    assert "ExecStart=/usr/lib/hal0/venv/bin/hal0 serve --port ${HAL0_PORT}\n" in out
    assert "TimeoutStopSec=30\n" in out
    assert "SupplementaryGroups=systemd-journal\n" in out
    assert "EnvironmentFile=-/etc/hal0/hermes-python.env\n" in out


def test_render_without_journal_group_emits_comment_only() -> None:
    out = render_api_unit(_tmpl(), ApiUnitParams(**{**PARAMS.__dict__, "journal_group": False}))
    assert "SupplementaryGroups" not in out.replace("# ", "") or "\nSupplementaryGroups" not in out
    assert "# no systemd-journal group on this host" in out


def test_unknown_token_is_rejected() -> None:
    with pytest.raises(ValueError):
        render_api_unit("x=@HAL0_NOPE@", PARAMS)


def test_value_containing_token_is_not_rescanned() -> None:
    p = ApiUnitParams(**{**PARAMS.__dict__, "workdir": "/odd/@HAL0_BIN@/dir"})
    assert "WorkingDirectory=/odd/@HAL0_BIN@/dir\n" in render_api_unit(_tmpl(), p)


@pytest.mark.parametrize("journal", [True, False])
def test_install_sh_render_matches_updater_render_byte_for_byte(
    journal: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.sh runs the script; refresh_api_unit imports the module."""
    monkeypatch.setattr(api_unit, "host_has_journal_group", lambda: journal)
    # The script path decides via the real host; force the same answer there by
    # driving main() in-process with the identical argv install.sh builds.
    argv = [
        "render",
        "--template", str(TEMPLATE),
        "--bin", PARAMS.bin,
        "--workdir", PARAMS.workdir,
        "--api-env", PARAMS.api_env,
        "--etc-dir", PARAMS.etc_dir,
        "--hf-secrets-env", PARAMS.hf_secrets_env,
    ]  # fmt: skip
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert api_unit.main(argv) == 0
    via_script = buf.getvalue()
    via_updater = desired_api_unit(
        _tmpl(),
        installed=None,
        venv_dir="/usr/lib/hal0/venv",
        current_link="/usr/lib/hal0/current",
        bin_exists=lambda _p: True,
    )
    assert via_script == via_updater


def test_script_entrypoint_runs_isolated(tmp_path: Path) -> None:
    """install.sh's exact invocation: stdlib only, ``python -I``."""
    out = subprocess.run(
        [
            sys.executable, "-I", str(SCRIPT), "render",
            "--template", str(TEMPLATE),
            "--bin", "/b/hal0", "--workdir", "/w", "--api-env", "/e/api.env",
            "--etc-dir", "/e", "--hf-secrets-env", "/s/hal0-api.env",
        ],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    assert "ExecStart=/b/hal0 serve --port ${HAL0_PORT}" in out.stdout
    assert "@HAL0_" not in out.stdout


def test_install_sh_has_no_unit_heredoc() -> None:
    sh = (REPO / "installer" / "install.sh").read_text(encoding="utf-8")
    assert 'cat > "${API_UNIT}"' not in sh
    assert "hal0-api.service.in" in sh
    assert "TimeoutStopSec" not in sh


def test_recover_params_round_trips_a_custom_layout() -> None:
    custom = ApiUnitParams(
        bin="/opt/h/venv/bin/hal0",
        workdir="/opt/h/current",
        api_env="/opt/etc/api.env",
        etc_dir="/opt/etc",
        hf_secrets_env="/opt/var/secrets/hal0-api.env",
        journal_group=False,
    )
    got = api_unit.recover_params(render_api_unit(_tmpl(), custom))
    assert got == {
        "bin": custom.bin,
        "workdir": custom.workdir,
        "api_env": custom.api_env,
        "etc_dir": custom.etc_dir,
        "hf_secrets_env": custom.hf_secrets_env,
    }


def test_desired_keeps_box_layout_and_falls_back_for_dead_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_unit, "host_has_journal_group", lambda: True)
    old = render_api_unit(
        _tmpl(),
        ApiUnitParams(
            bin="/opt/h/venv/bin/hal0",
            workdir="/opt/h/current",
            api_env="/opt/etc/api.env",
            etc_dir="/opt/etc",
            hf_secrets_env="/opt/var/secrets/hal0-api.env",
            journal_group=True,
        ),
    )
    kept = desired_api_unit(
        _tmpl(), installed=old, venv_dir="/v", current_link="/c", bin_exists=lambda _p: True
    )
    assert kept == old
    healed = desired_api_unit(
        _tmpl(), installed=old, venv_dir="/v", current_link="/c", bin_exists=lambda _p: False
    )
    assert "ExecStart=/v/bin/hal0 serve" in healed
    assert "WorkingDirectory=/opt/h/current" in healed


# ── refresh_api_unit ─────────────────────────────────────────────────────────

OLD_UNIT = """[Service]
User=hal0
WorkingDirectory=/usr/lib/hal0/current
EnvironmentFile=/etc/hal0/api.env
EnvironmentFile=-/etc/hal0/hermes-python.env
EnvironmentFile=-/var/lib/hal0/secrets/hal0-api.env
ExecStart=/usr/lib/hal0/venv/bin/hal0 serve --port ${HAL0_PORT}
"""


def _old(fb: Path) -> str:
    return OLD_UNIT.replace("/usr/lib/hal0/venv/bin/hal0", str(fb))


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "rel"
    (target / api_unit.TEMPLATE_RELPATH).parent.mkdir(parents=True)
    (target / api_unit.TEMPLATE_RELPATH).write_text(_tmpl(), encoding="utf-8")
    dst = tmp_path / "hal0-api.service"
    dropin = tmp_path / "hal0-api.service.d"
    dropin.mkdir()
    (dropin / "10-local.conf").write_text("[Service]\nEnvironment=X=1\n")
    monkeypatch.setattr(updater_mod, "API_UNIT_DST", dst)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(api_unit, "host_has_journal_group", lambda: True)
    calls: list[list[str]] = []

    def fake_run(argv, **_kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", b"")

    monkeypatch.setattr(updater_mod.subprocess, "run", fake_run)
    # Fake interpreter so the recovered ExecStart "exists".
    fake_bin = tmp_path / "venv" / "bin" / "hal0"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("")
    return target, dst, dropin, calls, fake_bin


def test_refresh_upgrades_old_unit_and_daemon_reloads_without_restart(box) -> None:
    target, dst, dropin, calls, fb = box
    dst.write_text(_old(fb))
    assert updater_mod.refresh_api_unit(target) == "installed"
    new = dst.read_text()
    assert "TimeoutStopSec=30" in new and "SupplementaryGroups=systemd-journal" in new
    assert calls == [["systemctl", "daemon-reload"]]
    assert (dropin / "10-local.conf").read_text().endswith("X=1\n")
    # second run is a no-op: no write, no reload
    calls.clear()
    assert updater_mod.refresh_api_unit(target) == "unchanged"
    assert calls == []


def test_refresh_skips_without_unit_template_or_root(box, monkeypatch) -> None:
    target, dst, _d, calls, fb = box
    assert updater_mod.refresh_api_unit(target) == "skipped"  # no installed unit
    dst.write_text(_old(fb))
    (target / api_unit.TEMPLATE_RELPATH).unlink()
    assert updater_mod.refresh_api_unit(target) == "skipped"  # a tree without the template
    assert dst.read_text() == _old(fb)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert updater_mod.refresh_api_unit(target) == "skipped"
    assert calls == []


def test_refresh_preserves_custom_layout(box) -> None:
    target, dst, _d, _c, fb = box  # noqa: RUF059
    dst.write_text(
        OLD_UNIT.replace("/etc/hal0", "/srv/etc").replace("/usr/lib/hal0/current", "/srv/cur")
    )
    updater_mod.refresh_api_unit(target)
    new = dst.read_text()
    assert "EnvironmentFile=/srv/etc/api.env" in new
    assert "WorkingDirectory=/srv/cur" in new


def test_failed_daemon_reload_restores_the_file_so_the_next_run_retries(box, monkeypatch) -> None:
    target, dst, _d, calls, fb = box
    dst.write_text(_old(fb))
    good_run = updater_mod.subprocess.run

    def bad_reload(argv, **_kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", b"")

    monkeypatch.setattr(updater_mod.subprocess, "run", bad_reload)
    assert updater_mod.refresh_api_unit(target) == "failed"
    assert dst.read_text() == _old(fb)
    assert not dst.with_name(dst.name + ".tmp").exists()

    def hung_reload(argv, **_kw):
        raise subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setattr(updater_mod.subprocess, "run", hung_reload)
    assert updater_mod.refresh_api_unit(target) == "failed"
    assert dst.read_text() == _old(fb)

    monkeypatch.setattr(updater_mod.subprocess, "run", good_run)
    assert updater_mod.refresh_api_unit(target) == "installed"
    assert "TimeoutStopSec=30" in dst.read_text()


@pytest.mark.parametrize(
    "bad",
    ["/x\nExecStartPre=/bin/evil", "/x\r", "/x\0", "/x\\"],
    ids=["lf", "cr", "nul", "backslash"],
)
def test_render_refuses_a_value_that_would_break_out_of_its_directive(bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe value"):
        render_api_unit(_tmpl(), ApiUnitParams(**{**PARAMS.__dict__, "workdir": bad}))


def test_refresh_holds_a_host_flag_unit_when_api_env_has_no_bind_host(box, tmp_path) -> None:
    """A pre-WS-C unit binds through --host. Re-rendering it would fall back to
    loopback at the next restart unless api.env carries HAL0_BIND_HOST."""
    target, dst, _d, calls, fb = box
    env = tmp_path / "api.env"
    env.write_text("HAL0_PORT=8080\n")
    old = (
        _old(fb)
        .replace("/etc/hal0/api.env", str(env))
        .replace("serve --port", "serve --host 0.0.0.0 --port")
    )
    dst.write_text(old)
    assert updater_mod.refresh_api_unit(target) == "skipped"
    assert dst.read_text() == old
    assert calls == []
    assert da.check_api_unit_current(
        installed=lambda: old, template=_tmpl, venv_dir="/v", current_link="/c"
    ).detail.count("HAL0_BIND_HOST")

    env.write_text("HAL0_BIND_HOST=0.0.0.0\nHAL0_PORT=8080\n")
    assert updater_mod.refresh_api_unit(target) == "installed"
    assert "serve --port" in dst.read_text() and "serve --host" not in dst.read_text()


# ── first update and rollback (#3160) ────────────────────────────────────────


def test_activate_release_never_refreshes_the_unit_in_process(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activate_release runs the OUTGOING release: a call here misses the upgrade
    that introduces it, and on a rollback imports a renderer the re-pip removed."""
    monkeypatch.setattr(updater_mod, "_is_editable_install", lambda: False)
    monkeypatch.setattr(updater_mod, "_reinstall_into_venv", lambda *_a, **_k: None)
    monkeypatch.setitem(sys.modules, "hal0.install.api_unit", None)  # the older package

    def forbidden(*_a, **_k):
        raise AssertionError("activate_release must not refresh the API unit")

    monkeypatch.setattr(updater_mod, "refresh_api_unit", forbidden)
    root = updater_mod._usr_lib_root()
    (root / "hal0-1.0.0").mkdir(parents=True)
    result = updater_mod.activate_release("hal0-1.0.0")
    assert result["target"].endswith("hal0-1.0.0")
    assert "api_unit" not in result


def _route_seam_to_root_side(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Make UpdateSeam behave as hal0: its sudo call runs the root entry point."""
    argvs: list[list[str]] = []

    def fake_sudo(argv, **_kw):
        argvs.append(list(argv))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = privileged.main(argv[3:])
        return subprocess.CompletedProcess(argv, rc, buf.getvalue(), "")

    monkeypatch.setattr(privileged.UpdateSeam, "routed", property(lambda _s: True))
    real_init = privileged.UpdateSeam.__init__

    def init(self, **kw):
        real_init(self, **{**kw, "run": fake_sudo})

    monkeypatch.setattr(privileged.UpdateSeam, "__init__", init)
    return argvs


def test_first_update_refreshes_through_the_incoming_seam_verb(box, monkeypatch, tmp_path) -> None:
    """The outgoing release's activate_release has no hook. It installs the
    incoming wrapper and swaps `current`; the incoming migration child then
    calls run_post_activation_migrations, the shape every older commit() uses."""
    target, dst, _d, _c, fb = box
    dst.write_text(_old(fb))
    link = tmp_path / "current"
    link.symlink_to(target)
    monkeypatch.setattr(updater_mod, "_current_symlink", lambda: link)
    monkeypatch.setattr(privileged, "_current_symlink", lambda: link)
    trusted: list[Path] = []
    monkeypatch.setattr(privileged, "assert_trusted_release_dir", trusted.append)
    argvs = _route_seam_to_root_side(monkeypatch)
    stop = RuntimeError("the fatal conversion stops the sequence here")

    def conversion(**_kw):
        raise stop

    monkeypatch.setattr("hal0.config.migrations.runner_system.migrate_runner_system", conversion)
    with pytest.raises(RuntimeError):
        updater_mod.run_post_activation_migrations(1, job_id="job-1")

    assert argvs == [["sudo", "-n", privileged.SEAM_BIN, "refresh-api-unit"]]
    assert trusted == [target.resolve()]
    new = dst.read_text()
    assert "TimeoutStopSec=30" in new and "SupplementaryGroups=systemd-journal" in new


def test_refresh_verb_refuses_arguments_and_an_untrusted_tree(box, monkeypatch, tmp_path) -> None:
    target, dst, _d, _c, fb = box
    dst.write_text(_old(fb))
    link = tmp_path / "current"
    link.symlink_to(target)
    monkeypatch.setattr(privileged, "_current_symlink", lambda: link)
    assert privileged.main(["refresh-api-unit", "/etc/passwd"]) == 1

    def untrusted(_path):
        raise updater_mod.UpdatePrivilegeError("not root-owned", details={})

    monkeypatch.setattr(privileged, "assert_trusted_release_dir", untrusted)
    assert privileged.main(["refresh-api-unit"]) == 1
    assert dst.read_text() == _old(fb)


def test_converge_contains_a_wrapper_without_the_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed wrapper refresh leaves the old one, which exits 64 on the verb."""

    def stale_wrapper(argv, **_kw):
        return subprocess.CompletedProcess(argv, 64, "", "hal0-update: bad cmd\n")

    monkeypatch.setattr(privileged.UpdateSeam, "routed", property(lambda _s: True))
    real_init = privileged.UpdateSeam.__init__
    monkeypatch.setattr(
        privileged.UpdateSeam,
        "__init__",
        lambda self, **kw: real_init(self, **{**kw, "run": stale_wrapper}),
    )
    assert updater_mod.converge_api_unit() == "failed"


def test_boot_net_does_not_touch_the_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(
        updater_mod, "converge_api_unit", lambda *, job_id=None: seen.append(job_id)
    )

    def conversion(**_kw):
        raise RuntimeError("stop")

    monkeypatch.setattr("hal0.config.migrations.runner_system.migrate_runner_system", conversion)
    assert updater_mod.check_outstanding_migrations() is None
    assert seen == []


def test_rollback_to_a_release_without_the_function_stays_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real fresh interpreter importing an older package with no hook at all."""
    pkg = tmp_path / "older" / "hal0" / "updater"
    pkg.mkdir(parents=True)
    (pkg.parent / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "updater.py").write_text("OLD = True\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "older"))
    results: list[subprocess.CompletedProcess[str]] = []
    real_run = subprocess.run

    def spy(*a, **k):
        results.append(real_run(*a, **k))
        return results[-1]

    monkeypatch.setattr(updater_mod.subprocess, "run", spy)
    asyncio.run(updater_mod._refresh_api_unit_after_swap("job-1"))
    assert [(r.returncode, r.stdout.strip()) for r in results] == [(0, "absent")]


# ── doctor row ───────────────────────────────────────────────────────────────


def _row(installed: str | None, tmpl: str | None = None):
    return da.check_api_unit_current(
        installed=lambda: installed,
        template=lambda: _tmpl() if tmpl is None else tmpl,
        venv_dir="/usr/lib/hal0/venv",
        current_link="/usr/lib/hal0/current",
    )


class _Exists:
    def __init__(self, p: str) -> None:
        self.p = p

    def exists(self) -> bool:
        return True


def test_doctor_row_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_unit, "host_has_journal_group", lambda: True)
    current = render_api_unit(_tmpl(), PARAMS)
    monkeypatch.setattr(api_unit, "Path", lambda p: _Exists(p))
    assert _row(current).status == "pass"
    stale = _row(OLD_UNIT)
    assert stale.status == "warn" and "refresh-api-unit" in stale.detail
    assert "`hal0 update`" not in stale.detail
    assert _row(None).status == "pass"
    assert (
        da.check_api_unit_current(
            installed=lambda: current, template=lambda: None, current_link="/x"
        ).status
        == "warn"
    )
