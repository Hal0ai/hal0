"""Apply the profile-centric conversion (ADR-0029): back up, write, verify.

:func:`migrate_profile_centric` runs :func:`~hal0.config.migrations.profile_centric.plan_conversion`
and, unless it is a dry run, writes the result:

* ``profiles.toml`` — every custom profile in the new shape (converted,
  renamed, adopted and generated; one already in the new shape, or mixing
  both shapes, is kept exactly as stored), the demoted-seed markers dropped
  once nothing is blocked;
* each converted slot file — re-pointed at its profile, tuning keys removed;
  a blocked slot's file is left untouched;
* ``stacks.toml`` — row tuning keys dropped, row profiles re-pointed,
  embedded profiles in the new shape;
* the model registry — tuning cleared (the SQL migration 012 holding table
  rows deleted, an explicit ``mtp = false`` cleared); the holding table is
  dropped once nothing is blocked;
* ``/var/lib/hal0/profile-centric.json`` — an audit record of this run
  (mode 0640). It is never read back: what still needs converting is read
  from the content (ADR-0029 §7), so a rerun plans nothing.

Contract, as for the runner-system conversion (ADR-0028):

* **Runs on raw files before config validation.** First in
  ``run_post_activation_migrations`` (after the runner-system conversion)
  and in ``hal0 serve`` before the API is imported; never on import.
* **Backup first.** The original of every file it changes, plus the model
  tuning it clears, goes into ``/var/lib/hal0/backups/profiles-<ts>.tar.gz``
  (mode 0600). No backup, no write.
* **Verified, or rolled back.** After writing, every converted llama slot's
  launch is rendered through the real launch path and compared with the
  plan's; any difference, or a failed write, restores every file and the
  registry and raises.
* **Restore is for downgrades** (``hal0 profile migrate --restore``): with
  ``hal0-api`` stopped it puts the files back, re-adds the dropped model
  columns with their values, and leaves a version-keyed marker so the
  boot-time conversion stands down.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sqlite3
import tarfile
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import tomli_w

from hal0.config.migrations import profile_centric as pc
from hal0.config.migrations.runner_system import (
    _api_is_active,
    _backups_dir,
    _check_archive,
    _replace_file,
    _running_version,
)
from hal0.errors import Hal0Error

log = structlog.get_logger(__name__)

RESTORE_MARKER_NAME = "profile-centric.restored"
_BACKUP_PROFILES = "etc/profiles.toml"
_BACKUP_STACKS = "etc/stacks.toml"
_BACKUP_STATE = "var/profile-centric.json"
_BACKUP_MODELS = "registry/model-tuning.json"
_BACKUP_SLOT_RE = re.compile(r"^etc/slots/(?P<name>[A-Za-z0-9_.-]+\.toml)$")
_BACKUP_MEMBER_MAX = 8 * 1024 * 1024
#: Columns SQL migration 012 drops from ``model`` (restore re-adds them).
_DROPPED_COLUMNS: dict[str, str] = {
    "profile": "TEXT",
    "extra_args": "TEXT",
    "n_gpu_layers": "INTEGER",
    "context_size": "INTEGER",
    "rope_freq_base": "REAL",
    "preferred_runner": "TEXT",
}


class ProfileConversionError(Hal0Error):
    """The conversion could not be applied (and was rolled back)."""

    code = "profiles.conversion_failed"


@dataclass
class ConversionReport:
    """What a run did (or, for a dry run, would do)."""

    dry_run: bool
    plan: pc.ConversionPlan | None = None
    backup: str | None = None
    written: list[str] = field(default_factory=list)
    skipped_for_restore: bool = False

    def lines(self) -> list[str]:
        out = list(self.plan.lines()) if self.plan is not None else []
        if self.backup:
            out.append(f"backup: {self.backup}")
        out.extend(f"wrote {w}" for w in self.written)
        if self.skipped_for_restore:
            out.append("skipped: a restore marker is active (run --apply to convert again)")
        return out


@dataclass
class _FileWrite:
    path: Path
    member: str
    original: bytes
    new: bytes


# ── file edits ───────────────────────────────────────────────────────────────


def _dump(data: dict[str, Any]) -> bytes:
    return tomli_w.dumps(data).encode()


def _edit_slot(raw: dict[str, Any], sp: pc.SlotPlan) -> dict[str, Any]:
    """The slot file after conversion (raw TOML in, raw TOML out)."""
    data = json.loads(json.dumps(raw))
    tables: list[dict[str, Any]] = [data]
    nested = data.get("slot")
    if isinstance(nested, dict):
        tables.append(nested)
    for key in pc.SLOT_DROP_KEYS:
        for table in tables:
            table.pop(key, None)
    model = data.get("model")
    if isinstance(model, dict):
        for key in pc.MODEL_TABLE_DROP:
            model.pop(key, None)
    server = data.get("server")
    if isinstance(server, dict):
        server.pop("extra_args", None)
        if not server:
            data.pop("server")
    data.pop("defaults", None)
    home = nested if isinstance(nested, dict) and "profile" in nested else data
    if sp.profile_after:
        home["profile"] = sp.profile_after
    for key, value in sp.set_keys.items():
        if key == "npu":
            data["npu"] = value
        else:
            (nested if isinstance(nested, dict) and key in nested else data)[key] = value
    return data


def _profiles_file(raw: dict[str, Any] | None, plan: pc.ConversionPlan) -> dict[str, Any]:
    data: dict[str, Any] = {} if raw is None else json.loads(json.dumps(raw))
    stored = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    # Entries already in the new shape that the plan does not carry (one a
    # new seed shadows) and entries mixing both shapes (blocked) stay exactly
    # as stored: the converter never deletes or rewrites what it cannot read.
    kept = {
        name: entry
        for name, entry in stored.items()
        if name not in plan.custom_profiles
        and isinstance(entry, dict)
        and pc.profile_shape(entry)[0] in ("new", "mixed")
    }
    data["profile"] = dict(
        sorted({**kept, **{n: dict(c) for n, c in plan.custom_profiles.items()}}.items())
    )
    if plan.drop_markers:
        data.pop("legacy_seeds_migrated", None)
        data.pop("legacy_seeds_adopted", None)
    return data


def _stacks_file(raw: dict[str, Any], plan: pc.ConversionPlan) -> dict[str, Any]:
    data = json.loads(json.dumps(raw))
    stacks = data.get("stack") if isinstance(data.get("stack"), dict) else {}
    for st in plan.stacks:
        stack = stacks.get(st.stack)
        if not isinstance(stack, dict):
            continue
        if st.profiles:
            # Update in place: an embedded profile already in the new shape
            # (or mixing shapes, blocked) is not in the plan and stays as is.
            embedded = stack.get("profiles") if isinstance(stack.get("profiles"), dict) else {}
            stack["profiles"] = {**embedded, **{k: dict(v) for k, v in st.profiles.items()}}
        rows = stack.get("slots") if isinstance(stack.get("slots"), list) else []
        for edit in st.rows:
            row = rows[edit["index"]]
            for key in edit["drop"]:
                row.pop(key, None)
            if edit["profile"]:
                row["profile"] = edit["profile"]
    return data


def _plan_writes(inputs: pc.Inputs, plan: pc.ConversionPlan) -> list[_FileWrite]:
    from hal0.config import paths

    writes: list[_FileWrite] = []
    slots_dir = paths.slots_config_dir()
    raw_by_file = {fname: raw for fname, raw, _err in inputs.slots}
    for sp in plan.slots:
        if sp.status != "convert":
            continue
        path = slots_dir / sp.file
        original = path.read_bytes()
        new = _dump(_edit_slot(raw_by_file[sp.file] or {}, sp))
        if new != original:
            writes.append(_FileWrite(path, f"etc/slots/{sp.file}", original, new))

    profiles_path = paths.profiles_toml()
    original = profiles_path.read_bytes() if profiles_path.exists() else b""
    if plan.custom_profiles or inputs.profiles_raw is not None:
        new = _dump(_profiles_file(inputs.profiles_raw, plan))
        if new != original:
            writes.append(_FileWrite(profiles_path, _BACKUP_PROFILES, original, new))

    if inputs.stacks_raw is not None and plan.stacks:
        stacks_path = paths.stacks_toml()
        original = stacks_path.read_bytes()
        new = _dump(_stacks_file(inputs.stacks_raw, plan))
        if new != original:
            writes.append(_FileWrite(stacks_path, _BACKUP_STACKS, original, new))
    return writes


# ── registry ─────────────────────────────────────────────────────────────────


def _ensure_db_migrated(db_path: Path) -> None:
    """Apply pending SQL migrations (012 moves the tuning into the holding table)."""
    from hal0.db.connection import connect
    from hal0.db.migrate import migrate

    with connect(db_path) as conn:
        migrate(conn)


def _model_snapshot(db_path: Path) -> list[dict[str, Any]]:
    """The model tuning the conversion clears, for the backup and a rollback."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows: dict[str, dict[str, Any]] = {}
        if pc.HOLDING_TABLE in tables:
            for r in conn.execute(f"SELECT * FROM {pc.HOLDING_TABLE}"):
                rows[str(r["id"])] = {"held": dict(r)}
        if "model" in tables:
            for r in conn.execute("SELECT id, mtp FROM model WHERE mtp IS NOT NULL"):
                rows.setdefault(str(r["id"]), {})["mtp"] = r["mtp"]
        return [{"id": mid, **v} for mid, v in sorted(rows.items())]
    finally:
        conn.close()


