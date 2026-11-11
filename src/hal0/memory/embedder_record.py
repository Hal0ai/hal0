"""The embedder a memory store was built with (#3518).

hindsight-api's embedder is instance-wide and a stored vector carries no
note of the model that made it. Its two local models, the engine default
``BAAI/bge-small-en-v1.5`` and ``hindsight-ai/hs-embed-1``, are both
384-dimensional, so starting the engine on the other one raises nothing: new
vectors land in a different space from the stored ones and recall over older
memories quietly degrades. The engine has no re-embed job.

So the embedder is a property of the *store*, and hal0 keeps that fact itself,
in one small state file beside the other hal0-owned state files::

    /var/lib/hal0/state/memory-embedder.json
        {"version": 1, "provider": "local", "model": "BAAI/bge-small-en-v1.5",
         "source": "engine-process", "mixed": false,
         "recorded_at": "2026-11-10T21:04:11+00:00"}

Provider and model are one fact (:class:`Embedder`): a remote provider swaps
the embedder under a store exactly as another local model does.

``hal0.memory.extraction_env.render_showcase_env`` renders both
``HINDSIGHT_API_EMBEDDINGS_*`` keys from here, never from ``[memory.models]``.
When config and record differ nothing on disk changes:
:func:`embedder_status` reports the difference (``/api/memory/models``,
``hal0 doctor``) and the engine stays where it is.

This module is the only reader and writer of the file.

Evidence
--------

What the engine runs comes from one place,
``extraction_env.effective_engine_embedder`` (:func:`engine_evidence`), the
same environment readers the lanes route and the ``memory_engine_env`` doctor
row use. It gives two layers:

* ``live``: the running engine's own environment;
* ``on_disk``: what the unit files would start it with: the unit's merged
  ``Environment=`` (every drop-in, in systemd's order) with the unit's
  ``EnvironmentFile`` on top. Where there is no unit to ask, hal0's own
  showcase drop-in file.

A key the engine's environment does not set means the engine default.

First run on a box with no record
---------------------------------

:func:`determine_embedder`, in this order:

1. The engine is running: what it runs. If the unit files would start it on
   something else, that is kept as a ``conflict`` note.
2. The unit files name an embedder: that one.
3. Neither, and a store exists (:func:`store_state`): the engine default.
4. The store's location is unavailable (a symlink that does not resolve, a
   mount point that is not mounted, an installed unit whose home directory
   is missing): the engine default, never written down.
5. No store: the configured embedder.

``mixed`` is set only on evidence: a store exists, it has no record, and the
embedder found is not the engine default. Every installer that did not write a
record started a new engine on the base unit, so such a store was created
under the default. (An installer that starts a new engine on the configured
embedder writes the record first, :mod:`hal0.memory.first_start`, so its store
never reaches this rule.)

One invariant keeps the record truthful: it never names an embedder the engine
was not started with. Cases 1 to 3 describe an engine that has run, so the
first update or drop-in write records them (:func:`ensure_embedder_record`).
Case 5 describes an engine that has not started, so it is written only by
:func:`persist_embedder_record`, which ``apply_showcase_dropin`` calls once
the drop-in carrying it is on disk. Recorded any earlier, a box whose engine
then started without the drop-in would build its store with the engine
default under a record that says otherwise, and the next render would swap
the embedder: #3518 again. ``install.sh`` is such a caller: it runs the
update migrations long before the engine's unit exists. The drop-in and the
record of a new install are written later, immediately before the engine's
first start, by :mod:`hal0.memory.first_start`.

One writer replaces a record: when that first start fails on the configured
embedder and a trial start on the engine default then comes up healthy,
``first_start`` rewrites it as the engine default with the source
``first-start-fallback``. The engine never answered a request on the
configured pair, so no vector was made with the pair being replaced.

When a record exists
--------------------

The record is compared with the evidence every time (:class:`EmbedderView`).
A disagreement changes neither: it is reported as a ``conflict``. The render
uses the live embedder while the engine is running, and the record otherwise,
so nothing hal0 writes can swap a running engine's embedder.

A record file that exists but cannot be read or parsed is never overwritten.
For that run the embedder is taken from the evidence and nothing is written.

Status surfaces (:func:`embedder_status`) never write.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import structlog

from hal0.config.paths import var_lib
from hal0.install import perms
from hal0.security import nofollow

log = structlog.get_logger(__name__)


class Embedder(NamedTuple):
    """The pair that decides which space a vector lands in."""

    provider: str
    model: str


#: What hindsight-api embeds with when its environment names nothing:
#: ``DEFAULT_EMBEDDINGS_PROVIDER`` and ``DEFAULT_EMBEDDINGS_LOCAL_MODEL`` in
#: ``hindsight_api/config.py`` (the model is the same in 1.0.0, 1.1.0 and
#: 1.2.0). Every store created before hal0 rendered an embedder was built
#: with it.
ENGINE_DEFAULT_PROVIDER = "local"
ENGINE_DEFAULT_EMBEDDER = "BAAI/bge-small-en-v1.5"
ENGINE_DEFAULT = Embedder(ENGINE_DEFAULT_PROVIDER, ENGINE_DEFAULT_EMBEDDER)

#: The engine environment variables the recorded pair is rendered into.
EMBEDDINGS_PROVIDER_ENV = "HINDSIGHT_API_EMBEDDINGS_PROVIDER"
EMBEDDINGS_MODEL_ENV = "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL"

RECORD_VERSION = 1

#: How a record's embedder was determined (the ``source`` field).
SOURCE_PROCESS = "engine-process"
SOURCE_UNIT = "unit-files"
SOURCE_DROPIN = "showcase-drop-in"
SOURCE_ENGINE_DEFAULT = "engine-default"
SOURCE_CONFIGURED = "configured"
SOURCE_UNAVAILABLE = "store-unavailable"
#: A new install whose engine could not start on the configured embedder and
#: was started on the engine default instead (:mod:`hal0.memory.first_start`).
SOURCE_FIRST_START_FALLBACK = "first-start-fallback"

#: Sources that describe an engine that has not run (or cannot be seen). A
#: record from one of these is never written by :func:`ensure_embedder_record`.
_UNSETTLED_SOURCES = frozenset({SOURCE_CONFIGURED, SOURCE_UNAVAILABLE})

#: :func:`store_state` answers.
STORE_EXISTS = "exists"
STORE_ABSENT = "absent"
STORE_UNAVAILABLE = "unavailable"

#: ``on_disk_source`` values that mean "the unit was asked", as opposed to
#: hal0's own drop-in file read on a box with no unit to ask.
_UNIT_SOURCES = frozenset({"unit", "unit+file"})

#: 0644: an embedder name is not a secret, and ``hal0 doctor`` run as another
#: account reads the file directly.
_RECORD_MODE = 0o644

_PG_DIR = ".pg0"
_FSTAB = Path("/etc/fstab")


@dataclass(frozen=True)
class EmbedderRecord:
    """The store's embedder.

    ``recorded_at`` is ``None`` for a value that was determined but is not in
    the state file (yet): see :attr:`persisted`. ``conflict`` is what the unit
    files said when the record was taken from a running engine that disagreed
    with them.
    """

    provider: str
    model: str
    source: str
    mixed: bool = False
    recorded_at: str | None = None
    conflict: dict[str, Any] | None = None

    @property
    def embedder(self) -> Embedder:
        return Embedder(self.provider, self.model)

    @property
    def persisted(self) -> bool:
        return self.recorded_at is not None


class EmbedderRecordError(Exception):
    """The record file exists but cannot be read or does not hold a usable record."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class EmbedderView:
    """The record set against what the engine runs, for one moment.

    ``record`` is the state file's record, or what :func:`determine_embedder`
    says while there is none. ``live`` and ``on_disk`` are the two evidence
    layers, each ``None`` when that layer has nothing to say. ``record_error``
    is the path of a record file that exists and cannot be used.
    """

    record: EmbedderRecord
    live: Embedder | None
    on_disk: Embedder | None
    store: str
    record_error: str | None = None
    effective_source: str | None = None

    @property
    def render(self) -> Embedder:
        """The pair to render into the engine environment now.

        The live one while the engine is running, so a render can never move
        a running engine onto something else; the record otherwise.
        """
        return self.live if self.live is not None else self.record.embedder

    @property
    def effective(self) -> Embedder | None:
        """What the engine runs, or would start on; ``None`` when nothing says."""
        return self.live if self.live is not None else self.on_disk

    @property
    def conflict(self) -> dict[str, Any] | None:
        """Evidence that disagrees with the record, by layer; ``None`` when none does.

        A box with no store has no record to disagree with yet. With no
        evidence at all, the note taken when the record was made stands.
        """
        if not self.record.persisted and self.record.source in _UNSETTLED_SOURCES:
            return None
        if self.live is None and self.on_disk is None:
            return self.record.conflict
        out: dict[str, Any] = {}
        if self.live is not None and self.live != self.record.embedder:
            out["live"] = self.live._asdict()
        if self.on_disk is not None and self.on_disk != self.record.embedder:
            out["on_disk"] = self.on_disk._asdict()
        return out or None


