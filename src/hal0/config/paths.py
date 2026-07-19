"""FHS-aligned path resolver for hal0.

All filesystem paths used by hal0 flow through this module.  The
HAL0_HOME environment variable overrides all roots for dev installs and
integration tests.

FHS layout (when HAL0_HOME is unset):
    /usr/lib/hal0/current/    — code (symlink to versioned dir)
    /etc/hal0/                — user-editable config (preserved on update)
    /var/lib/hal0/            — mutable runtime state (preserved on update)
    /var/log/hal0/            — optional log files (journald is primary)

HAL0_HOME layout (when HAL0_HOME=/some/path):
    $HAL0_HOME/usr-lib/       — code root
    $HAL0_HOME/etc/           — config root
    $HAL0_HOME/var-lib/       — state root
    $HAL0_HOME/var-log/       — log root

Port target: haloai lib/paths.py (adapted for hal0's FHS layout).
See PLAN.md §2 (filesystem layout) and PLAN.md §3 (module port plan).
"""

from __future__ import annotations

import os
from pathlib import Path


def _hal0_home() -> Path | None:
    """Return the HAL0_HOME override path, or None if unset."""
    val = os.environ.get("HAL0_HOME", "").strip()
    return Path(val) if val else None


def usr_lib() -> Path:
    """Return the hal0 code root.

    FHS: /usr/lib/hal0/current  (the current symlink)
    HAL0_HOME: $HAL0_HOME/usr-lib/hal0/current
    """
    home = _hal0_home()
    if home is not None:
        return home / "usr-lib" / "hal0" / "current"
    return Path("/usr/lib/hal0/current")


def lib() -> Path:
    """Return the hal0 shipped-tree root (the whole ``/usr/lib/hal0`` dir).

    Distinct from :func:`usr_lib` (which returns the ``current`` version
    symlink): this is the PARENT that also holds every versioned release dir,
    the ``bin/`` wrapper seams (``hal0-agentenv``, ``hal0-benchctl``,
    ``hal0-systemctl``), and the guards/hooks/bench trees — the whole
    read-only, root-owned shipped surface (P3-perms, ``OwnershipStore``).

    FHS: /usr/lib/hal0
    HAL0_HOME: $HAL0_HOME/usr-lib/hal0
    """
    home = _hal0_home()
    if home is not None:
        return home / "usr-lib" / "hal0"
    return Path("/usr/lib/hal0")


def etc() -> Path:
    """Return the hal0 config root (/etc/hal0 or $HAL0_HOME/etc/hal0).

    Files under this path are preserved across updates and uninstall
    (unless --purge is passed).
    """
    home = _hal0_home()
    if home is not None:
        return home / "etc" / "hal0"
    return Path("/etc/hal0")


def var_lib() -> Path:
    """Return the hal0 runtime state root (/var/lib/hal0 or $HAL0_HOME/var-lib/hal0).

    Preserved across updates.  Survives uninstall when --keep-data is passed.
    """
    home = _hal0_home()
    if home is not None:
        return home / "var-lib" / "hal0"
    return Path("/var/lib/hal0")


def var_log() -> Path:
    """Return the hal0 log directory (/var/log/hal0 or $HAL0_HOME/var-log/hal0).

    journald is the primary log sink; this directory is for optional
    supplementary files (e.g. installer transcript).
    """
    home = _hal0_home()
    if home is not None:
        return home / "var-log" / "hal0"
    return Path("/var/log/hal0")


# ── Derived paths ──────────────────────────────────────────────────────────────
# These functions build on the four roots above.  Using functions (rather than
# module-level constants) means HAL0_HOME changes during tests are always
# reflected.


def slots_config_dir() -> Path:
    """Return the slot config directory (/etc/hal0/slots/)."""
    return etc() / "slots"


def registry_dir() -> Path:
    """Return the model registry directory (/var/lib/hal0/registry/)."""
    return var_lib() / "registry"


def agents_config_dir() -> Path:
    """Return the per-agent allow-list config directory.

    Each bundled or user-added agent gets one TOML at
    ``/etc/hal0/agents/<name>.toml`` carrying its workspace path,
    enabled MCP servers, per-server auth, and the three-tier tool
    classification (allow / gated / blocked).
    """
    return etc() / "agents"


