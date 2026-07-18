"""Unified model-store resolver — read == write, one precedence, one default.

Fixes the 🔴 dual-resolver store trap (plan §7.1e defect #1, seam S2):
historically ``config/paths.model_store_root()`` (used for container mounts)
and ``registry/pull._pull_root()`` (used for writing) had *different*
precedence and *different* fallback defaults, so an install that only set
``[models].pull_root`` (PR-#313) or hit a config-load error at bootstrap
would write model bytes to one root while the container mounted another —
a silent "model file not found" at slot launch. This module is now the
ONE place that decides "where do model files live", used identically by
every reader and every writer.

Precedence (identical to the old ``model_store_root()`` reader, minus the
diverging final fallback)::

    1. ``HAL0_MODEL_STORE`` env var           — explicit operator/CI override
    2. ``load_hal0_config().models.effective_store()``  — ``store`` or the
       superseded ``pull_root`` (see ``ModelsConfig.effective_store``)
    3. ``paths.models_dir()`` (``/var/lib/hal0/models``) — the aligned
       read+write default. NOTE: this is a deliberate change from the old
       reader's ``/mnt/ai-models`` default — that mismatch (vs the writer's
       ``models_dir()`` fallback) *was* the bug (plan §7.1e defect #1 case 3).

``config/paths.model_store_root()`` becomes a thin shim delegating to
:func:`store_root` (kept for the ~dozen existing callers that import the
``paths`` name); ``registry/pull.py``'s old ``_pull_root()`` collapses onto
the same function.

Layout (plan §7.1e Part b, HF-cache-shaped)::

    <store_root>/models--<org>--<repo>/snapshots/<revision>/<rel...>
    <store_root>/by-id/<model_id>              — symlink → the entry file

``by-id/<model_id>`` is the stable indirection a slot resolves through so a
revision bump (new snapshot dir) never requires editing a slot TOML — see
:func:`set_entry_pointer`.

``assert_under_store`` severity is intentionally SPLIT (plan §23.3a): a
*write* (new pull, new-launch path resolution) must fail fast on any
escape attempt (path/id injection guard); a resolve against an
*already-running* slot must never hard-fail (a live container must not
be killed by a sanity check) — it logs and returns the path unchanged.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from pathlib import Path
from typing import Literal

from hal0.config import paths
from hal0.errors import Hal0Error
from hal0.providers.base import Mount

log = logging.getLogger(__name__)

#: filesystem types (as reported by /proc/mounts) that are NFS.
_NFS_FSTYPES = frozenset({"nfs", "nfs4"})

#: Path-safety regex — mirrors registry/pull.py's historic ``_SANITISE_RE``.
#: Model ids / repo segments / revisions are all, in principle, operator or
#: upstream controlled; strip anything that could escape the store tree.
_SANITISE_RE = re.compile(r"[^A-Za-z0-9._-]+")

_BY_ID_DIRNAME = "by-id"


class StorePathEscape(Hal0Error):
    """A derived store path resolved outside the configured store root.

    Raised (fail-fast) on any write / new-launch path derivation; on an
    already-running-slot resolve the same condition is downgraded to a
    logged warning instead (see :func:`assert_under_store`).
    """

    code = "store.path_escape"
    status = 500


# ── the one resolver ─────────────────────────────────────────────────────


def store_root() -> Path:
    """Resolve the single model-store root — identical for reads and writes.

    See module docstring for precedence. Degrades to the default on any
    config-load failure (early bootstrap) rather than raising, exactly like
    the historic per-path resolvers did.
    """
    env = os.environ.get("HAL0_MODEL_STORE", "").strip()
    if env:
        return Path(env)
    try:
        from hal0.config.loader import load_hal0_config

        effective = (load_hal0_config().models.effective_store() or "").strip()
        if effective:
            return Path(effective)
    except Exception:
        pass
    return paths.models_dir()


def assert_under_store(
    p: Path | str,
    *,
    severity: Literal["fail", "warn"] = "fail",
) -> Path:
    """Require ``p`` to resolve inside :func:`store_root`.

    ``severity="fail"`` (the default — every write / new-launch path
    derivation) raises :class:`StorePathEscape`. ``severity="warn"`` (an
    already-running slot's resolve) logs and returns the resolved path
    unchanged instead — a live container must never be torn down by a
    sanity check (plan §23.3a).
    """
    resolved = Path(p).resolve()
    root = store_root().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        if severity == "warn":
            log.warning("store.path_escape_warn path=%s store_root=%s", resolved, root)
            return resolved
        raise StorePathEscape(
            f"path {resolved} escapes model store root {root}",
            details={"path": str(resolved), "store_root": str(root)},
        ) from None
    return resolved


# ── layout derivation (repo/revision-addressed) ──────────────────────────


def _sanitise(value: str) -> str:
    """Strip path-unsafe characters — shared shape with the historic pull
    engine's ``_sanitise_id`` so existing flat-layout dirs keep matching."""
    cleaned = _SANITISE_RE.sub("-", value).strip("-.") or "model"
    return cleaned


def repo_dirname(repo: str) -> str:
    """``org/repo`` → ``models--org--repo`` (HF local-cache shape).

    :func:`hal0.registry.detect._hf_repo_name_from_path` already parses this
    exact shape back out, and :mod:`hal0.registry.discover` already skips
    ``blobs/``/``.no_exist`` — the two sides of the HF-cache convention.
    """
    segments = [seg for seg in repo.strip("/").split("/") if seg]
    if not segments:
        return "models--" + _sanitise(repo)
    return "models--" + "--".join(_sanitise(seg) for seg in segments)


def model_dir(repo: str, revision: str) -> Path:
    """``<store_root>/models--<org>--<repo>/snapshots/<revision>``."""
    return store_root() / repo_dirname(repo) / "snapshots" / _sanitise(revision)


def file_dest(repo: str, revision: str, rel: str) -> Path:
    """Final on-disk destination for one file of a repo/revision fileset.

    Always passes through :func:`assert_under_store` (fail-fast) — this is
    the write path, so a crafted ``rel`` (``../../etc/passwd``) can never
    escape the store tree even though ``rel`` ultimately comes from an
    upstream (HF) tree listing.
    """
    # rel may carry subdirectories (quant-variant folders); keep them.
    rel_clean = rel.strip("/").replace("\\", "/")
    dest = model_dir(repo, revision) / rel_clean
    return assert_under_store(dest, severity="fail")


# ── by-id pointer (stable indirection across revision bumps) ────────────


def by_id_dir() -> Path:
    return store_root() / _BY_ID_DIRNAME


def entry_pointer(model_id: str) -> Path:
    """``<store_root>/by-id/<sane_id>`` — the stable pointer slots resolve
    through, so a revision bump (new snapshot dir) never touches a slot
    TOML (plan §7.1e Part d / seam S3)."""
    return by_id_dir() / _sanitise(model_id)


def set_entry_pointer(model_id: str, target: Path | str) -> Path:
    """Atomically (re)point ``by-id/<model_id>`` at ``target``.

    Symlink swap via a temp-symlink + ``os.replace`` — same-fs rename is
    atomic, so a concurrent reader through the pointer never observes a
    half-updated link. Publishing a new revision then flipping this
    pointer is the "atomic pointer flip" that makes an update-in-place
    safe for an already-running slot (plan §7.1e Part d).
    """
    pointer = entry_pointer(model_id)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    tmp = pointer.parent / f".{pointer.name}.tmp-{os.getpid()}"
    with contextlib.suppress(OSError):
        tmp.unlink(missing_ok=True)
    os.symlink(str(target), tmp)
    os.replace(tmp, pointer)
    return pointer


def resolve_entry_pointer(model_id: str) -> Path | None:
    """Return the file the ``by-id/<model_id>`` pointer targets, or ``None``."""
    pointer = entry_pointer(model_id)
    try:
        if pointer.is_symlink() or pointer.exists():
            return pointer.resolve()
    except OSError:
        return None
    return None


# ── NFS detection (SELinux relabel is unsupported there) ────────────────


def _fstype_from_proc_mounts(path: Path) -> str | None:
    """Best-effort: the fstype of the mount owning ``path``.

    Longest-matching-prefix lookup over ``/proc/mounts`` — the portable
    substitute for a ``statfs`` ``f_type`` check (no ctypes binding
    shipped). Absent ``/proc/mounts`` (non-Linux) → ``None`` (caller treats
    unknown as "not NFS", the historic behaviour).
    """
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    best_match = ""
    best_fstype: str | None = None
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mnt_point, fstype = parts[1], parts[2]
                owns_path = resolved == mnt_point or resolved.startswith(
                    mnt_point.rstrip("/") + "/"
                )
                if owns_path and len(mnt_point) >= len(best_match):
                    best_match = mnt_point
                    best_fstype = fstype
    except OSError:
        return None
    return best_fstype


def is_nfs_path(path: Path | str) -> bool:
    """True when ``path`` lives on an NFS mount (fstype ``nfs``/``nfs4``).

    Equivalent in intent to detecting ``statfs().f_type == 0x6969``
    (``NFS_SUPER_MAGIC``) — plan §23.3d. NFS relabel (``:z``/``:Z``) fails
    with ``chcon: Operation not supported`` there, so callers building a
    :class:`Mount` must omit the SELinux suffix entirely, not merely swap
    ``z``→``Z`` (both relabel).
    """
    return _fstype_from_proc_mounts(Path(path)) in _NFS_FSTYPES


def mount_for(
    source: str | Path,
    *,
    read_only: bool = True,
    target: str | Path | None = None,
) -> Mount:
    """Build the store :class:`Mount` — the one place providers get it from.

    Sets ``selinux="z"`` on a local filesystem (the historic behaviour,
    required on SELinux-enforcing hosts) and ``selinux=""`` on NFS (relabel
    unsupported there — plan §23.3d). ``target`` defaults to an
    identical-path bind (the existing container/kokoro/qwen3tts convention).
    """
    src = str(source)
    dst = str(target) if target is not None else src
    selinux = "" if is_nfs_path(source) else "z"
    return Mount(src, dst, read_only=read_only, selinux=selinux)


# ── store permissions (plan §7.1e#7 / §23.3d) ────────────────────────────

_FILE_MODE = 0o644
_DIR_MODE = 0o2775  # setgid so new children inherit the store group


def finalize_perms(path: Path | str) -> None:
    """Best-effort post-install permission fixup.

    Files land ``0644``; directories land ``02775`` (setgid, so files a
    *different* process later writes into the same tree inherit the store
    group — the ``ai-models`` shared-NFS-group class of bug). ``chown`` is
    deliberately NOT attempted here: under NFS root-squash a non-root
    writer can't change ownership anyway, and getting the right shared gid
    is an install-time/OwnershipStore concern, not this per-file call's
    job. Every failure is caught and logged, never raised — a permissions
    nice-to-have must never fail a pull or a GC pass.
    """
    p = Path(path)
    try:
        mode = _DIR_MODE if p.is_dir() else _FILE_MODE
        p.chmod(mode)
    except OSError as exc:
        log.debug("store.finalize_perms_failed path=%s error=%s", p, exc)


__all__ = [
    "StorePathEscape",
    "assert_under_store",
    "by_id_dir",
    "entry_pointer",
    "file_dest",
    "finalize_perms",
    "is_nfs_path",
    "model_dir",
    "mount_for",
    "repo_dirname",
    "resolve_entry_pointer",
    "set_entry_pointer",
    "store_root",
]