def embedder_record_path() -> Path:
    """``/var/lib/hal0/state/memory-embedder.json`` (sandboxed under ``HAL0_HOME``)."""
    return var_lib() / "state" / "memory-embedder.json"


def configured_embedder(models: Any) -> Embedder:
    """The pair ``[memory.models]`` asks for."""
    return Embedder(models.embeddings_provider, models.embeddings_model)


def shipped_default_embedder() -> Embedder:
    """The pair ``[memory.models]`` holds when the operator has set nothing."""
    from hal0.config.schema import MemoryModelsConfig

    fields = MemoryModelsConfig.model_fields
    return Embedder(fields["embeddings_provider"].default, fields["embeddings_model"].default)


def _valid_pair(provider: Any, model: Any) -> bool:
    """True when ``[memory.models]`` would accept the pair.

    The same shapes the root-side drop-in writer allow-lists, so a record
    that fails them could never be rendered anyway.
    """
    if not isinstance(provider, str) or not isinstance(model, str):
        return False
    from pydantic import ValidationError

    from hal0.config.schema import MemoryModelsConfig

    try:
        MemoryModelsConfig(embeddings_provider=provider, embeddings_model=model)
    except ValidationError:
        return False
    return True


def load_embedder_record(path: Path | None = None) -> EmbedderRecord | None:
    """The record in the state file, or ``None`` when there is no file.

    Raises :class:`EmbedderRecordError` for a file that exists but cannot be
    read or does not hold a usable record. Callers keep such a file: it may
    be the only note of what the store was built with.
    """
    target = path if path is not None else embedder_record_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise EmbedderRecordError(target, f"cannot be read ({type(exc).__name__})") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise EmbedderRecordError(target, "is not JSON") from exc
    if (
        not isinstance(data, dict)
        or not _valid_pair(data.get("provider"), data.get("model"))
        or not isinstance(data.get("source"), str)
        or not isinstance(data.get("recorded_at"), str)
    ):
        raise EmbedderRecordError(target, "does not hold an embedder record")
    conflict = data.get("conflict")
    return EmbedderRecord(
        provider=data["provider"],
        model=data["model"],
        source=data["source"],
        mixed=data.get("mixed") is True,
        recorded_at=data["recorded_at"],
        conflict=conflict if isinstance(conflict, dict) else None,
    )


