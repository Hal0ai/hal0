"""hal0-api.service single-source template (#3160)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    import io
    from contextlib import redirect_stdout

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
    assert updater_mod.refresh_api_unit(target) == "skipped"  # old release: rollback path
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
    assert stale.status == "warn" and "hal0 update" in stale.detail
    assert _row(None).status == "pass"
    assert (
        da.check_api_unit_current(
            installed=lambda: current, template=lambda: None, current_link="/x"
        ).status
        == "warn"
    )
