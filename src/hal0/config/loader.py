"""Config loaders — read and validate TOML files at startup.

All loaders return validated pydantic models.  A ValidationError at startup
means the user has a malformed config file; the error message includes the
field path (PLAN.md §5 Tier 1: "Typos in [slot] backend = vukan raise at
startup with the field path").

Atomic writes mirror hal0.config.env.write_env_atomic: write to a tmpfile
in the same directory, fsync, then os.replace().  If the process dies
mid-write the prior file is left intact.

Port target: haloai lib/config.py (420 lines).
See PLAN.md §3 and §5 Tier 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import tomllib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

from hal0.config import paths
from hal0.config.locking import PerLoopLock, async_file_lock, file_lock
from hal0.config.schema import (
    CURRENT_SCHEMA_VERSION,
    REMOVED_SLOT_KEYS,
    SEED_PROFILES,
    SEED_STACKS,
    AgentConfig,
    Hal0Config,
    HardwareInfo,
    ProfileConfig,
    ProfilesConfig,
    ProvidersConfig,
    SlotConfig,
    StackConfig,
    StacksConfig,
    UpstreamsConfig,
)
from hal0.errors import Hal0Error

log = logging.getLogger(__name__)

# ── Typed errors ──────────────────────────────────────────────────────────────


class ConfigError(Hal0Error):
    """Base class for config load/save errors."""

    code = "config.error"
    status = 500


class ConfigNotFound(ConfigError):
    """A required config file does not exist."""

    code = "config.not_found"
    status = 404


class ConfigParseError(ConfigError):
    """A config file is present but contains invalid TOML or fails validation."""

    code = "config.parse_error"
    status = 500


# ── Atomic TOML write ─────────────────────────────────────────────────────────


def write_toml_atomic(
    path: Path | str,
    data: dict[str, Any],
    *,
    mode: int | None = None,
    owner: tuple[int, int] | None = None,
) -> None:
    """Write a TOML file atomically.

    Mirrors hal0.config.env.write_env_atomic but for TOML payloads:
    write to a tempfile in the same directory, fsync, then os.replace().
    The rename is atomic on POSIX when src and dst share a mount; because
    the tempfile is created in the same directory, that invariant holds.

    Args:
        path: Destination path for the TOML file.
        data: Mapping that tomli_w.dump understands.
        mode: Optional explicit mode for the result. ``tempfile.mkstemp``
            creates the replacement 0600 and ``os.replace`` carries that mode
            onto the destination, so a file whose canonical mode is anything
            else drifts on every rewrite until ``doctor perms --fix`` runs.
            Callers with a canonical mode pass it here.
        owner: Optional ``(uid, gid)`` for the result, set with ``fchown`` on
            the temp file's descriptor before the rename. A root caller
            writing into a directory another account can write must use this
            instead of chowning the destination afterwards: a ``chown`` by
            name follows a symlink swapped in after the rename. Only root may
            give a file away, so a non-root caller's ``PermissionError`` is
            ignored and the file keeps the caller's ownership.

    Raises:
        OSError: If the directory cannot be created, disk full, or the
                 rename fails for a filesystem reason.
        TypeError: If data contains non-TOML-encodable values.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "wb") as f:
                # fchmod, not chmod: the mode is applied to the descriptor we
                # already own, before the rename — so there is never a moment
                # where the destination carries the wrong mode, and no second
                # path lookup that a swapped tempfile could ride in on.
                if owner is not None:
                    with contextlib.suppress(PermissionError):
                        os.fchown(f.fileno(), owner[0], owner[1])
                if mode is not None:
                    os.fchmod(f.fileno(), mode)
                tomli_w.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None  # rename succeeded; don't clean up in finally
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


# ── TOML reader ───────────────────────────────────────────────────────────────


def _read_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file and return its contents as a dict.

    Raises:
        ConfigNotFound: If the file does not exist.
        ConfigParseError: If the file cannot be parsed as TOML.
    """
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as exc:
        raise ConfigNotFound(
            f"config file not found: {path}",
            details={"path": str(path)},
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError(
            f"failed to parse TOML at {path}: {exc}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


# ── hal0.toml ─────────────────────────────────────────────────────────────────

#: Table -> key pairs pulled out of a raw ``hal0.toml`` dict before
#: validation, for fields that used to exist and no longer do.
#: ``BrainChatConfig`` is ``extra="forbid"`` (a leaf tunable table), so a
#: file written before a field's removal would otherwise hard-fail
#: :meth:`Hal0Config.model_validate` on every boot — not just after the
#: packaged ``hal0 update`` path (which runs ``hal0.config.migrations``
#: first); a plain code swap + service restart hits this too. Dropping the
#: key here makes every load path forgiving regardless of how the new code
#: got onto disk.
#: NOTE: ``[brain_chat] tool_model`` (#1453 briefly called it dead) is back
#: as a real, read field — see ``BrainChatConfig.tool_model`` — routing tool
#: rounds to a tool-capable model (Stream G, GH #1546-era). No dead keys
#: tracked here at the moment; kept as the seam for the next one.
_DEAD_KEYS: dict[str, tuple[str, ...]] = {}


def _drop_dead_keys(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip fields removed from the schema out of a raw config dict.

    A no-op (returns ``raw`` unchanged) when none of ``_DEAD_KEYS`` are
    present, so an already-clean file isn't rewritten or copied.
    """
    out = raw
    for table, keys in _DEAD_KEYS.items():
        section = out.get(table)
        if not isinstance(section, dict):
            continue
        present = [k for k in keys if k in section]
        if not present:
            continue
        if out is raw:
            out = dict(raw)
        out[table] = {k: v for k, v in section.items() if k not in keys}
    return out


