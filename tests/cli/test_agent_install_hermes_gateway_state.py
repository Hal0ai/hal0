"""``hal0 agent install hermes`` must respect a disabled/masked gateway (#2815).

Mirrors installer/install.sh (#2807/#2811): the operator's on/off state of
hermes-gateway.service is read BEFORE the unit is laid down. Since #2943 the
unit comes from hal0's template (``hermes_gateway_regen.install_gateway_unit``),
never from upstream's generator run as root, and the template never enables
or starts it; the on/off decision below is hal0's alone.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import hal0.cli.agent_commands as ac

UNIT = "hermes-gateway.service"
INSTALL = ["<template-install>"]


class _Done:
    def __init__(self, rc: int = 0, out: str = "") -> None:
        self.returncode = rc
        self.stdout = out


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    *,
    state: str,
    active: bool = False,
    install_fails: bool = False,
    which: str | None = "/usr/bin/systemctl",
    unit_text: str = "[Unit]\n",
) -> list[list[str]]:
    from hal0.agents import hermes_gateway_regen as regen

    gateway_unit = tmp_path / UNIT
    calls: list[list[str]] = []

    def _fake_run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        argv = list(argv)
        calls.append(argv)
        if argv[:2] == ["systemctl", "is-enabled"]:
            return _Done(
                0 if state == "enabled" else 1, "" if state == "not-found" else state + "\n"
            )
        if argv[:2] == ["systemctl", "is-active"]:
            return _Done(0 if active else 3)
        return _Done(0)

    def _fake_install(**_k):  # type: ignore[no-untyped-def]
        calls.append(INSTALL)
        if install_fails:
            raise regen.GatewayRegenError("write boom")
        gateway_unit.write_text(unit_text)
        return regen.RegenResult("installed", f"{UNIT}: wrote {UNIT}")

    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr(regen, "install_gateway_unit", _fake_install)
    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: True)
    monkeypatch.setattr(ac, "_HERMES_GATEWAY_UNIT", str(gateway_unit))
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda _n: which)
    monkeypatch.setattr(ac, "_wait_active_unit", lambda _u, timeout=15.0: True)
    monkeypatch.setattr("hal0.agents.hermes_provision._detect_foreign_gateways", lambda **_k: [])
    monkeypatch.setattr(
        "hal0.agents.hermes_provision.write_gateway_secrets_dropin",
        lambda *a, **k: type("_R", (), {"outcome": "skipped", "reason": ""})(),
    )
    return calls


def _installs(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c == INSTALL]


def _no_generator(calls: list[list[str]]) -> bool:
    """Upstream's generator never runs (#2943)."""
    return not any(c and c[0] == ac._HERMES_BIN for c in calls)


def _enables(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c[:2] == ["systemctl", "enable"]]


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    return " ".join(capsys.readouterr().out.split())


@pytest.mark.parametrize("state", ["not-found", "enabled"])
def test_fresh_or_enabled_installs_and_enables(monkeypatch, tmp_path, state) -> None:
    calls = _setup(monkeypatch, tmp_path, state=state)
    ac._install_hermes_gateway()
    assert _installs(calls) == [INSTALL] and _no_generator(calls)
    assert ["systemctl", "enable", "--now", UNIT] in calls


def test_disabled_refreshes_unit_but_does_not_enable(monkeypatch, tmp_path, capsys) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled")
    ac._install_hermes_gateway()
    assert _installs(calls) == [INSTALL] and _no_generator(calls)
    assert _enables(calls) == []
    assert not any(c[:2] == ["systemctl", "stop"] for c in calls)
    assert (
        "hermes-gateway.service is disabled; left as is "
        "(run 'systemctl enable --now hermes-gateway.service' to turn it on)"
    ) in _out(capsys)


def test_disabled_but_active_is_left_running(monkeypatch, tmp_path, capsys) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled", active=True)
    ac._install_hermes_gateway()
    assert _enables(calls) == []
    # Never stopped: only a plain `disable` backstop, no --now.
    assert not any(c[0] == "systemctl" and c[1] in ("stop", "restart") for c in calls)
    assert ["systemctl", "disable", "--now", UNIT] not in calls
    assert "left running" in _out(capsys)


