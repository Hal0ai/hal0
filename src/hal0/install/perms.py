"""OwnershipStore — one declarative truth for filesystem ownership + mode.

hal0's path ownership is currently set by ~15 scattered ``chown``/``chmod``/
``install -o`` calls in ``installer/install.sh`` plus ad-hoc fixups, with no
single place that says "this path should be owned by X:Y mode Z". That spread is
the filesystem-layer twin of the slot-config "too many cooks" problem #697
solved for ``slots/*.toml``: the cure is the same shape — one declarative table,
a compute-only ``plan()`` that snapshots disk, an atomic ``commit()`` with
rollback, and a ``drift`` audit that reports (never silently repairs).

This module mirrors :mod:`hal0.slot_config` and :mod:`hal0.stacks.apply`
deliberately: :class:`PermObservation` is the ownership analogue of
``FileState`` (it snapshots ``owner``/``group``/``mode`` rather than TOML
content), :class:`OwnershipPlan` is the analogue of ``ChangeSet``, and
:meth:`OwnershipStore.commit` rolls back exactly like ``SlotConfigStore.commit``.

``service_user="hal0"`` is now the DEFAULT (P3-perms, the "hardened flip"
adopted as the single ownership authority): ``/etc/hal0`` and its mutable
contents are ``hal0``-owned (the config root setgid ``2775`` so the daemon's
own temp-file+rename rewrites work), while ``agents/`` and ``secrets/`` stay
``root:root`` by design (read-only-to-the-service surfaces). ``hal0-api`` runs
``User=hal0`` (``installer/install.sh``); the born-owned contract (drop
privileges to ``hal0`` before any config-writing step — installer's
``run-as-hal0`` seam, §23.3) means a fresh install never produces a
root:root file under either tree in the first place, so ``plan()`` reports no
drift and ``commit()`` writes nothing on a fresh box. An EXISTING (pre-P3-perms)
install, by contrast, genuinely drifts against this table — that's the intended
one-shot migration: ``hal0 doctor perms --fix`` (root-gated, already wired to
:func:`commit`) reconciles it once.

Passing ``service_user="root"`` explicitly reproduces the OLD root-era table
byte-for-byte — kept as the emergency rollback path (``hal0 doctor perms
--table-root``), not the default.

Design notes:
  - ``owner``/``group`` are resolved to uid/gid at *commit* time via
    :mod:`pwd`/:mod:`grp`, so the table is portable across boxes where the
    ``hal0`` uid differs.
  - A row may be ``optional`` (skipped when absent — e.g. ``secrets/`` only
    exists once an agent is provisioned) or a ``glob`` (``slots/*.toml``).
    A glob is single-level by default; ``recursive=True`` walks every depth
    instead (``slots/`` runtime state — see the O13 follow-up note below the
    ``slots/`` row) and ``child_file_mode`` gives recursed FILES a mode
    distinct from recursed dirs (``child_mode``).
  - Stat/chown/chmod are injected seams so the plan/diff/audit logic is unit
    tested without a real privileged filesystem.
"""

from __future__ import annotations

import contextlib
import grp
import logging
import os
import pwd
import stat as stat_mod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from hal0.config import paths

log = logging.getLogger(__name__)


# ── the declarative table ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PermRow:
    """One path's declared ownership + mode.

    ``mode`` is the permission bits only (e.g. ``0o755`` or ``0o2775`` with the
    setgid bit) — the file-type bits returned by ``os.stat`` are masked off
    before comparison so a dir's mode compares cleanly.
    """

    target: Path
    owner: str
    group: str
    mode: int  # the dir's / file's own mode
    glob: str | None = None  # when set, ``target`` is a dir and this globs its children
    child_mode: int | None = (
        None  # mode for globbed children (dirs; files when child_file_mode unset)
    )
    child_file_mode: int | None = None  # mode for FILES matched by a recursive glob (O13 follow-up:
    # a plain (non-recursive) row applies ``child_mode`` to whatever it matches, unchanged — this
    # field only takes effect when ``recursive=True`` finds a file, so it never alters an existing
    # single-level row's behavior. Falls back to ``child_mode`` when unset.
    recursive: bool = False  # when True with ``glob`` set, walk EVERY depth (``rglob`` instead of
    # ``glob``) so nested files below the first level (e.g. ``slots/<id>/state.json``) are covered
    # too, not just the immediate children. Defaults False so every pre-existing glob row keeps its
    # single-level behavior byte-for-byte.
    optional: bool = True  # skip silently when the path is absent
    role: str = ""  # human label for the audit table

    @property
    def label(self) -> str:
        return self.role or str(self.target)