def config_file_key(path: Path | str) -> tuple[int, int, int] | None:
    """Change key for a config file: ``(mtime_ns, inode, size)``, or None if absent.

    Every writer replaces hal0.toml atomically (a new inode) or rewrites it in
    place (a new mtime, usually a new size), so a cache keyed on all three sees
    a Settings save, a CLI write or a hand edit even when two writes land within
    the filesystem's mtime granularity. Shared by the per-request origin gate
    (``hal0.api.agents._auth``) and ``GET /api/settings``.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_ino, st.st_size)


def load_hal0_config(path: Path | None = None) -> Hal0Config:
    """Load and validate hal0.toml.

    Args:
        path: Override path.  If None, uses hal0.config.paths.hal0_toml().

    Returns:
        A validated Hal0Config.  If the file does not exist, returns the
        default config (all defaults, schema_version=CURRENT_SCHEMA_VERSION).

    Raises:
        ConfigParseError: If the TOML is malformed or fails validation.
    """
    target = path if path is not None else paths.hal0_toml()
    if not Path(target).exists():
        return Hal0Config()
    raw = _read_toml(Path(target))
    raw = _drop_dead_keys(raw)
    try:
        return Hal0Config.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate hal0 config at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


#: Serializes concurrent hal0.toml read-modify-write between coroutines in the
#: API process. Held by :func:`hal0_config_txn`, which is the ONLY supported way
#: to run a read-modify-write on hal0.toml from the event loop — do not acquire
#: this by hand at a call site (#1721: four routes each hand-rolled the RMW and
#: three of them forgot the lock entirely).
#:
#: A per-route lock is not enough on its own (#1682 review): the memory/graph
#: route holds its critical section across a multi-second-to-~60s
#: extraction-slot propagation, and without a SHARED lock an unrelated settings
#: save can land mid-flight, persisting a config snapshot taken before the
#: propagation started and clobbering the section it just wrote.
HAL0_TOML_LOCK: PerLoopLock = PerLoopLock()


class ConfigLockBusy(ConfigError):
    """Another writer held hal0.toml's advisory lock past the wait timeout."""

    code = "config.lock_busy"
    status = 503


def save_hal0_config(cfg: Hal0Config, path: Path | None = None) -> None:
    """Atomically write hal0.toml.

    Low-level writer: it takes NO lock. Any read-modify-write (load → mutate →
    save) must run inside :func:`hal0_config_txn` (async) or
    :func:`hal0_config_file_lock` (sync/threaded) instead of calling this
    directly, or it can clobber a concurrent writer's section. Calling it bare
    is correct only when the whole config is being written from nothing (tests,
    first-run seeding).

    Args:
        cfg: Validated Hal0Config to persist.
        path: Override path.  If None, uses hal0.config.paths.hal0_toml().
    """
    target = path if path is not None else paths.hal0_toml()
    # ``exclude_none=True`` keeps tomli_w happy — None has no TOML
    # representation and tomli_w raises TypeError on it. Pydantic
    # re-supplies the default on load, so dropping None on write is
    # safe for any field whose default is None.
    data = cfg.model_dump(mode="python", exclude_none=True)
    write_toml_atomic(target, data)


# ── the one serialized hal0.toml read-modify-write path (#1721) ───────────────
#
# Every writer that reads hal0.toml, changes part of it and writes it back is a
# lost-update hazard against every other writer. #1717 fixed one PAIR of them
# by sharing an asyncio lock between two routes; #1721 is the same bug arriving
# from the four routes that were never taught about it. A lock every future
# caller has to remember is the failure mode, so the lock now lives INSIDE the
# accessor and the accessor is the API:
#
#   async with hal0_config_txn(request) as txn:      # loads fresh under lock
#       txn.config.security.require_auth = True
#       txn.save()                                   # writes + refreshes cache
#
# Three properties fall out of putting it here rather than at each call site:
#
#   * **Fresh read.** The config handed to the body is loaded from DISK inside
#     the critical section, never ``app.state.hal0_config`` — the cache is a
#     startup snapshot that any other writer may have invalidated (#1717
#     review found exactly this: a settings PUT waiting behind a graph PUT
#     merged into the pre-graph snapshot and erased the graph section).
#   * **Cache coherence.** ``txn.save()`` refreshes ``app.state.hal0_config``
#     when a request was passed, so the "writer forgot to update the cache"
#     variant of the same bug cannot come back either.
#   * **Cross-process exclusion.** The in-process ``asyncio.Lock`` cannot see
#     the CLI, the installer, or ``installer/install.sh``'s post-activation
#     migration step — and that step deliberately runs BEFORE it restarts
#     hal0-api, i.e. against a live daemon that may be serving a settings PUT.
#     So the txn also takes the same ``fcntl`` advisory lock
#     (``hal0.toml.lock``) that the sync writers in the updater and installer
#     now take, which is what actually serializes those two processes.
#     ``/etc/hal0/*.lock`` already has a perms row, so no new install surface.