@pytest.mark.parametrize(
    ("state", "hint"),
    [
        ("masked", "systemctl unmask hermes-gateway.service && systemctl enable --now"),
    ],
)
def test_masked_never_writes_the_unit(monkeypatch, tmp_path, capsys, state, hint) -> None:
    calls = _setup(monkeypatch, tmp_path, state=state)
    ac._install_hermes_gateway()
    assert _installs(calls) == []
    assert _enables(calls) == []
    out = _out(capsys)
    assert f"is {state}; left as is" in out
    assert hint in out


def test_masked_runtime_never_writes_the_unit(monkeypatch, tmp_path, capsys) -> None:
    """systemd reports masked-runtime only while there is no /etc file; writing
    one would shadow the /run mask and turn the gateway back on (#3024)."""
    calls = _setup(monkeypatch, tmp_path, state="masked-runtime")
    ac._install_hermes_gateway()
    assert _installs(calls) == [] and _no_generator(calls)
    assert _enables(calls) == []
    assert not (tmp_path / UNIT).exists()
    out = _out(capsys)
    assert "is masked-runtime; left as is" in out
    assert "systemctl unmask --runtime hermes-gateway.service && systemctl enable --now" in out


def test_a_failed_template_install_keeps_a_disabled_gateway_off(
    monkeypatch, tmp_path, capsys
) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled", install_fails=True)
    ac._install_hermes_gateway()  # must not raise
    assert _enables(calls) == []
    assert "write boom" in _out(capsys)


def test_a_failed_template_install_on_a_fresh_box_does_not_enable(
    monkeypatch, tmp_path, capsys
) -> None:
    calls = _setup(monkeypatch, tmp_path, state="not-found", install_fails=True)
    ac._install_hermes_gateway()  # must not raise
    assert _enables(calls) == []
    assert "not installed" in _out(capsys)


def test_not_root_never_writes_the_unit(monkeypatch, tmp_path, capsys) -> None:
    calls = _setup(monkeypatch, tmp_path, state="not-found")
    monkeypatch.setattr("os.geteuid", lambda: 999)
    ac._install_hermes_gateway()
    assert _installs(calls) == [] and _enables(calls) == []
    assert "needs root" in _out(capsys)


def test_disabled_backstop_disables_now_when_inactive(monkeypatch, tmp_path) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled", active=False)
    ac._install_hermes_gateway()
    assert ["systemctl", "disable", "--now", UNIT] in calls


def test_disabled_backstop_keeps_running_unit_up(monkeypatch, tmp_path) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled", active=True)
    ac._install_hermes_gateway()
    assert ["systemctl", "disable", UNIT] in calls
    assert ["systemctl", "disable", "--now", UNIT] not in calls


def test_unknown_state_behaves_like_today(monkeypatch, tmp_path) -> None:
    calls = _setup(monkeypatch, tmp_path, state="static")
    ac._install_hermes_gateway()
    assert _installs(calls) == [INSTALL] and _no_generator(calls)
    assert ["systemctl", "enable", "--now", UNIT] in calls


def test_no_systemctl_installs_unit_only(monkeypatch, tmp_path) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled", which=None)
    ac._install_hermes_gateway()
    assert _installs(calls) == [INSTALL]
    assert not any(c[0] == "systemctl" for c in calls)


def test_systemctl_oserror_is_treated_as_not_found(monkeypatch, tmp_path) -> None:
    calls = _setup(monkeypatch, tmp_path, state="enabled")
    inner = subprocess.run

    def _boom(argv, *a, **k):  # type: ignore[no-untyped-def]
        if list(argv)[:2] in (["systemctl", "is-enabled"], ["systemctl", "is-active"]):
            raise OSError("no systemctl")
        return inner(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _boom)
    ac._install_hermes_gateway()
    assert ["systemctl", "enable", "--now", UNIT] in calls


# ── ADR-0032 decision 12: the normaliser still runs straight after the install ──

RELEASE = "/var/lib/hal0/hermes/releases/3ef6bbd20126-20261021T000000Z"