def ownership_table(
    *,
    service_user: str = "hal0",
    service_group: str = "hal0",
) -> list[PermRow]:
    """THE single source of truth for hal0 path ownership.

    ``service_user="hal0"`` (the default, P3-perms) is the hardened flip:
    ``/etc/hal0`` and its *mutable* contents are ``service_user``-owned so the
    daemon can atomically rewrite them (temp-file + ``rename``, which needs
    *directory* write — not just file write). The config root itself is
    ``2775`` (setgid) so files the service or the ``hal0`` group create there
    inherit the shared group. ``service_group`` is the shared ``hal0`` group
    that already owns ``/opt/hal0`` (setgid) and ``/var/lib/hal0``.

    Passing ``service_user="root"`` reproduces the OLD root-era table
    byte-for-byte (every row identical to the pre-P3-perms on-disk layout) —
    the emergency rollback path (``hal0 doctor perms --table-root``), not the
    default.

    Two subtrees stay ``root:root`` regardless of ``service_user``:
      * ``agents/`` — the dashboard-only Hermes allow-list world (#843); the API
        only reads it.
      * ``secrets/`` — systemd reads ``EnvironmentFile`` here *as root* before
        dropping to the service user, so it must not be service-writable.
      * ``/usr/lib/hal0`` — the shipped, read-only code + wrapper-seam tree;
        never service-writable at any point.

    The mutable-config modes below (``hal0.toml`` 0600, ``api.env`` 0644, ...)
    are unchanged by the flip either way — the flip changes *ownership*, not
    file modes. See module docstring.
    """
    etc = paths.etc()
    var_lib = paths.var_lib()
    var_log = paths.var_log()

    # Whether to apply the hardened flip. When False, every row below is
    # byte-identical to the root-era table (existing installs unchanged).
    flipped = service_user != "root"
    # Owner/group for the service-writable config surface. Under the flip the
    # config root is setgid (2775) so the shared hal0 group keeps write across
    # both daemon- and group-created files; root-era keeps the plain 0755 dir.
    etc_owner = service_user if flipped else "root"
    etc_group = service_group if flipped else "root"
    etc_dir_mode = 0o2775 if flipped else 0o755
    slots_dir_mode = 0o2775 if flipped else 0o755
    # Slot/state runtime owner — already service-owned today (defaulting the
    # root-era "root" service_user to the literal hal0 service account).
    state_owner = service_user if flipped else "hal0"

    return [
        # ── /usr/lib/hal0 — shipped, read-only tree ─────────────────────────────
        # Code, versioned release dirs, and the wrapper-seam binaries
        # (hal0-agentenv, hal0-benchctl, hal0-systemctl). Never service-writable
        # — the API only executes seams here, it never rewrites its own tree.
        PermRow(
            paths.lib(),
            "root",
            "root",
            0o755,
            optional=False,
            role="/usr/lib/hal0 (shipped, read-only)",
        ),
        # ── /etc/hal0 — config seed (root-owned today; service-owned under flip) ─
        # The API atomically rewrites slots/*.toml, capabilities.toml, hal0.toml,
        # api.env and chat-templates via temp-file + rename, which needs *dir*
        # write on /etc/hal0 — hence the dir (and its mutable files) flip to the
        # service user. agents/ + secrets/ below stay root:root.
        PermRow(
            etc, etc_owner, etc_group, etc_dir_mode, optional=False, role="/etc/hal0 (config root)"
        ),
        PermRow(paths.hal0_toml(), etc_owner, etc_group, 0o600, role="hal0.toml"),
        PermRow(etc / "profiles.toml", etc_owner, etc_group, 0o600, role="profiles.toml"),
        # FIXME(phase4): api.env is 0644 (world-readable) but may carry tokens —
        # candidate for 0640 root:hal0 under the hardened model.
        PermRow(etc / "api.env", etc_owner, etc_group, 0o644, role="api.env"),
        PermRow(etc / "capabilities.toml", etc_owner, etc_group, 0o600, role="capabilities.toml"),
        PermRow(etc / "upstreams.toml", etc_owner, etc_group, 0o644, role="upstreams.toml"),
        PermRow(paths.hardware_json(), etc_owner, etc_group, 0o644, role="hardware.json"),
        PermRow(paths.openwebui_env(), etc_owner, etc_group, 0o600, role="openwebui.env"),
        PermRow(
            paths.slots_config_dir(),
            etc_owner,
            etc_group,
            slots_dir_mode,
            glob="*.toml",
            child_mode=0o600,  # slots/*.toml are 0600 on disk (owner flips with the dir)
            optional=False,
            role="slots/ (+ *.toml)",
        ),
        # *.lock siblings — advisory RMW locks (config/locking.py) created on
        # demand by WHOEVER writes first. A root-run install that touches the
        # slot store leaves e.g. slots.lock root:root and the hal0-run API can
        # then never open it (halo150 Phase-2 finding: POST /api/slots 500 on
        # a fresh box; doctor perms --fix missed it because no row covered
        # lock files). 0664: owner+group writable — root and hal0 both hold
        # the flock across the seam.
        PermRow(
            etc,
            etc_owner,
            etc_group,
            etc_dir_mode,
            glob="*.lock",
            child_mode=0o664,
            role="/etc/hal0 *.lock (advisory RMW locks)",
        ),
        # agents/ is the dashboard-only Hermes world — pinned root:root (#843),
        # under the flip too: the API only reads it.
        PermRow(paths.agents_config_dir(), "root", "root", 0o755, role="agents/"),
        # ── /var/lib/hal0 — mutable state (already service-owned) ──────────────
        PermRow(
            var_lib,
            state_owner,
            service_group,
            0o2775,
            optional=False,
            role="/var/lib/hal0 (state root)",
        ),
        # *.lock siblings under the state root — same root-run-install hazard
        # as the /etc/hal0 lock row above. `.first-run.lock` needs its own row:
        # pathlib glob's `*` never matches a leading dot.
        PermRow(
            var_lib,
            state_owner,
            service_group,
            0o2775,
            glob="*.lock",
            child_mode=0o664,
            role="/var/lib/hal0 *.lock (advisory RMW locks)",
        ),
        PermRow(
            paths.var_lib() / ".first-run.lock",
            state_owner,
            service_group,
            0o664,
            role=".first-run.lock",
        ),
        PermRow(
            paths.var_lib() / ".hermes",
            state_owner,
            service_group,
            0o700,
            role="HERMES_HOME",
        ),
        # slots/ (runtime slot state) — the per-slot working dirs +
        # ``<slot>/state.json`` the User=hal0 daemon writes at load time.
        # install.sh `mkdir -p ${VAR_DIR}/slots` creates it root:root at
        # install (born under root's umask), but hal0-api runs as hal0 and must
        # create ``slots/<id>/`` + write state.json there — a root:root slots/
        # leaves every slot unable to persist state and they degrade to
        # ``error`` on a fresh box (O13). The previous table only covered the
        # /etc/hal0/slots *config* dir (slots_config_dir above), NOT this
        # runtime state tree, so `doctor perms --fix` never healed it. setgid
        # 2775 (mirrors the benchmarks/ row) so state files inherit the shared
        # hal0 group; the glob heals any pre-existing root-owned per-slot dir.
        #
        # O13 follow-up (r4-stage-validation.md): the single-level glob above
        # healed the DIR (``slots/<id>/``) but never recursed into
        # ``slots/<id>/state.json`` one level deeper — an operator still had to
        # run a manual ``chown -R``. ``recursive=True`` walks every depth;
        # ``child_file_mode=0o600`` matches (does not fight) the ACTUAL mode
        # ``hal0.slots.state.write_state_atomic`` births state.json with —
        # it writes via ``tempfile.mkstemp`` + ``os.replace``, and mkstemp
        # always creates its tempfile 0600 regardless of umask (verified:
        # same pattern as ``hal0.toml``/``profiles.toml`` above, both 0600 for
        # the identical reason). Only the ``hal0`` daemon ever reads/writes
        # state.json, so 0600 is also functionally correct — no other user or
        # group member needs access.
        PermRow(
            var_lib / "slots",
            state_owner,
            service_group,
            0o2775,
            glob="*",
            child_mode=0o2775,
            child_file_mode=0o600,
            recursive=True,
            optional=False,
            role="slots/ (runtime slot state, recursive)",
        ),
        # registry/ — the model registry, also born root:root from the same
        # install.sh mkdir and also written by the User=hal0 daemon. Same O13
        # birth-ownership class as slots/ above; heal the dir. registry/ is
        # flat (no per-item subdirs like slots/<id>/), so its 3 known files —
        # each written by a different mechanism with a different birth mode —
        # get their own explicit (non-recursive, non-glob) rows below rather
        # than one blanket recursive glob, to match each writer instead of
        # fighting it:
        #   * registry.toml    — hal0.registry.import_toml / store, written via
        #     the same write_toml_atomic tempfile.mkstemp path as hal0.toml
        #     above -> born 0600.
        #   * registry.toml.lock — hal0.registry.store.registry_write_lock,
        #     opened via ``os.open(..., 0o644)``; declared 0664 here (not its
        #     0644 birth mode) to match the SAME cross-process-shared
        #     rationale as the *.lock rows above — a root-run tool may create
        #     it first, and the hal0 daemon must still be able to flock it.
        #   * hal0.db — hal0.registry.sqlite_store, opened via sqlite3.connect
        #     -> born 0644 (verified locally); single writer (the hal0
        #     daemon), so 0644 is both the birth mode and functionally
        #     sufficient.
        PermRow(
            var_lib / "registry",
            state_owner,
            service_group,
            0o2775,
            optional=False,
            role="registry/ (model registry)",
        ),
        PermRow(
            var_lib / "registry" / "registry.toml",
            state_owner,
            service_group,
            0o600,
            role="registry/registry.toml",
        ),
        PermRow(
            var_lib / "registry" / "registry.toml.lock",
            state_owner,
            service_group,
            0o664,
            role="registry/registry.toml.lock",
        ),
        PermRow(
            var_lib / "registry" / "hal0.db",
            state_owner,
            service_group,
            0o644,
            role="registry/hal0.db",
        ),
        # agents/ (var_lib) — per-agent sub-homes. 0711 (not 2775): the
        # User=hal0 unit needs to traverse INTO its own home without being able
        # to enumerate siblings. Was repaired ad hoc by hermes_provision's late
        # `_phase_ownership_reconcile` (now dead code, P3-perms F.7) — this row
        # is the declarative replacement.
        PermRow(
            var_lib / "agents",
            state_owner,
            service_group,
            0o711,
            role="agents/ (per-agent sub-homes)",
        ),
        # secrets/ stays root:root even under the flip: systemd reads the
        # EnvironmentFile here AS ROOT before dropping to the service user, so it
        # must not be service-writable (hardened-model decision).
        PermRow(var_lib / "secrets", "root", "root", 0o755, role="secrets/"),
        # secrets/agents/ — per-agent secret .env files (written by the
        # hal0-agentenv seam, never by the service directly). Pinned root:root
        # like secrets/ itself; the dir mode is 0755 (traverse + list), the
        # per-agent .env files are 0600 (owner-read-only tokens).
        PermRow(
            var_lib / "secrets" / "agents",
            "root",
            "root",
            0o755,
            glob="*.env",
            child_mode=0o600,
            role="secrets/agents/ (+ <id>.env)",
        ),
        # benchmarks/ (+ runs/, logs/, server-ab/) — GPU bench artifacts,
        # written by the hal0-benchctl seam then read by hal0-agent/hal0-api.
        # Mirrors the (now-redundant) install.sh chown -R at the old
        # benchmarks install site.
        PermRow(
            var_lib / "benchmarks",
            state_owner,
            service_group,
            0o2775,
            glob="*",
            child_mode=0o2775,
            role="benchmarks/ (+ subdirs)",
        ),
        # skills/ — the writable drop-in skill library (context_link mirrors
        # bundled skills alongside operator-dropped ones).
        PermRow(
            var_lib / "skills",
            state_owner,
            service_group,
            0o2775,
            role="skills/ (drop-in agent skills)",
        ),
        # STATE.md — the live session-state snapshot the Hermes session-start
        # hook cats. Optional: absent until the first render. Replaces the old
        # install.sh chgrp/chmod/touch/chown STATE.md dance.
        PermRow(
            var_lib / "STATE.md",
            state_owner,
            service_group,
            0o664,
            role="STATE.md (session-state snapshot)",
        ),
        # ── /var/log/hal0 ─────────────────────────────────────────────────────
        PermRow(var_log, "hal0", "hal0", 0o755, role="/var/log/hal0"),
    ]