class Hal0ConfigTxn:
    """Handle yielded by :func:`hal0_config_txn` — a locked RMW on hal0.toml."""

    def __init__(self, config: Hal0Config, path: Path, request: Any | None = None) -> None:
        #: Config loaded from disk inside the lock. Mutate it (or build a
        #: replacement) and hand the result to :meth:`save`.
        self.config = config
        self.path = path
        self._request = request

    def save(self, cfg: Hal0Config | None = None) -> Hal0Config:
        """Persist ``cfg`` (default: :attr:`config`) and refresh the app cache.

        Returns the config that was written, so callers can echo it back.
        """
        new = self.config if cfg is None else cfg
        save_hal0_config(new, self.path)
        self.config = new
        request = self._request
        if request is not None:
            with contextlib.suppress(AttributeError):
                request.app.state.hal0_config = new
        return new


@dataclass
class _ActiveTxn:
    """The open transaction for one hal0.toml path, and the task that owns it."""

    task: Any
    txn: Hal0ConfigTxn
    depth: int = 1


#: Open :func:`hal0_config_txn` per hal0.toml path. Only ever read/written from
#: the loop thread while the locks are held, so a plain dict is sufficient.
_TXN_OWNERS: dict[str, _ActiveTxn] = {}


@contextlib.asynccontextmanager
async def hal0_config_txn(
    request: Any | None = None,
    *,
    path: Path | None = None,
    timeout: float | None = 60.0,
) -> AsyncIterator[Hal0ConfigTxn]:
    """Serialized read-modify-write of hal0.toml, for the event loop.

    Holds :data:`HAL0_TOML_LOCK` (in-process) and the ``hal0.toml.lock``
    advisory file lock (cross-process) for the whole body, and yields a
    :class:`Hal0ConfigTxn` whose ``config`` was loaded from disk under both.

    The body may await: the memory/graph route deliberately keeps its
    hindsight-api propagation inside the critical section so no other writer
    can persist a pre-propagation snapshot over it. A cross-process writer
    blocking behind that is the intended outcome — waiting is strictly better
    than the lost update it would otherwise commit.

    Args:
        request: Optional Starlette request; when given, ``txn.save()`` also
            refreshes ``request.app.state.hal0_config``.
        path: Override hal0.toml path (tests).
        timeout: Seconds to wait for the cross-process lock before failing the
            request with :class:`ConfigLockBusy`.

    Raises:
        ConfigLockBusy: Another process held the advisory lock too long.
    """
    target = Path(path) if path is not None else paths.hal0_toml()

    # Re-entrancy (#1721 review): ``HAL0_TOML_LOCK`` is reached BEFORE the file
    # lock's own owner check, so a writer that opened a txn and then called a
    # helper which opens another would hang here forever rather than fail.
    #
    # The nested caller gets the SAME handle, not a fresh load (#1721 review
    # round 2). Re-reading would fork the working state: an outer caller that
    # mutated ``txn.config`` and then reached a helper would hand that helper a
    # model without its edits, and the outer ``save()`` would later write its
    # own stale object back over whatever the inner one committed — a lost
    # update inside a single request, which is the exact bug class this
    # accessor exists to remove.
    key = str(target)
    current_task = asyncio.current_task()
    active = _TXN_OWNERS.get(key)
    if current_task is not None and active is not None and active.task is current_task:
        active.depth += 1
        try:
            yield active.txn
        finally:
            active.depth -= 1
        return

    async with HAL0_TOML_LOCK:
        # ``acquired`` narrows the except to the ACQUIRE. A body that raises its
        # own TimeoutError (the memory route awaits a bounded hindsight-api
        # restart in here) must surface as itself, not be relabelled "another
        # process is writing hal0.toml".
        acquired = False
        try:
            async with async_file_lock(target, timeout=timeout):
                acquired = True
                txn = Hal0ConfigTxn(load_hal0_config(target), target, request)
                if current_task is not None:
                    _TXN_OWNERS[key] = _ActiveTxn(task=current_task, txn=txn)
                try:
                    yield txn
                finally:
                    if current_task is not None:
                        _TXN_OWNERS.pop(key, None)
        except TimeoutError as exc:
            if acquired:
                raise
            raise ConfigLockBusy(
                f"another process is writing {target} — try again",
                details={"path": str(target)},
            ) from exc