def test_generated_unit_is_normalised_before_enable(monkeypatch, tmp_path) -> None:
    unit_text = f"ExecStart={RELEASE}/venv/bin/python -m hermes_cli.main gateway run\n"
    calls = _setup(monkeypatch, tmp_path, state="not-found", unit_text=unit_text)
    ac._install_hermes_gateway()
    assert (tmp_path / UNIT).read_text() == (
        "ExecStart=/var/lib/hal0/hermes/current/venv/bin/python -m hermes_cli.main gateway run\n"
    )
    try_restart = calls.index(["systemctl", "try-restart", UNIT])
    enable = calls.index(["systemctl", "enable", "--now", UNIT])
    assert try_restart < enable


def test_a_unit_still_naming_a_release_is_left_disabled(monkeypatch, tmp_path, capsys) -> None:
    # The releases/ directory with no id: the prefix rewrite cannot match it.
    unit_text = "ExecStart=/var/lib/hal0/hermes/releases/\n"
    calls = _setup(monkeypatch, tmp_path, state="not-found", unit_text=unit_text)
    ac._install_hermes_gateway()
    assert not _enables(calls)
    assert ["systemctl", "disable", "--now", UNIT] in calls
    assert "could not be normalised" in _out(capsys)


def test_disabled_gateway_with_a_bad_unit_is_only_warned_about(
    monkeypatch, tmp_path, capsys
) -> None:
    calls = _setup(
        monkeypatch,
        tmp_path,
        state="disabled",
        active=True,
        unit_text="X=/var/lib/hal0/hermes/releases/\n",
    )
    ac._install_hermes_gateway()
    assert ["systemctl", "disable", "--now", UNIT] not in calls
    assert "could not be normalised" in _out(capsys)


def test_a_disabled_suffixed_unit_is_read_as_the_operators_off_switch(
    monkeypatch, tmp_path, capsys
) -> None:
    """#2943 review: on a box a 1.6.0 nightly installed there is no
    hermes-gateway.service, only upstream's suffixed unit. Its disabled state
    is the operator's choice; the reinstall must not turn the gateway on."""
    from pathlib import Path as _P

    from hal0.agents.hermes_gateway_regen import HASHED_UNIT

    calls = _setup(monkeypatch, tmp_path, state="disabled")
    fixture = (
        _P(__file__).resolve().parents[1]
        / "fixtures"
        / "hermes"
        / "gateway-unit-hashed-v0.22.4.service"
    )
    (tmp_path / HASHED_UNIT).write_text(fixture.read_text())
    ac._install_hermes_gateway()
    probed = [c for c in calls if c[:2] == ["systemctl", "is-enabled"]]
    # The canonical unit is probed for a runtime mask first (#3024); the state
    # read is the suffixed unit's.
    assert probed and probed[-1][-1] == HASHED_UNIT
    assert _enables(calls) == []


def test_a_profile_unit_with_the_same_name_is_not_read_as_the_default_gateway(
    monkeypatch, tmp_path
) -> None:
    """A standalone profile whose id happens to be the hash (#2946 review)."""
    from hal0.agents.hermes_gateway_regen import HASHED_UNIT

    calls = _setup(monkeypatch, tmp_path, state="disabled")
    (tmp_path / HASHED_UNIT).write_text(
        '[Service]\nUser=hal0\nEnvironment="HERMES_HOME=/var/lib/hal0/.hermes/profiles/1975ce4b"\n'
    )
    ac._install_hermes_gateway()
    probed = [c for c in calls if c[:2] == ["systemctl", "is-enabled"]]
    assert probed and probed[0][-1] == UNIT


def test_runtime_only_enablement_is_kept_on_reinstall(monkeypatch, tmp_path) -> None:
    calls = _setup(monkeypatch, tmp_path, state="enabled-runtime")
    ac._install_hermes_gateway()
    assert ["systemctl", "enable", "--runtime", "--now", UNIT] in calls
    assert ["systemctl", "enable", "--now", UNIT] not in calls