# ── observation (the ownership snapshot) ──────────────────────────────────────


@dataclass(frozen=True)
class PermObservation:
    """A path's current ownership snapshot — the analogue of ``FileState``.

    ``exists is False`` means the path is absent; ``owner``/``group``/``mode``
    are then ``None``.
    """

    path: Path
    exists: bool
    owner: str | None
    group: str | None
    mode: int | None


def _owner_name(uid: int) -> str | None:
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OSError):
        return None


def _group_name(gid: int) -> str | None:
    try:
        return grp.getgrgid(gid).gr_name
    except (KeyError, OSError):
        return None


def observe(path: Path) -> PermObservation:
    """Snapshot one path's ownership + permission bits, or absence."""
    try:
        st = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return PermObservation(path=path, exists=False, owner=None, group=None, mode=None)
    return PermObservation(
        path=path,
        exists=True,
        owner=_owner_name(st.st_uid),
        group=_group_name(st.st_gid),
        mode=stat_mod.S_IMODE(st.st_mode),
    )


# ── plan (compute-only) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class PermDiff:
    """The declared target for one concrete path, plus its current observation.

    ``changed`` is true only when the path EXISTS and at least one of owner /
    group / mode differs from the declared target. An absent path is never
    "changed" (nothing to chown); it is surfaced separately as ``absent``.
    """

    path: Path
    before: PermObservation
    owner: str
    group: str
    mode: int
    role: str

    @property
    def changed(self) -> bool:
        if not self.before.exists:
            return False
        return (
            self.before.owner != self.owner
            or self.before.group != self.group
            or (self.before.mode is not None and self.before.mode != self.mode)
        )


