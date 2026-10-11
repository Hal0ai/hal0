"""Upgrade migration: strip ``--reasoning-format none`` from persisted flags (#2545).

Upstream llama.cpp answers HTTP 400 "Failed to initialize samplers" to every
``response_format`` request (json_object / json_schema) when the server runs
with ``--reasoning-format none``. The demoted workload seed profiles used to pass
``--reasoning off --reasoning-format none``; boxes seeded from them still carry
the pair in three places this migration heals:

* operator profiles — ``/etc/hal0/profiles.toml`` ``[profile.<name>].flags``;
* slot TOMLs — ``[server].extra_args`` (inert at launch since flags-ownership,
  but ``hal0 slot migrate-flags`` would otherwise fold it into the model);
* registry models — ``defaults.extra_args``.

``--reasoning off`` is left alone: it is the "no thinking" half of the pair and
works without the format override. Any other ``--reasoning-format`` value
(``deepseek``, ``auto``) is a deliberate choice and is never touched.

Contract (same as the other upgrade passes in :mod:`hal0.updater.updater`):

* **Idempotent.** A file / row is rewritten only when it still carries the
  pair; a second run touches nothing.
* **Backed up first.** Every file that is rewritten is first copied to
  ``<var_lib>/backups/<kind>-<UTC stamp>.toml`` (owner-only 0600, collision-suffixed, never
  overwritten — the :func:`hal0.updater.updater._backup_profiles_toml`
  convention). Registry rows have no file of their own, so the prior
  ``defaults.extra_args`` of every changed row is written to
  ``<var_lib>/backups/model-extra-args-<UTC stamp>.json`` instead.
* **Best-effort per item.** One unreadable file or failed write is logged and
  skipped; it never aborts the sweep or the update that called it.
* **Automatic.** Runs from :func:`hal0.updater.updater.run_post_activation_migrations`
  — i.e. on ``hal0 update``, on an ``install.sh`` re-run, and at API boot via
  ``check_outstanding_migrations``. It needs no operator command.

The token rule itself lives in :func:`hal0.slots.argv.strip_reasoning_format_none`
so the fold path (:mod:`hal0.config.migrations.slot_flags_fold`) applies the
identical rule and the order the two run in does not matter.
"""

from __future__ import annotations

import json
import os
import shlex
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from hal0.config import paths
from hal0.slots.argv import strip_reasoning_format_none

log = structlog.get_logger("hal0.config.migrations.reasoning_format_none")

__all__ = [
    "migrate_model_defaults",
    "migrate_profiles",
    "migrate_slots",
    "run_migration",
    "strip_flag_string",
]


def strip_flag_string(flags: str) -> tuple[str, int]:
    """Strip ``--reasoning-format none`` from a flag string.

    Returns ``(new_flags, removed)``. A string that does not carry the pair — or
    whose quoting is malformed and so cannot be tokenised safely — comes back
    byte-identical with ``removed == 0``.
    """
    if "reasoning-format" not in flags:
        return flags, 0
    try:
        tokens = shlex.split(flags)
    except ValueError:
        return flags, 0
    clean, removed = strip_reasoning_format_none(tokens)
    if not removed:
        return flags, 0
    return " ".join(shlex.quote(tok) for tok in clean), removed


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _write_backup(kind: str, suffix: str, data: bytes) -> Path | None:
    """Create ``<var_lib>/backups/<kind>-<stamp><suffix>`` owner-only; ``None`` on failure.

    Backups copy operator config that can hold secrets (slot env, extra args),
    so the file is created ``0600`` atomically (``O_EXCL``, so a name collision
    suffixes rather than overwrites) and the backups directory is ``0700`` when
    this call is the one that creates it. An existing directory is left as the
    operator set it.
    """
    root = paths.var_lib() / "backups"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        stem = f"{kind}-{_stamp()}"
        n = 1
        while True:
            dest = root / (f"{stem}{suffix}" if n == 1 else f"{stem}-{n}{suffix}")
            try:
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                n += 1
                continue
            break
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        log.warning("reasoning_format_none.backup_failed", kind=kind, error=str(exc))
        return None
    return dest


def _backup_file(src: Path, kind: str) -> Path | None:
    """Back up ``src`` as ``<kind>-<stamp>.toml`` (owner-only)."""
    try:
        data = src.read_bytes()
    except OSError as exc:
        log.warning("reasoning_format_none.backup_failed", path=str(src), error=str(exc))
        return None
    return _write_backup(kind, ".toml", data)


def migrate_profiles(path: Path | None = None) -> list[str]:
    """Strip the pair from every profile's ``flags`` in ``profiles.toml``.

    Returns the names of the profiles rewritten. Seed-named profiles are
    included: an operator may have edited one, and the built-in (virtual) seed
    no longer carries the pair, so a persisted copy that does is stale either
    way.
    """
    from hal0.config.loader import write_toml_atomic

    # No cross-process lock to take here: unlike slots/*.toml (`slot_write_lock`)
    # and hal0.toml (`hal0_config_file_lock`), no writer of profiles.toml takes
    # one (ProfileCatalog serialises only in-process). The read-strip-backup-write
    # below is a single short pass with no await.
    target = path or paths.profiles_toml()
    if not target.is_file():
        return []
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("reasoning_format_none.profiles_unreadable", error=str(exc))
        return []
    table = raw.get("profile")
    if not isinstance(table, dict):
        return []

    changed: list[str] = []
    for name, entry in table.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("flags"), str):
            continue
        new_flags, removed = strip_flag_string(entry["flags"])
        if removed:
            entry["flags"] = new_flags
            changed.append(name)
    if not changed:
        return []

    if _backup_file(target, "profiles") is None:
        return []  # never rewrite without a restore point
    try:
        write_toml_atomic(target, raw)
    except Exception as exc:
        log.warning("reasoning_format_none.profiles_write_failed", error=str(exc))
        return []
    for name in changed:
        log.warning(
            "reasoning_format_none.profile_stripped",
            profile=name,
            note=(
                "profile flags carried `--reasoning-format none`, which makes upstream "
                "llama.cpp reject every response_format request (#2545); removed"
            ),
        )
    return changed


