"""Regenerate a stale Hermes gateway unit as root, without running Hermes (ADR-0034 d6).

A box that ran Hermes before hal0 1.6.0 keeps the
``/etc/systemd/system/hermes-gateway.service`` that Hermes 0.19.0 wrote: no
``After=``/``Wants=user@<uid>.service`` and no linger for ``hal0``. Under
v0.22.2 its kanban workers cannot start and cron jobs run unscoped. A box
where upstream rendered the unit itself (``hermes gateway migrate
--multiplex``, or ``hermes gateway install`` run outside hal0) runs
upstream's release-local launcher shim, which fails on every start once
``current`` moves to a new release (#2894). ``hal0 update`` heals both
through the ``hal0-update hermes-gateway`` verb, which runs
:func:`regenerate_gateway_unit` as root.

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
is that output. v0.22.4 (``4d043038``, ``gateway.py:3196``) generates the same
unit byte for byte: the generator and every helper it calls are unchanged, and
both refs render an identical unit from the same inputs. Where upstream reads the target user's Hermes config, the
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

Nothing in hal0 runs upstream's generator any more (#2943). Under the
v0.22.4 pin, ``hermes gateway install --system`` run as root resolves its
default home from root's ACCOUNT home (``/root/.hermes``), so with
``HERMES_HOME=/var/lib/hal0/.hermes`` it names the unit
``hermes-gateway-<hash>.service`` and bakes the release path it ran from;
running it also creates a stray ``/root/.hermes``. The installer and
``hal0 agent install hermes`` therefore lay the unit down with
:func:`install_gateway_unit`, and every path that heals a unit first moves a
hal0 gateway off such a hashed unit (:func:`migrate_hashed_units`).
"""

from __future__ import annotations

import contextlib
import hashlib
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


#: Where upstream writes its per-checkout launcher shim. The shim hard-codes
#: one release's checkout, interpreter and lazily installed dependency
#: environment, so the gateway fails on the next release (#2894).
UPSTREAM_SHIM_DIR = "/.hermes/bin/"

#: The launcher hal0 renders, per ``Exec*`` key: its interpreter, then the
#: ``-I -c`` bootstrap that puts ``current/src`` on ``sys.path``.
_LAUNCHER_PREFIX = {
    "ExecStart": f'"{PYTHON}" "-I" "-c" ',
    "ExecStop": f'-"{PYTHON}" "-I" "-c" ',
    "ExecStopPost": f'-"{PYTHON}" "-I" "-c" ',
}
_LAUNCHER_PATH = f"sys.path.insert(0, '{SRC}')"


def unit_stale_reason(text: str) -> str | None:
    """Why ``text`` is not a gateway unit hal0 leaves in place, or ``None``.

    Stale is the pre-0.22 shape (no ``user@`` ordering, #2853), or any
    ``ExecStart``/``ExecStop``/``ExecStopPost`` that is not hal0's launcher:
    ``current/venv/bin/python -I -c`` with ``current/src`` on ``sys.path``.
    That covers upstream's release-local shim under ``src/.hermes/bin/``
    (#2894), a unit still naming ``releases/<id>``, and any other launcher.
    A pure text check: it reads no file and runs nothing.
    """
    if unit_is_old_shape(text):
        return "the hermes-gateway unit predates Hermes 0.22 (no user@<uid>.service ordering)"
    seen: dict[str, list[str]] = {key: [] for key in _LAUNCHER_PREFIX}
    for raw in text.splitlines():
        m = _EXEC_KEY_RE.match(raw.strip())
        if m and m.group(1) in seen:
            seen[m.group(1)].append(m.group(2))
    for key, prefix in _LAUNCHER_PREFIX.items():
        if not seen[key]:
            return f"the hermes-gateway unit has no {key}= running hal0's launcher"
        for value in seen[key]:
            if UPSTREAM_SHIM_DIR in value:
                return (
                    f"the hermes-gateway unit's {key}= runs upstream's release-local "
                    "launcher (src/.hermes/bin/hermes), not hal0's"
                )
            if not value.startswith(prefix) or _LAUNCHER_PATH not in value:
                return f"the hermes-gateway unit's {key}= is not hal0's launcher"
    return None


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