def save_embedder_record(record: EmbedderRecord, path: Path | None = None) -> EmbedderRecord:
    """Write ``record`` to the state file, stamped now, and return what was written.

    Atomic. A root caller (``install.sh``, a root-run update) gives the file
    the state root's owner so the service can read and later rewrite it, the
    same as the other files under ``state/``. Raises ``OSError`` on failure.
    Replaces whatever is there: :func:`persist_embedder_record` is the caller
    that checks first.
    """
    target = path if path is not None else embedder_record_path()
    stamped = replace(record, recorded_at=datetime.now(UTC).isoformat(timespec="seconds"))
    body: dict[str, Any] = {
        "version": RECORD_VERSION,
        "provider": stamped.provider,
        "model": stamped.model,
        "source": stamped.source,
        "mixed": stamped.mixed,
        "recorded_at": stamped.recorded_at,
    }
    if stamped.conflict:
        body["conflict"] = stamped.conflict
    nofollow.write_file(
        target,
        json.dumps(body, indent=2) + "\n",
        mode=_RECORD_MODE,
        owner=perms.state_root_owner(),
    )
    return stamped


def _unmounted_mount_point(path: Path, fstab: Path) -> str | None:
    """The ``fstab`` mount point that should hold ``path`` and is not mounted, if any.

    Looks at the path as written and with its symlinks resolved, so a state
    root that is a link into another disk is covered. Mounts made outside
    ``fstab`` are not seen.
    """
    try:
        lines = fstab.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    candidates = {os.path.normpath(str(path)), os.path.realpath(str(path))}
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if len(fields) < 2 or not fields[1].startswith("/") or fields[1] == "/":
            continue
        point = os.path.normpath(fields[1])
        if not any(c == point or c.startswith(point + "/") for c in candidates):
            continue
        if not os.path.ismount(point):
            return point
    return None