def test_a_failed_move_never_enables_a_second_gateway(monkeypatch, tmp_path, capsys) -> None:
    """The rollback may have put the suffixed gateway back; enabling the canonical
    unit after that would start a second poller (#2946 review)."""
    from hal0.agents.hermes_gateway_regen import HASHED_UNIT

    calls = _setup(monkeypatch, tmp_path, state="not-found", install_fails=True)
    (tmp_path / UNIT).write_text("[Unit]\n")  # a canonical file is on disk
    (tmp_path / HASHED_UNIT).write_text(
        '[Service]\nUser=hal0\nEnvironment="HERMES_HOME=/var/lib/hal0/.hermes"\n'
    )
    ac._install_hermes_gateway()
    assert _enables(calls) == []
    out = _out(capsys)
    assert "suffixed gateway unit is still in place" in out
    assert "continuing" not in out


def test_a_failure_without_a_suffixed_unit_still_enables_the_gateway(
    monkeypatch, tmp_path, capsys
) -> None:
    """A transient failure on a box with no suffixed unit (a plain daemon-reload,
    a stale-unit restart) keeps the old behaviour: the operator's on decision
    still enables the canonical unit."""
    calls = _setup(monkeypatch, tmp_path, state="not-found", install_fails=True)
    (tmp_path / UNIT).write_text("[Unit]\n")
    ac._install_hermes_gateway()
    assert ["systemctl", "enable", "--now", UNIT] in _enables(calls)
    assert "continuing" in _out(capsys)


# ── #2950: mixed and interrupted states ──────────────────────────────────────

_HAL0_SUFFIXED = '[Service]\nUser=hal0\nEnvironment="HERMES_HOME=/var/lib/hal0/.hermes"\n'


def _per_unit(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], states: dict) -> None:
    """is-enabled/is-active answer per unit; everything else succeeds."""

    def _run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        argv = list(argv)
        calls.append(argv)
        state, active = states.get(argv[-1], ("not-found", False))
        if argv[:2] == ["systemctl", "is-enabled"]:
            return _Done(0 if state == "enabled" else 1, state + "\n")
        if argv[:2] == ["systemctl", "is-active"]:
            return _Done(0 if active else 3)
        return _Done(0)

    monkeypatch.setattr(subprocess, "run", _run)


def test_an_interrupted_move_is_finished_not_turned_off(monkeypatch, tmp_path) -> None:
    """#2950 item 1: the canonical unit exists, disabled and stopped, beside the
    enabled suffixed unit. The capture must read the suffixed unit (the
    operator's choice), so the disabled branch never stops the gateway."""
    from hal0.agents.hermes_gateway_regen import HASHED_UNIT

    calls = _setup(monkeypatch, tmp_path, state="disabled")
    (tmp_path / UNIT).write_text("[Unit]\n")
    (tmp_path / HASHED_UNIT).write_text(_HAL0_SUFFIXED)
    _per_unit(monkeypatch, calls, {HASHED_UNIT: ("enabled", True), UNIT: ("disabled", False)})
    ac._install_hermes_gateway()
    assert ["systemctl", "disable", "--now", UNIT] not in calls
    assert ["systemctl", "enable", "--now", UNIT] in calls


def test_a_masked_canonical_unit_still_moves_a_live_suffixed_gateway_off(
    monkeypatch, tmp_path, capsys
) -> None:
    """#2950 item 2: the early masked return used to skip the move, so a
    running suffixed gateway stayed connected despite the mask."""
    from hal0.agents import hermes_gateway_regen as regen

    calls = _setup(monkeypatch, tmp_path, state="masked")
    (tmp_path / UNIT).symlink_to("/dev/null")
    (tmp_path / regen.HASHED_UNIT).write_text(_HAL0_SUFFIXED)
    moved: list[Any] = []

    def _fake_migrate(**kw):  # type: ignore[no-untyped-def]
        moved.append(kw["unit_path"])
        return regen.RegenResult("migrated", f"{UNIT}: {UNIT} is masked; left off")

    monkeypatch.setattr(regen, "migrate_hashed_units", _fake_migrate)
    ac._install_hermes_gateway()
    assert moved == [tmp_path / UNIT]
    assert _installs(calls) == [] and _enables(calls) == []
    assert "is masked; left as is" in _out(capsys)