def _enabled_state(run: Runner, unit: str = GATEWAY_UNIT) -> str:
    try:
        proc = _run(run, ["systemctl", "is-enabled", unit], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = (getattr(proc, "stdout", "") or "").strip()
    return out.splitlines()[0] if out else "unknown"


def _is_active(run: Runner, unit: str = GATEWAY_UNIT) -> bool:
    try:
        proc = _run(run, ["systemctl", "is-active", "--quiet", unit], timeout=30)
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


#: Hal0's Hermes home. Upstream names a non-default home's unit after the
#: first 8 hex of sha256(home): ``hermes_cli/gateway_runtime_service.py:37-60``.
HERMES_HOME = f"{AGENT_HOME}/.hermes"
HASHED_UNIT = (
    "hermes-gateway-" + hashlib.sha256(HERMES_HOME.encode("utf-8")).hexdigest()[:8] + ".service"
)


def _is_hal0_hashed_unit(text: str) -> bool:
    """A suffixed unit for hal0's Hermes: ``User=hal0`` and ``HERMES_HOME`` set to hal0's home."""
    user = re.search(r"^User=(\S+)\s*$", text, re.MULTILINE)
    home = re.search(r'^Environment="?HERMES_HOME=([^"\s]+)"?\s*$', text, re.MULTILINE)
    return bool(user and user.group(1) == GATEWAY_USER and home and home.group(1) == HERMES_HOME)


def hal0_hashed_units(unit_dir: Path) -> list[Path]:
    """Upstream's suffixed unit for hal0's Hermes home in ``unit_dir``, if present.

    Exactly :data:`HASHED_UNIT` (a standalone profile's unit is named after
    the profile, never this hash), a regular file (a mask is skipped), with
    ``User=hal0`` and ``HERMES_HOME=/var/lib/hal0/.hermes``. Read-only and
    unprivileged (unit files are world-readable), so the ``hal0 update``
    probe can call it.
    """
    path = unit_dir / HASHED_UNIT
    try:
        if path.is_symlink() or not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [path] if _is_hal0_hashed_unit(text) else []


def effective_gateway_state(
    *,
    unit_path: Path = GATEWAY_UNIT_PATH,
    run: Runner | None = None,
) -> tuple[str, bool]:
    """``(is-enabled word, active)`` of the gateway the operator actually has.

    The canonical unit when it exists (a mask counts); otherwise hal0's
    suffixed unit, so a caller deciding whether to turn the gateway on reads
    the operator's real on/off choice on a box a 1.6.0 nightly installed
    (#2943); otherwise ``("not-found", False)``.
    """
    run = run if run is not None else subprocess.run
    if unit_path.is_symlink() or unit_path.exists():
        return _enabled_state(run, unit_path.name), _is_active(run, unit_path.name)
    hashed = hal0_hashed_units(unit_path.parent)
    if not hashed:
        return "not-found", False
    return _enabled_state(run, hashed[0].name), _is_active(run, hashed[0].name)


def _write_rendered(unit_path: Path, uid_of: Callable[[str], int], done: list[str]) -> None:
    try:
        uid = uid_of(GATEWAY_USER)
    except KeyError as exc:
        raise GatewayRegenError(_failure("resolve the hal0 uid", str(exc), done)) from exc
    rendered = render_unit(uid)
    problems = validate_unit(rendered, uid)
    if problems:
        raise GatewayRegenError("refusing the rendered unit: " + "; ".join(problems))
    try:
        _write_unit(unit_path, rendered)
    except OSError as exc:
        raise GatewayRegenError(_failure(f"write {unit_path}", str(exc), done)) from exc


def _ensure_linger(
    run: Runner, linger_dir: str, which: Callable[[str], str | None], done: list[str]
) -> str | None:
    """Turn linger on for ``hal0``. Returns an error string instead of raising."""
    if os.path.exists(os.path.join(linger_dir, GATEWAY_USER)) or which("loginctl") is None:
        return None
    try:
        proc = _run(run, ["loginctl", "enable-linger", GATEWAY_USER])
    except (OSError, subprocess.SubprocessError) as exc:
        return f"loginctl enable-linger {GATEWAY_USER} failed: {exc}"
    if proc.returncode != 0:
        detail = ((proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}")[:300]
        return f"loginctl enable-linger {GATEWAY_USER} failed: {detail}"
    done.append(f"linger on for {GATEWAY_USER}")
    return None


def migrate_hashed_units(
    *,
    unit_path: Path = GATEWAY_UNIT_PATH,
    linger_dir: str = LINGER_DIR,
    run: Runner | None = None,
    uid_of: Callable[[str], int] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> RegenResult:
    """Move a hal0 gateway off upstream's suffixed unit onto ``hermes-gateway.service`` (#2943).

    Order, so no failure loses the gateway: the canonical unit is rendered,
    validated, written and loaded FIRST (a failure there leaves the suffixed
    unit untouched); then the suffixed unit is stopped and disabled (a
    failure there removes nothing); then it is removed; then the canonical
    unit takes over its state. An enabled suffixed unit means the operator
    wants the gateway on, so the canonical unit is enabled AND started even
    if the old one was crash-looping on a pruned release; a disabled one that
    was running is started but left disabled. A canonical unit that already
    existed keeps its own state (#2807), and a mask is never written through.
    Linger is turned on before any start. Root only. Raises
    :class:`GatewayRegenError` naming the steps already done; a failed
    removal is reported after the canonical unit has taken over.
    """
    run = run if run is not None else subprocess.run
    uid_of = uid_of if uid_of is not None else (lambda name: pwd.getpwnam(name).pw_uid)
    hashed = hal0_hashed_units(unit_path.parent)
    if not hashed:
        return RegenResult("noop", "no hashed hermes-gateway unit")
    if which("systemctl") is None:
        return RegenResult("noop", "hashed hermes-gateway unit found but no systemctl; left alone")
    masked = unit_path.is_symlink()
    pre_existing = masked or unit_path.exists()
    old_text = None
    if pre_existing and not masked:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            old_text = unit_path.read_text(encoding="utf-8")
    result = RegenResult("migrated", "")
    done = result.steps
    for path in hashed:
        state = _enabled_state(run, path.name)
        result.was_enabled = result.was_enabled or state in ("enabled", "enabled-runtime")
        result.was_active = result.was_active or _is_active(run, path.name)
    if not masked:
        _write_rendered(unit_path, uid_of, done)
        result.unit_rewritten = True
        done.append(f"wrote {GATEWAY_UNIT}")
        _check(run, ["systemctl", "daemon-reload"], "systemctl daemon-reload", done)
    unlink_errors: list[str] = []
    for path in hashed:
        try:
            _check(
                run,
                ["systemctl", "disable", "--now", path.name],
                f"systemctl disable --now {path.name}",
                done,
            )
        except GatewayRegenError:
            # Roll back the canonical unit this run wrote: left on disk, a
            # retry would read it as one that "already existed" and keep its
            # (disabled) state instead of the suffixed unit's (#2946 review).
            if not pre_existing:
                with contextlib.suppress(OSError):
                    unit_path.unlink()
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    _run(run, ["systemctl", "daemon-reload"])
            raise
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            unlink_errors.append(f"remove {path}: {exc}")
            continue
        done.append(f"removed {path.name}")
    _check(run, ["systemctl", "daemon-reload"], "systemctl daemon-reload", done)
    if masked:
        done.append(f"{GATEWAY_UNIT} is masked; left off")
    elif not pre_existing:
        start = result.was_enabled or result.was_active
        if start:
            linger_error = _ensure_linger(run, linger_dir, which, done)
            if linger_error:
                done.append(linger_error)
        if result.was_enabled:
            _check(
                run, ["systemctl", "enable", GATEWAY_UNIT], f"systemctl enable {GATEWAY_UNIT}", done
            )
            done.append("enabled")
        if start:
            _check(
                run, ["systemctl", "start", GATEWAY_UNIT], f"systemctl start {GATEWAY_UNIT}", done
            )
            result.restarted = True
            done.append("started")
    elif (
        old_text is not None
        and unit_stale_reason(old_text) is not None
        and _is_active(run, GATEWAY_UNIT)
    ):
        # The pre-existing canonical unit was stale and running: the rewrite
        # only takes hold on a restart.
        _check(
            run, ["systemctl", "restart", GATEWAY_UNIT], f"systemctl restart {GATEWAY_UNIT}", done
        )
        result.restarted = True
        done.append("restarted")
    if unlink_errors:
        raise GatewayRegenError(_failure("; ".join(unlink_errors), "left behind", done))
    result.message = f"{GATEWAY_UNIT}: " + ", ".join(done)
    return result


def install_gateway_unit(
    *,
    unit_path: Path = GATEWAY_UNIT_PATH,
    linger_dir: str = LINGER_DIR,
    run: Runner | None = None,
    uid_of: Callable[[str], int] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> RegenResult:
    """Lay ``hermes-gateway.service`` down from the template, as the installer does (#2943).

    Replaces running upstream's ``hermes gateway install --system`` as root.
    A masked unit is left masked. Otherwise a hal0 suffixed unit is migrated
    first (its on/off state carried over, see :func:`migrate_hashed_units`),
    the canonical unit is (re)written and loaded, a stale one that was running
    is restarted, and linger is turned on for ``hal0`` (kanban workers and
    cron need its user manager). It never enables or starts the unit beyond
    that carry-over: the caller applies the operator's on/off decision, read
    with :func:`effective_gateway_state`. A linger failure is reported in the
    message, not raised: the unit is in place. Root only; never runs Hermes.
    """
    run = run if run is not None else subprocess.run
    uid_of = uid_of if uid_of is not None else (lambda name: pwd.getpwnam(name).pw_uid)
    migrated = migrate_hashed_units(
        unit_path=unit_path, linger_dir=linger_dir, run=run, uid_of=uid_of, which=which
    )
    result = RegenResult("installed", "", migrated.was_enabled, migrated.was_active)
    done = result.steps
    done.extend(migrated.steps)
    if unit_path.is_symlink():
        result.action = "noop"
        result.message = f"{GATEWAY_UNIT} is masked; left alone" + (
            f" ({', '.join(done)})" if done else ""
        )
        return result
    if not migrated.unit_rewritten:
        old_text = None
        with contextlib.suppress(OSError, UnicodeDecodeError):
            old_text = unit_path.read_text(encoding="utf-8")
        _write_rendered(unit_path, uid_of, done)
        done.append(f"wrote {GATEWAY_UNIT}")
        if which("systemctl") is not None:
            _check(run, ["systemctl", "daemon-reload"], "systemctl daemon-reload", done)
            if (
                old_text is not None
                and unit_stale_reason(old_text) is not None
                and _is_active(run, GATEWAY_UNIT)
            ):
                _check(
                    run,
                    ["systemctl", "restart", GATEWAY_UNIT],
                    f"systemctl restart {GATEWAY_UNIT}",
                    done,
                )
                result.restarted = True
                done.append("restarted")
    result.unit_rewritten = True
    linger_error = _ensure_linger(run, linger_dir, which, done)
    if linger_error:
        done.append(linger_error)
    else:
        result.linger_enabled = any(s.startswith("linger on") for s in done)
    result.restarted = result.restarted or migrated.restarted
    result.message = f"{GATEWAY_UNIT}: " + ", ".join(done)
    return result


def regenerate_gateway_unit(
    *,
    unit_path: Path = GATEWAY_UNIT_PATH,
    linger_dir: str = LINGER_DIR,
    run: Runner | None = None,
    uid_of: Callable[[str], int] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> RegenResult:
    """Heal an in-use stale gateway unit and turn linger on, preserving its state.

    Acts only when the unit exists, is a regular file (a masked unit is a
    ``/dev/null`` symlink and is left alone), is enabled or running, and the
    unit is stale (:func:`unit_stale_reason`: no ``user@`` ordering, or an
    ``Exec*`` line that is not hal0's launcher, such as upstream's
    ``src/.hermes/bin/hermes`` shim) or ``hal0`` has no linger.
    Then it renders the v0.22.2 unit (when the unit is stale), writes it
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

    # A fresh 1.6.0 nightly install ran upstream's generator as root and got
    # a hashed unit instead of this one (#2943): move the gateway first, then
    # heal the canonical unit like any other.
    migrated = migrate_hashed_units(
        unit_path=unit_path, linger_dir=linger_dir, run=run, uid_of=uid_of, which=which
    )
    if migrated.action == "migrated":
        try:
            tail = regenerate_gateway_unit(
                unit_path=unit_path, linger_dir=linger_dir, run=run, uid_of=uid_of, which=which
            )
        except GatewayRegenError as exc:
            # The migration itself succeeded (the gateway moved over); a
            # follow-up such as linger with no loginctl is reported, not fatal.
            tail = RegenResult("noop", "", steps=[f"follow-up: {exc}"])
        steps = [*migrated.steps, *tail.steps]
        return RegenResult(
            "regenerated",
            f"{GATEWAY_UNIT}: " + ", ".join(steps),
            was_enabled=migrated.was_enabled,
            was_active=migrated.was_active,
            unit_rewritten=True,
            linger_enabled=tail.linger_enabled,
            restarted=migrated.restarted or tail.restarted,
            steps=steps,
        )
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

    stale = unit_stale_reason(text) is not None
    linger_on = os.path.exists(os.path.join(linger_dir, GATEWAY_USER))
    if not stale and linger_on:
        return RegenResult("noop", f"{GATEWAY_UNIT} is current and linger is on", **base)

    result = RegenResult("regenerated", "", **base)
    done = result.steps
    if stale:
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
    "HASHED_UNIT",
    "GatewayRegenError",
    "RegenResult",
    "effective_gateway_state",
    "hal0_hashed_units",
    "install_gateway_unit",
    "migrate_hashed_units",
    "regenerate_gateway_unit",
    "render_unit",
    "unit_is_old_shape",
    "unit_stale_reason",
    "validate_unit",
]


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.agents.hermes_gateway_regen install|state`` (the installer's entries)."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["state"]:
        # `<is-enabled word> <0|1 active>` of the gateway the operator has:
        # the installer's on/off capture on a box with only a suffixed unit.
        state, active = effective_gateway_state()
        print(f"{state} {int(active)}")
        return 0
    if args != ["install"]:
        print("usage: python -m hal0.agents.hermes_gateway_regen install|state", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("hermes-gateway unit: install needs root", file=sys.stderr)
        return 1
    try:
        result = install_gateway_unit()
    except GatewayRegenError as exc:
        print(f"hermes-gateway unit: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised through main()
    raise SystemExit(main())