@contextlib.contextmanager
def hal0_config_file_lock(path: Path | None = None) -> Iterator[Path]:
    """Serialized read-modify-write of hal0.toml, for sync/threaded writers.

    The blocking counterpart of :func:`hal0_config_txn` for code that is not on
    the event loop — the updater's schema-migration passes (``asyncio.to_thread``
    inside the API process, or a bare ``hal0`` process from ``install.sh``) and
    the installer's storage-choice writer. Takes the same ``hal0.toml.lock``, so
    those writers serialize against the API's routes and each other; it
    deliberately does NOT touch :data:`HAL0_TOML_LOCK`, which is an
    ``asyncio.Lock`` and cannot be acquired off-loop.

    Yields the locked hal0.toml path so callers can read it inside the body.
    """
    target = Path(path) if path is not None else paths.hal0_toml()
    with file_lock(target):
        yield target


# ── slots/<name>.toml ─────────────────────────────────────────────────────────


def load_slot_config(slot_name: str, path: Path | None = None) -> SlotConfig:
    """Load and validate /etc/hal0/slots/<slot_name>.toml.

    The on-disk shape (per haloai lib/config.py) nests fields under
    [slot], [model], etc.  We normalise that into the flat SlotConfig
    shape: [slot] keys hoist to the top level, [model] stays nested, and
    every other table keeps its own name. A key the schema does not declare
    fails the load, naming the key and the file (ADR-0029 §5).

    Args:
        slot_name: e.g. "primary", "embed", "stt", "tts".
        path: Override path.  If None, uses
              hal0.config.paths.slots_config_dir() / f"{slot_name}.toml".

    Returns:
        A validated SlotConfig.

    Raises:
        ConfigNotFound: If the slot TOML doesn't exist.
        ConfigParseError: If the TOML is malformed, fails validation or
            carries a key the schema does not declare.
    """
    target = path if path is not None else paths.slots_config_dir() / f"{slot_name}.toml"
    raw = _read_toml(Path(target))
    flattened = _flatten_slot_toml(raw, slot_name=slot_name)
    try:
        return SlotConfig.model_validate(flattened)
    except Exception as exc:
        reason, unknown = _slot_validation_reason(exc)
        raise ConfigParseError(
            f"failed to validate slot config {slot_name!r} at {target}: {reason}",
            details={
                "path": str(target),
                "slot": slot_name,
                "reason": reason,
                "unknown_keys": unknown,
            },
        ) from exc


def load_slot_config_by_id(slot_id: int, path: Path | None = None) -> SlotConfig:
    """Load and validate an id-keyed slot TOML (``slots/<slot_id>.toml``).

    The id-keyed sibling of :func:`load_slot_config`. An id-keyed TOML is
    self-describing: the migrator (and every bilingual writer) embeds both the
    stable ``id`` and the human ``name``, so the returned :class:`SlotConfig`
    carries the real display ``name`` — never the digit filename. The digit
    stem is passed to ``_flatten_slot_toml`` only as the *last-resort* fallback
    for a genuinely nameless file; a well-formed id-keyed TOML overrides it from
    its embedded ``name``.

    Raises:
        ConfigNotFound: If the id TOML doesn't exist.
        ConfigParseError: If the TOML is malformed or fails validation.
    """
    target = path if path is not None else paths.slots_config_dir() / f"{slot_id}.toml"
    raw = _read_toml(Path(target))
    flattened = _flatten_slot_toml(raw, slot_name=str(slot_id))
    try:
        return SlotConfig.model_validate(flattened)
    except Exception as exc:
        reason, unknown = _slot_validation_reason(exc)
        raise ConfigParseError(
            f"failed to validate id-keyed slot config {slot_id!r} at {target}: {reason}",
            details={
                "path": str(target),
                "slot_id": slot_id,
                "reason": reason,
                "unknown_keys": unknown,
            },
        ) from exc


def save_slot_config(cfg: SlotConfig, path: Path | None = None) -> None:
    """Atomically write a slot config TOML.

    The pydantic SlotConfig is flat; we re-nest into the on-disk shape
    that haloai writes ([slot] / [model] sections) so hand-edits stay
    readable.

    Bilingual key (P3-runtime-db): the on-disk stem is the stable ``cfg.id``
    when one is set (id-keyed layout, post-M5-migration) and the mutable
    ``cfg.name`` otherwise (name-keyed, the pre-migration default). Either way
    the serialized ``[slot]`` table embeds BOTH ``id`` (when present) and
    ``name`` so the file is self-describing and a reader recovers the display
    name without the identity DB. An explicit *path* overrides the derivation
    untouched.

    Args:
        cfg: Validated SlotConfig to persist.
        path: Override path.  If None, derives the stem from ``cfg.id`` (when
              set) else ``cfg.name``.
    """
    if path is not None:
        target = path
    else:
        stem = cfg.id if cfg.id is not None else cfg.name
        target = paths.slots_config_dir() / f"{stem}.toml"
    data = _unflatten_slot_toml(cfg)
    write_toml_atomic(target, data)


def read_slot_profile_ref(slot_name: str) -> tuple[str, str | None]:
    """Return ``(display_name, profile)`` from a slot TOML without validating it.

    The untyped sibling of :func:`load_slot_config` for the profile in-use scan:
    a slot file that fails the typed load (an unconverted tune, a stale key)
    still *names* its profile, and a profile delete must see that. Resolves both
    layouts exactly as the typed loader does (top-level ``profile`` or
    ``[slot].profile``) by sharing :func:`_flatten_slot_toml`.

    Raises:
        ConfigNotFound: If the slot TOML doesn't exist.
        ConfigParseError: If the file cannot be parsed as TOML at all.
    """
    raw = _read_toml(paths.slots_config_dir() / f"{slot_name}.toml")
    flat = _flatten_slot_toml(raw, slot_name=slot_name)
    name = flat.get("name")
    profile = flat.get("profile")
    return (
        name if isinstance(name, str) and name else slot_name,
        profile if isinstance(profile, str) and profile else None,
    )


