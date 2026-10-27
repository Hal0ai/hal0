"""Unit tests for :mod:`hal0.system.seam` — the hal0-systemctl privileged seam
adopted for P3-perms.

Covers:
  * Direct passthrough (today's exact behaviour) when NOT running as the hal0
    service user — the dev/CI/test-suite default, and every pre-flip install.
  * Seam routing (``sudo -n hal0-systemctl ...``) when running as hal0,
    for the systemctl verbs / restart_self.
  * Read-only queries (``is-active``) and non-slot units never route through
    the seam even when running as hal0.
  * Bad slot-unit names raise instead of silently mis-routing.

``run`` and ``is_hal0_user`` are injected seams so this never touches sudo, a
real ``hal0`` user, or a privileged filesystem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hal0.system import seam as seam_mod
from hal0.system.seam import SEAM_BIN, SeamTimeout, SystemCtlSeam, bounded_call


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


# ── write_unit is gone (#1740) ─────────────────────────────────────────────


def test_write_unit_is_removed() -> None:
    """``write_unit``/``write-unit`` shipped an unvalidated body to a root-owned
    ``/etc/systemd/system/*.service``. P3-quadlet left it without a producer —
    ``_render_quadlet_from_plan`` emits a ``.container`` — so there was no
    render contract to allow-list against and the verb was deleted outright."""
    assert not hasattr(SystemCtlSeam, "write_unit")
    assert not hasattr(SystemCtlSeam, "remove_unit")


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


# ── write_hindsight_dropin (#1641) ─────────────────────────────────────────


def test_write_hindsight_dropin_direct_when_not_hal0_user(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)
    dropin = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"

    seam.write_hindsight_dropin("[Service]\n", path=dropin)

    assert dropin.read_text() == "[Service]\n"  # parent dir auto-created
    assert calls == []
    # Atomic write: the temp file is renamed, never left behind.
    assert sorted(p.name for p in dropin.parent.iterdir()) == ["extraction-model.conf"]


def test_write_hindsight_dropin_routes_through_seam_when_hal0_user(tmp_path: Path) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)
    dropin = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"

    seam.write_hindsight_dropin("[Service]\n", path=dropin)

    assert not dropin.exists()  # never written directly — root owns the path
    assert calls == [["sudo", "-n", SEAM_BIN, "write-hindsight-dropin"]]


def test_write_hindsight_dropin_pipes_the_body_on_stdin() -> None:
    """No id to validate: the body is the ENTIRE payload, so it must ride
    stdin rather than argv (the write-gateway-dropin contract)."""
    seen: dict[str, object] = {}

    def _run(argv: object, **kwargs: object) -> MagicMock:
        seen["argv"] = list(argv)  # type: ignore[arg-type]
        seen.update(kwargs)
        return _completed()

    SystemCtlSeam(run=_run, is_hal0_user=lambda: True).write_hindsight_dropin("BODY")

    assert seen["input"] == "BODY"
    assert seen["check"] is True
    # No path token anywhere in argv — the root side owns the literal.
    assert not any("/" in a for a in seen["argv"][3:])  # type: ignore[index]


def test_hindsight_dropin_verb_exists_in_the_wrapper() -> None:
    """The sudoers grant is pinned to the BINARY, so the wrapper's case
    statement is the allow-list. A Python-side verb the wrapper doesn't have
    fails at runtime with `bad cmd` — pin the two spellings together."""
    wrapper = Path(__file__).resolve().parents[2] / "installer" / "wrappers" / "hal0-systemctl"
    text = wrapper.read_text()
    assert "write-hindsight-dropin)" in text
    assert str(seam_mod.HINDSIGHT_DROPIN_PATH.parent) in text
    assert seam_mod.HINDSIGHT_DROPIN_PATH.name in text


def test_every_companion_service_key_is_accepted_by_the_wrapper() -> None:
    """``COMPANION_SERVICE_UNITS`` is the client-side spelling of the wrapper's closed
    ``svc-*`` map. A unit the Python side maps but the wrapper does not fails at runtime
    with ``bad service key``: pin each (key, unit) pair to the wrapper's case arm."""
    import re

    wrapper = Path(__file__).resolve().parents[2] / "installer" / "wrappers" / "hal0-systemctl"
    text = wrapper.read_text()
    assert seam_mod.COMPANION_SERVICE_UNITS
    for unit, key in seam_mod.COMPANION_SERVICE_UNITS.items():
        arm = rf'^\s*{re.escape(key)}\)\s+unit="{re.escape(unit)}"'
        assert re.search(arm, text, re.MULTILINE), f"{key} -> {unit} is not a svc-* arm"


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
        ("hermes-gateway.service", "restart", ["svc-restart", "hermes-gateway"]),
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


# ── bounded_call / SeamTimeout (#1869/#1870) ─────────────────────────────────


def _timing_out_run(argv: list[str], **kw: object) -> MagicMock:
    raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout") or 0)


def test_bounded_call_converts_timeout_expired_to_seam_timeout() -> None:
    with pytest.raises(SeamTimeout) as excinfo:
        bounded_call(
            ["systemctl", "restart", "hal0-slot@chat.service"], timeout=5.0, run=_timing_out_run
        )

    assert excinfo.value.code == "slot.seam_timeout"
    assert excinfo.value.status == 504
    assert isinstance(excinfo.value.__cause__, subprocess.TimeoutExpired)


def test_seam_timeout_details_carry_command_budget_and_last_state() -> None:
    def _run(argv: list[str], **kw: object) -> MagicMock:
        raise subprocess.TimeoutExpired(
            cmd=argv, timeout=kw.get("timeout") or 0, output="Starting hal0-slot@chat.service...\n"
        )

    with pytest.raises(SeamTimeout) as excinfo:
        bounded_call(["systemctl", "restart", "hal0-slot@chat.service"], timeout=42.0, run=_run)

    exc = excinfo.value
    assert exc.details["command"] == ["systemctl", "restart", "hal0-slot@chat.service"]
    assert exc.details["budget_s"] == 42.0
    assert exc.details["last_state"] == "Starting hal0-slot@chat.service..."
    assert "42s" in str(exc)
    assert "Starting hal0-slot@chat.service" in str(exc)


def test_seam_timeout_last_state_is_none_when_child_produced_no_output() -> None:
    with pytest.raises(SeamTimeout) as excinfo:
        bounded_call(["systemctl", "daemon-reload"], timeout=5.0, run=_timing_out_run)

    assert excinfo.value.details["last_state"] is None


def test_bounded_call_passthrough_on_success() -> None:
    calls, run = _recorder()
    result = bounded_call(["systemctl", "is-active", "chat"], timeout=5.0, run=run)

    assert calls == [["systemctl", "is-active", "chat"]]
    assert result.returncode == 0


def test_bounded_call_none_timeout_never_raises_seam_timeout() -> None:
    """``timeout=None`` is a deliberate passthrough — subprocess.run itself
    never raises TimeoutExpired without a bound, so bounded_call has nothing
    to convert."""
    calls, run = _recorder()

    bounded_call(["systemctl", "daemon-reload"], timeout=None, run=run)

    assert calls == [["systemctl", "daemon-reload"]]


def test_systemctl_wedged_seam_call_raises_seam_timeout_not_bare_timeout_expired() -> None:
    """#1869: every ``systemctl()`` route (direct AND seamed) now bounds
    through :func:`bounded_call` — a wedged child raises the typed error the
    slot state machine and the CLI know how to handle, not the bare stdlib
    exception every prior caller would have had to catch individually."""
    seam = SystemCtlSeam(run=_timing_out_run, is_hal0_user=lambda: True)

    with pytest.raises(SeamTimeout) as excinfo:
        seam.systemctl("systemctl", "stop", "hal0-slot@chat.service", timeout=7.5)

    assert excinfo.value.details["command"] == [
        "sudo",
        "-n",
        SEAM_BIN,
        "stop",
        "chat",
    ]
    assert excinfo.value.details["budget_s"] == 7.5


def test_write_quadlet_wedged_seam_call_raises_seam_timeout(tmp_path: Path) -> None:
    seam = SystemCtlSeam(run=_timing_out_run, is_hal0_user=lambda: True)
    quadlet = tmp_path / "hal0-slot@chat.container"

    with pytest.raises(SeamTimeout):
        seam.write_quadlet(quadlet, "[Container]\n", timeout=15.0)


# ── hal0_service_home() / running_hal0_bin() — hoisted from the CLI ─────────


def test_hal0_service_home_is_the_hal0_users_home(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Ent:
        pw_dir = "/srv/hal0-home"

    monkeypatch.setattr(seam_mod.pwd, "getpwnam", lambda _name: _Ent())
    assert seam_mod.hal0_service_home() == "/srv/hal0-home"


def test_hal0_service_home_falls_back_when_user_absent_or_home_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _no_such_user(_name: str) -> None:
        raise KeyError("hal0")

    monkeypatch.setattr(seam_mod.pwd, "getpwnam", _no_such_user)
    assert seam_mod.hal0_service_home() == "/var/lib/hal0"

    class _Empty:
        pw_dir = ""

    monkeypatch.setattr(seam_mod.pwd, "getpwnam", lambda _name: _Empty())
    assert seam_mod.hal0_service_home() == "/var/lib/hal0"


def test_running_hal0_bin_prefers_the_script_next_to_the_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "hal0"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(seam_mod.sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr(seam_mod.shutil, "which", lambda _n: "/usr/local/bin/hal0")
    assert seam_mod.running_hal0_bin() == str(script)


def test_running_hal0_bin_falls_back_to_path_then_the_bare_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seam_mod.sys, "executable", str(tmp_path / "nowhere" / "python"))
    monkeypatch.setattr(seam_mod.shutil, "which", lambda _n: "/usr/bin/hal0")
    assert seam_mod.running_hal0_bin() == "/usr/bin/hal0"
    monkeypatch.setattr(seam_mod.shutil, "which", lambda _n: None)
    assert seam_mod.running_hal0_bin() == "hal0"


# ── hal0_service_account (#3066) ──────────────────────────────────────────────


def test_hal0_service_account_is_none_without_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(name: str) -> None:
        raise KeyError(name)

    monkeypatch.setattr(seam_mod.pwd, "getpwnam", _missing)
    assert seam_mod.hal0_service_account() is None


def test_hal0_service_account_carries_sorted_supplementary_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary gid excluded, the rest sorted — the img plan merges them into
    ``--group-add`` after the GPU groups, so the order must be stable."""
    pw = MagicMock(pw_uid=996, pw_gid=989)
    monkeypatch.setattr(seam_mod.pwd, "getpwnam", lambda name: pw)
    monkeypatch.setattr(seam_mod.os, "getgrouplist", lambda name, gid: [2000, 989, 44, 993, 44])
    account = seam_mod.hal0_service_account()
    assert account == seam_mod.ServiceAccount(996, 989, (44, 993, 2000))