@dataclass(frozen=True)
class OwnershipPlan:
    """Compute-only result of planning the ownership table against disk.

    ``diffs`` covers every concrete path the table addresses (glob rows expand
    to one diff per match). The analogue of ``slot_config.ChangeSet``.
    """

    diffs: tuple[PermDiff, ...]

    @property
    def changed(self) -> bool:
        return any(d.changed for d in self.diffs)

    @property
    def drifted(self) -> tuple[PermDiff, ...]:
        return tuple(d for d in self.diffs if d.changed)


def _child_mode_for(row: PermRow, child: Path) -> int:
    """Resolve the effective mode for one glob-matched child.

    Directories always use ``child_mode`` (falling back to the row's own
    ``mode``). Files use ``child_file_mode`` when the row set one, else fall
    back to the SAME ``child_mode`` value — that fallback is what keeps every
    pre-existing (non-recursive) glob row's behavior byte-identical, since
    those rows only ever declared a single ``child_mode`` and relied on it
    covering whatever type actually matched (e.g. the ``*.lock``/``*.env``
    rows only ever match files).
    """
    dir_mode = row.child_mode if row.child_mode is not None else row.mode
    if child.is_dir():
        return dir_mode
    return row.child_file_mode if row.child_file_mode is not None else dir_mode