def list_slots() -> list[str]:
    """Return all configured slot *stems* of /etc/hal0/slots/*.toml, sorted.

    NAME-KEYED public wrapper — preserved verbatim for the callers (doctor
    bundle, portable export, slot-flags migration) that still address slots by
    their on-disk stem. On a name-keyed box every stem IS the slot name; on an
    id-keyed box a stem may be a digit id (see :func:`list_slot_layout` for the
    per-stem classification, and the bilingual manager enumerator for the
    id→name recovery those callers migrate to in the follow lane).
    """
    d = paths.slots_config_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.toml"))


def list_slot_layout() -> dict[str, str]:
    """Classify every slots/*.toml stem as ``"id"`` or ``"name"``.

    The bilingual enumerator split out of :func:`list_slots`: a thin wrapper
    over :func:`hal0.slots.layout.classify_layout` so a caller can tell an
    id-keyed stem (``"143"``) from a name-keyed one (``"brain"``) without
    re-deriving the all-digit rule. Empty when the dir is absent.
    """
    from hal0.slots.layout import classify_layout

    return classify_layout(paths.slots_config_dir())


# ── slot TOML shape helpers ──────────────────────────────────────────────────


def _slot_validation_reason(exc: Exception) -> tuple[str, list[str]]:
    """One readable reason for a failed slot validation, plus the unknown keys.

    A key ``SlotConfig`` does not declare (``extra="forbid"``) is named as
    ``unknown key 'lru'`` (``'[model].foo'`` inside a table), with what replaced
    it when it is a key hal0 removed (:data:`REMOVED_SLOT_KEYS`). Every other
    failure keeps pydantic's own message under its field path, so a slot with
    one bad value and one stray key reports both. The second element lists the
    unknown keys for callers that act on them.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc), []
    try:
        entries = list(errors())
    except Exception:
        return str(exc), []
    unknown: list[str] = []
    parts: list[str] = []
    for entry in entries:
        loc = tuple(str(p) for p in entry.get("loc", ()))
        if entry.get("type") == "extra_forbidden":
            key = f"[{loc[0]}].{'.'.join(loc[1:])}" if len(loc) > 1 else ".".join(loc)
            unknown.append(key)
            hint = REMOVED_SLOT_KEYS.get(key)
            parts.append(f"unknown key {key!r}" + (f" ({hint})" if hint else ""))
        elif loc == ("image",) and isinstance(entry.get("input"), str):
            # A bare ``image = "<ref>"`` is the retired container-image override.
            unknown.append("image")
            parts.append(
                "unknown key 'image' (a bare image ref was renamed 'image_pin'; "
                "'[image]' is the image-generation settings table)"
            )
        else:
            where = ".".join(loc)
            msg = str(entry.get("msg", ""))
            parts.append(f"{where}: {msg}" if where else msg)
    if unknown:
        parts.append(
            "SlotConfig declares every key a slot file may carry (ADR-0029); remove the "
            "key, or run `hal0 profile migrate` for a file written before ADR-0029"
        )
    return "; ".join(parts) or str(exc), unknown


def _flatten_slot_toml(raw: dict[str, Any], slot_name: str) -> dict[str, Any]:
    """Normalise both slot-TOML shapes into the flat SlotConfig shape.

    Two on-disk shapes exist and must both load:

    Nested (haloai-compatible) — slot fields under a ``[slot]`` table::

        [slot]
        name = "primary"
        port = 8081
        device = "gpu-vulkan"

        [model]
        default = "qwen3-4b-q4_k_m"

    Flat — slot fields as top-level scalars alongside sibling tables::

        name = "primary"
        port = 8081
        profile = "chat-gpu"
        device = "gpu-vulkan"

        [model]
        default = "qwen3-4b-q4_k_m"

        [server]
        env = { HSA_OVERRIDE_GFX_VERSION = "11.0.0" }

    In both shapes, slot fields are *scalars* while sibling sections
    (``[model]``, ``[server]``, ``[npu]``, ``[image]``, …) are *tables*. We
    hoist every top-level scalar plus the ``[slot]`` table to the top level
    (``[slot]`` wins on a clash, as it does on the launch path) and carry every
    sibling table through under its own name. Nothing is set aside: a key or a
    table ``SlotConfig`` does not declare reaches it and fails the load, naming
    the key (ADR-0029 §5).
    """
    if not isinstance(raw, dict):
        raw = {}

    out: dict[str, Any] = {
        k: v for k, v in raw.items() if k not in ("slot", "model") and not isinstance(v, dict)
    }
    slot_section = raw.get("slot")
    if isinstance(slot_section, dict):
        out.update(slot_section)

    # Fall back to the on-disk filename for `name` if the TOML omits it.
    out.setdefault("name", slot_name)

    # Nested [model] section.
    model_section = raw.get("model")
    if isinstance(model_section, dict):
        out["model"] = model_section

    # Heal-on-load (O23): a type-less, llm-shaped slot defaults to type="llm"
    # so hal0/<slot> aliases resolve on boxes whose TOML predates the seeded
    # type key. Skip when raw carries an [image] table (image-gen slot): the
    # tables are carried over below, so guard on ``raw`` explicitly (the
    # comfyui provider guards it too).
    from hal0.slots._cfg_helpers import heal_missing_llm_type

    if not isinstance(raw.get("image"), dict):
        heal_missing_llm_type(out)

    # Sibling tables ([server], [npu], [image], and any the schema does not
    # know) keep their own names.
    for k, v in raw.items():
        if k in ("slot", "model") or k in out or not isinstance(v, dict):
            continue
        out[k] = v

    return out


def _unflatten_slot_toml(cfg: SlotConfig) -> dict[str, Any]:
    """Inverse of _flatten_slot_toml — produce the on-disk shape.

    Every declared scalar goes under ``[slot]`` (the stable ``id`` only when one
    is assigned, so a name-keyed file round-trips without a stray ``id``), the
    typed tables keep their own names, and a value of ``None`` — which TOML
    cannot represent — is left out.
    """
    data = cfg.model_dump(mode="python", exclude_none=True)
    model = data.pop("model", {})
    tables = {k: data.pop(k) for k in ("server", "npu", "image", "tune") if k in data}
    out: dict[str, Any] = {"slot": data, "model": model}
    out.update(tables)
    return out


# ── providers.toml ────────────────────────────────────────────────────────────


def load_providers_config(path: Path | None = None) -> ProvidersConfig:
    """Load and validate providers.toml.

    Returns an empty ProvidersConfig if the file does not exist.
    """
    target = path if path is not None else paths.etc() / "providers.toml"
    if not Path(target).exists():
        return ProvidersConfig()
    raw = _read_toml(Path(target))
    try:
        return ProvidersConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate providers.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_providers_config(cfg: ProvidersConfig, path: Path | None = None) -> None:
    """Atomically write providers.toml."""
    target = path if path is not None else paths.etc() / "providers.toml"
    write_toml_atomic(target, cfg.model_dump(mode="python"))


# ── upstreams.toml ────────────────────────────────────────────────────────────


def load_upstreams_config(path: Path | None = None) -> UpstreamsConfig:
    """Load and validate upstreams.toml.

    Returns an empty UpstreamsConfig if the file does not exist.
    """
    target = path if path is not None else paths.etc() / "upstreams.toml"
    if not Path(target).exists():
        return UpstreamsConfig()
    raw = _read_toml(Path(target))
    try:
        return UpstreamsConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate upstreams.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_upstreams_config(cfg: UpstreamsConfig, path: Path | None = None) -> None:
    """Atomically write upstreams.toml.

    Uses ``exclude_none=True`` so optional fields that were never set
    (``slot_name`` on remote-kind entries) don't surface as TOML-incompatible
    ``None`` values. The round-trip through ``load_upstreams_config`` is
    unaffected: any field omitted on write is re-defaulted on read.
    """
    target = path if path is not None else paths.etc() / "upstreams.toml"
    write_toml_atomic(
        target,
        cfg.model_dump(mode="python", exclude_none=True),
        mode=paths.UPSTREAMS_TOML_MODE,
    )


# ── profiles.toml ─────────────────────────────────────────────────────────────


#: Quarantined profile entries already logged, by (path, name, reason).
_QUARANTINE_LOGGED: set[tuple[str, str, str]] = set()


def _validation_reason(exc: Exception) -> str:
    """One line naming each invalid field (``quant: Extra inputs are not permitted``)."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return "; ".join(
                f"{'.'.join(str(p) for p in e.get('loc', ())) or 'entry'}: {e.get('msg', '')}"
                for e in errors()
            )
        except Exception:
            pass
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


