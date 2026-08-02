"""Unit tests for :mod:`hal0.system.seam` — the hal0-systemctl privileged seam
adopted for P3-perms.

Covers:
  * Direct passthrough (today's exact behaviour) when NOT running as the hal0
    service user — the dev/CI/test-suite default, and every pre-flip install.
  * Seam routing (``sudo -n hal0-systemctl ...``) when running as hal0,
    for write_unit / remove_unit / systemctl verbs / restart_self.
  * Read-only queries (``is-active``) and non-slot units never route through
    the seam even when running as hal0.
  * Bad slot-unit names raise instead of silently mis-routing.

``run`` and ``is_hal0_user`` are injected seams so this never touches sudo, a
real ``hal0`` user, or a privileged filesystem.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hal0.system import seam as seam_mod
from hal0.system.seam import SEAM_BIN, SystemCtlSeam


def _completed(returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = ""
    m.stderr = ""
    return m


def _recorder():
    calls: list[list[str]] = []

    def _run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        return _completed()

    return calls, _run


# ── write_unit ─────────────────────────────────────────────────────────────


def test_write_unit_direct_when_not_hal0_user(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)
    unit = tmp_path / "hal0-slot@chat.service"

    seam.write_unit(unit, "[Unit]\n")

    assert unit.read_text() == "[Unit]\n"
    assert calls == []  # never shelled out


def test_write_unit_routes_through_seam_when_hal0_user(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)
    unit = tmp_path / "hal0-slot@chat.service"

    seam.write_unit(unit, "[Unit]\n")

    assert not unit.exists()  # never written directly
    assert calls == [["sudo", "-n", SEAM_BIN, "write-unit", "chat"]]


def test_write_unit_rejects_non_slot_unit_name(tmp_path: Path) -> None:
    _, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)
    with pytest.raises(ValueError, match="not a hal0-slot@ unit"):
        seam.write_unit(tmp_path / "hal0-api.service", "[Unit]\n")


# ── write_quadlet / remove_quadlet (P3-quadlet) ──────────────────────────────


def test_write_quadlet_direct_when_not_hal0_user(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)
    quadlet = tmp_path / "sub" / "hal0-slot@chat.container"

    seam.write_quadlet(quadlet, "[Container]\n")

    assert quadlet.read_text() == "[Container]\n"  # parent dir auto-created
    assert calls == []


def test_write_quadlet_routes_through_seam_when_hal0_user(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)
    quadlet = tmp_path / "hal0-slot@chat.container"

    seam.write_quadlet(quadlet, "[Container]\n")

    assert not quadlet.exists()  # never written directly
    assert calls == [["sudo", "-n", SEAM_BIN, "write-quadlet", "chat"]]


def test_write_quadlet_rejects_non_slot_name(tmp_path: Path) -> None:
    _, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)
    with pytest.raises(ValueError, match="not a hal0-slot@ quadlet file"):
        seam.write_quadlet(tmp_path / "hal0-slot@chat.service", "[Container]\n")


def test_remove_quadlet_direct_when_not_hal0_user(tmp_path: Path) -> None:
    quadlet = tmp_path / "hal0-slot@chat.container"
    quadlet.write_text("[Container]\n")
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    seam.remove_quadlet(quadlet)  # idempotent

    assert not quadlet.exists()
    assert calls == []


def test_remove_quadlet_routes_through_seam_when_hal0_user(tmp_path: Path) -> None:
    quadlet = tmp_path / "hal0-slot@chat.container"
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.remove_quadlet(quadlet)

    assert calls == [["sudo", "-n", SEAM_BIN, "remove-quadlet", "chat"]]


# ── remove_unit ────────────────────────────────────────────────────────────


def test_remove_unit_direct_when_not_hal0_user(tmp_path: Path) -> None:
    unit = tmp_path / "hal0-slot@chat.service"
    unit.write_text("[Unit]\n")
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    seam.remove_unit(unit)

    assert not unit.exists()
    assert calls == []


def test_remove_unit_direct_noop_when_absent(tmp_path: Path) -> None:
    unit = tmp_path / "hal0-slot@chat.service"
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    seam.remove_unit(unit)  # must not raise

    assert calls == []


def test_remove_unit_routes_through_seam_when_hal0_user(tmp_path: Path) -> None:
    unit = tmp_path / "hal0-slot@chat.service"
    unit.write_text("[Unit]\n")  # present locally, but the seam path never touches it
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.remove_unit(unit)

    assert unit.exists()  # untouched by this process — the seam owns the real path
    assert calls == [["sudo", "-n", SEAM_BIN, "remove-unit", "chat"]]


# ── systemctl() verb routing ───────────────────────────────────────────────


def test_systemctl_direct_when_not_hal0_user() -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    seam.systemctl("systemctl", "daemon-reload")
    seam.systemctl("systemctl", "restart", "hal0-slot@chat.service")

    assert calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "hal0-slot@chat.service"],
    ]


def test_systemctl_daemon_reload_routes_through_seam() -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", "daemon-reload")

    assert calls == [["sudo", "-n", SEAM_BIN, "daemon-reload"]]


@pytest.mark.parametrize("verb", ["start", "stop", "restart", "enable", "disable", "reset-failed"])
def test_systemctl_slot_unit_verbs_route_through_seam(verb: str) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", verb, "hal0-slot@chat.service")

    assert calls == [["sudo", "-n", SEAM_BIN, verb, "chat"]]


@pytest.mark.parametrize("is_hal0", [False, True])
def test_systemctl_forwards_the_timeout_on_both_routes(is_hal0: bool) -> None:
    """#1224: the caller's bound must reach the child on the seamed route too.

    Bounding only the direct path would leave a real (hal0-service-user)
    install able to wedge on ``systemctl stop`` of an already-failed unit —
    precisely the deployment where the bug was observed.
    """
    kwargs: list[object] = []

    def _run(argv: object, **kw: object) -> MagicMock:
        kwargs.append(kw.get("timeout"))
        m = MagicMock()
        m.returncode = 0
        return m

    seam = SystemCtlSeam(run=_run, is_hal0_user=lambda: is_hal0)

    seam.systemctl("systemctl", "stop", "hal0-slot@chat.service", check=False, timeout=7.5)

    assert kwargs == [7.5]


def test_systemctl_timeout_defaults_to_unbounded() -> None:
    """Unchanged default: callers that pass no bound still wait forever."""
    kwargs: list[object] = []

    def _run(argv: object, **kw: object) -> MagicMock:
        kwargs.append(kw.get("timeout"))
        m = MagicMock()
        m.returncode = 0
        return m

    SystemCtlSeam(run=_run, is_hal0_user=lambda: False).systemctl("systemctl", "daemon-reload")

    assert kwargs == [None]


def test_systemctl_read_only_query_never_routed_even_as_hal0_user() -> None:
    """is-active is a read-only D-Bus query — never needs root, never seamed."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", "is-active", "hal0-slot@chat.service")

    assert calls == [["systemctl", "is-active", "hal0-slot@chat.service"]]