def store_state(hs_dir: Path | None = None, *, fstab: Path | None = None) -> str:
    """Whether this box already has a memory store: exists, absent or unavailable.

    One look at the engine home, no call to the engine. The ``hindsight-api``
    unit pins ``HOME`` to that directory and the engine's embedded Postgres
    (pg0) keeps everything under ``$HOME/.pg0``: its own Postgres install, and
    the cluster at ``.pg0/instances/<name>/data``. ``install.sh`` creates only
    ``hf-cache`` and ``.cache`` there, so ``.pg0`` appears the first time the
    engine starts.

    The answer leans towards "exists". Answering "absent" for a store that is
    there lets the configured embedder be recorded over it; answering
    "exists" for one that is not records the engine default, which is what an
    engine with no embedder environment runs anyway.

    ``exists`` when, by ``lstat`` (nothing is followed):

    * a ``.pg0`` entry of any kind is there: a directory holding anything, a
      symlink whether or not it resolves, anything that is not a directory;
    * ``.pg0`` is an empty directory and an engine venv sits beside it (an
      engine that was installed and has a data directory has been started);
    * a ``.pg0.*`` sibling is there (``.pg0.pre-<version>``,
      ``.pg0.snapshot-*``, ``.pg0.restore-failed``): the engine upgrade
      leaves those, and a failed restore can leave them beside a missing
      ``.pg0`` that the next attempt puts back;
    * the home cannot be examined (permissions).

    ``unavailable`` when the home is a symlink that does not resolve, or sits
    under an ``fstab`` mount point that is not mounted: the store may be on a
    disk that is not there right now, so nothing may be concluded.
    :func:`resolve_embedder` adds one more case this function cannot see: the
    engine's unit is installed and the home does not exist at all.

    ``absent`` otherwise: no home, or a home with no ``.pg0`` (or an empty one
    and no venv).

    The exact cluster path is pg0's business and may move between pg0
    versions, so it is not probed. A store that exists is not proof it holds
    vectors (a fresh install has an initialised, empty one).
    """
    if hs_dir is None:
        from hal0.memory.engine_upgrade import hindsight_dir

        hs_dir = hindsight_dir()
    if _unmounted_mount_point(hs_dir, fstab if fstab is not None else _FSTAB) is not None:
        return STORE_UNAVAILABLE
    try:
        home = os.lstat(hs_dir)
    except FileNotFoundError:
        return STORE_ABSENT
    except OSError:
        return STORE_EXISTS
    if stat.S_ISLNK(home.st_mode) and not os.path.exists(hs_dir):
        return STORE_UNAVAILABLE
    try:
        names = set(os.listdir(hs_dir))
    except OSError:
        return STORE_EXISTS
    if any(name.startswith(_PG_DIR + ".") for name in names):
        return STORE_EXISTS
    if _PG_DIR not in names:
        return STORE_ABSENT
    pg = hs_dir / _PG_DIR
    try:
        entry = os.lstat(pg)
        if not stat.S_ISDIR(entry.st_mode):
            return STORE_EXISTS
        if os.listdir(pg):
            return STORE_EXISTS
    except OSError:
        return STORE_EXISTS
    return STORE_EXISTS if ".venv" in names else STORE_ABSENT


