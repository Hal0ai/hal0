"""Regenerate an old-shape Hermes gateway unit as root, without running Hermes (ADR-0034 d6).

A box that ran Hermes before hal0 1.6.0 keeps the
``/etc/systemd/system/hermes-gateway.service`` that Hermes 0.19.0 wrote: no
``After=``/``Wants=user@<uid>.service`` and no linger for ``hal0``. Under
v0.22.2 its kanban workers cannot start and cron jobs run unscoped.
``hal0 update`` heals it through the ``hal0-update hermes-gateway`` verb,
which runs :func:`regenerate_gateway_unit` as root.

**Root never runs Hermes code.** The Hermes release tree (venv and checkout)
is owned by ``hal0``. Running ``hermes gateway install --system`` from it as
root would let a process compromised as ``hal0`` edit that tree and then
call this verb, which the sudoers grant lets ``hal0`` call, to run its code
as root. So this module renders the unit itself from a template, using fixed
paths only. It never reads, imports or executes anything under the
``hal0``-writable tree. Its inputs are the existing unit file (root-owned,
under ``/etc/systemd/system``), ``systemctl`` and ``loginctl`` state, and
the ``hal0`` uid from :mod:`pwd`.

The template matches what upstream's ``generate_systemd_unit(system=True,
run_as_user="hal0")`` writes at ``d923a5eb`` (``hermes_cli/gateway.py:3256``),
once :mod:`hal0.agents.hermes_gateway_unit` has normalised it to
``current/``. The test fixture ``tests/fixtures/hermes/gateway-unit-v0.22.2.service``
is that output. Where upstream reads the target user's Hermes config, the
template uses upstream's defaults instead, because root must not read
``hal0``'s config:

* ``Type=simple`` and no ``WatchdogSec=`` (upstream switches to
  ``Type=notify`` only when the config sets a watchdog);
* ``TimeoutStopSec=70`` (upstream derives it from the config's drain
  timeouts; 70 is the value for the defaults);
* no ``LD_LIBRARY_PATH`` line, and none of the ``PATH`` entries upstream
  adds under the target user's Hermes home (``~/.hermes/node/bin``,
  ``~/.hermes/node_modules/.bin`` and pm's managed-Node dirs), because finding
  them means reading that home. ``PATH`` does keep the checkout's
  ``node_modules/.bin``: upstream prepends it whenever the checkout has it
  (``_build_service_path_dirs``), every built release does, and it is a fixed
  path under ``current/``. A missing ``PATH`` directory is harmless.

``sudo hal0 agent install hermes`` still runs upstream's generator. That is
a root run the operator chose, not one ``hal0`` can trigger.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import re
import shutil
import subprocess  # nosec B404 — fixed systemctl/loginctl argv
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hal0.agents.hermes_layout import GATEWAY_UNIT_PATH, HERMES_RELEASE_ROOT

#: The service user the gateway runs as.
GATEWAY_USER = "hal0"
GATEWAY_UNIT = "hermes-gateway.service"

#: Fixed paths in the rendered unit. Never derived from the release tree.
AGENT_HOME = "/var/lib/hal0"
HERMES_HOME = f"{AGENT_HOME}/.hermes"
CURRENT = HERMES_RELEASE_ROOT / "current"
PYTHON = f"{CURRENT}/venv/bin/python"
SRC = f"{CURRENT}/src"
#: The checkout's node tools, which upstream puts first on ``PATH``.
NODE_BIN = f"{SRC}/node_modules/.bin"
#: The ``PATH`` the normalised v0.22.2 unit carries for ``hal0``.
SERVICE_PATH = (
    f"{NODE_BIN}:{AGENT_HOME}/.local/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
#: Upstream's ``TimeoutStopSec`` for the default drain timeouts.
TIMEOUT_STOP_SEC = 70

#: Where logind records lingering users.
LINGER_DIR = "/var/lib/systemd/linger"

Runner = Callable[..., subprocess.CompletedProcess]


class GatewayRegenError(Exception):
    """The unit could not be regenerated; the message says what was done."""


def _launcher(module: str, args: tuple[str, ...] = ()) -> str:
    """Upstream's ``runtime_command`` launcher, quoted the way systemd reads it."""
    code = (
        "import os, sys, runpy; os.environ.pop('PYTHONHOME', None); "
        "os.environ.pop('PYTHONPATH', None); os.environ.pop('VIRTUAL_ENV', None); "
        f"sys.path.insert(0, '{SRC}'); "
        f"os.environ['HERMES_HOME'] = os.environ.get('HERMES_HOME') or '{HERMES_HOME}'; "
        f"import hermes_bootstrap; runpy.run_module('{module}', run_name='__main__', "
        "alter_sys=True)"
    )
    return " ".join(f'"{part}"' for part in (PYTHON, "-I", "-c", code, *args))