def load_profiles_config(path: Path | None = None) -> ProfilesConfig:
    """Load and validate /etc/hal0/profiles.toml, with the seeds overlaid.

    Seed profiles are **virtual**: they come from code (``SEED_PROFILES``) on
    every load and win over a same-named disk entry; operator profiles on disk
    are returned as written. No file means a fresh install: the seeds alone.

    One invalid entry never fails the catalog: it is logged and quarantined
    (kept verbatim on disk, resolving to ``profiles.invalid`` for the slots
    that name it). Unknown top-level keys are logged and kept as written.

    Raises:
        ConfigParseError: If the TOML itself does not parse.
    """
    target = path if path is not None else paths.profiles_toml()
    if not Path(target).exists():
        return ProfilesConfig.model_validate({"profile": SEED_PROFILES})
    raw = _read_toml(Path(target))
    entries = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    cfg = ProfilesConfig()
    for name, entry in entries.items():
        try:
            cfg.profile[str(name)] = ProfileConfig.model_validate(entry)
        except Exception as exc:
            reason = _validation_reason(exc)
            key = (str(target), str(name), reason)
            if key not in _QUARANTINE_LOGGED:  # once per entry, not on every load
                _QUARANTINE_LOGGED.add(key)
                log.warning(
                    "profiles.entry_quarantined name=%s path=%s reason=%s", name, target, reason
                )
            cfg.quarantined[str(name)] = {"entry": entry, "reason": reason}
    unknown = {k: v for k, v in raw.items() if k != "profile"}
    if unknown:
        log.warning("profiles.unknown_keys_kept keys=%s path=%s", sorted(unknown), target)
        cfg._unknown = unknown
    for key, seed_raw in SEED_PROFILES.items():
        cfg.profile[key] = ProfileConfig.model_validate(seed_raw)
        cfg.quarantined.pop(key, None)
    return cfg