def _expand_row(row: PermRow) -> list[tuple[Path, PermRow]]:
    """Expand a glob row to one (path, row) per match; identity for plain rows.

    Non-recursive rows (the default) use a single-level ``Path.glob`` — exactly
    the prior behavior. ``recursive=True`` rows use ``Path.rglob`` instead, so
    matches at every depth are covered (O13 follow-up: a bare single-level glob
    on ``slots/`` heals the per-slot ``slots/<id>/`` dirs but never reaches
    ``slots/<id>/state.json`` one level deeper).
    """
    if row.glob is None:
        return [(row.target, row)]
    if not row.target.is_dir():
        return [(row.target, row)]  # the dir itself (absent/optional handled in plan)
    out: list[tuple[Path, PermRow]] = [(row.target, row)]
    matches = row.target.rglob(row.glob) if row.recursive else row.target.glob(row.glob)
    for child in sorted(matches):
        out.append(
            (
                child,
                replace(
                    row,
                    target=child,
                    mode=_child_mode_for(row, child),
                    glob=None,
                    child_mode=None,
                    child_file_mode=None,
                    recursive=False,
                    role=f"{row.label} :: {child.relative_to(row.target)}",
                ),
            )
        )
    return out


def plan(
    table: Iterable[PermRow] | None = None,
    *,
    observe_fn: Callable[[Path], PermObservation] = observe,
) -> OwnershipPlan:
    """Snapshot disk and compute the per-path ownership diff. Writes NOTHING.

    ``observe_fn`` is injected so the plan/diff logic is unit-tested without a
    real filesystem. Glob rows expand against the live directory.
    """
    rows = list(table) if table is not None else ownership_table()
    diffs: list[PermDiff] = []
    for row in rows:
        for concrete, eff in _expand_row(row):
            before = observe_fn(concrete)
            diffs.append(
                PermDiff(
                    path=concrete,
                    before=before,
                    owner=eff.owner,
                    group=eff.group,
                    mode=eff.mode,
                    role=eff.label,
                )
            )
    return OwnershipPlan(diffs=tuple(diffs))