def agent_workspace_dir(agent_name: str) -> Path:
    """Return the filesystem sandbox root for a bundled agent.

    The agent driver chroots/bind-mounts the agent process
    to this path; writes outside require approval. Each agent
    gets its own subtree so a malicious / buggy agent can't poke at
    another's workspace.
    """
    return var_lib() / "agents" / agent_name / "workspace"


def models_dir() -> Path:
    """Return the default model cache directory (/var/lib/hal0/models/)."""
    return var_lib() / "models"


def activity_db() -> Path:
    """Return the durable activity/audit store path (/var/lib/hal0/activity.db).

    SQLite file (plus its -wal/-shm siblings) holding the audit trail of
    every config-mutating user action and system state change. Preserved
    across updates like the rest of ``var_lib()``. Under HAL0_HOME it lands
    in the tmp tree, so tests are auto-isolated.
    """
    return var_lib() / "activity.db"


def db_path() -> Path:
    """Return the primary hal0 SQLite database path (/var/lib/hal0/hal0.db).

    The ``db/`` foundation's single embedded-DB substrate (see
    ``hal0.db.connection``): the model registry (ML-1), and later
    PortAuthority/metrics/runtime-state tables, all live in this one file.
    Same Runtime tier as :func:`activity_db` — preserved across updates and
    survives uninstall with ``--keep-data``. Under HAL0_HOME it lands in the
    tmp tree, so tests are auto-isolated.
    """
    return var_lib() / "hal0.db"


#: Conventional external model-store mount and the historic default for slot
#: container bind-mounts. Most hal0 deployments target an NFS / fast-disk
#: mount here; overridable per-install via ``[models].store`` or the
#: ``HAL0_MODEL_STORE`` env var (see :func:`model_store_root`).
DEFAULT_MODEL_STORE = "/mnt/ai-models"

#: ``statfs.f_type`` magic for an NFS mount (``NFS_SUPER_MAGIC``). A Quadlet
#: ``Volume=`` on an NFS source must OMIT the SELinux ``:z``/``:Z`` relabel —
#: ``chcon`` is ENOTSUP on NFS and podman aborts the bind (rework §23.3).
_NFS_SUPER_MAGIC = 0x6969


def is_nfs(path: str | Path) -> bool:
    """Return True iff *path* lives on an NFS mount.

    Used by :meth:`hal0.providers.base.Mount.render_quadlet` to decide whether a
    slot bind mount may carry the SELinux ``:z`` relabel — NFS can't be
    relabelled (``chcon`` → ENOTSUP), and podman refuses the bind if we ask.

    Resolves the longest ``/proc/mounts`` prefix covering *path* and checks its
    filesystem type (``nfs`` / ``nfs4``). Fail-soft to ``False`` (assume a
    relabel-capable local FS) on any error: a missing/unreadable ``/proc/mounts``
    (non-Linux, CI, container-in-container) must never be the thing that decides
    a mount is NFS. Walking ``/proc/mounts`` rather than issuing the raw
    ``statfs(2)`` (which Python's stdlib doesn't expose without ctypes) keeps the
    check dependency-free and unit-testable, and matches ``f_type == 0x6969``
    for every real NFS mount.
    """
    try:
        target = os.path.realpath(str(path))
    except OSError:
        return False
    best_prefix = ""
    best_fstype = ""
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point, fstype = parts[1], parts[2]
                # Longest mount point that is a path-prefix of target wins.
                if (
                    target == mount_point or target.startswith(mount_point.rstrip("/") + "/")
                ) and len(mount_point) >= len(best_prefix):
                    best_prefix = mount_point
                    best_fstype = fstype
    except OSError:
        return False
    return best_fstype.startswith("nfs")


def model_store_root() -> str:
    """Resolve the model-store directory that slot containers bind-mount.

    THIN SHIM (ML-3 / plan §7.1e) delegating to
    :func:`hal0.config.store.store_root` — the ONE resolver now shared,
    identically, by every reader (this function; provider container mounts)
    AND every writer (the pull engine). Historically this function and
    ``registry.pull._pull_root()`` had different precedence and different
    fallback defaults (``/mnt/ai-models`` here vs ``models_dir()`` there),
    which let a pull land where the container's read-only mount didn't
    reach — the "🔴 dual-resolver store trap" (plan §7.1e defect #1). That
    divergence is gone: both now call :func:`hal0.config.store.store_root`.

    NOTE the default fallback CHANGED as part of that fix: it used to be
    :data:`DEFAULT_MODEL_STORE` (``/mnt/ai-models``); it is now
    ``paths.models_dir()`` (``/var/lib/hal0/models``), matching the write
    side. :data:`DEFAULT_MODEL_STORE` is kept only as the conventional/
    historic constant a few call sites still reference.

    Kept as a ``str``-returning function (not repointed to return a
    ``Path``) for signature compatibility with the ~dozen existing callers.
    """
    from hal0.config.store import store_root

    return str(store_root())