def _server_tables(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``[server]`` table(s) of a slot dict, in either known shape."""
    out: list[dict[str, Any]] = []
    for holder in (raw, raw.get("slot")):
        if isinstance(holder, dict) and isinstance(holder.get("server"), dict):
            out.append(holder["server"])
    return out


def migrate_slots(slots_dir: Path | None = None) -> list[str]:
    """Strip the pair from every slot TOML's ``[server].extra_args``.

    Returns the stems of the slot files rewritten.
    """
    from hal0.slot_config import slot_write_lock, write_slot_toml

    base = slots_dir or paths.slots_config_dir()
    if not base.is_dir():
        return []

    changed: list[str] = []
    for toml_path in sorted(base.glob("*.toml")):
        # The WHOLE read -> strip -> backup -> write is one locked section:
        # `slot_write_lock` guards slot read-modify-write, so reading outside
        # it could overwrite a concurrent API edit with a stale snapshot.
        with slot_write_lock(base):
            try:
                raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning(
                    "reasoning_format_none.slot_unreadable", slot=toml_path.stem, error=str(exc)
                )
                continue
            dirty = False
            for server in _server_tables(raw):
                extra = server.get("extra_args")
                if not isinstance(extra, str):
                    continue
                new_extra, removed = strip_flag_string(extra)
                if removed:
                    server["extra_args"] = new_extra
                    dirty = True
            if not dirty:
                continue
            if _backup_file(toml_path, f"slot-{toml_path.stem}") is None:
                continue
            try:
                write_slot_toml(toml_path, raw)
            except Exception as exc:
                log.warning(
                    "reasoning_format_none.slot_write_failed",
                    slot=toml_path.stem,
                    error=str(exc),
                )
                continue
        changed.append(toml_path.stem)
        log.warning(
            "reasoning_format_none.slot_stripped",
            slot=toml_path.stem,
            note="[server].extra_args carried `--reasoning-format none` (#2545); removed",
        )
    return changed


def migrate_model_defaults(registry: Any = None) -> list[str]:
    """Strip the pair from every registry model's ``defaults.extra_args``.

    ``registry`` is anything with ``list()`` and ``update(model_id, updates)``;
    ``None`` uses the real :class:`~hal0.registry.store.ModelRegistry`. Returns
    the ids of the models rewritten.
    """
    if registry is None:
        from hal0.registry.store import ModelRegistry

        registry = ModelRegistry()

    pending: list[tuple[Any, str, str]] = []  # (model, old_extra, new_extra)
    for model in registry.list():
        defaults = getattr(model, "defaults", None)
        extra = getattr(defaults, "extra_args", None) if defaults is not None else None
        if not isinstance(extra, str) or not extra.strip():
            continue
        new_extra, removed = strip_flag_string(extra)
        if removed:
            pending.append((model, extra, new_extra))
    if not pending:
        return []

    # Registry rows are not a file; record what each row held so the change is
    # reversible by hand.
    payload = json.dumps({m.id: old for m, old, _ in pending}, indent=2, sort_keys=True) + "\n"
    if _write_backup("model-extra-args", ".json", payload.encode("utf-8")) is None:
        return []  # never rewrite without a restore point

    changed: list[str] = []
    for model, _old, new_extra in pending:
        defaults_dict = model.defaults.model_dump(mode="python")
        # An emptied string is stored as None so the row reads as "no tune",
        # not as an empty tune.
        defaults_dict["extra_args"] = new_extra or None
        try:
            registry.update(model.id, {"defaults": defaults_dict})
        except Exception as exc:
            log.warning("reasoning_format_none.model_write_failed", model=model.id, error=str(exc))
            continue
        changed.append(model.id)
        log.warning(
            "reasoning_format_none.model_stripped",
            model=model.id,
            note="defaults.extra_args carried `--reasoning-format none` (#2545); removed",
        )
    return changed


def run_migration(*, registry: Any = None) -> dict[str, list[str]]:
    """Run all three sweeps; each is independent and best-effort.

    Returns ``{"profiles": [...], "slots": [...], "models": [...]}`` — the
    items rewritten (all empty once converged).
    """

    def _models() -> list[str]:
        return migrate_model_defaults(registry)

    result: dict[str, list[str]] = {"profiles": [], "slots": [], "models": []}
    for key, fn in (
        ("profiles", migrate_profiles),
        ("slots", migrate_slots),
        ("models", _models),
    ):
        try:
            result[key] = fn()
        except Exception as exc:
            log.warning("reasoning_format_none.sweep_failed", sweep=key, error=str(exc))
    return result