def save_profiles_config(cfg: ProfilesConfig, path: Path | None = None) -> None:
    """Atomically write the operator (non-seed) profile catalog to profiles.toml.

    Serializes the operator-authored profiles in *cfg* to
    ``paths.profiles_toml()`` (or *path* if given) via
    :func:`write_toml_atomic`.  **Seed profiles are stripped before writing** —
    they are virtual (overlaid from :data:`SEED_PROFILES` on every
    :func:`load_profiles_config`), so persisting them here would only re-freeze
    a stale copy on disk, the exact bug the virtual-seed overlay fixes.  Callers
    may pass a full catalog (the seeds are simply dropped) or just their custom
    profiles; the on-load overlay restores every seed either way.

    Note: :func:`write_toml_atomic` emits pure TOML — no header comment is
    written (tomli_w has no comment support and the atomic writer takes no
    prefix).  ``exclude_none=True`` mirrors :func:`save_hal0_config` and
    keeps tomli_w from raising TypeError on optional None fields.

    Args:
        cfg: Validated :class:`ProfilesConfig` to persist (seed entries ignored).
        path: Override destination.  If ``None``, uses
              :func:`hal0.config.paths.profiles_toml`.
    """
    target = Path(path) if path is not None else paths.profiles_toml()
    data = cfg.model_dump(mode="python", exclude_none=True)
    # Drop virtual seed profiles — they always overlay from code at load time,
    # so writing them would freeze a stale copy on disk (the #PS-2 bug).
    profiles = data.get("profile") or {}
    data["profile"] = {name: entry for name, entry in profiles.items() if name not in SEED_PROFILES}
    # The reasoning tunes are sparse on disk: a profile at the defaults does not
    # grow the keys (an old hal0 reading the file would refuse them).
    for entry in data["profile"].values():
        if entry.get("reasoning_effort") == "default":
            entry.pop("reasoning_effort")
        if entry.get("reasoning_budget") == -1:
            entry.pop("reasoning_budget")
    # A quarantined entry (invalid as stored) is written back exactly as it
    # was, unless the operator has since defined that name validly.
    for name, held in cfg.quarantined.items():
        if name not in data["profile"] and name not in SEED_PROFILES:
            data["profile"][name] = held["entry"]
    for key, value in cfg._unknown.items():
        data.setdefault(key, value)
    write_toml_atomic(target, data)


# ── stacks.toml ───────────────────────────────────────────────────────────────


def load_stacks_config(path: Path | None = None) -> StacksConfig:
    """Load and validate /etc/hal0/stacks.toml, with the seeds overlaid.

    Seed stacks are **virtual**, exactly like seed profiles: they come from
    code (``SEED_STACKS``) on every load and win over a same-slug disk entry,
    which is ignored unvalidated (a stale copy written by an older release can
    name retired profiles, or fail today's schema, and must never block the
    catalog or be applied). Operator stacks on disk are returned as written.
    No file means a fresh install: the seeds alone. Seeds come first, in code
    order, then the operator stacks in file order.

    Raises:
        ConfigParseError: If the TOML is malformed or an operator stack fails
            validation.
        StacksStateUnreadable: If the file exists but cannot be read
            (permissions) — a root-written 0600 stacks.toml must surface as an
            actionable typed error, not a raw ``PermissionError`` (ct105).
    """
    target = path if path is not None else paths.stacks_toml()
    if not Path(target).exists():
        return _with_seed_stacks({})
    try:
        raw = _read_toml(Path(target))
    except PermissionError as exc:
        # Lazy import: hal0.stacks.__init__ imports this module, so a
        # module-level import here would be circular.
        from hal0.stacks.state import StacksStateUnreadable

        raise StacksStateUnreadable(
            f"stacks.toml unreadable at {target}: {exc}; fix: chgrp hal0 && chmod 664",
            details={"path": str(target)},
        ) from exc
    entries = raw.get("stack")
    if isinstance(entries, dict):
        raw = {**raw, "stack": {k: v for k, v in entries.items() if k not in SEED_STACKS}}
    try:
        return _with_seed_stacks(StacksConfig.model_validate(raw).stack)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate stacks.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def _with_seed_stacks(operator: dict[str, StackConfig]) -> StacksConfig:
    """The seed stacks (fresh copies) followed by the operator stacks."""
    seeds = {slug: stack.model_copy(deep=True) for slug, stack in SEED_STACKS.items()}
    return StacksConfig(stack={**seeds, **operator})