def _covers(ancestor: str, descendant: str) -> bool:
    """True iff *ancestor* is *descendant* or a path-ancestor of it.

    Purely lexical (``normpath`` + component compare) — no symlink resolution,
    so an identical-path bind mount stays identical-path. ``/a`` covers ``/a``
    and ``/a/b`` but not ``/ab``.
    """
    a = os.path.normpath(str(ancestor))
    d = os.path.normpath(str(descendant))
    if a == d:
        return True
    try:
        Path(d).relative_to(Path(a))
        return True
    except ValueError:
        return False


def model_mount_roots() -> list[str]:
    """Model-store roots a slot container must bind-mount, deduped.

    The renderer historically mounted only :func:`model_store_root` (the
    effective ``[models].store``). But registry file paths are absolute and can
    live under ``[models].pull_root`` — a *distinct* external tree (e.g.
    ``/mnt/ai-models``) — even when ``store`` points elsewhere. Mounting only
    ``store`` then leaves the model file unreachable in-container and the slot
    flaps ``error``↔``warming`` (rework O25). This returns every configured
    model root (effective store + ``pull_root``) so both are reachable.

    Deduped lexically: an exact duplicate, or a root nested under another kept
    root, collapses to the covering ancestor — ``store==pull_root`` renders ONE
    mount, and a nested pair renders only the outer root.
    """
    roots: list[str] = [model_store_root()]
    try:
        from hal0.config.loader import load_hal0_config

        pull_root = (load_hal0_config().models.pull_root or "").strip()
        if pull_root:
            roots.append(pull_root)
    except Exception:
        pass

    # Normalise + exact-dedup, order-preserving (normpath collapses trailing
    # slashes so a mutual-cover tie between "/a" and "/a/" can't drop both).
    seen: list[str] = []
    for r in roots:
        s = str(r).strip()
        if not s:
            continue
        s = os.path.normpath(s)
        if s not in seen:
            seen.append(s)

    # Drop any root covered by a *different* kept root (equal or nested).
    result: list[str] = []
    for r in seen:
        if any(other != r and _covers(other, r) for other in seen):
            continue
        result.append(r)
    return result


def default_flm_models_dir() -> str:
    """Return FLM's default model cache (/var/lib/hal0/.config/flm/models).

    The FLM binary hardcodes ``~/.config/flm/models`` (not configurable in
    FLM itself); the hal0 service HOME is ``var_lib()``, so this is both the
    default store and the in-container mount target for NPU slots.
    """
    return str(var_lib() / ".config" / "flm" / "models")


def flm_models_dir() -> str:
    """Resolve the FLM (NPU) model-store directory.

    Single source of truth shared by the FLM container mount, the host
    ``flm`` probe/pull bookkeeping, and the installer, so the directory the
    NPU slot bind-mounts can never drift from where pulls land. Precedence
    mirrors :func:`model_store_root`:

      1. ``HAL0_FLM_MODELS_DIR`` env var — explicit operator / CI override,
      2. ``[models].flm_store`` from hal0.toml — the persistent, settings-
         editable field (``ModelsConfig.flm_store``),
      3. :func:`default_flm_models_dir` — FLM's own ``~/.config/flm/models``
         cache under the hal0 service HOME.

    Operators relocating the store off the root filesystem (e.g. onto
    ``/mnt/ai-models/flm/models``) should prefer the TOML field: unlike the
    env var it survives api.env rewrites and shows up in config tooling.
    Config is read lazily so provider code can call this without an import
    cycle, degrading to the default during early bootstrap.
    """
    env = os.environ.get("HAL0_FLM_MODELS_DIR", "").strip()
    if env:
        return env
    try:
        from hal0.config.loader import load_hal0_config

        store = (load_hal0_config().models.flm_store or "").strip()
        if store:
            return store
    except Exception:
        pass
    return default_flm_models_dir()


