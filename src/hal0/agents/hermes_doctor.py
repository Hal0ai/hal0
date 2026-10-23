"""The Hermes doctor checks, below both ``hal0 doctor`` and ``GET /api/agents/{name}/overview``.

Each check is a plain function over the live host that returns ``{status, detail}``
rows (the ownership audit returns a row per path). They sit in ``hal0.agents`` so the
API layer can run them without importing ``hal0.cli`` (the layering rule in
:mod:`hal0.health_report`); ``hal0.cli.doctor_commands`` and ``hal0.cli.doctor_all``
import them from here and keep the names they always exported.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hal0.agents.hermes_layout import (
    HERMES_PYTHON_ROOT,
    HERMES_RELEASE_ROOT,
    HERMES_VENV_PATH,
    SQLITE_MIN,
)


def check_hermes_sqlite(
    venv: Path | None = None, *, run: Callable[..., Any] = subprocess.run
) -> dict[str, str]:
    """The SQLite version the live Hermes interpreter links; ``{status, detail}``.

    ``status`` is ``ok``, ``warn`` (older than 3.51.3, or the probe failed) or
    ``absent`` (no Hermes venv).
    """
    venv = venv if venv is not None else HERMES_VENV_PATH
    python = venv / "bin" / "python"
    if not python.exists():
        return {"status": "absent", "detail": "no Hermes venv"}
    try:
        proc = run(
            [
                str(python),
                "-I",
                "-c",
                "import sqlite3, sys; print(sys.version.split()[0], sqlite3.sqlite_version)",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "warn", "detail": f"could not run {python}: {exc}"}
    parts = (getattr(proc, "stdout", "") or "").split()
    if getattr(proc, "returncode", 1) != 0 or len(parts) != 2:
        return {"status": "warn", "detail": f"could not read the SQLite version from {python}"}
    py, sqlite = parts
    try:
        version = tuple(int(x) for x in sqlite.split("."))
    except ValueError:
        return {"status": "warn", "detail": f"unparsable SQLite version {sqlite!r}"}
    floor = ".".join(map(str, SQLITE_MIN))
    if version < SQLITE_MIN:
        return {
            "status": "warn",
            "detail": (
                f"Python {py} links SQLite {sqlite} (< {floor}): Hermes forces DELETE journal "
                "mode and warns per database. Clear HAL0_HERMES_PYTHON so hal0 uses a "
                "uv-managed interpreter, then `sudo hal0 agent reprovision hermes --repair`."
            ),
        }
    return {"status": "ok", "detail": f"Python {py}, SQLite {sqlite} (>= {floor})"}


def check_hermes_plugins(
    hermes_home: Path | None = None, src_root: Path | None = None
) -> dict[str, str]:
    """Compare the plugin trees mirrored into HERMES_HOME with the shipped ones.

    ``status`` is ``ok``, ``warn`` (a mirrored tree differs from, or is missing
    next to, the shipped source, or the required hal0-memory source is itself
    missing), ``unreadable`` (HERMES_HOME not readable by this user) or
    ``absent`` (no HERMES_HOME or no shipped source to compare against). A stale manifest makes Hermes downgrade the
    plugin (#2884).
    """
    from hal0.agents.hermes_provision import (
        REPO_ROOT_FOR_INSTALLER,
        _dirs_identical,
    )

    home = hermes_home if hermes_home is not None else _HERMES_HOME
    src = (
        src_root
        if src_root is not None
        else REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "plugins"
    )
    try:
        if not home.is_dir() or not src.is_dir():
            return {"status": "absent", "detail": "no HERMES_HOME or shipped plugins"}
        pairs = {
            "hal0-memory": (home / "plugins" / "hal0-memory", False),
            "hal0-provider": (home / "plugins" / "model-providers" / "hal0", True),
        }
        drifted: list[str] = []
        for name, (dst, optional) in pairs.items():
            shipped = src / name
            if not shipped.is_dir():
                if optional:
                    continue
                # hal0-memory is required by the mirror; a release without it
                # is broken, never "matching".
                drifted.append(f"{shipped} (shipped source missing)")
                continue
            if not dst.is_dir() or not _dirs_identical(shipped, dst):
                drifted.append(str(dst))
    except OSError as exc:
        # HERMES_HOME is hal0:hal0 0700; as a non-root user is_dir() raises
        # EACCES on Python 3.12. Advisory only: never abort `hal0 doctor`.
        return {
            "status": "unreadable",
            "detail": f"cannot read HERMES_HOME plugin trees ({exc.strerror or exc}); run with sudo",
        }
    if drifted:
        return {
            "status": "warn",
            "detail": (
                f"{', '.join(drifted)} differ from the shipped plugins; Hermes may reject "
                "the stale manifest. Run `sudo hal0 agent reprovision hermes`."
            ),
        }
    return {"status": "ok", "detail": "plugin trees match the shipped source"}


# ── hal0 doctor perms — Hermes ownership drift (#843) ─────────────────────────
#
# Read-only audit for the root-clobber regression: when root runs Hermes it
# writes a split-brain /root/.hermes tree and/or leaves root:root files the
# User=hal0 unit can't read (so Hermes silently falls back to the default
# provider). This surfaces that loudly. It NEVER repairs — reconciliation is the
# explicit `sudo hal0 agent bootstrap hermes --repair` path.

_HERMES_HOME = Path("/var/lib/hal0/.hermes")
_HERMES_VENV = HERMES_VENV_PATH
_HERMES_RELEASES = HERMES_RELEASE_ROOT
_HERMES_PYTHON = HERMES_PYTHON_ROOT
_STRAY_ROOT_HOME = Path("/root/.hermes")
_EXPECTED_OWNER = "hal0"


def check_hermes_ownership(
    *,
    expected_user: str = _EXPECTED_OWNER,
    hermes_home: Path = _HERMES_HOME,
    venv: Path = _HERMES_VENV,
    release_root: Path = _HERMES_RELEASES,
    python_root: Path = _HERMES_PYTHON,
    stray_home: Path = _STRAY_ROOT_HOME,
    owner_of: Callable[[Path], str | None],
    exists: Callable[[Path], bool],
) -> list[dict[str, str]]:
    """Audit Hermes runtime ownership; return rows ``{path,label,status,detail}``.

    ``status`` is ``ok`` (owned by ``expected_user``), ``drift`` (wrong owner, or
    a stray /root/.hermes), or ``absent`` (not present — not a problem).
    """
    rows: list[dict[str, str]] = []
    checks = (
        (hermes_home, "HERMES_HOME tree"),
        (hermes_home / "config.yaml", "config.yaml"),
        # Follows the venvs/hermes symlink into the live release, which the
        # hal0 user built, so a migrated host keeps passing this row.
        (venv, "hermes venv"),
        (release_root, "hermes release tree"),
        # The builder's interpreters, inside the release tree. The pre-layout
        # /var/lib/hal0/python is not audited: it is root-owned on existing
        # hosts by design and builds never write it.
        (python_root, "uv-managed Python"),
    )
    for path, label in checks:
        if not exists(path):
            rows.append(
                {"path": str(path), "label": label, "status": "absent", "detail": "not present"}
            )
            continue
        owner = owner_of(path)
        if owner == expected_user:
            rows.append(
                {"path": str(path), "label": label, "status": "ok", "detail": f"owned by {owner}"}
            )
        else:
            rows.append(
                {
                    "path": str(path),
                    "label": label,
                    "status": "drift",
                    "detail": f"owned by {owner or '?'} (expected {expected_user})",
                }
            )
    if exists(stray_home):
        rows.append(
            {
                "path": str(stray_home),
                "label": "split-brain /root/.hermes",
                "status": "drift",
                "detail": "root ran Hermes; remove after reconciling",
            }
        )
    return rows


def has_ownership_drift(rows: list[dict[str, str]]) -> bool:
    """True iff any row is in the ``drift`` state."""
    return any(r["status"] == "drift" for r in rows)


def _unmanaged_gateways(proc: Path) -> list[str]:
    """``pid (cgroup)`` for every ``gateway run --idle-exit`` process under ``proc``."""
    found: list[str] = []
    try:
        entries = sorted(proc.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"--idle-exit" not in argv or b"gateway" not in argv or b"run" not in argv:
            continue
        cgroup = "unknown cgroup"
        try:
            last = (entry / "cgroup").read_text(encoding="utf-8").strip().splitlines()[-1]
            cgroup = last.rsplit(":", 1)[-1] or cgroup
        except (OSError, IndexError, UnicodeDecodeError):
            pass
        found.append(f"pid {entry.name} ({cgroup})")
    return found


def _unit_present(unit: Path) -> bool:
    """Presence by lstat, the test Hermes applies (``_exists``,
    ``hermes_cli/gateway_runtime_service.py:84-90``): a persistently masked
    unit is a symlink to /dev/null, which ``is_file()`` would call missing,
    yet a mask is exactly one of the states that blocks the spawn."""
    try:
        unit.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _standalone_profiles(home: Path) -> list[str]:
    """Profiles whose ``config.yaml`` sets ``gateway.standalone: true``."""
    from hal0.agents import hermes_api as ha
    from hal0.agents import hermes_provision as hp

    found: list[str] = []
    for name in ha.profile_dirs(home):
        try:
            text = (home / "profiles" / name / "config.yaml").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cfg = hp._parse_yaml_config(text) or {}
        gateway = cfg.get("gateway") if isinstance(cfg, dict) else None
        if isinstance(gateway, dict) and gateway.get("standalone") is True:
            found.append(name)
    return found


def check_hermes_gateway_unit(
    *,
    home: Path | None = None,
    unit_path: Path | None = None,
    proc: Path = Path("/proc"),
) -> dict[str, str]:
    """Hermes installed means the gateway unit file exists and no unmanaged gateway runs.

    ``{status, detail}`` with ``status`` ``ok``, ``warn``, ``fail`` or
    ``unreadable`` (Hermes' home is not readable by this user).

    From Hermes v0.22.3 a profile-scoped dashboard chat and the ``hermes chat``
    CLI are gateway clients. With NO gateway unit file installed they start
    their own ``gateway run --idle-exit`` inside the caller's cgroup, and that
    gateway runs cron and the kanban dispatcher (upstream
    ``hermes_cli/gateway_runtime_service.py:92-108`` and
    ``hermes_cli/gateway_runtime_start.py:35-81`` at ``4d043038``). A unit
    that is installed but disabled, stopped, masked or failed blocks that
    start. hal0 only ever ``systemctl disable --now``s the unit and never
    removes the file, so this row guards the one state that would let a client
    spawn a gateway where the operator turned it off (ADR-0034 amendment,
    #2874). Never critical: the row names the fix, it does not block setup.
    """
    from hal0.agents import hermes_api as ha
    from hal0.agents.hermes_layout import GATEWAY_UNIT_PATH

    root = home if home is not None else ha.hermes_home()
    unit = unit_path if unit_path is not None else GATEWAY_UNIT_PATH
    try:
        (root / "config.yaml").stat()
    except PermissionError:
        return {
            "status": "unreadable",
            "detail": f"cannot read {root}; run hal0 doctor as hal0 or root",
        }
    except OSError:
        return {"status": "ok", "detail": "no Hermes agent installed"}
    rogue = _unmanaged_gateways(proc)
    if rogue:
        return {
            "status": "fail",
            "detail": "unmanaged Hermes gateway running: "
            + ", ".join(rogue)
            + "; it runs cron and the kanban dispatcher outside hermes-gateway.service. "
            "Stop it, then run sudo hal0 agent install hermes so the unit file is installed",
        }
    from hal0.agents.hermes_gateway_regen import hal0_hashed_units

    hashed = hal0_hashed_units(unit.parent)
    if hashed and _unit_present(unit):
        # An interrupted move (#2943, #2946 review): both units exist, and the
        # live gateway may still be the suffixed one, on a prunable release
        # and without hal0's drop-in.
        return {
            "status": "fail",
            "detail": "both hermes-gateway.service and upstream's suffixed "
            + ", ".join(p.name for p in hashed)
            + " are installed; the gateway may still run from the suffixed unit. Run sudo "
            "hal0 update (or sudo hal0 agent install hermes) to finish moving it",
        }
    if not _unit_present(unit):
        if hashed:
            # A 1.6.0 nightly install ran upstream's generator as root (#2943):
            # the gateway runs from a suffixed unit with a baked release path,
            # without hal0's drop-ins, and dies at the next release prune.
            return {
                "status": "fail",
                "detail": f"{unit} is missing; the gateway runs from upstream's suffixed "
                + ", ".join(p.name for p in hashed)
                + " instead, which names a release that the next update prunes and skips "
                "hal0's secrets drop-in. Run sudo hal0 update (or sudo hal0 agent install "
                "hermes) to move it onto hermes-gateway.service",
            }
        return {
            "status": "fail",
            "detail": f"{unit} is missing; a profile-scoped Hermes chat or hermes chat would "
            "start an unmanaged gateway (Hermes v0.22.3+). Run sudo hal0 agent install hermes; "
            "to keep the gateway off afterwards, systemctl disable --now hermes-gateway",
        }
    # A profile with ``gateway.standalone: true`` is excluded from the
    # multiplexed gateway and owns its own ``hermes-gateway-<profile>.service``
    # (``hermes_cli/gateway_runtime_service.py:37-60``): with that suffixed
    # unit absent, a chat scoped to it spawns its own gateway even though the
    # default unit is installed.
    missing = [
        p
        for p in _standalone_profiles(root)
        if not _unit_present(unit.with_name(f"hermes-gateway-{p}.service"))
    ]
    if missing:
        return {
            "status": "fail",
            "detail": "standalone Hermes profile(s) without their own gateway unit: "
            + ", ".join(f"{p} (hermes-gateway-{p}.service)" for p in missing)
            + "; a chat scoped to one would start an unmanaged gateway (Hermes v0.22.3+). "
            "Install the unit (hermes -p <profile> gateway install --system) or unset "
            "gateway.standalone in that profile's config.yaml",
        }
    state = "installed (masked)" if unit.is_symlink() else "installed"
    return {"status": "ok", "detail": f"{unit.name} {state}; no unmanaged gateway"}
