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
  - SYMLINKS ARE NEVER FOLLOWED, AND NEVER WRITTEN TO (#1739). ``os.chown`` /
    ``os.chmod`` dereference by default, so a symlink matched by a row would
    have rewritten its TARGET's owner+mode — a file that by definition lives
    OUTSIDE hal0's declared tree. ``hal0 migrate model-layout`` plants exactly
    such links (``/var/lib/hal0/models/... -> /mnt/ai-models/...``) under the
    recursive ``models/`` row, so every install/upgrade re-chowned the
    operator's real model store. A link target's ownership is not hal0's
    concern; symlinks are therefore dropped during glob expansion, never
    counted as drift, and hard-refused in :func:`_apply_one`.
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


# ── umask-proof directory creation (#1896) ────────────────────────────────────

#: The mode every SHARED hal0 state directory is declared at below: setgid so
#: children inherit the ``hal0`` group, group-writable so any hal0-group member
#: (the daemon, a root-run CLI, the wrapper seams) can write the same tree.
SHARED_DIR_MODE = 0o2775


def ensure_shared_dir(path: Path | str, *, mode: int = SHARED_DIR_MODE) -> Path:
    """``mkdir -p`` whose result the process umask cannot narrow (#1896).

    ``Path.mkdir(mode=...)`` masks its mode with the umask, and the setgid bit
    a parent passes down only carries GROUP OWNERSHIP, not the group-write bit.
    So a daemon under the shipped ``UMask=0022`` births ``2755`` everywhere the
    table declares ``2775`` — drift that regenerates after every ``--fix``,
    once per slot loaded and once per model pulled. Creating the dir and then
    ``chmod``-ing it is the only way to land the declared mode.

    Only the components THIS call creates are chmod'ed. A dir that already
    exists is left exactly as it is, so a lazy ``mkdir(parents=True)`` passing
    through a deliberately tighter parent (``secrets/`` 0700, ``agents/`` 0711)
    can never widen it.

    The ``chmod`` is CONTAINED: it only ever touches a path that resolves
    inside one of hal0's own roots (:func:`_shared_dir_roots`). Callers derive
    these paths from request-supplied ids (``persist_pull_job`` ->
    ``pull_job_file(job.model_id)``, a slot's state dir, ...), and while each
    caller sanitises its own component, a mode-changing sink must not depend on
    that being true forever. Same shape as
    :func:`hal0.config.store.assert_under_store` with ``severity="warn"``: an
    out-of-tree path is still created (behaviour preserved for callers pointed
    at an operator's own store) but never re-moded.

    Fail-soft on ``chmod``, like :func:`hal0.config.store.finalize_perms`: an
    operator's model store may live on an NFS export that refuses the call, and
    a permissions nice-to-have must never break a pull or a slot load.
    """
    path = Path(path)
    # Deepest-first list of the components that do NOT exist yet.
    missing: list[Path] = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent

    path.mkdir(parents=True, exist_ok=True)

    roots = _shared_dir_roots()
    for created in reversed(missing):
        resolved = created.resolve()
        if not any(_is_within(resolved, root) for root in roots):
            log.warning(
                "perms.ensure_shared_dir_outside_hal0_roots path=%s (created, not chmod'ed)",
                resolved,
            )
            continue
        try:
            os.chmod(resolved, mode)
        except OSError as exc:  # pragma: no cover - exercised via monkeypatch
            log.debug("perms.ensure_shared_dir_chmod_failed path=%s error=%s", resolved, exc)
    return path


def _is_within(candidate: Path, root: Path) -> bool:
    """True when ``candidate`` (already resolved) sits at or under ``root``."""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _shared_dir_roots() -> tuple[Path, ...]:
    """Resolved roots :func:`ensure_shared_dir` is allowed to ``chmod`` inside.

    hal0's own state + config trees, plus whatever model-store mounts the
    operator configured (a pull stages and installs weights there). Root
    discovery is best-effort: a config-load failure must degrade to the two
    built-in trees, never raise into a pull.
    """
    roots: list[Path] = []
    for probe in (paths.var_lib, paths.etc, paths.var_log):
        with contextlib.suppress(OSError, ValueError):
            roots.append(probe().resolve())
    try:
        roots.extend(Path(m).resolve() for m in paths.model_mount_roots())
    except Exception as exc:  # config load is untrusted here; never raise into a pull
        log.debug("perms.shared_dir_roots_model_mounts_failed error=%s", exc)
    return tuple(roots)


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
        # The seam wrappers themselves (#1465). They were named in the comment
        # above but had no row, so `doctor perms --fix` could not repair a
        # wrapper whose mode or owner had drifted — and a wrapper the service
        # account can write is a privilege hole, not a cosmetic nit. Optional:
        # a dev/HAL0_HOME tree has no bin/ at all. `hal0 doctor all`'s
        # privileged-seam row additionally proves the matching
        # /etc/sudoers.d grants (which live outside every hal0 prefix and so
        # cannot be a PermRow) actually work.
        PermRow(
            paths.lib() / "bin",
            "root",
            "root",
            0o755,
            glob="hal0-*",
            child_mode=0o755,
            role="seam wrappers (hal0-systemctl, hal0-update, …)",
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
        # api.env carries live tokens (HF/provider keys, HERMES_SESSION_TOKEN)
        # and, after a rotation, HAL0_ADMIN_KEY/HAL0_CLIENT_KEY. It is owner-only
        # (#1466 — this row said 0644 behind a FIXME(phase4), so the engine whose
        # job is converging the filesystem re-flattened every tightening the
        # dashboard writer and the key rotation applied). One constant, shared
        # with both of those and with installer/install.sh.
        PermRow(etc / "api.env", etc_owner, etc_group, paths.API_ENV_MODE, role="api.env"),
        # update.conf is the ROOT-owned releases-URL override (#1750). It is
        # pinned root:root under the flip too, on purpose: the privileged
        # hal0-update wrapper reads it as root and lets it name a file:// URL,
        # which api.env (service-owned once flipped) is not allowed to do —
        # otherwise a process compromised as hal0 would choose the manifest
        # root fetches. Optional: only boxes with a custom release source have
        # one.
        PermRow(etc / "update.conf", "root", "root", 0o644, role="update.conf"),
        PermRow(etc / "capabilities.toml", etc_owner, etc_group, 0o600, role="capabilities.toml"),
        # upstreams.toml holds no credential VALUES — `auth_value_env` names the
        # variable and the registry resolves it from the process environment at
        # request time (upstreams/registry.py). Its 0644 was therefore metadata
        # disclosure, not credential exposure: the box's provider/endpoint
        # inventory readable by every local account. 0640 keeps every real
        # consumer working (hal0-api, hal0-agent@* and the CLI all run as
        # hal0/root, group hal0) and drops the world bit (ADR-0002, Option C).
        PermRow(
            etc / "upstreams.toml",
            etc_owner,
            etc_group,
            paths.UPSTREAMS_TOML_MODE,
            role="upstreams.toml",
        ),
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
        # agent-skills/ — the bundled-skill MIRROR (#1828). Unlike agents/ next
        # to it, this dir is WRITTEN by the provisioner: context_link's
        # ``_mirror_bundled_skills`` symlinks each /usr/share/hal0/skills entry
        # into it, and since the §7.4 privilege drop (5faef6d6) that writer is
        # `hal0 agent bootstrap hermes` re-exec'd as the hal0 user. install.sh
        # creates the dir as root (root:root 0755) and the /etc/hal0 root row
        # above is non-recursive, so nothing ever handed it to hal0 — all five
        # symlinks got EACCES on 100% of fresh installs and Hermes came up with
        # an empty skill manifest.
        #
        # Narrowest row that fixes it: the DIRECTORY only, group-writable to the
        # already-shared hal0 group, mirroring the /etc/hal0 config-root row
        # (2775 — setgid so links planted by any hal0-group member keep the
        # shared group). Deliberately NOT: world-writable (0777 would let any
        # local user plant a skill Hermes then executes), recursive/globbed (the
        # contents are symlinks, which the store refuses to write anyway —
        # #1739), and NOT a change to any root-owned parent (/etc/hal0 is
        # already hal0:hal0 2775 under the flip; nothing above it moves).
        # Root-era (service_user="root") keeps root:root 0755 byte-for-byte —
        # that era never drops privileges, so it never had the bug.
        PermRow(
            etc / "agent-skills",
            etc_owner,
            etc_group,
            etc_dir_mode,
            role="agent-skills/ (bundled-skill mirror)",
        ),
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
        # hal0.db — the PRIMARY database (``paths.db_path()`` ->
        # ``var_lib/"hal0.db"``): the model registry today, plus metrics and
        # runtime-state tables later. Distinct from the ``registry/hal0.db``
        # row further down, which only ever matches a
        # ``SqliteModelRegistry(registry_dir=...)`` override — a test/dev
        # isolation path that production never takes.
        #
        # This row was missing entirely (#1546). install.sh creates the file
        # as root (the schema migrator runs during install, before the
        # User=hal0 daemon first starts), the ownership pass then reconciled
        # 37 paths without touching it, and ``doctor perms`` reported the box
        # clean — while the daemon could not write the registry. Every model
        # pull downloaded in full and only then failed with "attempt to write
        # a readonly database", leaving orphaned weights on disk. Same shape
        # as #1466: the check agreed with the bug.
        #
        # 0644 is sqlite3.connect's birth mode and is functionally sufficient
        # — a single writer (the hal0 daemon). The -wal/-shm siblings get
        # their own rows because ``hal0.db.connection`` switches every
        # connection to WAL journaling: SQLite must write those two as well,
        # and they are born from whichever process opens the database first,
        # which during install is root. Covering the database but not its
        # journal would leave the identical failure reachable by a side door.
        PermRow(
            var_lib / "hal0.db",
            state_owner,
            service_group,
            0o644,
            role="hal0.db (primary database)",
        ),
        PermRow(
            var_lib / "hal0.db-wal",
            state_owner,
            service_group,
            0o644,
            role="hal0.db-wal (WAL journal)",
        ),
        PermRow(
            var_lib / "hal0.db-shm",
            state_owner,
            service_group,
            0o644,
            role="hal0.db-shm (WAL shared-memory index)",
        ),
        # activity.db — the durable audit trail (``paths.activity_db()`` ->
        # ``var_lib/"activity.db"``, :class:`hal0.activity.AuditStore`). Same
        # O13 birth-ownership class as ``hal0.db`` above: no row existed, so a
        # box where the file's first writer was root (an install-time /
        # doctor-invoked path) left it unreadable/unwritable to the
        # User=hal0 daemon with no way for ``doctor perms --fix`` to heal it.
        # ``AuditStore.__init__`` sets ``PRAGMA journal_mode=WAL``, so the
        # ``-wal``/``-shm`` siblings need their own rows for the identical
        # reason the ``hal0.db`` journal siblings do above.
        PermRow(
            var_lib / "activity.db",
            state_owner,
            service_group,
            0o644,
            role="activity.db (durable audit trail)",
        ),
        PermRow(
            var_lib / "activity.db-wal",
            state_owner,
            service_group,
            0o644,
            role="activity.db-wal (WAL journal)",
        ),
        PermRow(
            var_lib / "activity.db-shm",
            state_owner,
            service_group,
            0o644,
            role="activity.db-shm (WAL shared-memory index)",
        ),
        # model-pull-jobs/ — the durable pull-job snapshot store
        # (``hal0.registry.pull._pull_jobs_dir()`` -> ``var_lib/"model-pull-jobs"``,
        # the #626/#MR-1 restart-survival fallback). Same O13 birth-ownership
        # class as ``models/`` above: the installer's root-run brain-model
        # pull (bundle-tier auto-pull) calls ``persist_pull_job`` -> lazy
        # ``path.parent.mkdir(parents=True, exist_ok=True)`` as root on a
        # fresh install, so the dir is born ``root:root 0755`` with a
        # root-only ``0600`` snapshot inside. ``hal0-api`` (``User=hal0``)
        # then fails every pull-job snapshot write
        # (``model.pull_job_persist_failed``, WARNING-only fail-soft — a
        # write failure here must never break the pull itself) and cannot
        # read the existing one, so ``GET /api/models/<id>/pull/status``
        # 404s after a restart even though the pull succeeded (#1895). No
        # row meant ``doctor perms --fix`` could not heal it either.
        # ``persist_pull_job`` writes each snapshot via ``tempfile.mkstemp``
        # + ``os.replace`` — mkstemp always creates its tempfile ``0600``
        # regardless of umask, matching the ``registry.toml`` /
        # ``slots/*/state.json`` convention above.
        PermRow(
            var_lib / "model-pull-jobs",
            state_owner,
            service_group,
            0o2775,
            glob="*.json",
            child_mode=0o600,
            optional=False,
            role="model-pull-jobs/ (durable pull-job snapshots)",
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
        #   * hal0.db — NOT the production database. This path is only ever
        #     reached by ``SqliteModelRegistry(registry_dir=...)``, whose
        #     db_path override puts the file at ``registry_dir/hal0.db`` for
        #     test/dev isolation. Production falls through to
        #     ``paths.db_path()`` -> ``var_lib/"hal0.db"``, covered by its own
        #     row above — do not read this row as covering the real registry
        #     (that misreading is #1546). Kept so a box that did take the
        #     override path still heals. Born 0644 via sqlite3.connect;
        #     single writer (the hal0 daemon), so 0644 is both the birth mode
        #     and functionally sufficient.
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
        # models/ — the default model-store directory (paths.models_dir()),
        # born root:root 0755 by the SAME install.sh mkdir as slots/ and
        # registry/ above (O13 class). No row existed for it: `doctor perms
        # --fix` could not heal it, and a default-store pull
        # (registry/pull_jobs.py:222 writes ``models_dir()/<model_id>/<file>``
        # via a plain ``open(part, "wb")`` — born 0644 under the User=hal0
        # unit's default umask 0022, with intermediate dirs made on demand)
        # fails with PermissionError the moment the User=hal0 daemon tries to
        # create a subdir under a root:root parent. (Live boxes mostly point
        # [models].store at /mnt/ai-models instead, so this default path was
        # plausibly never exercised — r5-sync-assessment §6.2.) Recursive +
        # setgid 2775 like the slots/ row above so nested per-model dirs
        # inherit the shared hal0 group; child files get 0644 (matches the
        # plain-open() birth mode, not 0600 — weight files have no secrecy
        # requirement and are read by the slot container's bind-mounted view).
        PermRow(
            var_lib / "models",
            state_owner,
            service_group,
            0o2775,
            glob="*",
            child_mode=0o2775,
            child_file_mode=0o644,
            recursive=True,
            optional=False,
            role="models/ (default model store, recursive)",
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
        #
        # 0700, NOT 0755 (#1896). The table used to declare 0755 while the
        # shipped `hal0-agentenv` seam ran `install -d -m 0700` on the same two
        # dirs on every merge-secrets — two shipped components asserting
        # different truths, so the mode oscillated and `doctor perms` could
        # never be durably green. Reconciled toward the RESTRICTIVE side
        # because nothing outside root has business here: systemd reads the
        # EnvironmentFile as root, the agentenv seam runs as root via
        # /etc/sudoers.d, and every *.env inside is already 0600 root:root, so
        # 0700 costs no reader any access it actually had. What it removes is
        # agent-id ENUMERATION by any local user — small, but free (ADR-0002,
        # agent credential isolation). See known-issues.yaml
        # `doctor-perms-secrets-dir-0755-is-declared`, now superseded.
        PermRow(var_lib / "secrets", "root", "root", 0o700, role="secrets/"),
        # secrets/agents/ — per-agent secret .env files (written by the
        # hal0-agentenv seam, never by the service directly). Pinned root:root
        # like secrets/ itself; the dir mode is 0700 (root-only traverse+list,
        # matching the seam — see above), the per-agent .env files are 0600
        # (owner-read-only tokens), unchanged by the tightening.
        PermRow(
            var_lib / "secrets" / "agents",
            "root",
            "root",
            0o700,
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
    #: True when the path itself is a symlink. Observations are ``lstat``-based,
    #: so ``owner``/``group``/``mode`` describe the LINK, never its target
    #: (#1739). A symlink is never reconciled — see :attr:`PermDiff.changed`.
    is_symlink: bool = False


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
        is_symlink=stat_mod.S_ISLNK(st.st_mode),
    )


# ── plan (compute-only) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class PermDiff:
    """The declared target for one concrete path, plus its current observation.

    ``changed`` is true only when the path EXISTS, is not a symlink, and at
    least one of owner / group / mode differs from the declared target. An
    absent path is never "changed" (nothing to chown); it is surfaced
    separately as ``absent``. A SYMLINK is never "changed" either (#1739):
    reconciling it would mean ``chown``/``chmod`` on its target, which lives
    outside the declared tree; it is surfaced as ``symlink``.
    """

    path: Path
    before: PermObservation
    owner: str
    group: str
    mode: int
    role: str

    @property
    def changed(self) -> bool:
        if not self.before.exists or self.before.is_symlink:
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
    if child.is_dir() and not child.is_symlink():
        return dir_mode
    return row.child_file_mode if row.child_file_mode is not None else dir_mode


def _is_or_is_under_symlink(child: Path, base: Path) -> bool:
    """True when ``child`` is a symlink, or sits below one, relative to ``base``.

    Both cases mean "reconciling this path would write outside the declared
    tree" (#1739): the link itself dereferences on ``chown``/``chmod``, and a
    path found by walking THROUGH a symlinked directory is a real file in
    someone else's tree (e.g. ``/mnt/ai-models/...`` reached via the
    ``models/`` links planted by ``hal0 migrate model-layout``). Checked
    explicitly rather than relying on ``Path.rglob``'s no-follow behaviour, so
    the guarantee does not depend on the pathlib version.
    """
    if child.is_symlink():
        return True
    try:
        rel = child.relative_to(base)
    except ValueError:  # pragma: no cover - matches are always under base
        return False
    cur = base
    for part in rel.parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


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
        # #1739: a symlink (or anything reached through one) is not hal0's to
        # own — drop it from the plan entirely so it can never be chowned.
        if _is_or_is_under_symlink(child, row.target):
            continue
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
    islink: Callable[[str], bool] = os.path.islink,
) -> None:
    """Resolve owner/group to ids and apply chown + chmod to one path.

    Hard-refuses symlinks (#1739). ``os.chown``/``os.chmod`` follow symlinks
    (and Linux has no ``lchmod``), so applying to a link would rewrite its
    TARGET outside the declared tree. Symlinks are already dropped in
    :func:`_expand_row` and never marked ``changed``; this is the last-line
    guard that also covers the rollback path and any future caller.
    """
    if islink(str(path)):
        return
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
        if d.before.is_symlink:
            # #1739: never reconciled — reported so the audit stays honest
            # about what it deliberately left alone.
            rows.append(
                {
                    "path": str(d.path),
                    "label": d.role,
                    "status": "symlink",
                    "detail": "symlink, not followed",
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
    "SHARED_DIR_MODE",
    "OwnershipPlan",
    "PermDiff",
    "PermObservation",
    "PermRow",
    "audit_rows",
    "commit",
    "ensure_shared_dir",
    "observe",
    "ownership_table",
    "plan",
]