def _clear_model_tuning(
    db_path: Path, plan: pc.ConversionPlan, *, drop_holding: bool, keep: set[str]
) -> None:
    """Clear the tuning the plan moved onto profiles; ``keep`` names models a
    blocked slot still launches from."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for m in plan.models:
                if not m.clear or m.model_id in keep:
                    continue
                if pc.HOLDING_TABLE in tables:
                    conn.execute(f"DELETE FROM {pc.HOLDING_TABLE} WHERE id = ?", (m.model_id,))
                if m.clear.get("mtp") is False:
                    conn.execute("UPDATE model SET mtp = NULL WHERE id = ?", (m.model_id,))
            if drop_holding and pc.HOLDING_TABLE in tables:
                conn.execute(f"DROP TABLE {pc.HOLDING_TABLE}")
    finally:
        conn.close()


def _restore_models(db_path: Path, rows: list[dict[str, Any]], *, columns: bool) -> None:
    """Put the model tuning back: the holding table (rollback) or, for a
    downgrade (``columns=True``), the dropped ``model`` columns themselves."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if columns:
                have = {r[1] for r in conn.execute("PRAGMA table_info(model)")}
                for col, kind in _DROPPED_COLUMNS.items():
                    if col not in have:
                        conn.execute(f"ALTER TABLE model ADD COLUMN {col} {kind}")
            for row in rows:
                mid = row["id"]
                if "mtp" in row:
                    conn.execute("UPDATE model SET mtp = ? WHERE id = ?", (row["mtp"], mid))
                held = row.get("held")
                if not held:
                    continue
                if columns:
                    sets = {c: held.get(c) for c in _DROPPED_COLUMNS}
                    conn.execute(
                        "UPDATE model SET " + ", ".join(f"{c} = ?" for c in sets) + " WHERE id = ?",
                        (*sets.values(), mid),
                    )
                    for key in ("enable_thinking", "vision"):
                        if held.get(key) is not None:
                            conn.execute(
                                "UPDATE model SET extra = json_set(COALESCE(extra, '{}'), ?, "
                                "json(?)) WHERE id = ?",
                                (f"$._{key}", "true" if held[key] else "false", mid),
                            )
                else:
                    if pc.HOLDING_TABLE not in tables:
                        conn.execute(
                            f"CREATE TABLE {pc.HOLDING_TABLE} (id TEXT PRIMARY KEY, profile TEXT, "
                            "extra_args TEXT, n_gpu_layers INTEGER, context_size INTEGER, "
                            "rope_freq_base REAL, preferred_runner TEXT, "
                            "enable_thinking INTEGER, vision INTEGER)"
                        )
                        tables.add(pc.HOLDING_TABLE)
                    keys = list(held)
                    conn.execute(
                        f"INSERT OR REPLACE INTO {pc.HOLDING_TABLE} ({', '.join(keys)}) "
                        f"VALUES ({', '.join('?' for _ in keys)})",
                        [held[k] for k in keys],
                    )
            if columns:
                if pc.HOLDING_TABLE in tables:
                    conn.execute(f"DROP TABLE {pc.HOLDING_TABLE}")
                conn.execute("DELETE FROM schema_migrations WHERE version = 12")
    finally:
        conn.close()