def render_unit(uid: int) -> str:
    """The v0.22.2-shape gateway unit for ``hal0`` (uid ``uid``), on ``current/``."""
    return f"""[Unit]
Description=Hermes Agent Gateway - Messaging Platform Integration
After=network-online.target
Wants=network-online.target
After=user@{uid}.service
Wants=user@{uid}.service
StartLimitIntervalSec=0

[Service]
Type=simple
User={GATEWAY_USER}
Group={GATEWAY_USER}
ExecStart={_launcher("hermes_cli.main", ("gateway", "run"))}
WorkingDirectory={HERMES_HOME}
Environment="HOME={AGENT_HOME}"
Environment="USER={GATEWAY_USER}"
Environment="LOGNAME={GATEWAY_USER}"
Environment="PATH={SERVICE_PATH}"

Environment="HERMES_HOME={HERMES_HOME}"
Environment="HERMES_SUPERVISED_CHILD=1"
Restart=always
RestartSec=5
RestartForceExitStatus=75
SuccessExitStatus=75
RestartPreventExitStatus=78
KillMode=mixed
KillSignal=SIGTERM
ExecReload=/bin/kill -USR1 $MAINPID
ExecStop=-{_launcher("gateway.systemd_stop_mark")}
ExecStopPost=-{_launcher("gateway.cgroup_cleanup")}
TimeoutStopSec={TIMEOUT_STOP_SEC}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


_EXEC_KEY_RE = re.compile(r"^(Exec[A-Za-z]+)=(.*)$")
_IDENTITY_RE = re.compile(r"^(User|Group)=(.*)$")
_PATH_RE = re.compile(r"/[A-Za-z0-9._/@+-]+")
#: Absolute paths the rendered unit may name. Anything else is refused.
_ALLOWED_PATHS = frozenset(
    {
        PYTHON,
        SRC,
        NODE_BIN,
        HERMES_HOME,
        AGENT_HOME,
        f"{AGENT_HOME}/.local/bin",
        "/bin/kill",
        "/usr/local/sbin",
        "/usr/local/bin",
        "/usr/sbin",
        "/usr/bin",
        "/sbin",
        "/bin",
    }
)
_ALLOWED_EXEC = {
    "ExecStart": f'"{PYTHON}"',
    "ExecStop": f'-"{PYTHON}"',
    "ExecStopPost": f'-"{PYTHON}"',
    "ExecReload": "/bin/kill ",
}


def validate_unit(text: str, uid: int) -> list[str]:
    """What is wrong with ``text`` as a gateway unit root may write; empty when fine.

    Refuses anything that would run as root (no ``User=hal0``, a second
    ``User=``, a ``+``/``!`` exec prefix, an unknown ``Exec*`` key) or name a
    path outside the fixed set (a ``releases/<id>`` path, a ``/tmp`` script).
    """
    problems: list[str] = []
    if uid <= 0:
        problems.append(f"refusing uid {uid} for {GATEWAY_USER}")
    identity: dict[str, list[str]] = {"User": [], "Group": []}
    for raw in text.splitlines():
        line = raw.strip()
        if m := _IDENTITY_RE.match(line):
            identity[m.group(1)].append(m.group(2).strip())
        if m := _EXEC_KEY_RE.match(line):
            key, value = m.group(1), m.group(2)
            prefix = _ALLOWED_EXEC.get(key)
            if prefix is None:
                problems.append(f"{key}= is not allowed")
            elif not value.startswith(prefix):
                problems.append(f"{key}= must run {prefix.strip()}")
    for key in ("User", "Group"):
        if identity[key] != [GATEWAY_USER]:
            problems.append(f"{key}= must be exactly {GATEWAY_USER} (got {identity[key]})")
    for needle in (f"After=user@{uid}.service", f"Wants=user@{uid}.service"):
        if needle not in text.splitlines():
            problems.append(f"missing {needle}")
    if f"{HERMES_RELEASE_ROOT}/releases/" in text:
        problems.append("names a release path")
    for path in sorted(set(_PATH_RE.findall(text))):
        if path in _ALLOWED_PATHS:
            continue
        problems.append(f"names a path outside the allowed set: {path}")
    return problems


def unit_is_old_shape(text: str) -> bool:
    """#2853's test: a unit from before Hermes 0.22 has no ``user@`` ordering."""
    return "user@" not in text


@dataclass
class RegenResult:
    """What :func:`regenerate_gateway_unit` did."""

    action: str
    message: str
    was_enabled: bool = False
    was_active: bool = False
    unit_rewritten: bool = False
    linger_enabled: bool = False
    restarted: bool = False
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "message": self.message,
            "was_enabled": self.was_enabled,
            "was_active": self.was_active,
            "unit_rewritten": self.unit_rewritten,
            "linger_enabled": self.linger_enabled,
            "restarted": self.restarted,
        }


