"""The embedder a memory store was built with (#3518).

hindsight-api's embedder is instance-wide and a stored vector carries no
note of the model that made it. Its two local models, the engine default
:data:`ENGINE_DEFAULT_EMBEDDER` and ``hindsight-ai/hs-embed-1``, are both
384-dimensional, so starting the engine on the other one raises nothing: new
vectors land in a different space from the stored ones and recall over older
memories quietly degrades. The engine has no re-embed job.

So the embedder is a property of the *store*, and hal0 keeps that fact itself,
in one small state file beside the other hal0-owned state files::

    /var/lib/hal0/state/memory-embedder.json
        {"version": 1, "model": "BAAI/bge-small-en-v1.5",
         "source": "engine-default", "mixed": false,
         "recorded_at": "2026-11-10T21:04:11+00:00"}

``hal0.memory.extraction_env.render_showcase_env`` renders
``HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL`` from this record, never from
``[memory.models].embeddings_model``. When the two differ nothing on disk
changes: :func:`embedder_status` reports the difference (``/api/memory/models``,
``hal0 doctor``) and the engine stays on the recorded model.

This module is the only reader and writer of the file.

First run on a box with no record
---------------------------------

:func:`determine_embedder` reads what is on disk, in this order:

1. hal0's showcase drop-in exists and names an embeddings model: that model.
   It is what the engine runs now, and moving it again would add a third
   boundary to the store. When it is not the engine default and a store
   exists, the record is ``mixed``: every installer to date starts the engine
   on the base unit (the engine default) before hal0 renders any drop-in, so
   such a store was created under the default and may hold vectors from both.
2. No drop-in, but a store exists (:func:`engine_store_exists`): the engine
   default, which is what an engine with no embedder environment used.
3. No store: the configured model.

One invariant keeps the record truthful: it never names a model the engine was
not started with. Cases 1 and 2 describe an engine that has already run, so
the first update or drop-in write records them
(:func:`ensure_embedder_record`). Case 3 describes an engine that has not
started yet, so it is written only by :func:`persist_embedder_record`, which
``apply_showcase_dropin`` calls once the drop-in carrying that model is on
disk. Recorded any earlier, a box whose engine then started without the
drop-in would build its store with the engine default under a record that
says otherwise, and the next render would swap the embedder: #3518 again.
``install.sh`` is such a caller today: it runs the update migrations and
then starts a new engine on the base unit, before any drop-in exists.

Status surfaces (:func:`embedder_status`) never write. They show the record,
or what disk says while there is none.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from hal0.config.paths import var_lib
from hal0.install import perms
from hal0.security import nofollow

log = structlog.get_logger(__name__)

#: What hindsight-api embeds with when its environment names no model:
#: ``DEFAULT_EMBEDDINGS_LOCAL_MODEL`` in ``hindsight_api/config.py``, the same
#: value in 1.0.0, 1.1.0 and 1.2.0. Every store created before hal0 rendered
#: an embedder was built with it.
ENGINE_DEFAULT_EMBEDDER = "BAAI/bge-small-en-v1.5"

#: The engine environment variable the recorded model is rendered into.
EMBEDDINGS_MODEL_ENV = "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL"

RECORD_VERSION = 1

#: How a record's model was determined (the ``source`` field).
SOURCE_DROPIN = "showcase-drop-in"
SOURCE_ENGINE_DEFAULT = "engine-default"
SOURCE_CONFIGURED = "configured"

#: 0644: a model name is not a secret, and ``hal0 doctor`` run as another
#: account reads the file directly.
_RECORD_MODE = 0o644

_PG_DIR = ".pg0"


@dataclass(frozen=True)
class EmbedderRecord:
    """The store's embedder.

    ``recorded_at`` is ``None`` for a value that was determined but is not in
    the state file (yet): see :attr:`persisted`.
    """

    model: str
    source: str
    mixed: bool = False
    recorded_at: str | None = None

    @property
    def persisted(self) -> bool:
        return self.recorded_at is not None


def embedder_record_path() -> Path:
    """``/var/lib/hal0/state/memory-embedder.json`` (sandboxed under ``HAL0_HOME``)."""
    return var_lib() / "state" / "memory-embedder.json"


def _valid_model(value: Any) -> bool:
    """True when ``value`` is a model id ``[memory.models]`` would accept.

    The same shape the root-side drop-in writer allow-lists, so a record or
    drop-in line that fails it could never be rendered anyway.
    """
    if not isinstance(value, str):
        return False
    from pydantic import ValidationError

    from hal0.config.schema import MemoryModelsConfig

    try:
        MemoryModelsConfig(embeddings_model=value)
    except ValidationError:
        return False
    return True


def load_embedder_record(path: Path | None = None) -> EmbedderRecord | None:
    """The record in the state file, or ``None`` when there is none.

    A file that cannot be read or does not hold a usable record also returns
    ``None`` (and logs it): the caller determines the embedder from disk
    again rather than rendering a value nobody can vouch for.
    """
    target = path if path is not None else embedder_record_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("hal0.memory.embedder_record_unreadable", path=str(target), error=str(exc))
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if (
        not isinstance(data, dict)
        or not _valid_model(data.get("model"))
        or not isinstance(data.get("source"), str)
        or not isinstance(data.get("recorded_at"), str)
    ):
        log.warning("hal0.memory.embedder_record_invalid", path=str(target))
        return None
    return EmbedderRecord(
        model=data["model"],
        source=data["source"],
        mixed=data.get("mixed") is True,
        recorded_at=data["recorded_at"],
    )


def save_embedder_record(record: EmbedderRecord, path: Path | None = None) -> EmbedderRecord:
    """Write ``record`` to the state file, stamped now, and return what was written.

    Atomic. A root caller (``install.sh``, a root-run update) gives the file
    the state root's owner so the service can read and later rewrite it, the
    same as the other files under ``state/``. Raises ``OSError`` on failure.
    """
    target = path if path is not None else embedder_record_path()
    stamped = EmbedderRecord(
        model=record.model,
        source=record.source,
        mixed=record.mixed,
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    body = {
        "version": RECORD_VERSION,
        "model": stamped.model,
        "source": stamped.source,
        "mixed": stamped.mixed,
        "recorded_at": stamped.recorded_at,
    }
    nofollow.write_file(
        target,
        json.dumps(body, indent=2) + "\n",
        mode=_RECORD_MODE,
        owner=perms.state_root_owner(),
    )
    return stamped


def engine_store_exists(hs_dir: Path | None = None) -> bool:
    """True when this box already has a memory store on disk.

    One directory listing of the engine home, no call to the engine. The
    ``hindsight-api`` unit pins ``HOME`` to that directory and the engine's
    embedded Postgres (pg0) keeps everything under ``$HOME/.pg0``: its own
    Postgres install, and the cluster at ``.pg0/instances/<name>/data``.
    ``install.sh`` creates only ``hf-cache`` and ``.cache`` there, so ``.pg0``
    appears the first time the engine starts. A store exists when:

    * ``.pg0`` holds anything at all. The exact cluster path is pg0's
      business and may move between pg0 versions, so it is not probed; an
      engine that got as far as unpacking Postgres has started with whatever
      embedder its environment named, which is the question being asked.
    * or a ``.pg0.*`` sibling exists (``.pg0.pre-<version>``,
      ``.pg0.snapshot-*``, ``.pg0.restore-failed``). The engine upgrade
      leaves those, and a failed restore can leave them beside a missing or
      empty ``.pg0`` that the next attempt puts back.

    Wrong answers are steered to "exists", the safe side: with no drop-in
    that records the engine default, which is what an engine with no embedder
    environment runs. A home that cannot be listed (permissions) is therefore
    treated as holding a store; one that does not exist is not.

    A store that exists is not proof it holds vectors (a fresh install has an
    initialised, empty one). Telling the two apart needs the engine.
    """
    if hs_dir is None:
        from hal0.memory.engine_upgrade import hindsight_dir

        hs_dir = hindsight_dir()
    try:
        with os.scandir(hs_dir) as entries:
            names = [e.name for e in entries if e.name.startswith(_PG_DIR)]
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    if any(name.startswith(_PG_DIR + ".") for name in names):
        return True
    if _PG_DIR not in names:
        return False
    try:
        with os.scandir(hs_dir / _PG_DIR) as inner:
            return next(inner, None) is not None
    except FileNotFoundError:
        return False
    except OSError:
        return True


def dropin_embeddings_model(dropin_path: Path) -> str | None:
    """The embeddings model ``dropin_path`` sets, or ``None``.

    ``None`` for a missing or unreadable file, a file that does not set
    :data:`EMBEDDINGS_MODEL_ENV`, or a value that is not a model id. The last
    assignment wins, as it does for systemd.
    """
    try:
        text = dropin_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    prefix = f"Environment={EMBEDDINGS_MODEL_ENV}="
    found: str | None = None
    for line in text.splitlines():
        if line.startswith(prefix):
            found = line[len(prefix) :].strip()
    return found if _valid_model(found) else None


def determine_embedder(
    *, configured: str, dropin_model: str | None, store_exists: bool
) -> EmbedderRecord:
    """Which embedder a box with no record is on (see the module docstring)."""
    if dropin_model:
        return EmbedderRecord(
            model=dropin_model,
            source=SOURCE_DROPIN,
            mixed=store_exists and dropin_model != ENGINE_DEFAULT_EMBEDDER,
        )
    if store_exists:
        return EmbedderRecord(model=ENGINE_DEFAULT_EMBEDDER, source=SOURCE_ENGINE_DEFAULT)
    return EmbedderRecord(model=configured, source=SOURCE_CONFIGURED)


def _showcase_dropin_path() -> Path:
    # Imported here: extraction_env imports this module to render from the record.
    from hal0.memory import extraction_env

    return extraction_env.SHOWCASE_DROP_IN_PATH


def resolve_embedder(
    configured: str,
    *,
    path: Path | None = None,
    dropin_path: Path | None = None,
    hs_dir: Path | None = None,
) -> EmbedderRecord:
    """The store's embedder: the record, else what disk says. Writes nothing.

    ``configured`` is ``[memory.models].embeddings_model``; it is the answer
    only on a box with no record, no drop-in and no store. ``path``,
    ``dropin_path`` and ``hs_dir`` are test seams for the state file, hal0's
    showcase drop-in and the engine home.
    """
    record = load_embedder_record(path)
    if record is not None:
        return record
    return determine_embedder(
        configured=configured,
        dropin_model=dropin_embeddings_model(
            dropin_path if dropin_path is not None else _showcase_dropin_path()
        ),
        store_exists=engine_store_exists(hs_dir),
    )


def persist_embedder_record(record: EmbedderRecord, *, path: Path | None = None) -> EmbedderRecord:
    """Write ``record`` unless the state file already holds one. Never raises.

    Returns the record now on disk. A record that is already there wins and
    is never rewritten: only a switch changes a store's embedder. On a failed
    write the unpersisted ``record`` comes back; disk still gives the same
    answer on the next call.

    For a ``configured`` record, call this only once the engine environment
    carrying the model is on disk (see the module docstring).
    """
    existing = load_embedder_record(path)
    if existing is not None:
        return existing
    try:
        saved = save_embedder_record(record, path)
    except OSError as exc:
        log.warning("hal0.memory.embedder_record_write_failed", model=record.model, error=str(exc))
        return record
    log.info(
        "hal0.memory.embedder_recorded", model=saved.model, source=saved.source, mixed=saved.mixed
    )
    return saved


def ensure_embedder_record(
    configured: str,
    *,
    path: Path | None = None,
    dropin_path: Path | None = None,
    hs_dir: Path | None = None,
) -> EmbedderRecord:
    """:func:`resolve_embedder`, writing the record down when the engine has already run.

    A box with a drop-in or a store gets its record here. A box with neither
    gets the configured model back unpersisted; ``apply_showcase_dropin``
    records it together with the drop-in that makes the engine use it.
    Never raises for a failed write.
    """
    record = resolve_embedder(configured, path=path, dropin_path=dropin_path, hs_dir=hs_dir)
    if record.persisted or record.source == SOURCE_CONFIGURED:
        return record
    return persist_embedder_record(record, path=path)


def record_store_embedder() -> EmbedderRecord | None:
    """Update hook: record this box's embedder before anything renders it.

    Reads the configured model from ``hal0.toml`` and calls
    :func:`ensure_embedder_record`. ``None`` when the config cannot be read.
    Never raises.
    """
    try:
        from hal0.config.loader import load_hal0_config

        configured = load_hal0_config().memory.models.embeddings_model
    except Exception as exc:
        log.warning("hal0.memory.embedder_record_skipped", error=str(exc))
        return None
    return ensure_embedder_record(configured)


def embedder_status(
    configured: str,
    *,
    path: Path | None = None,
    dropin_path: Path | None = None,
    hs_dir: Path | None = None,
) -> dict[str, Any]:
    """Recorded against configured, for a status surface. Reads only.

    ``needs_switch`` is true when the config asks for a different model than
    the store's, or when the store is mixed; re-embedding is the way out of
    both. ``recorded_at`` is ``None`` while the value is determined from disk
    but not written down yet.
    """
    record = resolve_embedder(configured, path=path, dropin_path=dropin_path, hs_dir=hs_dir)
    return {
        "recorded": record.model,
        "configured": configured,
        "mixed": record.mixed,
        "needs_switch": record.mixed or record.model != configured,
        "source": record.source,
        "recorded_at": record.recorded_at,
    }


def embedder_notice(status: dict[str, Any]) -> str | None:
    """One operator sentence for an :func:`embedder_status`, or ``None`` when it is clean."""
    if not status.get("needs_switch"):
        return None
    recorded, configured = status.get("recorded"), status.get("configured")
    parts: list[str] = []
    if status.get("mixed"):
        parts.append(
            f"the memory store may hold vectors from two embedders: it existed before the "
            f"engine was moved from {ENGINE_DEFAULT_EMBEDDER} to {recorded}, so recall over "
            "older memories is degraded"
        )
    if recorded != configured:
        parts.append(
            f"the memory store was built with {recorded} and the engine stays on it; "
            f"[memory.models].embeddings_model is set to {configured}, which has no effect"
        )
    parts.append(
        "a store's embedder can only change by re-embedding it; `hal0 memory embedder switch` "
        "will do that, and it is not in this release yet"
    )
    return "; ".join(parts)


__all__ = [
    "EMBEDDINGS_MODEL_ENV",
    "ENGINE_DEFAULT_EMBEDDER",
    "SOURCE_CONFIGURED",
    "SOURCE_DROPIN",
    "SOURCE_ENGINE_DEFAULT",
    "EmbedderRecord",
    "determine_embedder",
    "dropin_embeddings_model",
    "embedder_notice",
    "embedder_record_path",
    "embedder_status",
    "engine_store_exists",
    "ensure_embedder_record",
    "load_embedder_record",
    "persist_embedder_record",
    "record_store_embedder",
    "resolve_embedder",
    "save_embedder_record",
]