# ── verification ─────────────────────────────────────────────────────────────


def _verify(plan: pc.ConversionPlan) -> dict[str, str]:
    """Render every converted llama slot through the real launch path and
    compare it with the plan's launch. Returns ``{slot file: difference}``."""
    from hal0.config import paths
    from hal0.config.migrations import profile_centric_frozen as frozen
    from hal0.providers.container import _resolve_slot_argv

    problems: dict[str, str] = {}
    for sp in plan.slots:
        if sp.status != "convert" or sp.engine != frozen.LLAMA or not sp.argv_after:
            continue
        path = paths.slots_config_dir() / sp.file
        try:
            slot = frozen.hoist_slot(tomllib.loads(path.read_text(encoding="utf-8")), sp.file[:-5])
            resolved = _resolve_slot_argv(slot)
        except Exception as exc:
            problems[sp.file] = f"cannot render ({exc})"
            continue
        if resolved is None:
            problems[sp.file] = "does not resolve after conversion"
            continue
        live = pc.effective_map(resolved[1].argv)
        want = pc.effective_map(sp.argv_after)
        if live != want:
            diff = [
                f"{k}: plan {want.get(k)!r}, launch {live.get(k)!r}"
                for k in sorted(set(live) | set(want))
                if live.get(k) != want.get(k)
            ]
            problems[sp.file] = "launch differs from the plan on " + "; ".join(diff)
    return problems


