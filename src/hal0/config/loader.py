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
    SEED_PROFILES,
    SEED_STACKS,
    AgentConfig,
    Hal0Config,
    HardwareInfo,
    ProfileConfig,
    ProfilesConfig,
    ProvidersConfig,
    SlotConfig,
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


def write_toml_atomic(path: Path | str, data: dict[str, Any]) -> None:
    """Write a TOML file atomically.

    Mirrors hal0.config.env.write_env_atomic but for TOML payloads:
    write to a tempfile in the same directory, fsync, then os.replace().
    The rename is atomic on POSIX when src and dst share a mount; because
    the tempfile is created in the same directory, that invariant holds.

    Args:
        path: Destination path for the TOML file.
        data: Mapping that tomli_w.dump understands.

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
    shape: [slot] keys hoist to the top level, [model] stays nested,
    everything else lands in ``extra``.

    Args:
        slot_name: e.g. "primary", "embed", "stt", "tts".
        path: Override path.  If None, uses
              hal0.config.paths.slots_config_dir() / f"{slot_name}.toml".

    Returns:
        A validated SlotConfig.

    Raises:
        ConfigNotFound: If the slot TOML doesn't exist.
        ConfigParseError: If the TOML is malformed or fails validation.
    """
    target = path if path is not None else paths.slots_config_dir() / f"{slot_name}.toml"
    raw = _read_toml(Path(target))
    flattened = _flatten_slot_toml(raw, slot_name=slot_name)
    try:
        return SlotConfig.model_validate(flattened)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate slot config {slot_name!r} at {target}: {exc}",
            details={"path": str(target), "slot": slot_name, "reason": str(exc)},
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
        raise ConfigParseError(
            f"failed to validate id-keyed slot config {slot_id!r} at {target}: {exc}",
            details={"path": str(target), "slot_id": slot_id, "reason": str(exc)},
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


def _flatten_slot_toml(raw: dict[str, Any], slot_name: str) -> dict[str, Any]:
    """Normalise both slot-TOML shapes into the flat SlotConfig shape.

    Two on-disk shapes exist and must both load:

    Legacy nested (haloai-compatible) — slot fields under a ``[slot]`` table::

        [slot]
        name = "primary"
        port = 8081
        backend = "vulkan"

        [model]
        default = "qwen3-4b-q4_k_m"

        [defaults]
        threads = 12

    Current flat — slot fields as top-level scalars alongside sibling tables::

        name = "primary"
        port = 8081
        profile = "chat-gpu"
        device = "gpu-vulkan"

        [model]
        default = "qwen3-4b-q4_k_m"

        [server]
        extra_args = "--foo"

    In both shapes, slot fields are *scalars* while sibling sections
    (``[model]``, ``[server]``, ``[npu]``, ``[image]``, ``[defaults]``, …)
    are *tables*. We therefore hoist every top-level scalar plus any
    ``[slot]`` table to the top level, keep ``[model]`` nested, and stash
    the remaining tables under ``extra`` for lossless round-tripping.
    """
    if not isinstance(raw, dict):
        raw = {}

    slot_section = raw.get("slot")
    out: dict[str, Any] = {}
    if isinstance(slot_section, dict):
        # Legacy nested shape: slot fields live under [slot]. Preserve the
        # original behaviour (every top-level sibling → extra) so stray
        # top-level scalars still round-trip untouched.
        out.update(slot_section)
    else:
        # Flat shape: slot fields are top-level scalars alongside sibling
        # tables. Hoist the scalars; tables are handled below.
        for k, v in raw.items():
            if k in ("slot", "model") or isinstance(v, dict):
                continue
            out[k] = v

    # Fall back to the on-disk filename for `name` if the TOML omits it.
    out.setdefault("name", slot_name)

    # Nested [model] section.
    model_section = raw.get("model")
    if isinstance(model_section, dict):
        out["model"] = model_section

    # Heal-on-load (O23): a type-less, llm-shaped slot defaults to type="llm"
    # so hal0/<slot> aliases resolve on boxes whose TOML predates the seeded
    # type key. Skip when raw carries an [image] table (image-gen slot) — that
    # table lands in ``extra`` here, so guard on ``raw`` explicitly (the
    # comfyui provider guards it too).
    from hal0.slots._cfg_helpers import heal_missing_llm_type

    if not isinstance(raw.get("image"), dict):
        heal_missing_llm_type(out)

    # Anything not already hoisted (sibling tables like [defaults],
    # [server], [npu], [image]; and, in the legacy shape, stray top-level
    # scalars) lands in `extra` so we don't lose it on round-trip.
    extra: dict[str, Any] = {}
    for k, v in raw.items():
        if k in ("slot", "model") or k in out:
            continue
        extra[k] = v
    if extra:
        out["extra"] = extra

    return out


def _unflatten_slot_toml(cfg: SlotConfig) -> dict[str, Any]:
    """Inverse of _flatten_slot_toml — produce the on-disk shape.

    Writes only ``device`` (P2-device: the sole persisted truth).
    ``backend`` is no longer a ``SlotConfig`` field — a legacy on-disk
    ``backend`` key is promoted to ``device`` and dropped on load (see
    ``SlotConfig._promote_backend_to_device``), so it never reaches this
    round-trip.
    """
    data = cfg.model_dump(mode="python", exclude_none=False)
    slot_tbl: dict[str, Any] = {
        "name": data["name"],
        "port": data["port"],
        "device": data["device"],
        "provider": data["provider"],
        "workers": data["workers"],
        "idle_timeout_s": data["idle_timeout_s"],
    }
    # Bilingual self-description (P3-runtime-db): stamp the stable ``id`` into
    # the [slot] table whenever one is assigned, so an id-keyed file names its
    # own row and a name-keyed file (id is None) round-trips byte-identically
    # to the pre-id shape (no stray ``id`` key).
    if data.get("id") is not None:
        slot_tbl["id"] = data["id"]
    out: dict[str, Any] = {
        "slot": slot_tbl,
        "model": data["model"],
    }
    # context_size is Optional (unset → derived at load by the provider);
    # None has no TOML representation, so elide any None in the model table.
    model_tbl = out.get("model")
    if isinstance(model_tbl, dict):
        out["model"] = {k: v for k, v in model_tbl.items() if v is not None}
    extra = data.get("extra") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            # NOTE: keep user-authored sections (`[defaults]`, etc.) at
            # their original top-level position on round-trip.
            if k in ("slot", "model"):
                continue
            out[k] = v
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
    write_toml_atomic(target, cfg.model_dump(mode="python", exclude_none=True))


# ── profiles.toml ─────────────────────────────────────────────────────────────


def load_profiles_config(path: Path | None = None) -> ProfilesConfig:
    """Load and validate /etc/hal0/profiles.toml.

    Returns a :class:`ProfilesConfig` seeded with the built-in bench
    profiles when the file is absent so ``GET /api/profiles`` is always
    populated on a fresh install.  Seed profiles are **virtual**: when the
    file *is* present, every seed key is overlaid from code (SEED_PROFILES),
    overwriting any on-disk copy, so a re-tuned seed always reaches the
    install; operator (non-seed) profiles on disk are returned unchanged.

    Args:
        path: Override path.  If None, uses
              :func:`hal0.config.paths.profiles_toml`.

    Returns:
        A validated :class:`ProfilesConfig`.

    Raises:
        ConfigParseError: If the TOML is malformed or fails pydantic
                          validation (e.g. missing ``image`` field).
    """
    target = path if path is not None else paths.profiles_toml()
    if not Path(target).exists():
        return ProfilesConfig.model_validate({"profile": SEED_PROFILES})
    raw = _read_toml(Path(target))
    # spec-hw-slot-ownership §3: ``image`` was removed from ProfileConfig. Drop a
    # stray ``image`` key an un-migrated (or hand-edited) profiles.toml still
    # carries, BEFORE validation — ProfileConfig is ``extra="forbid"``, so an
    # unknown ``image`` would otherwise hard-fail load on a box that hasn't run
    # `hal0 slot migrate-hw` yet. Only ``image`` is dropped; every other typo
    # still raises. The deploy-window migration removes the key permanently.
    profiles_raw = raw.get("profile")
    if isinstance(profiles_raw, dict):
        for entry in profiles_raw.values():
            if isinstance(entry, dict):
                entry.pop("image", None)
    try:
        cfg = ProfilesConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate profiles.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc
    # Seeds are VIRTUAL: seed profiles are always overlaid from code
    # (``SEED_PROFILES``) and never trusted from disk.  Older installers
    # materialised every seed into profiles.toml, so the previous "inject only
    # missing keys" merge left stale seed definitions frozen on disk — a
    # re-tuned seed (new flags / bumped image) in a release never reached an
    # existing install.  Because seed profiles are immutable through the
    # catalog API (operators clone to customise — see
    # ``ProfileCatalog._guard_custom``), a seed key on disk is never a
    # legitimate operator edit, so overwriting it with the code definition
    # loses nothing.  Non-seed (operator) profiles are left exactly as written.
    for key, seed_raw in SEED_PROFILES.items():
        cfg.profile[key] = ProfileConfig.model_validate(seed_raw)
    _sanitize_custom_profile_flags(cfg, Path(target))
    return cfg


def _sanitize_custom_profile_flags(cfg: ProfilesConfig, target: Path) -> None:
    """Strip hal0-managed flags (§21.7) from non-seed profiles, healing disk.

    Older releases let custom profiles persist managed flags — clones of the
    ``-c``-carrying seeds, hand-edited TOML, pre-guard POST/PUT bodies. Such a
    profile only explodes later, when its flags are stamped onto a model
    (``slot.managed_arg_denied`` at save and launch). Strip exactly the
    denylisted tokens (+ their values) from every non-seed profile so the
    catalog never serves an unstampable template, and best-effort persist the
    cleaned text so the on-disk TOML self-heals too (a read-only /etc still
    gets the sanitized in-memory catalog). Seed keys are code-owned (overlaid
    above) and never touched.
    """
    import shlex

    # Lazy import: hal0.slots pulls in the manager stack, which imports config.
    from hal0.slots.argv import strip_managed_flags

    sanitized: dict[str, list[str]] = {}
    for key, profile in cfg.profile.items():
        if key in SEED_PROFILES or not profile.flags.strip():
            continue
        try:
            tokens = shlex.split(profile.flags)
        except ValueError:
            continue  # malformed quoting — leave for the write-path screens
        clean, removed = strip_managed_flags(tokens)
        if removed:
            profile.flags = " ".join(shlex.quote(tok) for tok in clean)
            sanitized[key] = removed
    if not sanitized:
        return
    log.warning(
        "custom profile flags carried hal0-managed flag(s); stripped",
        extra={"event": "profile.flags_sanitized", "profiles": sanitized},
    )
    try:
        save_profiles_config(cfg, path=target)
    except OSError as exc:
        log.warning(
            "could not persist sanitized profiles.toml; serving cleaned catalog in-memory",
            extra={"event": "profile.flags_sanitize_write_failed", "error": str(exc)},
        )


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
    write_toml_atomic(target, data)


# ── stacks.toml ───────────────────────────────────────────────────────────────


def load_stacks_config(path: Path | None = None) -> StacksConfig:
    """Load and validate /etc/hal0/stacks.toml.

    Returns the built-in seed stacks (``SEED_STACKS``, empty until they ship)
    when the file is absent, so the catalog is always well-formed on a fresh
    install. When the file is present, only its contents are returned — seeds
    are NOT merged in (load REPLACES, mirroring ``load_profiles_config``).

    Raises:
        ConfigParseError: If the TOML is malformed or fails validation.
    """
    target = path if path is not None else paths.stacks_toml()
    if not Path(target).exists():
        return StacksConfig.model_validate({"stack": SEED_STACKS})
    raw = _read_toml(Path(target))
    try:
        return StacksConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate stacks.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_stacks_config(cfg: StacksConfig, path: Path | None = None) -> None:
    """Atomically write the full stack catalog to stacks.toml.

    The written file is the single source of truth; callers must pass the
    COMPLETE catalog (start from ``load_stacks_config()``, then add/modify)
    so existing stacks survive the round trip. ``exclude_none=True`` keeps
    tomli_w from raising on optional None fields, mirroring
    :func:`save_profiles_config`.
    """
    target = Path(path) if path is not None else paths.stacks_toml()
    write_toml_atomic(target, cfg.model_dump(mode="python", exclude_none=True))


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
    `hal0 probe` first.

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


# ── manifest.json (toolbox image digests) ─────────────────────────────────────


def _find_manifest_path() -> Path | None:
    """Locate the release manifest.

    Resolution order:
      1. ``paths.manifest_json()`` — /etc/hal0/manifest.json, a deliberate
         operator override (nothing installs it by default).
      2. ``paths.usr_lib() / "manifest.json"`` — the manifest shipped inside
         the current release tree (/usr/lib/hal0/current/manifest.json).
         The ``current`` symlink is atomically swapped by both install and
         update, so these pins track the running release with no separate
         migration. Production installs resolve here: the venv's hal0 is a
         plain (non-editable) pip install, so the source-file fallback in
         (3) lands in the venv's lib/ where no manifest exists.
      3. Repo root relative to this source file — editable/dev installs
         that import hal0 straight from the checkout.  Only consulted when
         ``HAL0_HOME`` is NOT set, so unit tests with isolated
         tmp_hal0_home don't accidentally pick up the repo-root copy.

    Note the (2)-before-(3) consequence: on a box that carries BOTH a
    production install and a bare git checkout (no ``HAL0_HOME``), code
    imported from the checkout reads the *installed release's* pins from
    ``current/``, not the checkout's repo-root manifest — deliberate, so
    ad-hoc scripts on a prod box see the same images the running services
    use. Set ``HAL0_HOME`` (or pass an explicit path) to pin a checkout to
    its own manifest.

    Returns the first existing path, or None if none is found.  The
    loader's callers fall back to ":v1" tag pulls in that case.
    """
    candidates: list[Path] = []
    installed = paths.manifest_json()
    candidates.append(installed)
    candidates.append(paths.usr_lib() / "manifest.json")
    # Repo-root candidate: src/hal0/config/loader.py → ../../../manifest.json
    # Skip when HAL0_HOME is set — that env var means "isolated test home,
    # don't fall back to the source tree".
    if not os.environ.get("HAL0_HOME"):
        here = Path(__file__).resolve()
        candidates.append(here.parents[3] / "manifest.json")
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load the release manifest.

    `scripts/update-toolbox-digests.sh` patches `toolbox_images.<name>.digest`
    with the published image's content digest (run before a release).
    Callers (notably the providers when constructing ContainerSpec.image)
    use the digest to pin pulls, falling back to the `tag` when digest is
    null/missing (see PLAN.md §12 and §17 Risks).

    Schema (see manifest.json at repo root for the canonical comment):
      {
        "_schema": "hal0.manifest.v1",
        "version": "...",
        "channel": "...",
        "toolbox_images": {
          "<name>": {"tag": "ghcr.io/.../:v1", "digest": "sha256:..." | null},
          ...
        }
      }

    Args:
        path: Explicit manifest path. Defaults to the FHS-aware resolver.

    Returns:
        Parsed manifest as a dict. Empty dict if no manifest is present
        (the runtime treats this as "pull by tag").

    Raises:
        ConfigParseError: The manifest file exists but is not valid JSON.
    """
    import json

    resolved = path if path is not None else _find_manifest_path()
    if resolved is None or not Path(resolved).is_file():
        return {}
    try:
        with open(resolved, "rb") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigParseError(
            f"failed to parse manifest at {resolved}: {exc}",
            details={"path": str(resolved), "reason": str(exc)},
        ) from exc


def manifest_image_ref(
    name: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> str | None:
    """Return the pinned image reference for a toolbox image, if any.

    Resolution:
      - If `toolbox_images[name].digest` is a non-empty sha256:..., return
        the registry-qualified ref ``<tag-without-:v1-suffix>@<digest>``.
      - If only `tag` is present, return the tag as-is.
      - Else None.

    The runtime callers wire this into the existing
    ``HAL0_TOOLBOX_IMAGE_<BACKEND>`` env-var override pattern (see
    llama_server.py:image_ref) so no provider code needs to read the
    manifest directly — the installer materialises env vars per slot.

    Args:
        name: Short image key (vulkan, rocm, flm, moonshine, kokoro).
        manifest: Optional pre-loaded manifest dict. Loaded on demand
                  if omitted.

    Returns:
        Pull-ready image reference, or None if the manifest doesn't list
        this image.
    """
    if manifest is None:
        manifest = load_manifest()
    images = manifest.get("toolbox_images") or {}
    entry = images.get(name)
    if not isinstance(entry, dict):
        return None
    tag = entry.get("tag")
    digest = entry.get("digest")
    if digest and isinstance(digest, str) and digest.startswith("sha256:"):
        if tag and "@" not in str(tag):
            # Strip any :tag suffix from the registry ref before appending @digest.
            ref_no_tag = str(tag).rsplit(":", 1)[0] if ":" in str(tag).split("/")[-1] else str(tag)
            return f"{ref_no_tag}@{digest}"
        return str(tag) if tag else None
    return str(tag) if tag else None


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
    "load_manifest",
    "load_profiles_config",
    "load_providers_config",
    "load_slot_config",
    "load_slot_config_by_id",
    "load_upstreams_config",
    "manifest_image_ref",
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