def _unit_without_a_home(evidence: Mapping[str, Any], hs_dir: Path | None) -> bool:
    """True when the engine's unit is installed and its home directory is not there.

    ``install.sh`` creates the home before it installs the unit, so the two
    are never apart on a healthy box. A unit with no home means the home is
    on storage that is not there right now, however it is mounted (``fstab``
    is only one way), so its store is unavailable, not absent.
    """
    if evidence.get("on_disk_source") not in _UNIT_SOURCES:
        return False
    if hs_dir is None:
        from hal0.memory.engine_upgrade import hindsight_dir

        hs_dir = hindsight_dir()
    return not os.path.lexists(hs_dir)


def engine_evidence(**kwargs: Any) -> dict[str, Any]:
    """What the engine runs and what its unit files say (the one evidence source).

    Imported here: ``extraction_env`` imports this module to render from the
    record. ``kwargs`` pass through to
    :func:`hal0.memory.extraction_env.effective_engine_embedder`.
    """
    from hal0.memory import extraction_env

    return extraction_env.effective_engine_embedder(**kwargs)


def _pair(raw: Mapping[str, Any]) -> Embedder:
    """A layer's raw values as a pair; a key the environment leaves unset is the engine default."""
    return Embedder(
        raw.get("provider") or ENGINE_DEFAULT_PROVIDER,
        raw.get("model") or ENGINE_DEFAULT_EMBEDDER,
    )


def _names_embedder(raw: Mapping[str, Any] | None) -> bool:
    return raw is not None and bool(raw.get("provider") or raw.get("model"))


def _on_disk_pair(evidence: Mapping[str, Any]) -> Embedder | None:
    """What the unit files would start the engine on; ``None`` when nothing on disk says.

    A unit that names no embedder starts the engine on its default. hal0's
    own drop-in file, read where there is no unit to ask, says something only
    when it names one.
    """
    raw = evidence.get("on_disk")
    if raw is None:
        return None
    if evidence.get("on_disk_source") in _UNIT_SOURCES or _names_embedder(raw):
        return _pair(raw)
    return None


def determine_embedder(
    *, configured: Embedder, evidence: Mapping[str, Any], store: str
) -> EmbedderRecord:
    """Which embedder a box with no record is on (see the module docstring)."""
    live, on_disk = evidence.get("live"), evidence.get("on_disk")
    conflict: dict[str, Any] | None = None
    if live is not None:
        pair, source = _pair(live), SOURCE_PROCESS
        disk_pair = _on_disk_pair(evidence)
        if disk_pair is not None and disk_pair != pair:
            conflict = {"on_disk": disk_pair._asdict()}
    elif on_disk is not None and _names_embedder(on_disk):
        pair = _pair(on_disk)
        source = SOURCE_UNIT if evidence.get("on_disk_source") in _UNIT_SOURCES else SOURCE_DROPIN
    elif store == STORE_EXISTS:
        pair, source = ENGINE_DEFAULT, SOURCE_ENGINE_DEFAULT
    elif store == STORE_UNAVAILABLE:
        pair, source = ENGINE_DEFAULT, SOURCE_UNAVAILABLE
    else:
        pair, source = configured, SOURCE_CONFIGURED
    return EmbedderRecord(
        provider=pair.provider,
        model=pair.model,
        source=source,
        mixed=store == STORE_EXISTS and pair != ENGINE_DEFAULT,
        conflict=conflict,
    )