def _slot_model(raw: Mapping[str, Any] | None) -> str:
    """The model a raw slot file names (``[model].default``)."""
    table = (raw or {}).get("model")
    return str(table.get("default") or "") if isinstance(table, Mapping) else ""


# ── state + marker ───────────────────────────────────────────────────────────


def _state_path() -> Path:
    from hal0.config import paths

    return paths.var_lib() / pc.STATE_FILE


def restore_marker() -> Path:
    from hal0.config import paths

    return paths.var_lib() / RESTORE_MARKER_NAME


def _restore_marker_active() -> bool:
    try:
        text = restore_marker().read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "version":
            return value.strip() == _running_version()
    return False


def _clear_restore_marker() -> None:
    with contextlib.suppress(FileNotFoundError):
        restore_marker().unlink()


def _write_backup(
    writes: list[_FileWrite], models: list[dict[str, Any]], state: bytes | None
) -> Path:
    backup_dir = _backups_dir()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    buf = io.BytesIO()

    def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = 0o600
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for w in writes:
            _add(tar, w.member, w.original)
        _add(tar, _BACKUP_MODELS, json.dumps(models, indent=2, default=str).encode())
        if state is not None:
            _add(tar, _BACKUP_STATE, state)
    n = 0
    while True:  # never overwrite an earlier restore point
        suffix = f"-{n}" if n else ""
        target = backup_dir / f"profiles-{stamp}{suffix}.tar.gz"
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            n += 1
            continue
        break
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(buf.getvalue())
        f.flush()
        os.fsync(f.fileno())
    return target


# ── entry points ─────────────────────────────────────────────────────────────


def migrate_profile_centric(
    *,
    dry_run: bool = False,
    job_id: str | None = None,
    honour_restore_marker: bool = False,
    verify: bool = True,
) -> ConversionReport:
    """Convert this install onto profile-centric config. See the module docstring.

    ``honour_restore_marker`` is for the boot-time callers (``hal0 serve``, the
    API safety net): after a restore they stand down. ``hal0 update`` and an
    explicit ``--apply`` pass ``False`` and convert again.
    """
    from hal0.config import paths
    from hal0.config.loader import hal0_config_file_lock
    from hal0.slot_config import slot_write_lock

    if honour_restore_marker and _restore_marker_active():
        log.warning("migrate.profile_centric.skipped_after_restore", job_id=job_id)
        return ConversionReport(dry_run=True, skipped_for_restore=True)
    if dry_run:
        # A preview reads and writes nothing, not even a lock file (it may
        # run as a user who cannot create one under /etc/hal0).
        return _migrate(dry_run=True, job_id=job_id, verify=verify)

    with contextlib.ExitStack() as locks:
        if paths.etc().is_dir():
            locks.enter_context(hal0_config_file_lock(paths.hal0_toml()))
            if paths.slots_config_dir().is_dir():
                locks.enter_context(slot_write_lock())
        return _migrate(dry_run=dry_run, job_id=job_id, verify=verify)