def _run(run: Runner, argv: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return run(argv, capture_output=True, text=True, timeout=timeout, check=False)  # nosec B603


def _enabled_state(run: Runner) -> str:
    try:
        proc = _run(run, ["systemctl", "is-enabled", GATEWAY_UNIT], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = (getattr(proc, "stdout", "") or "").strip()
    return out.splitlines()[0] if out else "unknown"


def _is_active(run: Runner) -> bool:
    try:
        proc = _run(run, ["systemctl", "is-active", "--quiet", GATEWAY_UNIT], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _check(run: Runner, argv: list[str], what: str, done: list[str]) -> None:
    try:
        proc = _run(run, argv)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GatewayRegenError(_failure(what, str(exc), done)) from exc
    if proc.returncode != 0:
        detail = ((proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}")[:300]
        raise GatewayRegenError(_failure(what, detail, done))


def _failure(what: str, detail: str, done: list[str]) -> str:
    did = f" (already done: {', '.join(done)})" if done else ""
    return f"{what} failed: {detail}{did}"


def _write_unit(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.hal0-{os.getpid()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def regenerate_gateway_unit(
    *,
    unit_path: Path = GATEWAY_UNIT_PATH,
    linger_dir: str = LINGER_DIR,
    run: Runner | None = None,
    uid_of: Callable[[str], int] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> RegenResult:
    """Heal an in-use old-shape gateway unit and turn linger on, preserving its state.

    Acts only when the unit exists, is a regular file (a masked unit is a
    ``/dev/null`` symlink and is left alone), is enabled or running, and
    #2853's check fails: no ``user@`` ordering, or no linger for ``hal0``.
    Then it renders the v0.22.2 unit (when the shape is old), writes it
    ``root:root 0644`` atomically, reloads systemd, runs ``loginctl
    enable-linger hal0`` (when linger is off) and, only if the gateway was
    running, ``systemctl restart hermes-gateway.service``. It never enables,
    disables, starts or stops the unit otherwise. Idempotent: a second run
    finds a current unit with linger on and returns ``action="noop"``.

    Raises :class:`GatewayRegenError` when a step fails; the message names
    the steps already done.
    """
    run = run if run is not None else subprocess.run
    uid_of = uid_of if uid_of is not None else (lambda name: pwd.getpwnam(name).pw_uid)

    if unit_path.is_symlink() or not unit_path.exists():
        return RegenResult("noop", f"{GATEWAY_UNIT}: not installed or masked; nothing to do")
    if not unit_path.is_file():
        return RegenResult("noop", f"{GATEWAY_UNIT}: {unit_path} is not a regular file; left alone")
    try:
        text = unit_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GatewayRegenError(f"cannot read {unit_path}: {exc}") from exc

    if which("systemctl") is None:
        return RegenResult("noop", f"{GATEWAY_UNIT}: no systemctl; nothing to do")
    state = _enabled_state(run)
    was_enabled = state in ("enabled", "enabled-runtime")
    was_active = _is_active(run)
    base = {"was_enabled": was_enabled, "was_active": was_active}
    if state.startswith("masked"):
        return RegenResult("noop", f"{GATEWAY_UNIT} is {state}; left alone", **base)
    if not (was_enabled or was_active):
        return RegenResult("noop", f"{GATEWAY_UNIT} is {state} and stopped; left alone", **base)

    old_shape = unit_is_old_shape(text)
    linger_on = os.path.exists(os.path.join(linger_dir, GATEWAY_USER))
    if not old_shape and linger_on:
        return RegenResult("noop", f"{GATEWAY_UNIT} is current and linger is on", **base)

    result = RegenResult("regenerated", "", **base)
    done = result.steps
    if old_shape:
        try:
            uid = uid_of(GATEWAY_USER)
        except KeyError as exc:
            raise GatewayRegenError(f"no {GATEWAY_USER} user: {exc}") from exc
        rendered = render_unit(uid)
        problems = validate_unit(rendered, uid)
        if problems:
            raise GatewayRegenError("refusing the rendered unit: " + "; ".join(problems))
        try:
            _write_unit(unit_path, rendered)
        except OSError as exc:
            raise GatewayRegenError(f"cannot write {unit_path}: {exc}") from exc
        result.unit_rewritten = True
        done.append("unit rewritten")
        _check(run, ["systemctl", "daemon-reload"], "systemctl daemon-reload", done)
    if not linger_on:
        if which("loginctl") is None:
            raise GatewayRegenError(_failure("loginctl enable-linger", "no loginctl", done))
        _check(
            run,
            ["loginctl", "enable-linger", GATEWAY_USER],
            f"loginctl enable-linger {GATEWAY_USER}",
            done,
        )
        result.linger_enabled = True
        done.append(f"linger on for {GATEWAY_USER}")
    if was_active:
        _check(
            run, ["systemctl", "restart", GATEWAY_UNIT], f"systemctl restart {GATEWAY_UNIT}", done
        )
        result.restarted = True
        done.append("restarted")
    result.message = f"{GATEWAY_UNIT}: " + ", ".join(done)
    return result


__all__ = [
    "GATEWAY_UNIT",
    "GatewayRegenError",
    "RegenResult",
    "regenerate_gateway_unit",
    "render_unit",
    "unit_is_old_shape",
    "validate_unit",
]