def test_a_masked_unit_without_a_suffixed_one_runs_no_move(monkeypatch, tmp_path) -> None:
    from hal0.agents import hermes_gateway_regen as regen

    _setup(monkeypatch, tmp_path, state="masked")

    def _boom(**_kw):  # type: ignore[no-untyped-def]
        raise AssertionError("no suffixed unit: nothing to move")

    monkeypatch.setattr(regen, "migrate_hashed_units", _boom)
    ac._install_hermes_gateway()


def test_a_finished_move_with_a_leftover_file_does_not_say_not_enabled(
    monkeypatch, tmp_path, capsys
) -> None:
    """#2950 review residual: only the final unlink failed. The canonical unit
    is enabled and running; the message must not say it is not enabled."""
    from hal0.agents import hermes_gateway_regen as regen

    calls = _setup(monkeypatch, tmp_path, state="not-found")
    (tmp_path / regen.HASHED_UNIT).write_text(_HAL0_SUFFIXED)

    def _cleanup_fails(**_k):  # type: ignore[no-untyped-def]
        (tmp_path / UNIT).write_text("[Unit]\n")
        raise regen.GatewayCleanupError("remove suffixed: busy failed: left behind")

    monkeypatch.setattr(regen, "install_gateway_unit", _cleanup_fails)
    ac._install_hermes_gateway()
    out = _out(capsys)
    assert "is not enabled" not in out
    assert f"moved to {UNIT}" in out and "could not be removed" in out
    assert not any(c[:2] == ["systemctl", "disable"] for c in calls)


def test_a_cleanup_failure_still_reports_a_failed_linger(monkeypatch, tmp_path, capsys) -> None:
    """(c) install_gateway_unit ran its linger tail before raising; the CLI
    surfaces a linger failure from it like on success."""
    from hal0.agents import hermes_gateway_regen as regen

    _setup(monkeypatch, tmp_path, state="not-found")
    (tmp_path / regen.HASHED_UNIT).write_text(_HAL0_SUFFIXED)
    done = regen.RegenResult(
        "installed", f"{UNIT}: enabled, started, loginctl enable-linger hal0 failed: boom"
    )

    def _cleanup_fails(**_k):  # type: ignore[no-untyped-def]
        (tmp_path / UNIT).write_text("[Unit]\n")
        raise regen.GatewayCleanupError("remove suffixed: busy failed: left behind", done)

    monkeypatch.setattr(regen, "install_gateway_unit", _cleanup_fails)
    ac._install_hermes_gateway()
    out = _out(capsys)
    assert "could not be removed" in out
    assert "sudo loginctl enable-linger hal0" in out


def test_a_runtime_mask_with_no_canonical_file_keeps_a_suffixed_gateway_off(
    monkeypatch, tmp_path, capsys
) -> None:
    """#3024 (CT150 smoke): no /etc hermes-gateway.service, a /run mask
    (is-enabled = masked-runtime) and an enabled, running suffixed unit. The
    command must read the mask, move the suffixed gateway off and write nothing."""
    from hal0.agents import hermes_gateway_regen as regen

    calls = _setup(monkeypatch, tmp_path, state="disabled")
    (tmp_path / regen.HASHED_UNIT).write_text(_HAL0_SUFFIXED)
    _per_unit(
        monkeypatch,
        calls,
        {regen.HASHED_UNIT: ("enabled", True), UNIT: ("masked-runtime", False)},
    )
    moved: list[Any] = []

    def _fake_migrate(**kw):  # type: ignore[no-untyped-def]
        moved.append(kw["unit_path"])
        return regen.RegenResult("migrated", f"{UNIT}: {UNIT} is masked; left off")

    monkeypatch.setattr(regen, "migrate_hashed_units", _fake_migrate)
    ac._install_hermes_gateway()
    assert moved == [tmp_path / UNIT]
    assert _installs(calls) == [] and _enables(calls) == []
    assert not (tmp_path / UNIT).exists()
    assert "is masked-runtime; left as is" in _out(capsys)