def resolve_embedder(
    configured: Embedder,
    *,
    path: Path | None = None,
    hs_dir: Path | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> EmbedderView:
    """The store's embedder set against the evidence. Writes nothing.

    ``configured`` is the pair from ``[memory.models]``; it is the answer only
    on a box with no record, no evidence and no store. ``path``, ``hs_dir``
    and ``evidence`` are test seams for the state file, the engine home and
    :func:`engine_evidence`.
    """
    found = evidence if evidence is not None else engine_evidence()
    live = found.get("live")
    store = store_state(hs_dir)
    if store == STORE_ABSENT and _unit_without_a_home(found, hs_dir):
        store = STORE_UNAVAILABLE
    record_error: str | None = None
    try:
        record = load_embedder_record(path)
    except EmbedderRecordError as exc:
        log.warning("hal0.memory.embedder_record_unusable", path=str(exc.path), reason=exc.reason)
        record, record_error = None, str(exc.path)
    if record is None:
        record = determine_embedder(configured=configured, evidence=found, store=store)
    return EmbedderView(
        record=record,
        live=_pair(live) if live is not None else None,
        on_disk=_on_disk_pair(found),
        store=store,
        record_error=record_error,
        effective_source="process" if live is not None else found.get("on_disk_source"),
    )


def persist_embedder_record(record: EmbedderRecord, *, path: Path | None = None) -> EmbedderRecord:
    """Write ``record`` unless the state file already holds something. Never raises.

    Returns the record now on disk. A record that is already there wins and
    is never rewritten: only a switch changes a store's embedder. A file that
    is there but cannot be used is left alone too, and so is disk when the
    store's location is unavailable; ``record`` comes back unpersisted. The
    same on a failed write: the evidence gives the same answer next time.

    For a ``configured`` record, call this only once the engine environment
    carrying it is on disk (see the module docstring).
    """
    if record.source == SOURCE_UNAVAILABLE:
        return record
    try:
        existing = load_embedder_record(path)
    except EmbedderRecordError:
        return record
    if existing is not None:
        return existing
    try:
        saved = save_embedder_record(record, path)
    except OSError as exc:
        log.warning("hal0.memory.embedder_record_write_failed", model=record.model, error=str(exc))
        return record
    log.info(
        "hal0.memory.embedder_recorded",
        provider=saved.provider,
        model=saved.model,
        source=saved.source,
        mixed=saved.mixed,
        conflict=bool(saved.conflict),
    )
    return saved


def ensure_embedder_record(
    configured: Embedder,
    *,
    path: Path | None = None,
    hs_dir: Path | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> EmbedderView:
    """:func:`resolve_embedder`, writing the record down when the engine has already run.

    A box whose engine runs, whose unit files name an embedder, or that has a
    store gets its record here. A box with none of those gets the configured
    pair back unpersisted; ``apply_showcase_dropin`` records it together with
    the drop-in that makes the engine use it. Never raises for a failed write.
    """
    view = resolve_embedder(configured, path=path, hs_dir=hs_dir, evidence=evidence)
    record = view.record
    if record.persisted or view.record_error or record.source in _UNSETTLED_SOURCES:
        return view
    return replace(view, record=persist_embedder_record(record, path=path))


def record_store_embedder() -> EmbedderRecord | None:
    """Update hook: record this box's embedder before anything renders it.

    Reads the configured pair from ``hal0.toml`` and calls
    :func:`ensure_embedder_record`. ``None`` when the config cannot be read.
    Never raises.
    """
    try:
        from hal0.config.loader import load_hal0_config

        configured = configured_embedder(load_hal0_config().memory.models)
    except Exception as exc:
        log.warning("hal0.memory.embedder_record_skipped", error=str(exc))
        return None
    return ensure_embedder_record(configured).record


def embedder_status(
    configured: Embedder,
    *,
    path: Path | None = None,
    hs_dir: Path | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Recorded against configured and against the engine, for a status surface. Reads only.

    ``needs_switch`` is true when the config asks for a different embedder
    than the store's, or when the store is mixed; re-embedding is the way out
    of both. ``recorded_at`` is ``None`` while the value is determined but not
    written down. ``effective`` is what the engine runs or would start on.
    ``conflict`` lists evidence that disagrees with the record.
    ``record_error`` is the path of a record file that cannot be used, and
    ``store_unavailable`` the engine home when its location cannot be seen.
    """
    view = resolve_embedder(configured, path=path, hs_dir=hs_dir, evidence=evidence)
    record, effective = view.record, view.effective
    if hs_dir is None and record.source == SOURCE_UNAVAILABLE:
        from hal0.memory.engine_upgrade import hindsight_dir

        hs_dir = hindsight_dir()
    return {
        "recorded": record.model,
        "recorded_provider": record.provider,
        "configured": configured.model,
        "configured_provider": configured.provider,
        "mixed": record.mixed,
        "needs_switch": record.mixed or record.embedder != configured,
        "source": record.source,
        "recorded_at": record.recorded_at,
        "effective": (
            {**effective._asdict(), "source": view.effective_source}
            if effective is not None
            else None
        ),
        "conflict": view.conflict,
        "record_error": view.record_error,
        "store_unavailable": str(hs_dir) if record.source == SOURCE_UNAVAILABLE else None,
    }


def describe_embedder(provider: Any, model: Any) -> str:
    """A pair in words: the model for the local provider, the provider otherwise."""
    if provider == ENGINE_DEFAULT_PROVIDER:
        return str(model)
    return f"the {provider} provider (local model {model})"


def embedder_notice(status: Mapping[str, Any]) -> str | None:
    """One operator sentence for an :func:`embedder_status`, or ``None`` when it is clean.

    Covers, in this order: a record file that cannot be used, a store whose
    location is unavailable, evidence that disagrees with the record, a mixed
    store, and a configured embedder that differs from the recorded one.
    """
    recorded = describe_embedder(status.get("recorded_provider"), status.get("recorded"))
    configured = describe_embedder(status.get("configured_provider"), status.get("configured"))
    parts: list[str] = []
    if status.get("record_error"):
        parts.append(
            f"the embedder record at {status['record_error']} cannot be read; hal0 has left "
            f"the file as it is and keeps the engine on {recorded}, which is what the engine "
            "and its unit files say now. Repair or remove the file"
        )
    if status.get("store_unavailable"):
        parts.append(
            f"the memory store's location, {status['store_unavailable']}, is not available "
            "(a link that does not resolve, or a disk that is not mounted); nothing is "
            "recorded until it is back"
        )
    conflict = status.get("conflict") or {}
    if conflict and not status.get("record_error"):
        seen: list[str] = []
        if conflict.get("live"):
            live = conflict["live"]
            seen.append(
                f"the running engine is on {describe_embedder(live['provider'], live['model'])}"
            )
        if conflict.get("on_disk"):
            disk = conflict["on_disk"]
            seen.append(
                "its unit files (drop-ins and environment file) would start it on "
                f"{describe_embedder(disk['provider'], disk['model'])}"
            )
        parts.append(
            f"the embedder record says {recorded}, but {' and '.join(seen)}. hal0 has changed "
            "neither; it will not move a running engine to another embedder. Remove the "
            "override, or correct the record, so the two agree"
        )
    if status.get("mixed"):
        parts.append(
            f"the memory store may hold vectors from two embedders: it existed before the "
            f"engine was moved from {ENGINE_DEFAULT_EMBEDDER} to {recorded}, so recall over "
            "older memories is degraded"
        )
    differs = (status.get("recorded_provider"), status.get("recorded")) != (
        status.get("configured_provider"),
        status.get("configured"),
    )
    if differs and not status.get("record_error") and not status.get("store_unavailable"):
        parts.append(
            f"the memory store was built with {recorded} and the engine stays on it; "
            f"[memory.models] asks for {configured}, which has no effect"
        )
    if not parts:
        return None
    if status.get("mixed") or differs:
        parts.append(
            "a store's embedder can only change by re-embedding it; "
            "`hal0 memory embedder switch` will do that, and it is not in this release yet"
        )
    return "; ".join(parts)


__all__ = [
    "EMBEDDINGS_MODEL_ENV",
    "EMBEDDINGS_PROVIDER_ENV",
    "ENGINE_DEFAULT",
    "ENGINE_DEFAULT_EMBEDDER",
    "ENGINE_DEFAULT_PROVIDER",
    "SOURCE_CONFIGURED",
    "SOURCE_DROPIN",
    "SOURCE_ENGINE_DEFAULT",
    "SOURCE_FIRST_START_FALLBACK",
    "SOURCE_PROCESS",
    "SOURCE_UNAVAILABLE",
    "SOURCE_UNIT",
    "STORE_ABSENT",
    "STORE_EXISTS",
    "STORE_UNAVAILABLE",
    "Embedder",
    "EmbedderRecord",
    "EmbedderRecordError",
    "EmbedderView",
    "configured_embedder",
    "describe_embedder",
    "determine_embedder",
    "embedder_notice",
    "embedder_record_path",
    "embedder_status",
    "engine_evidence",
    "ensure_embedder_record",
    "load_embedder_record",
    "persist_embedder_record",
    "record_store_embedder",
    "resolve_embedder",
    "save_embedder_record",
    "shipped_default_embedder",
    "store_state",
]