def save_stacks_config(cfg: StacksConfig, path: Path | None = None) -> None:
    """Atomically write the operator (non-seed) stack catalog to stacks.toml.

    Callers pass the COMPLETE catalog (start from ``load_stacks_config()``,
    then add/modify) so existing stacks survive the round trip. Seed stacks
    are dropped: they are virtual (overlaid from :data:`SEED_STACKS` on every
    load), and writing them would freeze a stale copy on disk, the bug the
    overlay fixes. The next write of an already-stale file therefore cleans
    it. ``exclude_none=True`` keeps tomli_w from raising on optional None
    fields, mirroring :func:`save_profiles_config`.
    """
    target = Path(path) if path is not None else paths.stacks_toml()
    data = cfg.model_dump(mode="python", exclude_none=True)
    stacks = data.get("stack") or {}
    data["stack"] = {slug: entry for slug, entry in stacks.items() if slug not in SEED_STACKS}
    write_toml_atomic(target, data)


def resolve_profile(profile_name: str) -> ProfileConfig:
    """Look up a named profile in the profiles.toml catalog.

    Shared by every provider that resolves slot profiles
    (ContainerProvider, KokoroProvider, …).

    Args:
        profile_name: Key under ``[profile]`` in profiles.toml.

    Returns:
        The validated :class:`ProfileConfig` for *profile_name*.

    Raises:
        KeyError: If the profile name is not in the catalog; the message
                  lists the available profile names.
    """
    catalog = load_profiles_config()
    if profile_name not in catalog.profile:
        available = sorted(catalog.profile.keys())
        raise KeyError(f"profile {profile_name!r} not found in catalog; available: {available}")
    return catalog.profile[profile_name]


# ── agents/<name>.toml ─────────────────────────────────────────────────────────


def load_agent_config(agent_name: str, path: Path | None = None) -> AgentConfig:
    """Load and validate ``/etc/hal0/agents/<agent_name>.toml``.

    Raises:
        ConfigNotFound: file missing.
        ConfigParseError: bad TOML or schema-validation failure.
    """
    target = path if path is not None else paths.agents_config_dir() / f"{agent_name}.toml"
    raw = _read_toml(Path(target))
    try:
        return AgentConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate agent config {agent_name!r} at {target}: {exc}",
            details={"path": str(target), "agent": agent_name, "reason": str(exc)},
        ) from exc


def save_agent_config(cfg: AgentConfig, path: Path | None = None) -> None:
    """Atomically write an agent config TOML.

    ``exclude_none=True`` keeps tomli_w from choking on optional blocks
    (``auth.env``, ``server.url`` on builtins).
    """
    target = path if path is not None else paths.agents_config_dir() / f"{cfg.agent.name}.toml"
    data = cfg.model_dump(mode="python", exclude_none=True)
    write_toml_atomic(target, data)


def list_agent_configs() -> list[str]:
    """Return every configured agent name (stem of /etc/hal0/agents/*.toml)."""
    d = paths.agents_config_dir()
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.toml") if f.is_file())


# ── hardware.json (JSON, not TOML) ────────────────────────────────────────────


def load_hardware_info(path: Path | None = None) -> HardwareInfo:
    """Load /etc/hal0/hardware.json and return a validated HardwareInfo.

    Returns the all-defaults HardwareInfo if the file is absent — the
    hardware module owns probing; callers that need real data should run
    `hal0 config hardware --refresh` first.

    Raises:
        ConfigParseError: If the JSON cannot be parsed or fails validation.
    """
    import json

    target = path if path is not None else paths.hardware_json()
    if not Path(target).exists():
        return HardwareInfo()
    try:
        raw = json.loads(Path(target).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigParseError(
            f"failed to parse hardware.json at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc
    try:
        return HardwareInfo.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate hardware.json at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_hardware_info(info: HardwareInfo, path: Path | None = None) -> None:
    """Atomically write hardware.json.

    Uses the same tmpfile+fsync+rename pattern as write_toml_atomic, but
    against JSON (hardware.json is human-readable JSON per PLAN.md §2).
    """
    import json

    target = Path(path if path is not None else paths.hardware_json())
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(info.model_dump(mode="python"), indent=2, sort_keys=True) + "\n"

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "HAL0_TOML_LOCK",
    "ConfigError",
    "ConfigLockBusy",
    "ConfigNotFound",
    "ConfigParseError",
    "Hal0ConfigTxn",
    "hal0_config_file_lock",
    "hal0_config_txn",
    "list_agent_configs",
    "list_slot_layout",
    "list_slots",
    "load_agent_config",
    "load_hal0_config",
    "load_hardware_info",
    "load_profiles_config",
    "load_providers_config",
    "load_slot_config",
    "load_slot_config_by_id",
    "load_upstreams_config",
    "read_slot_profile_ref",
    "resolve_profile",
    "save_agent_config",
    "save_hal0_config",
    "save_hardware_info",
    "save_profiles_config",
    "save_providers_config",
    "save_slot_config",
    "save_upstreams_config",
    "write_toml_atomic",
]