def _block_mismatched_slots(
    inputs: pc.Inputs,
    plan: pc.ConversionPlan,
    done: list[_FileWrite],
    problems: dict[str, str],
    *,
    job_id: str | None,
) -> None:
    """A slot whose converted launch differs from the plan blocks alone.

    Each slot file is separate: its original goes back, it is reported as
    BLOCKED with the difference, and the rest of the install stays
    converted. A profile generated for it alone stays in ``profiles.toml``,
    unreferenced. The demoted-seed markers come back while anything is
    blocked.
    """
    from hal0.config import paths

    if not problems:
        return
    slots_dir = paths.slots_config_dir()
    for sp in plan.slots:
        if sp.file not in problems:
            continue
        for w in list(done):
            if w.path == slots_dir / sp.file:
                _replace_file(w.path, w.original)
                done.remove(w)
        sp.status = "blocked"
        sp.error = f"the converted launch does not match the plan: {problems[sp.file]}"
        log.error(
            "migrate.profile_centric.slot_blocked", job_id=job_id, slot=sp.file, error=sp.error
        )
    if plan.drop_markers:
        plan.drop_markers = False
        profiles_path = paths.profiles_toml()
        for w in done:
            if w.path == profiles_path:
                w.new = _dump(_profiles_file(inputs.profiles_raw, plan))
                _replace_file(w.path, w.new)


