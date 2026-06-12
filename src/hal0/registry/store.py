"""ModelRegistry — atomic TOML-backed model catalog.

The registry is the single source of truth for "what models exist."
It persists model metadata as an atomic TOML file at
``/var/lib/hal0/registry/registry.toml`` and uses mtime polling to
invalidate its in-memory cache when the file changes on disk.

Port target: haloai lib/registry.py.
See PLAN.md §3 and ARCHITECTURE.md §Key boundaries ("registry is the only
source of truth for what models exist").

# NOTE: haloai used a single ``registry.toml`` file under the registry
# directory.  We keep that shape — one file with all entries keyed by
# model id — rather than one file per model.  Rationale:
#   * Atomic batch updates are simpler (one tmpfile + rename vs N files).
#   * mtime-cache invalidation is one stat() call.
#   * The directory is still future-proof for sidecar files (e.g.
#     download progress, hash manifests) that PLAN.md hints at.

# NOTE: typed errors live here (not in api/middleware/error_codes.py)
# because the locked subtree for Agent I is config/ + registry/.  They
# subclass ``Hal0Error`` so the API middleware reshapes them into the
# structured envelope automatically.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tomli_w

from hal0.config import paths
from hal0.errors import Hal0Error
from hal0.registry.model import Model

log = logging.getLogger(__name__)


# ── Typed errors ──────────────────────────────────────────────────────────────


class RegistryError(Hal0Error):
    """Base error for registry operations."""

    code = "model.registry_error"
    status = 500


class ModelNotFound(RegistryError):
    """The requested model id is not in the registry."""

    code = "model.not_found"
    status = 404


class ModelAlreadyExists(RegistryError):
    """add() called but the model id is already present."""

    code = "model.already_exists"
    status = 409


# ── ModelRegistry ─────────────────────────────────────────────────────────────


_DEFAULT_REGISTRY_FILENAME = "registry.toml"


class ModelRegistry:
    """Atomic TOML-backed model registry.

    Thread-safety: a per-instance ``threading.RLock`` guards all reads
    and writes.  FastAPI sync routes run on the threadpool; the
    Dispatcher runs on the event loop.  Both paths share a single
    registry instance, so the lock has to be threading-aware.

    The mtime cache invalidates itself when the registry file changes on
    disk, allowing direct file edits to take effect on the next access.
    """

    # Optional post-mutation callback. When set (e.g. by create_app), every
    # successful add/update/remove invokes it AFTER the lock is released so
    # downstream catalog artifacts can be regenerated
    # from the freshly-written registry. Best-effort by design: a failing hook
    # is logged and swallowed, never propagated — a catalog-regen failure must
    # not roll back or mask a successful registry write.
    on_change: Callable[[], None] | None = None

    def __init__(self, registry_dir: str | Path | None = None) -> None:
        """Initialise the registry.

        Args:
            registry_dir: Override the registry directory.  If ``None``,
                resolves from ``hal0.config.paths.registry_dir()`` at
                call time (so ``HAL0_HOME`` changes during tests are
                always reflected).
        """
        self._registry_dir_override: Path | None = (
            Path(registry_dir) if registry_dir is not None else None
        )
        self._lock = threading.RLock()
        self._cache_mtime: float = -1.0
        self._cache: dict[str, Model] = {}

    # ── path resolution ───────────────────────────────────────────────────

    @property
    def registry_dir(self) -> Path:
        """Resolved registry directory (override or paths.registry_dir())."""
        if self._registry_dir_override is not None:
            return self._registry_dir_override
        return paths.registry_dir()

    @property
    def registry_file(self) -> Path:
        """Path to ``registry.toml`` under the registry directory."""
        return self.registry_dir / _DEFAULT_REGISTRY_FILENAME

    # ── mtime cache ───────────────────────────────────────────────────────

    def _stat_mtime(self) -> float:
        """Return the registry file's mtime, or -1.0 if missing."""
        try:
            return os.stat(self.registry_file).st_mtime
        except FileNotFoundError:
            return -1.0

    def _read_locked(self) -> dict[str, Model]:
        """Re-parse ``registry.toml`` under the held lock.

        TIER1: never silently return ``{}`` on parse failure.  If the
        file exists but is malformed, log at WARN and keep the prior
        cache view (better than blanking it).  haloai
        ``lib/registry.py``'s ``_read_locked`` did the same in spirit;
        we make the rationale explicit.
        """
        mt = self._stat_mtime()
        if mt < 0:
            self._cache_mtime = -1.0
            self._cache = {}
            return self._cache
        try:
            with open(self.registry_file, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            log.warning(
                "registry parse failed at %s: %s — keeping stale cache",
                self.registry_file,
                exc,
            )
            return self._cache
        except OSError as exc:
            log.warning(
                "registry read failed at %s: %s — keeping stale cache",
                self.registry_file,
                exc,
            )
            return self._cache

        raw = data.get("models", {}) if isinstance(data, dict) else {}
        out: dict[str, Model] = {}
        if isinstance(raw, dict):
            for mid, entry in raw.items():
                if not isinstance(entry, dict):
                    log.warning(
                        "registry entry %r at %s is not a table; skipping",
                        mid,
                        self.registry_file,
                    )
                    continue
                try:
                    out[mid] = Model.model_validate({**entry, "id": mid})
                except Exception as exc:
                    log.warning(
                        "registry entry %r failed validation: %s — skipping",
                        mid,
                        exc,
                    )
                    continue
        elif isinstance(raw, list):
            # haloai accepted both shapes; we mirror that for backcompat.
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("id")
                if not isinstance(mid, str) or not mid:
                    continue
                try:
                    out[mid] = Model.model_validate(entry)
                except Exception as exc:
                    log.warning(
                        "registry list-entry %r failed validation: %s — skipping",
                        mid,
                        exc,
                    )
                    continue

        self._cache_mtime = mt
        self._cache = out
        return out

    def _ensure_fresh(self) -> dict[str, Model]:
        """Re-read if the file's mtime advanced; return the cached map."""
        mt = self._stat_mtime()
        with self._lock:
            if mt != self._cache_mtime:
                return self._read_locked()
            return self._cache

    # ── atomic write ─────────────────────────────────────────────────────

    def _atomic_write(self, models: dict[str, Model]) -> None:
        """Write ``registry.toml`` with tmpfile + fsync + os.replace().

        Mirrors hal0.config.env.write_env_atomic — tmpfile in the same
        directory, fsync after write, atomic rename.  On rename failure
        the original file is left intact.
        """
        target = self.registry_file
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {"models": {mid: _model_to_toml(m) for mid, m in sorted(models.items())}}

        tmp_path: Path | None = None
        try:
            fd, tmp_str = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            tmp_path = Path(tmp_str)
            try:
                with os.fdopen(fd, "wb") as f:
                    tomli_w.dump(payload, f)
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

    def _invalidate(self) -> None:
        """Clear the mtime tag so the next read re-parses."""
        self._cache_mtime = -1.0

    # ── public reads ─────────────────────────────────────────────────────

    def list(self) -> list[Model]:
        """Return all registered models, sorted by id."""
        models = self._ensure_fresh()
        return [models[mid] for mid in sorted(models)]

    def get(self, model_id: str) -> Model:
        """Return a single model by id.

        Raises:
            ModelNotFound: If the model is not in the registry.
        """
        models = self._ensure_fresh()
        if model_id not in models:
            raise ModelNotFound(
                f"model {model_id!r} not in registry",
                details={"model_id": model_id},
            )
        return models[model_id]

    def has(self, model_id: str) -> bool:
        """Return True if ``model_id`` is registered."""
        return model_id in self._ensure_fresh()

    # ── public writes ────────────────────────────────────────────────────

    def _notify_change(self) -> None:
        """Invoke the post-mutation hook, if any. Best-effort.

        Called after every successful add/update/remove, OUTSIDE ``self._lock``
        (the hook reads ``registry.toml`` from disk, not the locked in-memory
        map, so there's no re-entrancy). A hook that raises is logged and
        swallowed — the registry write has already committed.
        """
        cb = self.on_change
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.warning("registry.on_change_failed", exc_info=True)

    def add(self, model: Model) -> None:
        """Add a new model to the registry.

        Writes atomically using tempfile + fsync + os.replace.

        Raises:
            ModelAlreadyExists: If the model id is already present.
        """
        with self._lock:
            models = dict(self._ensure_fresh())
            if model.id in models:
                raise ModelAlreadyExists(
                    f"model {model.id!r} already in registry",
                    details={"model_id": model.id},
                )
            models[model.id] = model
            self._atomic_write(models)
            self._invalidate()
        self._notify_change()

    def remove(self, model_id: str) -> bool:
        """Remove a model from the registry.

        Returns:
            ``True`` if the model was present and removed, ``False`` if absent.
        """
        with self._lock:
            models = dict(self._ensure_fresh())
            if model_id not in models:
                return False
            del models[model_id]
            self._atomic_write(models)
            self._invalidate()
        self._notify_change()
        return True

    def update(self, model_id: str, updates: dict[str, Any]) -> Model:
        """Partially update a model entry.

        ``updates`` is a flat field-level merge: any key present in
        ``updates`` overwrites the same field on the stored ``Model``.
        Keys not in ``updates`` are preserved.  The ``id`` field is
        never changeable through update (use remove + add).

        Raises:
            ModelNotFound: If the model is not in the registry.
            RegistryError: If ``updates`` produces an invalid Model.
        """
        if not isinstance(updates, dict):
            raise RegistryError(
                "updates must be a dict",
                details={"got": type(updates).__name__},
            )
        with self._lock:
            models = dict(self._ensure_fresh())
            if model_id not in models:
                raise ModelNotFound(
                    f"model {model_id!r} not in registry",
                    details={"model_id": model_id},
                )
            existing = models[model_id].model_dump(mode="python")
            merged = {**existing, **{k: v for k, v in updates.items() if k != "id"}}
            merged["id"] = model_id
            try:
                new_model = Model.model_validate(merged)
            except Exception as exc:
                raise RegistryError(
                    f"update for {model_id!r} produced an invalid Model: {exc}",
                    details={"model_id": model_id, "reason": str(exc)},
                ) from exc
            models[model_id] = new_model
            self._atomic_write(models)
            self._invalidate()
        self._notify_change()
        return new_model

    def route_for(self, model_id: str) -> str | None:
        """Return the upstream URL for a model, or ``None`` if not assigned.

        Used by the Dispatcher to resolve registry bindings.  The wire
        format is::

            metadata = { "upstream_url": "http://127.0.0.1:8081" }

        # NOTE: haloai's registry stored ``upstream`` as a name that the
        # dispatcher cross-resolved against upstreams.toml.  hal0 keeps
        # the same shape but exposes a `route_for()` helper that returns
        # the resolved URL string (or None).  The cross-resolution lives
        # in the dispatcher (Agent G's subtree); the registry only
        # surfaces the raw ``upstream_url`` from ``metadata``.
        """
        try:
            model = self.get(model_id)
        except ModelNotFound:
            return None
        url = model.metadata.get("upstream_url")
        if isinstance(url, str) and url.strip():
            return url
        return None

    def reload(self) -> None:
        """Force-invalidate the mtime cache; next access re-reads from disk."""
        with self._lock:
            self._invalidate()


def model_to_toml_dict(m: Model) -> dict[str, Any]:
    """Public alias for :func:`_model_to_toml`.

    Exposed so out-of-tree callers (migration scripts, archival tools)
    use the same None-stripping logic as the registry's atomic write
    path. Otherwise pydantic v2's ``model_dump`` emits ``defaults=None``
    for unset ``ModelDefaults``, which ``tomli_w`` refuses to serialise.
    """
    return _model_to_toml(m)


def _model_to_toml(m: Model) -> dict[str, Any]:
    """Serialise a Model to the TOML-friendly dict.

    Drops the synthetic ``id`` (it's the table key on disk) and any
    top-level ``None`` values (TOML has no null). For the nested
    ``defaults`` table we also strip ``None`` leaves so optional
    ModelDefaults fields aren't written as empty entries that would
    fail TOML serialisation.
    """
    data = m.model_dump(mode="python", exclude_none=False)
    data.pop("id", None)

    # Top-level None → drop. Nested 'defaults' table: drop None leaves
    # too, and collapse to no key at all when nothing is set.
    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        if v is None:
            continue
        if k == "defaults" and isinstance(v, dict):
            sub = {sk: sv for sk, sv in v.items() if sv is not None}
            if not sub:
                continue
            cleaned[k] = sub
            continue
        if k == "metadata" and isinstance(v, dict):
            cleaned[k] = {mk: mv for mk, mv in v.items() if mv is not None}
            continue
        cleaned[k] = v
    return cleaned


__all__ = [
    "ModelAlreadyExists",
    "ModelNotFound",
    "ModelRegistry",
    "RegistryError",
    "model_to_toml_dict",
]
