"""FLMProvider — AMD NPU (XDNA2) inference backend.

FLM (Flexible Language Model) targets the AMD Strix Halo NPU. Optional —
only loaded on hardware where the NPU driver is present and the FLM
binary tree + toolbox image are available.

Capabilities: chat, embed, ASR multiplexed on one NPU. A single
``flm serve <chat-tag> --embed 1 --asr 1`` process serves
``/v1/chat/completions`` + ``/v1/embeddings`` + ``/v1/audio/transcriptions``
against three different models simultaneously (embed-gemma + whisper-v3
+ the chat tag). The NPU serializes execution, so multiprocess FLM
provides no parallelism gain — port the single-multiplex design from
haloai's lib/providers/flm.py.

# Hybrid container packaging:
#   - Image: hal0-toolbox-flm carries Ubuntu 24.04 + ffmpeg + libxrt-npu2
#     + libboost-program-options. Freely redistributable, ~250 MB.
#   - Host: FLM binary tree (bin/, lib/, share/, deps/) bind-mounted in
#     at the SAME absolute path inside the container so that
#     ``bin/xclbins -> /mnt/ai-models/flm-ubuntu/share/flm/xclbins``
#     (an absolute symlink) still resolves. Discovered while
#     containerising on haloai 2026-05-15.
#   - Models: FLM's own cache dir (``~/.config/flm/models/``) bind-mounted
#     to a hal0-managed dir so model downloads persist across container
#     restarts.
# See docs/handoff-2026-05-15-autonomous.md for the test that proved
# this layout (Validate / serve / embed all succeeded; chat is blocked
# only by whoever currently holds the NPU's hardware context, not by
# anything container-side).
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from typing import Any

import httpx

from hal0.errors import Hal0Error
from hal0.http_client import async_client
from hal0.providers.base import ContainerSpec, Provider
from hal0.runners import RUNNER_IMAGES

# ── Toolbox image ─────────────────────────────────────────────────────────────
# Sourced from the runner-image registry (§7.1b / ML-4) so the literal tag
# lives in exactly one place (hal0.runners.RUNNER_IMAGES["flm"].image).
# Kept as a module attribute for back-compat: api/routes/updater.py and
# tests/providers/test_flm.py both import this name directly.
_DEFAULT_FLM_IMAGE = RUNNER_IMAGES["flm"].image

# ── On-disk layout ────────────────────────────────────────────────────────────
# The toolbox image is self-contained: it bundles FLM at /opt/fastflowlm/
# (binary, libs, xclbins, share assets) and symlinks /usr/local/bin/flm
# at the binary. ENTRYPOINT runs the in-image flm via tini, so the
# container_spec only supplies the subcommand + args.
#
# Two distinct FLM install roots — kept clearly named so they never get
# confused again:
#   * _CONTAINER_FLM_ROOT — where the toolbox IMAGE bundles FLM (binary, libs,
#     xclbins, share assets). Used only to build the container's env
#     (FLM_CONFIG_PATH / LD_LIBRARY_PATH) in container_spec().
#   * _NATIVE_FLM_ROOT — where a HOST/native FLM tree lives for the non-
#     container fallback path used by start_cmd() (native systemd runs, tests,
#     debug shells). Container runs never reference it.
_CONTAINER_FLM_ROOT = "/opt/fastflowlm"
_NATIVE_FLM_ROOT = "/opt/hal0/flm-ubuntu"
# FLM's per-user model cache default lives in config.paths.default_flm_models_dir
# (bind-mounted writable so `flm pull` downloads survive container restarts).
# FLM hardcodes ~/.config/flm/models as its model cache. The container runs
# as the hal0 user with HOME=/var/lib/hal0, so the cache resolves to this
# path. The host source must be the SAME directory the host flm binary uses,
# otherwise flm reports every model as not-installed and hides them from
# the dashboard.

# ── Host FLM (catalog probe + pull) ─────────────────────────────────────────────
# The catalog probe and model pulls run the HOST flm binary directly — NOT the
# docker toolbox — so they use the exact binary + on-disk cache the NPU slot
# *serves* with. The toolbox had drifted to FLM v0.9.42 while the host serves
# v0.9.43: it (a) bind-mounted the empty `_DEFAULT_FLM_MODELS_DIR`, so every
# model reported installed=False and vanished from the dashboard (the UI hides
# not-installed models), and (b) emitted "model may not be compatible" warnings
# that broke JSON parsing once pointed at the real cache. Serving was already
# host-native; probe/pull now match it.
# See docs/superpowers/plans/2026-06-07-flm-host-probe-and-unified-model-storage.md.
#
# Run as the `hal0` user with HOME=/var/lib/hal0 so flm resolves its real cache
# at ~/.config/flm/models. All three are env-overridable for dev/test.
_HOST_FLM_BIN = os.environ.get("HAL0_FLM_BIN", "/usr/bin/flm")
_HOST_FLM_HOME = os.environ.get("HAL0_FLM_HOME", "/var/lib/hal0")
_HOST_FLM_USER = os.environ.get("HAL0_FLM_USER", "hal0")
# Real FLM cache (HOME/.config/flm/models), relocatable via [models].flm_store
# or HAL0_FLM_MODELS_DIR. Resolved lazily (not at import) so a TOML edit or a
# settings change is honoured without an api restart — the historic module-
# level constant froze the env var at import time and silently ignored config.


def _host_flm_models_dir() -> str:
    """FLM model store for probe/pull bookkeeping and the container mount.

    Delegates to :func:`hal0.config.paths.flm_models_dir` (env var >
    ``[models].flm_store`` > FLM's default HOME cache) so every consumer —
    serving container bind-mount, pull bookkeeping, installer — agrees on
    one directory.
    """
    from hal0.config import paths as _cfg_paths

    return _cfg_paths.flm_models_dir()


# uid the FLM toolbox container runs as — fixed by the image, NOT the host
# hal0 user's uid (an LXC host commonly maps hal0 to a different uid, which
# is exactly how a chown-hal0:hal0 store dir ends up unwritable for the
# container and `flm pull` dies with Permission denied on subdir creation).
_FLM_CONTAINER_UID = 1000


class FLMStoreLinkError(Hal0Error):
    """flm's hardcoded cache could not be pointed at the configured FLM store.

    Raised instead of letting a host ``flm pull`` continue into
    ``$HOME/.config/flm/models`` while the NPU slot mounts ``[models].flm_store``
    (#2446): that pull reports success and the slot never sees the weights.
    """

    code = "model.flm_store_unlinked"
    status = 500


_FLM_STORE_REPAIR_HINT = "run `sudo hal0 doctor perms --fix` to repair ownership and relink"


def _ensure_flm_models_dir(path: str) -> None:
    """Best-effort create the FLM store so the bind-mount source exists.

    A missing source dir makes podman exit 125 (``statfs ... no such file or
    directory``) — the classic failure after a reboot when the store lives on
    a mount that wasn't there yet or was recreated empty. Called at spec
    build time; the rendered unit additionally orders after the backing
    mount and re-runs mkdir at ExecStartPre for the reboot path.

    When running as root, ownership is set to the container uid (1000) with
    the hal0 group and mode 2775 so both the in-container FLM (uid 1000) and
    host-side ``flm pull`` (hal0 user via group + setgid) can write. As the
    service user (hal0-api runs ``User=hal0``) it cannot chown, but it still
    re-asserts 2775 on a dir it owns: ``makedirs`` masks the mode with the
    unit's ``UMask=0022`` and drops the group-write bit (#2446). ``sudo hal0
    doctor perms --fix`` hands such a dir to uid 1000. Never raises: a failure
    here surfaces later as the slot health probe, or as
    :class:`FLMStoreLinkError` from :func:`ensure_host_flm_store_link`.
    """
    try:
        os.makedirs(path, mode=0o2775, exist_ok=True)
        if os.geteuid() == 0:
            import grp as _grp

            try:
                gid = _grp.getgrnam(_HOST_FLM_USER).gr_gid
            except KeyError:
                gid = _FLM_CONTAINER_UID
            os.chown(path, _FLM_CONTAINER_UID, gid)
            os.chmod(path, 0o2775)
        elif os.stat(path).st_uid == os.geteuid():
            os.chmod(path, 0o2775)
    except OSError:
        pass


def _reconcile_flm_store_link() -> str:
    """Reconcile flm's hardcoded host cache with the resolved store; return the store.

    The host ``flm pull`` (and ``flm list``) always read/write
    ``$HOME/.config/flm/models`` — flm hardcodes it, with no dir flag or env
    override (confirmed via ``flm --help``). When the operator relocates the
    store via ``[models].flm_store`` / ``HAL0_FLM_MODELS_DIR``
    (:func:`~hal0.config.paths.flm_models_dir`), that path diverges from flm's
    default (:func:`~hal0.config.paths.default_flm_models_dir`), so a host pull
    silently lands weights on the default root-fs cache instead of the store —
    where the serving container (which bind-mounts the store at flm's hardcoded
    path) can't see them, and where the pull-progress poller (which watches the
    store) reads 0 bytes for the whole download.

    Make flm's default path a **symlink** to the store — the host analog of the
    container bind-mount — so one host pull lands in the store, progress tracks
    it, and serving finds it. When the default path is already a real directory
    with content (legacy / previously-mispulled weights), migrate its children
    into the store first (never clobbering existing store files), then replace
    it with the symlink.

    Idempotent: a no-op when the store IS the default, or when the link already
    points at the store. Raises :class:`FLMStoreLinkError` when the link cannot
    be made — a name present in both dirs, a file where the dir should be, or
    an ``OSError`` such as a root-owned ``.config/flm`` under ``User=hal0``
    (#2446). Nothing is deleted on any of those paths. Raising stops the pull
    before it can write weights where the slot never looks; the old behaviour
    (log ``flm.store_link_failed`` and continue) reported success for a model
    the slot could not load.

    Not for the async event loop's thread: the one-time migration can copy
    multi-GB weights across filesystems. Callers on the loop must offload it
    (e.g. ``asyncio.to_thread``).
    """
    import logging
    import shutil
    from pathlib import Path

    from hal0.config.paths import default_flm_models_dir

    log = logging.getLogger(__name__)
    store = _host_flm_models_dir()
    default = default_flm_models_dir()
    store_p = Path(store)
    default_p = Path(default)
    details = {"store": store, "default": default}

    # Default box: flm already writes to the store — nothing to reconcile.
    if os.path.normpath(store) == os.path.normpath(default):
        return store

    try:
        _ensure_flm_models_dir(store)
        if not store_p.is_dir():
            raise FLMStoreLinkError(
                f"FLM store {store} does not exist and this user cannot create it. "
                f"Create it as root (install -d -o {_FLM_CONTAINER_UID} -g {_HOST_FLM_USER} "
                f"-m 2775 {store}) or point [models].flm_store at a writable path.",
                details=details,
            )

        # Already a symlink → repoint only if it aims elsewhere.
        if default_p.is_symlink():
            if os.path.realpath(default_p) != os.path.realpath(store_p):
                default_p.unlink()
                default_p.symlink_to(store_p)
                log.info(
                    "flm.store_link_repointed",
                    extra={"link": default, "target": store},
                )
            return store

        if default_p.exists():
            if not default_p.is_dir():
                raise FLMStoreLinkError(
                    f"{default} is a file, so flm pulls cannot be pointed at the FLM "
                    f"store {store}. Move the file aside, then retry.",
                    details=details,
                )
            # Real dir: migrate children into the store, skipping name
            # collisions so we never clobber weights already in the store.
            conflicts: list[str] = []
            for child in sorted(default_p.iterdir()):
                dest = store_p / child.name
                if os.path.lexists(dest):
                    conflicts.append(child.name)
                    continue
                shutil.move(str(child), str(dest))
            if conflicts:
                # Leave the dir in place rather than orphan it behind a symlink.
                log.warning(
                    "flm.store_link_skipped_nonempty",
                    extra={"path": default, "conflicts": conflicts},
                )
                raise FLMStoreLinkError(
                    f"{default} and the FLM store {store} both hold "
                    f"{', '.join(conflicts)}. Nothing was deleted: remove one copy, "
                    f"then retry so flm's cache can be linked to the store.",
                    details={**details, "conflicts": conflicts},
                )
            default_p.rmdir()

        # Path now absent → create the symlink. Another process (doctor
        # perms --fix, a second api) may have made the same link first.
        default_p.parent.mkdir(parents=True, exist_ok=True)
        try:
            default_p.symlink_to(store_p)
        except FileExistsError:
            if not (
                default_p.is_symlink() and os.path.realpath(default_p) == os.path.realpath(store_p)
            ):
                raise
            return store
        log.info("flm.store_link_created", extra={"link": default, "target": store})
    except OSError as exc:
        log.warning(
            "flm.store_link_failed",
            extra={"error": str(exc), "store": store, "default": default},
        )
        raise FLMStoreLinkError(
            f"cannot point flm's cache {default} at the FLM store {store}: {exc}. "
            f"A pull now would land where the NPU slot cannot see it; "
            f"{_FLM_STORE_REPAIR_HINT}.",
            details={**details, "error": str(exc)},
        ) from exc
    return store


# Serialises reconciliation across concurrent pulls in one process: two FLM
# pulls can start at once, and both would see flm's default path unlinked.
_FLM_STORE_LINK_LOCK = threading.Lock()


def ensure_host_flm_store_link() -> str:
    """Point flm's hardcoded host cache at the resolved store; return the store.

    Serialised wrapper around :func:`_reconcile_flm_store_link`, which
    documents the behaviour and the :class:`FLMStoreLinkError` cases (#2446).
    Not for the event loop's thread; offload with ``asyncio.to_thread``.
    """
    with _FLM_STORE_LINK_LOCK:
        return _reconcile_flm_store_link()


def _service_ids() -> tuple[int, int]:
    """``(uid, gid)`` of the hal0 service user that runs host ``flm`` and hal0-api."""
    import grp
    import pwd

    uid = pwd.getpwnam(_HOST_FLM_USER).pw_uid
    try:
        gid = grp.getgrnam(_HOST_FLM_USER).gr_gid
    except KeyError:
        gid = pwd.getpwnam(_HOST_FLM_USER).pw_gid
    return uid, gid


def _flm_link_paths() -> tuple[str, str, bool]:
    """``(store, default, relocated)`` for the host link audit and repair."""
    from hal0.config.paths import default_flm_models_dir

    store = _host_flm_models_dir()
    default = default_flm_models_dir()
    return store, default, os.path.normpath(store) != os.path.normpath(default)


def _container_can_write(st: os.stat_result) -> bool:
    """Whether the FLM container uid can write a dir with this stat.

    The container runs as uid 1000 with only the render group added
    (:meth:`FLMProvider.container_spec`), so the hal0 group's write bit does
    not reach it: owner-write as uid 1000, or other-write.
    """
    return (st.st_uid == _FLM_CONTAINER_UID and bool(st.st_mode & 0o200)) or bool(
        st.st_mode & 0o002
    )


def _writable_by(st: os.stat_result, uid: int, gid: int) -> bool:
    """Whether ``uid``/``gid`` may write a dir with this stat (mode bits only)."""
    if st.st_uid == uid:
        return bool(st.st_mode & 0o200)
    if st.st_gid == gid:
        return bool(st.st_mode & 0o020)
    return bool(st.st_mode & 0o002)


def _creatable_by(path: str, uid: int, gid: int) -> bool:
    """Whether ``uid``/``gid`` could ``mkdir -p`` ``path`` (mode bits only)."""
    from pathlib import Path

    for anc in Path(path).parents:
        if anc.exists():
            return _writable_by(anc.stat(), uid, gid)
    return False


def audit_host_flm_store_link() -> list[dict[str, str]]:
    """Audit rows (``{path,label,status,detail}``) for ``hal0 doctor perms`` (#2446).

    Same ``ok``/``drift``/``absent`` vocabulary as the other ``doctor perms``
    sub-checks. With a relocated store it flags every state that keeps host
    ``flm`` (pull and ``flm list``) off the store: flm's default path as a real
    dir (even an empty one, which ``flm list`` reads) or a wrong symlink, a
    HOME-side parent the service user does not own, and a store path the
    service user cannot create. In every case it flags a store the container
    uid cannot write (see :func:`_container_can_write`). Read-only.
    """
    import stat as _stat
    from pathlib import Path

    store, default, relocated = _flm_link_paths()
    store_p, default_p = Path(store), Path(default)
    rows: list[dict[str, str]] = []

    def _row(path: Path, label: str, status: str, detail: str) -> None:
        rows.append({"path": str(path), "label": label, "status": status, "detail": detail})

    try:
        ids: tuple[int, int] | None = _service_ids()
    except KeyError:  # no hal0 user (dev box): ownership is not checkable
        ids = None

    in_use = store_p.exists() or os.path.lexists(default_p) or default_p.parent.exists()
    if relocated and in_use:
        label = "flm cache → FLM store link"
        if default_p.is_symlink():
            if os.path.realpath(default_p) == os.path.realpath(store_p):
                _row(default_p, label, "ok", f"links to {store}")
            else:
                _row(default_p, label, "drift", f"links to {os.readlink(default_p)}, not {store}")
        elif default_p.is_dir():
            n = sum(1 for _ in default_p.iterdir())
            _row(
                default_p,
                label,
                "drift",
                f"real directory ({n} entries): host flm reads and pulls here, not {store}",
            )
        elif default_p.exists():
            _row(default_p, label, "drift", "a file, not a directory or link")
        else:
            _row(default_p, label, "absent", "created on the next FLM pull")

        for parent in (default_p.parent.parent, default_p.parent):
            if ids is None or not os.path.lexists(parent):
                continue
            if parent.is_symlink() or not parent.is_dir():
                _row(parent, f"{parent.name} owner", "drift", "not a plain directory")
            elif parent.stat().st_uid == ids[0] and parent.stat().st_mode & 0o200:
                _row(parent, f"{parent.name} owner", "ok", f"owned by {_HOST_FLM_USER}")
            else:
                _row(
                    parent,
                    f"{parent.name} owner",
                    "drift",
                    f"not owned by {_HOST_FLM_USER}: hal0-api cannot relink flm's cache",
                )

        if os.path.lexists(store_p) and not store_p.is_dir():
            _row(store_p, "FLM store", "drift", "configured path is not a directory")
        elif not store_p.exists() and ids is not None and not _creatable_by(store, *ids):
            _row(
                store_p,
                "FLM store",
                "drift",
                f"missing, and {_HOST_FLM_USER} cannot create it: install -d -o "
                f"{_FLM_CONTAINER_UID} -g {_HOST_FLM_USER} -m 2775 {store}",
            )

    if store_p.is_dir():
        st = store_p.stat()
        problems = []
        if not _container_can_write(st):
            problems.append(f"container uid {_FLM_CONTAINER_UID} cannot write")
        if ids is not None and not _writable_by(st, *ids):
            problems.append(f"{_HOST_FLM_USER} cannot write (host flm pull)")
        _row(
            store_p,
            "FLM store",
            "drift" if problems else "ok",
            f"uid {st.st_uid}, mode {oct(_stat.S_IMODE(st.st_mode))}"
            + "".join(f"; {p}" for p in problems),
        )
    return rows


def _link_as_service_user(uid: int, gid: int, *, run: Any = None) -> None:
    """Run :func:`ensure_host_flm_store_link` as the service user, from a root caller.

    The store path comes from ``hal0.toml``, which the service user can edit,
    and flm's default dir is in the service user's HOME. Creating dirs there and
    moving files into the store as root would let that user plant files or
    dirs anywhere root can write. As the service user, the move and the link
    can only reach what that user could already write.
    """
    import subprocess
    import sys

    runner = run or subprocess.run
    code = (
        "import sys\n"
        "from hal0.providers.flm import FLMStoreLinkError, ensure_host_flm_store_link\n"
        "try:\n"
        "    ensure_host_flm_store_link()\n"
        "except FLMStoreLinkError as exc:\n"
        "    sys.stderr.write(exc.message)\n"
        "    sys.exit(3)\n"
    )
    proc = runner(
        [sys.executable, "-c", code],
        user=uid,
        group=gid,
        extra_groups=[],
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise FLMStoreLinkError(detail.splitlines()[-1] if proc.returncode != 3 else detail)


def _own_home_dirs(home: str, uid: int, gid: int, *, fchown: Any) -> list[str]:
    """Create ``home/.config`` and ``home/.config/flm`` and hand them to ``uid:gid``.

    Every step goes through a directory fd opened with ``O_NOFOLLOW``, so a
    symlink planted at either name is refused instead of followed (HOME is
    owned by the service user, who can rename entries in it).
    """
    import contextlib

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    done: list[str] = []
    fds = [os.open(home, flags)]
    try:
        path = home
        for name in (".config", "flm"):
            path = os.path.join(path, name)
            with contextlib.suppress(FileExistsError):
                os.mkdir(name, 0o755, dir_fd=fds[-1])
            try:
                fds.append(os.open(name, flags, dir_fd=fds[-1]))
            except OSError as exc:
                raise FLMStoreLinkError(
                    f"{path} is not a plain directory ({exc.strerror}); refusing to "
                    f"follow it as root. Replace it with a directory, then retry.",
                    details={"path": path},
                ) from exc
            fchown(fds[-1], uid, gid)
            done.append(f"{path} → {_HOST_FLM_USER}")
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return done


def _chown_store_for_container(store: str, uid: int, gid: int, *, fchown: Any) -> str:
    """Give the store ``1000:<group>`` 2775 when the service user owns it.

    Opened ``O_NOFOLLOW`` and checked by ``fstat`` on the same fd, so nothing
    the service user points the configured path at is chowned unless it was
    already theirs. Any other owner, uid 1000 included, is left alone.
    """
    try:
        fd = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        return f"{store} left alone ({exc.strerror})"
    try:
        st = os.fstat(fd)
        if st.st_uid == _FLM_CONTAINER_UID and st.st_gid == gid:
            return f"{store} already uid {_FLM_CONTAINER_UID}:{_HOST_FLM_USER}; left alone"
        if st.st_uid != uid:
            # Not proof hal0 made it: the path comes from hal0.toml, which the
            # service user can edit, and uid 1000 is often a human's account.
            return (
                f"{store} left alone (owned by uid {st.st_uid}); chown it to "
                f"{_FLM_CONTAINER_UID}:{_HOST_FLM_USER} mode 2775 yourself if the NPU "
                f"container must write it"
            )
        fchown(fd, _FLM_CONTAINER_UID, gid)
        os.fchmod(fd, 0o2775)
    finally:
        os.close(fd)
    return f"{store} → uid {_FLM_CONTAINER_UID}:{_HOST_FLM_USER} mode 2775"


def repair_host_flm_store_link(*, fchown: Any = os.fchown, link: Any = None) -> list[str]:
    """Root-side repair for #2446; returns the actions taken.

    The ``hal0 doctor perms --fix`` step (the installer runs it before every
    service start, so upgrades get it too):

      1. creates ``$HOME/.config`` and ``.config/flm`` if needed and hands them
         to the service user, so hal0-api (``User=hal0``) can relink flm's cache
         on a later relocation (no-follow, see :func:`_own_home_dirs`);
      2. as the service user (:func:`_link_as_service_user`), creates the store
         if needed, moves models stranded in flm's default dir into it, and
         replaces that dir with the symlink;
      3. gives the store ``1000:<hal0 group>`` mode 2775 if the service user
         owns it (:func:`_chown_store_for_container`).

    Does nothing when neither the store nor flm's cache (or its parent)
    exists. Raises :class:`FLMStoreLinkError` on a symlinked HOME parent, a
    name present in both dirs, or a store the service user cannot create.
    Nothing is deleted. ``fchown`` and ``link`` are injectable so the logic is
    testable unprivileged.
    """
    from pathlib import Path

    store, default, relocated = _flm_link_paths()
    store_p, default_p = Path(store), Path(default)
    if not (store_p.exists() or os.path.lexists(default_p) or default_p.parent.exists()):
        return []

    uid, gid = _service_ids()
    actions: list[str] = []
    if relocated:
        actions += _own_home_dirs(str(default_p.parent.parent.parent), uid, gid, fchown=fchown)
        was_linked = default_p.is_symlink() and os.path.realpath(default_p) == os.path.realpath(
            store_p
        )
        (link or _link_as_service_user)(uid, gid)
        if not was_linked:
            actions.append(f"{default} → {store} (stranded models moved into the store)")
    if store_p.is_dir():
        actions.append(_chown_store_for_container(store, uid, gid, fchown=fchown))
    return actions


# ── Timeouts ───────────────────────────────────────────────────────────────────
# TIER1: separate health budget from infer budget.
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
# The sentinel completion can take a moment on cold NPU; give it a
# more generous read window than llama-server.
_HEALTH_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


class FLMHealthError(Hal0Error):
    """FLM health probe failed (typed for the error envelope)."""

    code = "slot.not_ready"
    status = 503


class FLMInferError(Hal0Error):
    """FLM inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


# ── NPU device nodes ──────────────────────────────────────────────────────────
# Strix Halo defaults, kept as the LAST link of the fallback chain below.
_DEFAULT_NPU_ACCEL_NODE = "/dev/accel/accel0"
_DEFAULT_NPU_RENDER_NODE = "/dev/dri/renderD128"


def _npu_device_nodes() -> list[str]:
    """Device nodes to pass through to the FLM container.

    Fallback chain (documented per the hardware-generalization wave):

      1. hardware.json ``npu.accel_path`` / ``npu.render_path`` — the actual
         nodes ``hal0 probe`` detected on this host (a second accel device or
         a non-renderD128 iGPU node lands here);
      2. the Strix Halo constants ``/dev/accel/accel0`` +
         ``/dev/dri/renderD128`` — today's behaviour, used when the snapshot
         is missing, pre-wave (fields absent/empty), or unreadable.

    Raw-JSON read (no pydantic) so spec builds stay cheap and never raise.
    """
    accel = ""
    render = ""
    try:
        import json as _json

        from hal0.config import paths as _paths

        raw = _json.loads(_paths.hardware_json().read_text())
        npu = raw.get("npu") or {}
        if isinstance(npu, dict):
            accel = str(npu.get("accel_path") or "")
            render = str(npu.get("render_path") or "")
    except Exception:
        pass
    return [accel or _DEFAULT_NPU_ACCEL_NODE, render or _DEFAULT_NPU_RENDER_NODE]


def _flm_shadow_role_args(env: dict[str, str]) -> list[str]:
    """Build the ``--embed`` / ``--asr`` argv tail.

    Shared by :meth:`FLMProvider.start_cmd` (native) and
    :meth:`FLMProvider.container_spec` (primary) so the two paths can never
    drift. FLM's ``--embed`` / ``--asr`` are booleans: each loads FLM's single
    bundled model (embed-gemma / whisper) — there is NO ``--embed-model`` /
    ``--asr-model`` flag, so nothing per-role to emit here.
    """
    args: list[str] = []
    if env.get("HAL0_FLM_LOAD_EMBED") == "1":
        args += ["--embed", "1"]
    if env.get("HAL0_FLM_LOAD_ASR") == "1":
        args += ["--asr", "1"]
    return args


def _resolve_render_gid() -> int | None:
    """Look up the ``render`` group's numeric gid on the host.

    Slot containers need this group to read /dev/accel/accel0 and
    /dev/dri/renderD128. The gid varies between hosts (993 on Strix Halo
    LXCs, 109 on some bare-metal Debian, etc.) so we resolve once at
    container-spec build time rather than baking it into the image.

    Returns ``None`` if the group can't be resolved — the slot will still
    launch but device reads may fail; the slot's health probe will catch
    it.
    """
    try:
        import grp

        return grp.getgrnam("render").gr_gid
    except (KeyError, ImportError, OSError):
        return None


class FLMProvider(Provider):
    """Provider for the AMD NPU FLM backend.

    Two-tier readiness (Option A — NPU double-free avoidance):

      * :meth:`health` is the CHEAP liveness probe (non-empty ``/v1/models``).
        It issues NO NPU work and is the ONLY probe safe to run on the hot
        paths — the 2s fail-watcher and the per-request readiness gate. FLM
        serialises the single NPU context and double-frees (SIGABRT, status
        134) when an in-flight completion is cancelled or overlapped, so a
        real-inference probe MUST NOT run on a repeating/concurrent path.
      * :meth:`verify_inference` is the one-shot ``/v1/chat/completions``
        sentinel (max_tokens=1). It runs EXACTLY ONCE at the warm→ready
        promotion (:meth:`SlotManager._await_ready`), where there is no
        contention — the slot is not yet dispatchable, the load lock is held,
        and the warming fail-watcher skips health probes. This preserves the
        Tier-1 "listing models is not the same as being able to infer" check
        (haloai lib/slots.py:899-920) without the polling storm that crashed
        the NPU.
    """

    name = "flm"

    # ── Env / argv ─────────────────────────────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Build HAL0_* env vars for an FLM slot.

        Returned vars are stamped into the slot's EnvironmentFile so the
        rendered docker command can refer to them via ``${HAL0_*}``. Kept
        separate from container_spec so non-container callers (tests,
        debug shells, native systemd fallback) can use them too.
        """
        port = slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port", 8086)
        # [model].context_size is the SlotConfig shape (container slots);
        # ctx_size / defaults.context_size are legacy (pre-container) shapes.
        ctx = (
            (slot_cfg.get("model") or {}).get("context_size")
            or slot_cfg.get("ctx_size")
            or slot_cfg.get("defaults", {}).get("context_size", 8192)
        )
        flm_tag = model_info.get("flm_tag") or model_info.get("_model_key") or "qwen3:0.6b"

        npu_table = slot_cfg.get("npu") or {}
        defaults = slot_cfg.get("defaults") or {}
        if slot_cfg.get("npu") is not None:
            # [npu] table is PRIMARY when present — explicit asr=false must
            # win even if stale legacy defaults say otherwise.
            load_asr = "1" if npu_table.get("asr") else "0"
            load_embed = "1" if npu_table.get("embed") else "0"
            load_chat = "1" if npu_table.get("chat", True) not in (False, "0", 0) else "0"
        else:
            # Legacy (pre-container) fallback — still reachable from legacy
            # TOMLs without an [npu] table (api/__init__.py reads the same
            # ``defaults.load_*`` shape): truthy check, same semantics as
            # before the [npu] table existed.
            load_asr = "1" if defaults.get("load_asr") else "0"
            load_embed = "1" if defaults.get("load_embed") else "0"
            # chat defaults on (NpuConfig.chat=True); always enabled here.
            load_chat = "1"

        return {
            "HAL0_FLM_TAG": str(flm_tag),
            "HAL0_PORT": str(port),
            "HAL0_FLM_CTX": str(ctx),
            "HAL0_FLM_LOAD_ASR": load_asr,
            "HAL0_FLM_LOAD_EMBED": load_embed,
            "HAL0_FLM_LOAD_CHAT": load_chat,
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Return argv for the native ``flm serve`` invocation.

        Used by tests and the fallback systemd-without-Docker path. The
        primary deployment path is ``container_spec``. Shares the
        ``--embed`` / ``--asr`` tail with it via :func:`_flm_shadow_role_args`.
        """
        binary = os.environ.get("HAL0_FLM_BINARY", f"{_NATIVE_FLM_ROOT}/bin/flm")
        argv = [binary, "serve"]
        if env.get("HAL0_FLM_LOAD_CHAT", "1") == "1":
            argv += [env["HAL0_FLM_TAG"]]
        argv += [
            "--host",
            "0.0.0.0",
            "--port",
            env["HAL0_PORT"],
            "--ctx-len",
            env["HAL0_FLM_CTX"],
        ]
        argv += _flm_shadow_role_args(env)
        return argv

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return the FLM toolbox image reference.

        Resolution (#2234 — the same tier order as
        :func:`hal0.providers.container._resolve_image_ref`, shared via
        :func:`hal0.providers._image.resolve_family_image`):
        ``slot_cfg["image_pin"]`` (top-level or ``[slot]``-nested, honored
        verbatim) → ``[slots].default_images["flm"]`` (operator family
        default) → the runner registry (``HAL0_TOOLBOX_IMAGE_FLM`` env
        override → the manifest digest pin → the bundled default) — see
        :func:`hal0.runners.resolve_runner_image`.
        """
        from hal0.providers._image import resolve_family_image

        return resolve_family_image(slot_cfg, "flm")

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Build a ContainerSpec for FLM in the toolbox image.

        The toolbox image is self-contained: FLM is built in and lives at
        /opt/fastflowlm/ (binary, libs, xclbins, share assets). The image
        ENTRYPOINT is ``tini -- /usr/local/bin/flm``, so the command we
        pass becomes the flm subcommand + args — no host bind-mount of
        the binary tree is needed.

        Only the model cache is bind-mounted, so ``flm pull`` downloads
        survive container restarts.

        FLM needs ``/dev/accel/accel0`` for the AMD XDNA2 NPU. ``/dev/dri``
        is included because some FLM model loaders hit iGPU helpers
        during init.
        """
        env = self.build_env(slot_cfg, model_info)
        port = int(env["HAL0_PORT"])

        # Per-slot [_paths].flm_models wins (rare, test/debug shape); otherwise
        # the shared resolver: HAL0_FLM_MODELS_DIR env > [models].flm_store >
        # FLM's default HOME cache. Historically this chain never consulted
        # config — [models]-level relocation only worked via the env var.
        paths = slot_cfg.get("_paths", {}) or {}
        flm_models = paths.get("flm_models") or _host_flm_models_dir()
        _ensure_flm_models_dir(flm_models)

        # Only the model cache is bind-mounted. FLM hardcodes
        # ~/.config/flm/models internally; map our hal0-managed cache
        # to that path. The toolbox image runs as the non-root ``hal0``
        # user (uid 1000, HOME=/var/lib/hal0), so ~/.config resolves to
        # /var/lib/hal0/.config — NOT /root/.config. Mounting at the
        # wrong HOME would silently drop every pulled model into the
        # container's writable overlay and lose it on exit.
        mounts: list[tuple[str, str]] = [
            (flm_models, "/var/lib/hal0/.config/flm/models"),
        ]

        # Build the argv passed to the image's ENTRYPOINT. The image runs
        # /usr/local/bin/flm via tini, so what we provide here is the
        # subcommand + flags — NOT a binary path. Passing an absolute
        # binary path here would be treated by flm as a stray positional
        # argument and rejected with "too many positional options".
        command: list[str] = ["serve"]
        # Chat modality is the positional tag. Gate it on HAL0_FLM_LOAD_CHAT so
        # toggling NPU · Chat off actually stops serving chat on the container
        # path (previously the tag was passed unconditionally, so the toggle
        # only worked on the native start_cmd fallback). With chat off, FLM
        # serves only the shadow roles (--embed / --asr) and has no LLM.
        if env.get("HAL0_FLM_LOAD_CHAT", "1") == "1":
            command += [env["HAL0_FLM_TAG"]]
        command += [
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--ctx-len",
            env["HAL0_FLM_CTX"],
        ]
        command += _flm_shadow_role_args(env)

        # render group resolves dynamically at run-time; use the numeric gid
        # so the spec doesn't depend on /etc/group in the container.
        render_gid = _resolve_render_gid()

        return ContainerSpec(
            image=self.image_ref(slot_cfg),
            command=command,
            env={
                # Image-internal paths (the Dockerfile installs FLM at
                # /opt/fastflowlm and XRT at /opt/xilinx/xrt).
                "FLM_CONFIG_PATH": f"{_CONTAINER_FLM_ROOT}/share/flm/model_list.json",
                # Docker `--env LD_LIBRARY_PATH=...` REPLACES the image ENV
                # set by the Dockerfile rather than augmenting it, so we
                # must spell out every path here even though the image's
                # own ENV would be correct on its own. libxrt_coreutil.so.2
                # (XRT runtime) and the FLM libs (libllama_npu.so &c, dlopen'd
                # when models load) both need to be findable; missing either
                # crashes /usr/local/bin/flm at startup before main().
                "LD_LIBRARY_PATH": f"{_CONTAINER_FLM_ROOT}/lib:/opt/xilinx/xrt/lib:/usr/lib/x86_64-linux-gnu",
            },
            mounts=mounts,
            # accel node: XDNA2 NPU. render node: iGPU companion. Probe-recorded
            # paths from hardware.json when present; Strix Halo constants
            # otherwise — see _npu_device_nodes for the fallback chain.
            devices=_npu_device_nodes(),
            cap_add=[],
            # apparmor=unconfined is required in LXC; on bare metal a
            # tailored profile would be tighter but Strix Halo deployments
            # under Proxmox LXC are the primary target.
            # seccomp=unconfined matches the GPU slot rendering so
            # the Quadlet renderer doesn't need special-casing here.
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            group_add=[str(render_gid)] if render_gid is not None else [],
            port=port,
            # FLM's /v1/* server needs to be reachable from the dispatcher
            # at 127.0.0.1:<port>. Use port-mapping rather than network=host
            # so multiple slots can coexist with overlapping internal ports.
            # The renderer derives --publish=127.0.0.1:<port>:<port> from
            # spec.port declaratively — no hand-rolled "-p" here.
            network_mode="",
            extra_args=[
                # NPU model weights are pinned in DMA-locked memory; this
                # is the same flag haloai's systemd unit sets.
                "--ulimit memlock=-1",
            ],
        )

    # ── Health / infer ─────────────────────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Cheap liveness probe — non-empty ``/v1/models``, NO NPU work.

        This is the ONLY health probe safe to run on the hot paths (the 2s
        fail-watcher and the per-request readiness gate). It deliberately does
        NOT issue a ``/v1/chat/completions`` sentinel: FLM serialises the
        single NPU context and its request-cancel path double-frees (SIGABRT,
        status 134), so a real-inference probe fired repeatedly — and
        overlapping real traffic — crashes the container. Real inferability is
        verified once, out of band, by :meth:`verify_inference` at the
        warm→ready promotion.

        Distinguishes "up but still loading" (``ok=False``,
        ``models_endpoint_empty``) from "up and serving a model"
        (``ok=True``). Transport errors surface as ``ok=False`` /
        ``http_error`` so the manager's strike-based fail-watcher can act.

        Returns {"ok": bool, "status": str, "model": str|None, ...}.
        """
        models_url = f"http://127.0.0.1:{port}/v1/models"
        try:
            async with async_client(timeout=_HEALTH_TIMEOUT) as client:
                models_resp = await client.get(models_url)
                models_resp.raise_for_status()
                data = models_resp.json()
                models = data.get("data", [])
                if not models:
                    return {
                        "ok": False,
                        "status": "models_endpoint_empty",
                        "detail": "/v1/models returned no entries",
                    }
                model_id = models[0].get("id")
            return {"ok": True, "status": "ready", "model": model_id}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "http_error", "detail": str(exc)}
        except Exception as exc:
            # Do not silently swallow — surface the failure to the fail-watcher.
            return {"ok": False, "status": "exception", "detail": str(exc)}

    async def verify_inference(
        self, port: int, expected_model: str | None = None
    ) -> dict[str, Any]:
        """One-shot real-inference gate — a single ``/v1/chat/completions``.

        Run EXACTLY ONCE at the warm→ready promotion (see
        :meth:`SlotManager._await_ready`), never on a repeating/concurrent
        path. This is the Tier-1 check that ``/v1/models`` listing a model is
        not proof the NPU can actually produce a token (haloai
        lib/slots.py:899-920).

        ``expected_model`` is the slot's assigned tag (the one it serves). The
        sentinel MUST probe that model, not ``models[0]``: FLM's ``/v1/models``
        returns its whole installed catalogue (sorted), so ``models[0]`` is an
        arbitrary OTHER model. Probing it makes FLM switch/reload the wrong
        weights onto its single NPU context mid-gate, which deadlocks the
        in-flight load — the slot then sits in ``warming`` forever (#1171). We
        fall back to ``models[0]`` only when the expected tag isn't advertised,
        so this can never make an otherwise-working slot worse.

        The sentinel POST is wrapped in :func:`asyncio.shield`: if the load
        coroutine is cancelled mid-flight we must NOT abort an in-progress NPU
        prefill — that is exactly the cancellation FLM double-frees on. The
        gate runs with no contention (slot not yet dispatchable, load lock
        held, warming fail-watcher skips /health), so draining the shielded
        request costs at most the read timeout.

        Returns {"ok": bool, "status": str, "model": str|None, ...}. Never
        raises; a transport failure resolves to ``ok=False`` so the caller can
        hold the slot in the retryable WARMING state rather than a lying READY.
        """
        models_url = f"http://127.0.0.1:{port}/v1/models"
        chat_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with async_client(timeout=_HEALTH_TIMEOUT) as client:
                models_resp = await client.get(models_url)
                models_resp.raise_for_status()
                data = models_resp.json()
                models = data.get("data", [])
                if not models:
                    return {
                        "ok": False,
                        "status": "models_endpoint_empty",
                        "detail": "/v1/models returned no entries",
                    }
                ids = [m.get("id") for m in models]
                model_id = expected_model if expected_model in ids else models[0].get("id")

            async with async_client(timeout=_HEALTH_INFER_TIMEOUT) as client:
                probe_body = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                    "stream": False,
                }
                # shield: never hand FLM a cancelled prefill (double-free).
                chat_resp = await asyncio.shield(client.post(chat_url, json=probe_body))
                if chat_resp.status_code != 200:
                    return {
                        "ok": False,
                        "status": f"sentinel_completion_http_{chat_resp.status_code}",
                        "detail": chat_resp.text[:200],
                        "model": model_id,
                    }
                try:
                    body = chat_resp.json()
                except Exception:
                    return {
                        "ok": False,
                        "status": "sentinel_completion_unparseable",
                        "model": model_id,
                    }
                if not body.get("choices"):
                    return {
                        "ok": False,
                        "status": "sentinel_completion_no_choices",
                        "model": model_id,
                    }
            return {"ok": True, "status": "ready", "model": model_id}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "http_error", "detail": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": "exception", "detail": str(exc)}

    async def verify_embed(self, port: int) -> dict[str, Any]:
        """One-shot embeddings gate — a single ``/v1/embeddings`` call.

        The warm→ready sentinel for FLM slots serving EMBED without CHAT
        (``[npu].chat=false``): :meth:`verify_inference`'s chat completion
        would fail because no chat model is loaded, wedging the slot in
        WARMING forever. This exercises the embed path instead — the actual
        role the slot serves — so an embed-primary slot can promote to READY.

        Mirrors :meth:`verify_inference`: reads ``/v1/models`` for the served
        model id, then a single shielded ``/v1/embeddings`` POST (shield: never
        hand FLM a cancelled request → NPU double-free). Never raises; a
        transport failure resolves to ``ok=False`` (retryable WARMING).
        """
        models_url = f"http://127.0.0.1:{port}/v1/models"
        embed_url = f"http://127.0.0.1:{port}/v1/embeddings"
        try:
            async with async_client(timeout=_HEALTH_TIMEOUT) as client:
                models_resp = await client.get(models_url)
                models_resp.raise_for_status()
                data = models_resp.json()
                models = data.get("data", [])
                if not models:
                    return {
                        "ok": False,
                        "status": "models_endpoint_empty",
                        "detail": "/v1/models returned no entries",
                    }
                model_id = models[0].get("id")

            async with async_client(timeout=_HEALTH_INFER_TIMEOUT) as client:
                probe_body = {"model": model_id, "input": "ping"}
                resp = await asyncio.shield(client.post(embed_url, json=probe_body))
                if resp.status_code != 200:
                    return {
                        "ok": False,
                        "status": f"sentinel_embed_http_{resp.status_code}",
                        "detail": resp.text[:200],
                        "model": model_id,
                    }
                try:
                    body = resp.json()
                except Exception:
                    return {"ok": False, "status": "sentinel_embed_unparseable", "model": model_id}
                if not body.get("data"):
                    return {"ok": False, "status": "sentinel_embed_no_data", "model": model_id}
            return {"ok": True, "status": "ready", "model": model_id}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "http_error", "detail": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": "exception", "detail": str(exc)}

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Passthrough /v1/chat/completions to FLM."""
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with async_client(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise FLMInferError(
                f"FLM returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise FLMInferError(
                f"FLM transport error: {exc}",
                details={"port": port},
            ) from exc


# ── FLM catalog probe ─────────────────────────────────────────────────────────
#
# What FLM can actually serve. Slot picking lives in
# hal0.capabilities.catalog, which calls flm_served_models() to learn
# which model tags the NPU advertises.
#
# Design call: cache the probe result with a short TTL. A `flm pull` (or a
# toolbox/host FLM upgrade) changes what's installed WITHOUT restarting
# hal0-api, so a restart-only cache would show a stale catalog for the rest of
# the process lifetime. A 5-minute TTL bounds that staleness cheaply; tests and
# the force-refresh CLI/UI hook call reset_flm_catalog_cache() to invalidate
# immediately.
_FLM_CATALOG_TTL_S = 300.0

_FLM_CATALOG_CACHE: list[dict[str, Any]] | None = None
_FLM_CATALOG_CACHED_AT: float = 0.0
#: True while the cached catalog stands in for a probe that gave no answer
#: (#2333). The cache then holds ``[]`` for :func:`flm_served_models`, but
#: :func:`flm_catalog` reports ``None``, so "could not ask" never reads as
#: "FLM serves nothing" to a caller that acts on absence.
_FLM_CATALOG_UNANSWERED: bool = False
#: Guards the cache, its timestamp and the unanswered flag as one unit, so a
#: reader never pairs one probe's catalog with another probe's flag. The probe
#: itself runs outside the lock: a slow ``flm list`` must not block readers.
_FLM_CATALOG_LOCK = threading.Lock()
# Held for the whole cold-cache probe so concurrent callers share one
# ``flm list -j`` instead of each spawning its own, and a slower probe can
# never overwrite a newer answer (#2334). Never taken while holding
# _FLM_CATALOG_LOCK.
_FLM_CATALOG_PROBE_LOCK = threading.Lock()


def _classify_flm_model(entry: dict[str, Any]) -> list[str]:
    """Map an ``flm list -j`` entry to hal0 capability strings.

    Rules, applied in order:

      1. ``label`` contains ``"embeddings"``                → ``["embed"]``
      2. chat-y signal (``"reasoning"`` or ``"tool-calling"`` label) →
         ``["chat"]`` and, if ``asr: true`` is also set, append
         ``"stt"`` (e.g. ``gemma4-it:e2b`` is a multimodal chat + asr
         model).
      3. ``asr: true`` alone, no chat-y labels (e.g. ``whisper-v3:turbo``)
         → ``["stt"]``.
      4. Anything else falls back to ``["chat"]``.

    Returned strings line up with the hal0 capability vocabulary used by
    :mod:`hal0.capabilities.catalog` (``embed``, ``chat``, ``stt``).
    """
    labels = set(entry.get("label", []) or [])
    if "embeddings" in labels:
        return ["embed"]
    has_asr = bool(entry.get("asr"))
    if labels & {"reasoning", "tool-calling"}:
        return ["chat", "stt"] if has_asr else ["chat"]
    if has_asr:
        return ["stt"]
    return ["chat"]


def flm_host_spawn_kwargs() -> dict[str, Any]:
    """``subprocess.run`` kwargs to run host ``flm`` as the hal0 identity.

    Sets ``HOME`` so flm resolves ``~/.config/flm/models`` to the real on-disk
    cache, and drops to the ``hal0`` user/group when we have the privilege
    (hal0-api runs as ``root`` in prod). When already running as a non-root
    user (dev/test), ``user=`` would raise ``PermissionError``, so we skip it
    and rely on the caller already being the model owner.

    SYNC CALLERS ONLY: ``user``/``group`` are Popen kwargs (Python ≥ 3.9) that
    uvloop's ``subprocess_exec`` rejects (``ValueError: unexpected kwargs``),
    and hal0-api serves under uvicorn/uvloop — async callers must use
    :func:`flm_host_async_spawn` instead.
    """
    import pwd

    kwargs: dict[str, Any] = {"env": {**os.environ, "HOME": _HOST_FLM_HOME}}
    try:
        if os.geteuid() == 0:
            pwd.getpwnam(_HOST_FLM_USER)  # raises KeyError if the user is absent
            kwargs["user"] = _HOST_FLM_USER
            kwargs["group"] = _HOST_FLM_USER
    except (KeyError, OSError, AttributeError):
        # Unknown user, or geteuid unavailable (non-POSIX) — run as-is.
        pass
    return kwargs


def flm_host_async_spawn(argv: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Return ``(argv, kwargs)`` for spawning host ``flm`` from async code.

    Same identity semantics as :func:`flm_host_spawn_kwargs`, but safe for
    ``asyncio.create_subprocess_exec`` under uvloop: uvloop rejects the
    ``user``/``group`` Popen kwargs (``ValueError: unexpected kwargs``), so
    the drop to the hal0 user happens on the command line instead — via
    ``setpriv`` (util-linux), falling back to ``runuser``. If neither tool
    exists we spawn undemoted rather than fail the pull; the weights then
    land root-owned but world-readable, so serving still works.
    """
    import shutil

    kwargs = flm_host_spawn_kwargs()
    user = kwargs.pop("user", None)
    kwargs.pop("group", None)
    if user:
        setpriv = shutil.which("setpriv")
        runuser = shutil.which("runuser")
        if setpriv:
            argv = [setpriv, f"--reuid={user}", f"--regid={user}", "--init-groups", "--", *argv]
        elif runuser:
            argv = [runuser, "-u", user, "--", *argv]
    return argv, kwargs


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Decode the first top-level JSON object in ``text``.

    ``flm list -j`` normally prints clean JSON, but can prepend ``[WARNING]``
    lines (e.g. a model written by a newer flm). Scan to the first ``{`` and
    decode just that object so a stray preamble (or trailing) line can't null
    the whole catalog.
    """
    import json

    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _probe_flm_catalog() -> list[dict[str, Any]] | None:
    """Run host ``flm list -j`` (as the hal0 user) and parse the JSON output.

    Uses the host ``/usr/bin/flm`` — the SAME binary the NPU slot serves
    with, NOT a docker toolbox — so the reported ``installed`` flag reflects the weights
    actually on disk at ``~/.config/flm/models`` and there is no
    toolbox-vs-host version skew. Returns the raw model list (FLM's own shape)
    or ``None`` on any failure (missing binary, perms, crash, parse error,
    timeout); the caller treats ``None`` as "advertise NPU with no served
    models" so the dashboard renders cleanly.

    No ``--device`` is needed: ``flm list`` reads the bundled
    ``model_list.json`` and checks on-disk files; it never touches NPU
    hardware, so the probe still works on dev hosts without XDNA.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [_HOST_FLM_BIN, "list", "-j"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30.0,
            **flm_host_spawn_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    payload = _extract_json_object(proc.stdout.decode("utf-8", errors="replace"))
    if payload is None:
        return None
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    return models


def flm_validate() -> bool | None:
    """Run host ``flm validate`` — the upstream NPU-runtime health check.

    Unlike :func:`_probe_flm_catalog` (``flm list``, which never touches the
    NPU), ``flm validate`` exercises the actual XDNA runtime. Returns:

    * ``True``  — validation passed (NPU runtime reachable); rc 0.
    * ``False`` — the binary ran but validation failed (NPU hardware absent
      or ``libxrt-npu2`` mismatched); non-zero rc.
    * ``None``  — could not run at all (flm not installed / OS error /
      timeout), so functional state is unknown.

    This mirrors the installer's ``flm validate`` smoke test but records the
    result into ``hardware.json`` (``npu.validated``) so slots and the FLM
    container spec have an authoritative functional signal, not just node
    presence. Runs as the hal0 identity via :func:`flm_host_spawn_kwargs`.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [_HOST_FLM_BIN, "validate"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60.0,
            **flm_host_spawn_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.returncode == 0


def flm_served_models() -> list[dict[str, Any]]:
    """Return what the FLM toolbox can serve, classified into hal0 capabilities.

    Same cache and shape as :func:`flm_catalog`, but a probe that gave no
    answer reads as an empty list so the catalog still renders. Callers that
    act on a tag's absence (``hal0 capabilities migrate``) must use
    :func:`flm_catalog` instead, which keeps that case distinct.
    """
    catalog = flm_catalog()
    return catalog if catalog is not None else []


def flm_catalog() -> list[dict[str, Any]] | None:
    """Return what the FLM toolbox can serve, or ``None`` if the probe gave no answer.

    Tri-state per tag (#2333): a tag in the list is served, a tag missing from
    a list is not, and ``None`` means ``flm list`` could not be asked (missing
    binary, perms, timeout, non-zero exit, unparseable output), so nothing is
    known either way.

    Each entry is a dict in hal0's shape (NOT FLM's raw JSON)::

        {
            "tag":          "embed-gemma:300m",  # id `flm pull / flm serve` consume
            "capabilities": ["embed"],           # hal0 capability strings
            "installed":    False,               # FLM reports weights on disk
            "size_bytes":   300_000_000,         # FLM's reported model size
            "footprint_gb": 0.62,                # FLM's runtime memory estimate
            "family":       "embed-gemma",       # details.family — useful for grouping
        }

    Cached at module scope with a 5-minute TTL (:data:`_FLM_CATALOG_TTL_S`);
    subsequent calls inside the window are O(1). A failed probe is cached for
    the same TTL as "no answer" (``None`` here, ``[]`` from
    :func:`flm_served_models`) — call :func:`reset_flm_catalog_cache` to force
    an immediate re-probe.
    """
    with _FLM_CATALOG_LOCK:
        if _flm_catalog_fresh():
            return None if _FLM_CATALOG_UNANSWERED else _FLM_CATALOG_CACHE

    with _FLM_CATALOG_PROBE_LOCK:
        # Another caller may have filled the cache while this one waited.
        with _FLM_CATALOG_LOCK:
            if _flm_catalog_fresh():
                return None if _FLM_CATALOG_UNANSWERED else _FLM_CATALOG_CACHE
        return _probe_and_cache_flm_catalog()


def _probe_and_cache_flm_catalog() -> list[dict[str, Any]] | None:
    """Run the ``flm list -j`` probe and store the result; caller holds
    :data:`_FLM_CATALOG_PROBE_LOCK`."""
    import time

    global _FLM_CATALOG_CACHE, _FLM_CATALOG_CACHED_AT, _FLM_CATALOG_UNANSWERED
    raw = _probe_flm_catalog()
    now = time.monotonic()
    if raw is None:
        with _FLM_CATALOG_LOCK:
            _FLM_CATALOG_CACHE = []
            _FLM_CATALOG_CACHED_AT = now
            _FLM_CATALOG_UNANSWERED = True
        return None

    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        tag = entry.get("model") or entry.get("name")
        if not isinstance(tag, str) or not tag:
            continue
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        out.append(
            {
                "tag": tag,
                "capabilities": _classify_flm_model(entry),
                "installed": bool(entry.get("installed")),
                "size_bytes": int(entry.get("size") or 0),
                "footprint_gb": float(entry.get("footprint") or 0.0),
                "family": str(details.get("family") or ""),
            }
        )

    # A non-empty reply with no usable entry (e.g. ``{"models": [{}]}``) says
    # nothing about which tags are served: keep it "no answer", not "empty".
    unanswered = bool(raw) and not out
    with _FLM_CATALOG_LOCK:
        _FLM_CATALOG_CACHE = out
        _FLM_CATALOG_CACHED_AT = now
        _FLM_CATALOG_UNANSWERED = unanswered
    return None if unanswered else out


def _flm_catalog_fresh() -> bool:
    """True while a cached catalog (answer or "no answer") is inside its TTL."""
    import time

    return (
        _FLM_CATALOG_CACHE is not None
        and (time.monotonic() - _FLM_CATALOG_CACHED_AT) < _FLM_CATALOG_TTL_S
    )


async def flm_served_models_async() -> list[dict[str, Any]]:
    """:func:`flm_served_models` for async callers: never probes on the event loop.

    A fresh cache answers inline. A cold or expired one runs the blocking
    ``flm list -j`` (up to its 30 s timeout) on a worker thread via
    :func:`asyncio.to_thread`, so a slow ``flm`` slows only the caller, not
    every other request, SSE stream and WebSocket (#2334).

    Async code that reaches the catalog through a sync helper
    (``models_for_capability``, :func:`is_flm_tag`, ``is_resolvable``) awaits
    this first, so the helper's own read is a cache hit.
    """
    if _flm_catalog_fresh():
        return flm_served_models()
    return await asyncio.to_thread(flm_served_models)


def reset_flm_catalog_cache() -> None:
    """Drop the cached FLM catalog so the next call re-probes immediately.

    Exposed for tests and for the "refresh catalog" CLI/UI hook. The 5-minute
    TTL bounds staleness on its own; this forces an out-of-band refresh (e.g.
    right after a ``flm pull``).
    """
    global _FLM_CATALOG_CACHE, _FLM_CATALOG_CACHED_AT, _FLM_CATALOG_UNANSWERED
    with _FLM_CATALOG_LOCK:
        _FLM_CATALOG_CACHE = None
        _FLM_CATALOG_CACHED_AT = 0.0
        _FLM_CATALOG_UNANSWERED = False


def is_flm_tag(model_id: str) -> bool:
    """True iff ``model_id`` matches an FLM-served tag.

    Routing helper for the pull endpoint: FLM tags are Ollama-style
    ``family:size`` ids (``qwen3:0.6b``, ``deepseek-r1:8b``, …) and
    don't carry HF coords, so the generic HF pull path can't pull them.
    Looks them up against the cached :func:`flm_served_models` so we
    only treat ids the toolbox actually knows about — a stray ``foo:bar``
    falls through to the HF resolver and gets a proper 422.
    """
    if ":" not in model_id:
        return False
    return any(m["tag"] == model_id for m in flm_served_models())


def is_installed_flm_id(model_id: str) -> bool:
    """True iff ``model_id`` is the ``<tag>-FLM`` id of an INSTALLED FLM model.

    FLM models are host-flm-owned tags and are **never** in hal0's ModelRegistry
    (see docs/internal/brain-redesign/{model,slot}-shapes-audit-2026-06-07.md):
    the registry is the source of truth for GGUF/local models only. So the
    slot-apply registry gate (``routes/slots.py``) wrongly rejects a perfectly
    loadable FLM model — yet the npu.toml ``[model].default`` config path loads
    the same id fine. This lets slot-apply accept an on-disk
    FLM model by *provider-resolvability* instead of registry membership.

    ``model_id`` is the served ``-FLM`` form (``gemma4-it-e4b-FLM``); we
    match it against the forward transform of each installed probe tag
    (``gemma4-it:e4b`` → ``gemma4-it-e4b-FLM``), the same map used to synthesise
    the picker rows in ``routes/models.py``.
    """
    if not model_id.endswith("-FLM"):
        return False
    return any(
        m.get("installed") and m["tag"].replace(":", "-") + "-FLM" == model_id
        for m in flm_served_models()
    )


def flm_id_to_tag(model_id: str) -> str | None:
    """Resolve a hal0 ``<tag>-FLM`` id back to FLM's native ``family:size`` tag.

    Inverse of the forward map in :func:`is_installed_flm_id`
    (``gemma4-it:e2b`` → ``gemma4-it-e2b-FLM``). FLM's ``serve``/``pull``
    subcommands only accept the colon tag, so any code that hands a hal0
    catalog id straight to FLM (the slot manager's ``flm_tag`` stamp) must
    translate first — otherwise FLM answers ``Model not found``.

    Matches on the served-catalog tag regardless of ``installed`` (the
    transform is a pure naming map). Returns the colon tag, or ``None`` when
    ``model_id`` isn't a recognised FLM id or the catalog probe is empty —
    the caller then falls back to the raw id.
    """
    if not model_id.endswith("-FLM"):
        return None
    for m in flm_served_models():
        tag = m.get("tag")
        if isinstance(tag, str) and tag.replace(":", "-") + "-FLM" == model_id:
            return tag
    return None


async def flm_id_to_tag_async(model_id: str) -> str | None:
    """:func:`flm_id_to_tag` for async callers: the catalog read never blocks the loop.

    Only a ``-FLM`` id reads the catalog; that read goes through
    :func:`flm_served_models_async` first, so a cold cache is probed on a
    worker thread (#2334).
    """
    if model_id.endswith("-FLM"):
        await flm_served_models_async()
    return flm_id_to_tag(model_id)


def flm_pull_command(tag: str) -> tuple[list[str], str]:
    """Return ``(argv, host_models_dir)`` for a host ``flm pull <tag>`` run.

    Uses the host ``/usr/bin/flm`` (same binary the NPU slot serves with),
    NOT a docker toolbox. The caller spawns ``argv`` with
    :func:`flm_host_spawn_kwargs` so it runs as the ``hal0`` user with ``HOME``
    set; flm writes to its hardcoded ``$HOME/.config/flm/models``. The returned
    ``host_models_dir`` is the RESOLVED store (:func:`_host_flm_models_dir`),
    which the caller uses for progress polling + registry bookkeeping and which
    the serving container bind-mounts — so when the store is relocated it
    differs from flm's default path. :func:`ensure_host_flm_store_link` (call
    it before the pull) symlinks flm's default path onto the store so the two
    agree; without it a relocated-store pull lands weights on the root-fs cache
    and progress reads 0.

    No ``--device``: ``flm pull`` downloads files; it doesn't touch the NPU,
    so it still runs on dev hosts without XDNA passthrough.
    """
    return [_HOST_FLM_BIN, "pull", tag], _host_flm_models_dir()


_FLM_PROGRESS_RE = re.compile(
    r"Downloading:\s*([0-9.]+)%\s*\(([0-9.]+)\s*([KMGT]?)B\s*/\s*([0-9.]+)\s*([KMGT]?)B\)"
)


def parse_flm_progress(line: str) -> tuple[int, int] | None:
    """Extract ``(bytes_downloaded, bytes_total)`` from a ``flm pull`` line.

    FLM emits progress like::

        [FLM]  Downloading: 38.8% (253.0MB / 652.1MB)

    Returns ``None`` on lines that don't match (status, hash check,
    blank, etc.) so the caller can skip them without branching.
    """
    m = _FLM_PROGRESS_RE.search(line)
    if not m:
        return None
    _pct, cur, cur_unit, tot, tot_unit = m.groups()
    units = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    try:
        bytes_downloaded = int(float(cur) * units[cur_unit])
        bytes_total = int(float(tot) * units[tot_unit])
    except (KeyError, ValueError):
        return None
    return bytes_downloaded, bytes_total