def _migrate(*, dry_run: bool, job_id: str | None, verify: bool) -> ConversionReport:
    from hal0.config import paths

    db_path = paths.db_path()
    if not dry_run and db_path.exists():
        _ensure_db_migrated(db_path)
    inputs = pc.read_inputs()
    plan = pc.plan_conversion(inputs)
    report = ConversionReport(dry_run=dry_run, plan=plan)
    if dry_run or not plan.changed:
        if not dry_run:
            _clear_restore_marker()
        return report

    writes = _plan_writes(inputs, plan)
    models = _model_snapshot(db_path)
    state_path = _state_path()
    state_original = state_path.read_bytes() if state_path.exists() else None
    try:
        backup = _write_backup(writes, models, state_original)
    except Exception as exc:
        raise ProfileConversionError(
            f"could not write the profile-conversion backup ({exc}); nothing was changed"
        ) from exc
    report.backup = str(backup)

    done: list[_FileWrite] = []
    db_touched = False
    try:
        for w in writes:
            _replace_file(w.path, w.new)
            done.append(w)
        if verify:
            _block_mismatched_slots(inputs, plan, done, _verify(plan), job_id=job_id)
        if db_path.exists():
            db_touched = True
            keep = {
                _slot_model(raw)
                for fname, raw, _err in inputs.slots
                if fname in {s.file for s in plan.blocked}
            }
            _clear_model_tuning(db_path, plan, drop_holding=not plan.blockers, keep=keep - {""})
        audit = {
            "complete": not plan.blockers,
            "converted": sorted(s.file for s in plan.slots if s.status == "convert"),
            "blocked": plan.blockers,
            "backup": str(backup),
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _replace_file(state_path, json.dumps(audit, indent=2).encode(), default_mode=0o640)
    except Exception as exc:
        not_restored: list[str] = []
        for w in reversed(done):
            try:
                _replace_file(w.path, w.original)
            except Exception as restore_exc:
                not_restored.append(f"{w.path} ({restore_exc})")
        if db_touched:
            try:
                _restore_models(db_path, models, columns=False)
            except Exception as restore_exc:
                not_restored.append(f"{db_path} ({restore_exc})")
        log.error(
            "migrate.profile_centric.failed",
            job_id=job_id,
            error=str(exc),
            backup=str(backup),
            not_restored=not_restored,
        )
        if not_restored:
            raise ProfileConversionError(
                f"profile conversion failed ({exc}) and these could NOT be restored: "
                f"{', '.join(not_restored)}. Stop hal0-api and run "
                f"`hal0 profile migrate --restore {backup}`"
            ) from exc
        raise ProfileConversionError(
            f"profile conversion failed and was rolled back: {exc} (backup: {backup})"
        ) from exc

    report.written = [str(w.path) for w in done]
    _clear_restore_marker()
    for line in report.lines():
        log.warning("migrate.profile_centric.applied", job_id=job_id, line=line)
    return report


def _owned_path(member: str) -> Path | None:
    from hal0.config import paths

    fixed = {
        _BACKUP_PROFILES: paths.profiles_toml(),
        _BACKUP_STACKS: paths.stacks_toml(),
        _BACKUP_STATE: _state_path(),
    }
    if member in fixed:
        return fixed[member]
    match = _BACKUP_SLOT_RE.match(member)
    if match is not None:
        return paths.slots_config_dir() / match.group("name")
    return None


def restore_backup(archive: Path | str) -> list[str]:
    """Put a ``profiles-<ts>.tar.gz`` backup back, for a downgrade.

    With ``hal0-api`` stopped, it writes back the files the conversion owns,
    re-adds the model columns SQL migration 012 dropped (with their values),
    forgets migration 012, and leaves a version-keyed marker so the boot-time
    conversion stands down. It refuses an archive another user could have
    written and any member it does not own.
    """
    from hal0.config import paths
    from hal0.config.loader import hal0_config_file_lock
    from hal0.slot_config import slot_write_lock

    archive = Path(archive)
    try:
        _check_archive(archive)
    except Hal0Error as exc:
        raise ProfileConversionError(str(exc)) from exc
    if _api_is_active():
        raise ProfileConversionError(
            "hal0-api is running; stop it first (`sudo systemctl stop hal0-api`) so it "
            "does not convert or overwrite the restored files"
        )
    files: dict[Path, bytes] = {}
    models: list[dict[str, Any]] = []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or member.size > _BACKUP_MEMBER_MAX:
                    raise ProfileConversionError(f"{archive}: bad member {member.name!r}")
                src = tar.extractfile(member)
                data = src.read() if src is not None else b""
                if member.name == _BACKUP_MODELS:
                    loaded = json.loads(data.decode("utf-8"))
                    if not isinstance(loaded, list):
                        raise ProfileConversionError(f"{archive}: {member.name} is not a list")
                    models = loaded
                    continue
                dest = _owned_path(member.name)
                if dest is None:
                    raise ProfileConversionError(
                        f"{archive}: unknown member {member.name!r}; refusing"
                    )
                files[dest] = data
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise ProfileConversionError(f"cannot read {archive}: {exc}") from exc

    restored: list[str] = []
    with contextlib.ExitStack() as locks:
        if paths.etc().is_dir():
            locks.enter_context(hal0_config_file_lock(paths.hal0_toml()))
            if paths.slots_config_dir().is_dir():
                locks.enter_context(slot_write_lock())
        for dest, data in files.items():
            dest.parent.mkdir(parents=True, exist_ok=True)
            _replace_file(dest, data)
            restored.append(str(dest))
        db_path = paths.db_path()
        if db_path.is_file():
            _restore_models(db_path, models, columns=True)
            restored.append(f"{db_path} (model tuning columns)")
        if _state_path() not in files:
            # The install had no conversion state before this run: forget it,
            # so a later conversion evaluates every slot again.
            with contextlib.suppress(FileNotFoundError):
                _state_path().unlink()
                restored.append(f"{_state_path()} (removed)")
    marker = restore_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    _replace_file(
        marker,
        f"version={_running_version()}\narchive={archive}\n".encode(),
        default_mode=0o644,
    )
    return restored


__all__ = [
    "RESTORE_MARKER_NAME",
    "ConversionReport",
    "ProfileConversionError",
    "migrate_profile_centric",
    "restore_backup",
    "restore_marker",
]