# ── commit / revert ───────────────────────────────────────────────────────────


def _apply_one(
    path: Path,
    owner: str,
    group: str,
    mode: int,
    *,
    chown: Callable[[str, int, int], None],
    chmod: Callable[[str, int], None],
) -> None:
    """Resolve owner/group to ids and apply chown + chmod to one path."""
    uid = pwd.getpwnam(owner).pw_uid
    gid = grp.getgrnam(group).gr_gid
    chown(str(path), uid, gid)
    chmod(str(path), mode)


def commit(
    plan_: OwnershipPlan,
    *,
    chown: Callable[[str, int, int], None] = os.chown,
    chmod: Callable[[str, int], None] = os.chmod,
) -> list[Path]:
    """Apply every drifted diff, rolling back on failure. Returns paths changed.

    Mirrors ``SlotConfigStore.commit``: each path is chowned+chmodded in order;
    if a later path fails, every already-applied path is restored to its
    ``before`` snapshot and the original exception re-raised — disk is never
    left half-reconciled. Absent paths are skipped (nothing to own).

    Requires privilege to chown to a different user; raises ``PermissionError``
    otherwise (the ``doctor perms --fix`` caller is root-gated, as today).
    """
    applied: list[PermDiff] = []
    for d in plan_.drifted:
        try:
            _apply_one(d.path, d.owner, d.group, d.mode, chown=chown, chmod=chmod)
        except BaseException:
            for prior in reversed(applied):
                b = prior.before
                if b.exists and b.owner and b.group and b.mode is not None:
                    with contextlib.suppress(OSError, KeyError):
                        _apply_one(b.path, b.owner, b.group, b.mode, chown=chown, chmod=chmod)
            raise
        applied.append(d)
    return [d.path for d in applied]


# ── audit (doctor perms) ──────────────────────────────────────────────────────


def audit_rows(plan_: OwnershipPlan) -> list[dict[str, str]]:
    """Render an :class:`OwnershipPlan` as ``doctor``-style audit rows.

    Uses the same ``ok`` / ``drift`` / ``absent`` status vocabulary as
    :func:`hal0.cli.doctor_commands.check_hermes_ownership` so the renderer is
    shared.
    """
    rows: list[dict[str, str]] = []
    for d in plan_.diffs:
        if not d.before.exists:
            rows.append(
                {
                    "path": str(d.path),
                    "label": d.role,
                    "status": "absent",
                    "detail": "not present",
                }
            )
            continue
        want = f"{d.owner}:{d.group} {d.mode:04o}"
        have = f"{d.before.owner or '?'}:{d.before.group or '?'} {(d.before.mode or 0):04o}"
        if d.changed:
            rows.append(
                {
                    "path": str(d.path),
                    "label": d.role,
                    "status": "drift",
                    "detail": f"is {have}, want {want}",
                }
            )
        else:
            rows.append({"path": str(d.path), "label": d.role, "status": "ok", "detail": have})
    return rows


__all__ = [
    "OwnershipPlan",
    "PermDiff",
    "PermObservation",
    "PermRow",
    "audit_rows",
    "commit",
    "observe",
    "ownership_table",
    "plan",
]
