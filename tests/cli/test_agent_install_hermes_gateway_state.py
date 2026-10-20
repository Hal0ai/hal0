"""``hal0 agent install hermes`` must respect a disabled/masked gateway (#2815).

Mirrors installer/install.sh (#2807/#2811): the operator's on/off state of
hermes-gateway.service is read BEFORE the generator runs.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import hal0.cli.agent_commands as ac

UNIT = "hermes-gateway.service"
NO_START = ["--no-start-now", "--no-start-on-login"]


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
    generator_rc: int = 0,
    generator_rejects_flags: bool = False,
) -> list[list[str]]:
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
        if argv[0] == ac._HERMES_BIN:
            if generator_rejects_flags and "--no-start-now" in argv:
                return _Done(2)
            if generator_rc == 0:
                gateway_unit.write_text("[Unit]\n")
            return _Done(generator_rc)
        return _Done(0)

    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: True)
    monkeypatch.setattr(ac, "_HERMES_GATEWAY_UNIT", str(gateway_unit))
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/systemctl")
    monkeypatch.setattr(ac, "_wait_active_unit", lambda _u, timeout=15.0: True)
    monkeypatch.setattr("hal0.agents.hermes_provision._detect_foreign_gateways", lambda **_k: [])
    monkeypatch.setattr(
        "hal0.agents.hermes_provision.write_gateway_secrets_dropin",
        lambda *a, **k: type("_R", (), {"outcome": "skipped", "reason": ""})(),
    )
    return calls


def _generator(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c[0] == ac._HERMES_BIN]


def _enables(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c[:2] == ["systemctl", "enable"]]


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    return " ".join(capsys.readouterr().out.split())


@pytest.mark.parametrize("state", ["not-found", "enabled"])
def test_fresh_or_enabled_installs_and_enables(monkeypatch, tmp_path, state) -> None:
    calls = _setup(monkeypatch, tmp_path, state=state)
    ac._install_hermes_gateway()
    (gen,) = _generator(calls)
    assert not any(f in gen for f in NO_START)
    assert ["systemctl", "enable", "--now", UNIT] in calls


def test_disabled_refreshes_unit_but_does_not_enable(monkeypatch, tmp_path, capsys) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled")
    ac._install_hermes_gateway()
    (gen,) = _generator(calls)
    assert gen[-2:] == NO_START
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
    assert not any(c[0] == "systemctl" and c[1] in ("stop", "disable", "restart") for c in calls)
    assert "left running" in _out(capsys)


@pytest.mark.parametrize(
    ("state", "hint"),
    [
        ("masked", "systemctl unmask hermes-gateway.service && systemctl enable --now"),
        (
            "masked-runtime",
            "systemctl unmask --runtime hermes-gateway.service && systemctl enable --now",
        ),
    ],
)
def test_masked_never_runs_generator(monkeypatch, tmp_path, capsys, state, hint) -> None:
    calls = _setup(monkeypatch, tmp_path, state=state)
    ac._install_hermes_gateway()
    assert _generator(calls) == []
    assert _enables(calls) == []
    out = _out(capsys)
    assert f"is {state}; left as is" in out
    assert hint in out


def test_flag_rejected_keeps_unit_off_and_does_not_raise(monkeypatch, tmp_path, capsys) -> None:
    calls = _setup(monkeypatch, tmp_path, state="disabled", generator_rejects_flags=True)
    ac._install_hermes_gateway()  # must not raise
    # One flagged attempt only: no retry without the flags.
    (gen,) = _generator(calls)
    assert gen[-2:] == NO_START
    assert _enables(calls) == []
    assert "rejected" in _out(capsys)