def test_systemctl_foreign_unit_passes_through_even_as_hal0_user() -> None:
    """A unit hal0 doesn't manage at all isn't covered by any seam family —
    passes straight through unprivileged. (hindsight-api used to be the
    example here; #1590 made it seam-routed, which was exactly the point.)"""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", "restart", "nginx.service")

    assert calls == [["systemctl", "restart", "nginx.service"]]


@pytest.mark.parametrize(
    ("unit", "verb", "seam_argv_tail"),
    [
        ("hal0-agent@hermes.service", "start", ["start-agent", "hermes"]),
        ("hal0-agent@hermes.service", "restart", ["restart-agent", "hermes"]),
        ("hal0-openwebui.service", "restart", ["svc-restart", "openwebui"]),
        ("hal0-openwebui.service", "start", ["svc-start", "openwebui"]),
        ("hindsight-api.service", "stop", ["svc-stop", "hindsight"]),
        ("hindsight-api.service", "enable", ["svc-enable", "hindsight"]),
    ],
)
def test_systemctl_routes_agent_and_companion_units_through_seam(
    unit: str, verb: str, seam_argv_tail: list[str]
) -> None:
    """#1590: as the hal0 user, agent + companion units route through the
    wrapper instead of falling through to bare systemctl (= polkit)."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", verb, unit)

    assert calls == [["sudo", "-n", seam._seam_bin, *seam_argv_tail]]


def test_systemctl_companion_units_pass_through_off_the_service_account() -> None:
    """Dev/CI/tests (not the hal0 user) keep the direct call — no sudo grant
    exists there, and root needs none."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    seam.systemctl("systemctl", "restart", "hal0-openwebui.service")

    assert calls == [["systemctl", "restart", "hal0-openwebui.service"]]


def test_systemctl_non_systemctl_argv_always_passes_through() -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("podman", "ps")

    assert calls == [["podman", "ps"]]


# ── restart_self ───────────────────────────────────────────────────────────


def test_restart_self_direct_when_not_hal0_user() -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    seam.restart_self()

    assert calls == [["systemctl", "restart", "hal0-api.service"]]


def test_restart_self_routes_through_seam_when_hal0_user() -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.restart_self()

    assert calls == [["sudo", "-n", SEAM_BIN, "restart-self"]]


# ── is_hal0_service_user() — the real default gate ─────────────────────────


def test_is_hal0_service_user_false_when_hal0_user_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No 'hal0' system user on this box (dev/CI/unit tests) -> never seam,
    regardless of this process's own euid."""

    def _no_such_user(_name: str) -> None:
        raise KeyError("hal0")

    monkeypatch.setattr(seam_mod.pwd, "getpwnam", _no_such_user)
    assert seam_mod.is_hal0_service_user() is False


def test_is_hal0_service_user_true_when_euid_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Ent:
        pw_uid = 4242

    monkeypatch.setattr(seam_mod.pwd, "getpwnam", lambda _name: _Ent())
    monkeypatch.setattr(seam_mod.os, "geteuid", lambda: 4242)
    assert seam_mod.is_hal0_service_user() is True


def test_is_hal0_service_user_false_when_euid_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Ent:
        pw_uid = 4242

    monkeypatch.setattr(seam_mod.pwd, "getpwnam", lambda _name: _Ent())
    monkeypatch.setattr(seam_mod.os, "geteuid", lambda: 1000)
    assert seam_mod.is_hal0_service_user() is False