def slot_data_dir(slot_name: str) -> Path:
    """Return the per-slot working directory (/var/lib/hal0/slots/<name>/)."""
    return var_lib() / "slots" / slot_name


def openwebui_data_dir() -> Path:
    """Return the OpenWebUI state directory (/var/lib/hal0/openwebui/)."""
    return var_lib() / "openwebui"


def hardware_json() -> Path:
    """Return the hardware probe result path (/etc/hal0/hardware.json)."""
    return etc() / "hardware.json"


def openwebui_env() -> Path:
    """Return the OpenWebUI env file path (/etc/hal0/openwebui.env)."""
    return etc() / "openwebui.env"


def hal0_toml() -> Path:
    """Return the top-level config file path (/etc/hal0/hal0.toml)."""
    return etc() / "hal0.toml"


def first_run_lock() -> Path:
    """Return the first-run claim lockfile path.

    The lockfile is dropped by ``installer/install.sh`` on a fresh
    install and contains a single-use OTP (UUID hex) that the wizard
    presents back to the API to claim ownership before any password is
    set. Once the wizard finishes and the operator's password is set,
    the auth surface uses cookies + Bearer tokens and the lockfile is
    deleted.

    Location: ``$HAL0_HOME/var-lib/hal0/.first-run.lock`` (or
    ``/var/lib/hal0/.first-run.lock`` in production). Lives alongside
    ``.first_run_done`` so a single ``rm -rf /var/lib/hal0`` clears
    both. Mode 0600 — the OTP is the key to first-run claim, so it
    must not be world-readable.

    See FINDINGS.md §28 (lockfile consumption) and §36 (the auth-on-
    by-default flip this lockfile bridges).
    """
    return var_lib() / ".first-run.lock"


# HAL0-SUNSET: v1.0.0 — picker deferred; marker unwired.
def bundle_chosen_marker() -> Path:
    """Return the bundle-picker completion marker path.

    Dropped by ``POST /api/bundles/{name}`` (or
    ``GET /api/bundles/skip``) once the operator has engaged the first-
    run bundle picker. The dashboard reads this to decide
    whether to render the picker or the regular dashboard on load.

    Location: ``$HAL0_HOME/var-lib/hal0/.bundle-chosen`` (or
    ``/var/lib/hal0/.bundle-chosen`` in production). Lives alongside
    ``.first_run_done`` so a single ``rm -rf /var/lib/hal0`` resets
    both. Contains a JSON blob with the picked tier name + npu opt-in
    flag + ISO timestamp; treat as advisory not authoritative — the
    canonical record of selections is ``capabilities.toml``.
    """
    return var_lib() / ".bundle-chosen"


def profiles_toml() -> Path:
    """Return the profile catalog path (/etc/hal0/profiles.toml).

    The file is optional — :func:`hal0.config.loader.load_profiles_config`
    returns the built-in seed profiles when it is absent.

    FHS:       /etc/hal0/profiles.toml
    HAL0_HOME: $HAL0_HOME/etc/hal0/profiles.toml
    """
    return etc() / "profiles.toml"


def stacks_toml() -> Path:
    """Return the stack catalog path (/etc/hal0/stacks.toml).

    The file is optional — :func:`hal0.config.loader.load_stacks_config`
    returns the built-in seed stacks (empty until they ship) when absent.

    FHS:       /etc/hal0/stacks.toml
    HAL0_HOME: $HAL0_HOME/etc/hal0/stacks.toml
    """
    return etc() / "stacks.toml"


def stacks_state_path() -> Path:
    """Return the active-stack pointer path (/var/lib/hal0/stacks/state.json).

    Records which stack is currently applied + a content hash for drift
    detection. HAL0_HOME-aware via :func:`var_lib`.
    """
    return var_lib() / "stacks" / "state.json"


def manifest_json() -> Path:
    """Return the release manifest path.

    The manifest pins toolbox image digests per hal0 release;
    `scripts/update-toolbox-digests.sh` refreshes them from ghcr.io
    before a release (see PLAN.md §12). At runtime we prefer the
    installed copy under /etc, falling back to the in-tree manifest at
    the source root for dev installs.

    FHS:        /etc/hal0/manifest.json
    HAL0_HOME:  $HAL0_HOME/etc/hal0/manifest.json
    Source dev: <repo>/manifest.json (looked up by the loader)
    """
    return etc() / "manifest.json"
