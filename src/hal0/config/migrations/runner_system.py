"""One-shot conversion of an install onto the fresh runner system (ADR-0028).

The runner registry was rebuilt from scratch: ``standard`` replaced the
``rocmfpx`` default (and its ``vulkanfpx`` alias), ``strix`` and
``promptforge`` were removed, every image moved to
``ghcr.io/hal0ai/hal0-runner-<key>`` pinned in ``manifest.json``, and
ROCmFPX/ROCmFP4/IU4/ActiveFPX GGUFs are no longer loadable. This module moves
an existing install onto that system, once, generically:

==============================================  ==========================================
on-disk state                                   becomes
==============================================  ==========================================
slot ``binary`` = rocmfpx / vulkanfpx / strix /  ``binary = "standard"``
promptforge / upstream / vulkan / rocm
slot ``binary`` naming no runner                cleared (the device picks the runner)
slot ``image_pin`` / ``image`` (top level or    removed when it names a retired hal0
``[slot]``)                                     image; any other image (an operator's own
                                                build) is kept and reported
slot whose ``[model].default`` is a fork-only   model unassigned; the rest of the slot
quant (ROCmFPX family, IU4, ActiveFPX)          config is kept; reported as a WARNING
``[slots].default_images``                      keys renamed as for ``binary``; entries
                                                naming no runner, or a retired hal0
                                                image, dropped
profile ``runner``                              renamed as for ``binary``; unknown dropped
profile ``image`` naming a retired image        removed
model ``preferred_runner`` (registry column)    renamed as for ``binary``; unknown → NULL
``HAL0_TOOLBOX_IMAGE_<KEY>`` in ``api.env``     ``HAL0_RUNNER_IMAGE_<NEWKEY>``, or dropped
                                                when the key or the image is retired
==============================================  ==========================================

The runner-image catalogue table is recreated by SQL migration
``011_runner_image_catalogue_v2`` and refilled by the next sync.

Contract:

* **Runs before config validation.** It reads and writes raw TOML / env / SQL,
  never the pydantic models: a removed runner key in ``[slots].default_images``
  would otherwise fail config load before the conversion could run. It runs
  first in :func:`hal0.updater.updater.run_post_activation_migrations` (the
  ``hal0 update`` post-swap step and ``install.sh`` re-runs) and first thing
  in :func:`hal0.api.create_app`, before it reads ``hal0.toml``.
* **Backup first, fail loudly.** Before the first write, every file it will
  change (and the registry DB when a column changes) goes into
  ``/var/lib/hal0/backups/runners-<ts>.tar.gz``. No backup, no write. Any
  failure while applying restores every file already written, rolls back the
  DB transaction and raises :class:`RunnerMigrationError`.
* **Idempotent.** Every rule is "old shape? convert : leave", so a converted
  install plans nothing and writes nothing.
* **Dry run.** ``dry_run=True`` plans and reports without touching disk
  (``hal0 runner-images migrate`` without ``--apply``).
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import tomli_w

from hal0.errors import Hal0Error

log = structlog.get_logger(__name__)

#: Runner keys the fresh system removed or renamed, and what they become.
#: Every GPU llama.cpp key of the old system maps to ``standard``.
RETIRED_RUNNER_KEYS: dict[str, str] = {
    "rocmfpx": "standard",
    "vulkanfpx": "standard",
    "strix": "standard",
    "promptforge": "standard",
    "upstream": "standard",
    "vulkan": "standard",
    "rocm": "standard",
}

#: Image repository names hal0 used to ship and no longer does. A pin to any
#: of them is debris from the old runner system and is removed. Matched on the
#: repository name, whatever registry or owner prefix it carries.
_RETIRED_IMAGE_NAMES = frozenset(
    {
        "hal0-combined",
        "hal0-combined-upstream",
        "hal0-rocmfpx",
        "hal0-strix-vulkan",
        "hal0-promptforge",
        "hal0-comfyui",
        "amd-strix-halo-toolboxes",
    }
)
_RETIRED_IMAGE_PREFIXES = ("hal0-toolbox-",)
#: Image owners whose images hal0 used to ship (the kyuz0 Strix Halo builds).
_RETIRED_IMAGE_OWNERS = frozenset({"kyuz0"})

_ENV_OLD_RE = re.compile(
    r"^(?P<export>export\s+)?HAL0_TOOLBOX_IMAGE_(?P<key>[A-Z0-9_]+)=(?P<value>.*)$"
)


class RunnerMigrationError(Hal0Error):
    """The runner-system conversion failed; every write it made was restored."""

    code = "config.runner_migration_failed"
    status = 500


@dataclass
class RunnerMigrationReport:
    """What the conversion did (or, on a dry run, would do)."""

    dry_run: bool
    actions: list[str] = field(default_factory=list)
    #: Loud lines an operator must see: unassigned models, dropped pins.
    warnings: list[str] = field(default_factory=list)
    #: Operator images left in place (not hal0's to manage). Reported, but
    #: not a change: they do not make the report "changed".
    kept: list[str] = field(default_factory=list)
    backup: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.actions or self.warnings)

    def lines(self) -> list[str]:
        verb = "would" if self.dry_run else "did"
        out = [f"runner-system migration ({'dry run' if self.dry_run else 'applied'}):"]
        if not self.changed:
            out.append("  nothing to convert")
            out += [f"  kept: {k}" for k in self.kept]
            return out
        out += [f"  {verb}: {a}" for a in self.actions]
        out += [f"  WARNING: {w}" for w in self.warnings]
        out += [f"  kept: {k}" for k in self.kept]
        if self.backup:
            out.append(f"  backup: {self.backup}")
        return out


# ── classification (pure) ─────────────────────────────────────────────────────


def _known_runner_keys() -> frozenset[str]:
    from hal0.runners import RUNNER_IMAGES

    return frozenset(RUNNER_IMAGES)


def map_runner_key(key: str, known: frozenset[str]) -> str | None:
    """The fresh-system key for ``key``; ``None`` when it names no runner."""
    key = key.strip()
    if key in known:
        return key
    return RETIRED_RUNNER_KEYS.get(key)


def split_image_ref(ref: str) -> tuple[str, str | None, str | None]:
    """``repo[:tag][@digest]`` → ``(repo, tag, digest)``."""
    body, _, digest = ref.strip().partition("@")
    head, sep, tail = body.rpartition(":")
    if sep and "/" not in tail:
        return head, tail, digest or None
    return body, None, digest or None


def image_is_retired(ref: str) -> bool:
    """Does ``ref`` name an image the old runner system shipped?

    The retired packages are ``hal0-combined`` (and ``-upstream``),
    ``hal0-rocmfpx``, ``hal0-strix-vulkan``, ``hal0-promptforge``,
    ``hal0-toolbox-*``, ``hal0-comfyui``, ``amd-strix-halo-toolboxes`` and
    the kyuz0 builds, under any registry. Everything else, including an
    operator's own build, is not.
    """
    repo, _tag, _digest = split_image_ref(ref)
    parts = [p for p in repo.lower().split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    if name in _RETIRED_IMAGE_NAMES or name.startswith(_RETIRED_IMAGE_PREFIXES):
        return True
    return any(owner in _RETIRED_IMAGE_OWNERS for owner in parts[:-1])


def image_is_hal0_runner(ref: str) -> bool:
    """Does ``ref`` name one of the current runner packages?"""
    repo, _tag, _digest = split_image_ref(ref)
    name = repo.lower().rsplit("/", 1)[-1]
    return name.startswith("hal0-runner-") or repo.lower().endswith("ggml-org/llama.cpp")


# ── plans (pure dict transforms) ──────────────────────────────────────────────


def _as_table(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def plan_slot(
    raw: dict[str, Any],
    *,
    name: str,
    known: frozenset[str],
    fork_quant_of: Callable[[str], str | None],
    actions: list[str],
    warnings: list[str],
    kept: list[str] | None = None,
) -> dict[str, Any] | None:
    """The converted slot TOML, or ``None`` when nothing changes."""
    out = _deep_copy(raw)
    changed = False
    tables: list[dict[str, Any]] = [out]
    nested = _as_table(out.get("slot"))
    if nested is not None:
        tables.append(nested)

    for table in tables:
        binary = table.get("binary")
        if isinstance(binary, str) and binary.strip():
            new = map_runner_key(binary, known)
            if new is None:
                del table["binary"]
                warnings.append(
                    f"slot {name}: runtime {binary!r} no longer exists; cleared so the "
                    "device picks the runner"
                )
                changed = True
            elif new != binary:
                table["binary"] = new
                actions.append(f"slot {name}: runtime {binary!r} -> {new!r}")
                changed = True
        for key in ("image_pin", "image"):
            ref = table.get(key)
            if not (isinstance(ref, str) and ref.strip()):
                continue
            if image_is_retired(ref):
                del table[key]
                warnings.append(
                    f"slot {name}: dropped image pin {ref!r} (a retired hal0 image); "
                    "the slot now runs its runner's pinned image"
                )
                changed = True
            elif kept is not None and not image_is_hal0_runner(ref):
                kept.append(f"slot {name}: custom image pin {ref!r} (not managed by hal0)")

    model = _as_table(out.get("model"))
    model_id = model.get("default") if model is not None else None
    if isinstance(model_id, str) and model_id.strip():
        quant = fork_quant_of(model_id)
        if quant is not None:
            assert model is not None
            model["default"] = ""
            warnings.append(
                f"slot {name}: model {model_id!r} is a {quant} build no runner can load; "
                "unassigned it (the slot keeps its settings). Pick a plain GGUF build "
                "for this slot."
            )
            changed = True
    return out if changed else None


def plan_hal0_toml(
    raw: dict[str, Any],
    *,
    known: frozenset[str],
    actions: list[str],
    warnings: list[str],
    kept: list[str] | None = None,
) -> dict[str, Any] | None:
    """The converted ``hal0.toml``, or ``None`` when nothing changes."""
    slots = _as_table(raw.get("slots"))
    images = _as_table(slots.get("default_images")) if slots is not None else None
    if not images:
        return None
    converted: dict[str, Any] = {}
    changed = False
    for key, ref in images.items():
        new = map_runner_key(str(key), known)
        if new is None:
            warnings.append(f"[slots].default_images: dropped {key!r} (no such runner)")
            changed = True
            continue
        if not isinstance(ref, str) or not ref.strip() or image_is_retired(ref):
            warnings.append(
                f"[slots].default_images: dropped {key!r} = {ref!r} (a retired hal0 image)"
            )
            changed = True
            continue
        if kept is not None and not image_is_hal0_runner(ref):
            kept.append(f"[slots].default_images: custom image {key!r} = {ref!r}")
        if new in converted:
            warnings.append(
                f"[slots].default_images: dropped {key!r} (another entry already sets {new!r})"
            )
            changed = True
            continue
        if new != key:
            actions.append(f"[slots].default_images: renamed {key!r} -> {new!r}")
            changed = True
        converted[new] = ref
    if not changed:
        return None
    out = _deep_copy(raw)
    out["slots"]["default_images"] = converted
    return out


def plan_profiles(
    raw: dict[str, Any],
    *,
    known: frozenset[str],
    actions: list[str],
    warnings: list[str],
) -> dict[str, Any] | None:
    """The converted ``profiles.toml``, or ``None`` when nothing changes."""
    profiles = _as_table(raw.get("profile"))
    if not profiles:
        return None
    out = _deep_copy(raw)
    changed = False
    for name, prof in out["profile"].items():
        if not isinstance(prof, dict):
            continue
        runner = prof.get("runner")
        if isinstance(runner, str) and runner.strip():
            new = map_runner_key(runner, known)
            if new is None:
                del prof["runner"]
                warnings.append(f"profile {name}: runtime {runner!r} no longer exists; cleared")
                changed = True
            elif new != runner:
                prof["runner"] = new
                actions.append(f"profile {name}: runtime {runner!r} -> {new!r}")
                changed = True
        ref = prof.get("image")
        if isinstance(ref, str) and ref.strip() and image_is_retired(ref):
            del prof["image"]
            actions.append(f"profile {name}: dropped image {ref!r}")
            changed = True
    return out if changed else None


def plan_env(
    text: str,
    *,
    known: frozenset[str],
    actions: list[str],
    warnings: list[str],
) -> str | None:
    """``api.env`` with ``HAL0_TOOLBOX_IMAGE_*`` converted, or ``None``."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    changed = False
    for line in lines:
        match = _ENV_OLD_RE.match(line.rstrip("\r\n"))
        if match is None:
            out.append(line)
            continue
        changed = True
        old_key = match.group("key")
        value = match.group("value").strip().strip("'\"")
        new = map_runner_key(old_key.lower(), known)
        if new is None:
            warnings.append(f"api.env: dropped HAL0_TOOLBOX_IMAGE_{old_key} (no such runner)")
            continue
        if not value or image_is_retired(value):
            warnings.append(
                f"api.env: dropped HAL0_TOOLBOX_IMAGE_{old_key}={value} (a retired hal0 image)"
            )
            continue
        newline = f"{match.group('export') or ''}HAL0_RUNNER_IMAGE_{new.upper()}={value}\n"
        out.append(newline)
        actions.append(f"api.env: HAL0_TOOLBOX_IMAGE_{old_key} -> HAL0_RUNNER_IMAGE_{new.upper()}")
    return "".join(out) if changed else None


def _deep_copy(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = _deep_copy(v)
        elif isinstance(v, list):
            out[k] = [_deep_copy(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


# ── registry DB (read-only lookups + one transaction) ─────────────────────────


def _db_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _fork_quant_lookup(db_path: Path) -> Callable[[str], str | None]:
    """``model id -> fork-only quant label | None``, read-only against the DB."""
    from hal0.registry.detect import unsupported_quant

    rows: dict[str, tuple[str, str, str]] = {}
    if db_path.is_file():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cols = _db_columns(conn, "model")
                if {"id", "path"} <= cols:
                    quant_col = "quant" if "quant" in cols else "NULL"
                    hf_col = "hf_filename" if "hf_filename" in cols else "NULL"
                    for mid, path, quant, hf in conn.execute(
                        f"SELECT id, path, {quant_col}, {hf_col} FROM model"
                    ):
                        rows[str(mid)] = (str(path or ""), str(quant or ""), str(hf or ""))
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RunnerMigrationError(
                f"cannot read the model registry at {db_path}: {exc}",
                details={"path": str(db_path)},
            ) from exc

    def lookup(model_id: str) -> str | None:
        path, quant, hf = rows.get(model_id, ("", "", ""))
        return unsupported_quant(quant, Path(path).name, hf, model_id)

    return lookup


def _plan_preferred_runner(
    db_path: Path, known: frozenset[str], actions: list[str], warnings: list[str]
) -> list[tuple[str, str | None]]:
    """``[(model_id, new_value)]`` for leftover ``preferred_runner`` values."""
    if not db_path.is_file():
        return []
    changes: list[tuple[str, str | None]] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            if "preferred_runner" not in _db_columns(conn, "model"):
                return []
            for mid, value in conn.execute(
                "SELECT id, preferred_runner FROM model WHERE preferred_runner IS NOT NULL "
                "AND preferred_runner != ''"
            ):
                new = map_runner_key(str(value), known)
                if new == value:
                    continue
                changes.append((str(mid), new))
                if new is None:
                    warnings.append(
                        f"model {mid}: cleared preferred runner {value!r} (no such runner)"
                    )
                else:
                    actions.append(f"model {mid}: preferred runner {value!r} -> {new!r}")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RunnerMigrationError(
            f"cannot read the model registry at {db_path}: {exc}",
            details={"path": str(db_path)},
        ) from exc
    return changes


# ── IO ────────────────────────────────────────────────────────────────────────


@dataclass
class _FileWrite:
    path: Path
    original: bytes
    new: bytes


def _read_toml(path: Path) -> tuple[bytes, dict[str, Any]] | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return data, tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise RunnerMigrationError(
            f"cannot parse {path}: {exc}; fix or remove it, then re-run "
            "`hal0 runner-images migrate --apply`",
            details={"path": str(path)},
        ) from exc


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` atomically, keeping its mode and (when allowed) owner."""
    st = path.stat()
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, st.st_mode & 0o7777)
        with contextlib.suppress(PermissionError, OSError):
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _write_backup(writes: Iterable[_FileWrite], db_path: Path | None) -> Path:
    from hal0.config import paths

    backup_dir = paths.var_lib() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    target = backup_dir / f"runners-{stamp}.tar.gz"
    n = 1
    while target.exists():  # never overwrite an earlier restore point
        target = backup_dir / f"runners-{stamp}-{n}.tar.gz"
        n += 1
    with tarfile.open(target, "w:gz") as tar:
        for w in writes:
            info = tarfile.TarInfo(name=str(w.path).lstrip("/"))
            info.size = len(w.original)
            info.mode = w.path.stat().st_mode & 0o7777
            tar.addfile(info, io.BytesIO(w.original))
        if db_path is not None and db_path.is_file():
            with tempfile.TemporaryDirectory() as tmpdir:
                snap = Path(tmpdir) / db_path.name
                src = sqlite3.connect(str(db_path))
                dst = sqlite3.connect(str(snap))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
                tar.add(str(snap), arcname=str(db_path).lstrip("/"))
    os.chmod(target, 0o600)
    return target


# ── entry point ───────────────────────────────────────────────────────────────


def migrate_runner_system(
    *,
    dry_run: bool = False,
    job_id: str | None = None,
) -> RunnerMigrationReport:
    """Convert this install onto the fresh runner system. See the module docstring.

    Raises :class:`RunnerMigrationError` after restoring every write on any
    failure.
    """
    from hal0.config import paths
    from hal0.config.loader import hal0_config_file_lock
    from hal0.slot_config import slot_write_lock

    with contextlib.ExitStack() as locks:
        # Serialize against the API's own config writers (a live hal0-api may be
        # serving a settings PUT while `hal0 update` runs this). Only when the
        # config tree exists: a fresh box has nothing to convert or lock.
        if paths.etc().is_dir():
            locks.enter_context(hal0_config_file_lock(paths.hal0_toml()))
            if paths.slots_config_dir().is_dir():
                locks.enter_context(slot_write_lock())
        return _migrate(dry_run=dry_run, job_id=job_id)


def _migrate(*, dry_run: bool, job_id: str | None) -> RunnerMigrationReport:
    from hal0.config import paths

    known = _known_runner_keys()
    report = RunnerMigrationReport(dry_run=dry_run)
    actions, warnings = report.actions, report.warnings

    hal0_toml = paths.hal0_toml()
    slots_dir = paths.slots_config_dir()
    profiles_toml = paths.profiles_toml()
    api_env = paths.api_env()
    db_path = paths.db_path()

    writes: list[_FileWrite] = []
    fork_quant_of = _fork_quant_lookup(db_path)

    loaded = _read_toml(hal0_toml)
    if loaded is not None:
        new = plan_hal0_toml(
            loaded[1], known=known, actions=actions, warnings=warnings, kept=report.kept
        )
        if new is not None:
            writes.append(_FileWrite(hal0_toml, loaded[0], tomli_w.dumps(new).encode()))

    if slots_dir.is_dir():
        for slot_path in sorted(slots_dir.glob("*.toml")):
            loaded = _read_toml(slot_path)
            if loaded is None:
                continue
            new = plan_slot(
                loaded[1],
                name=slot_path.stem,
                known=known,
                fork_quant_of=fork_quant_of,
                actions=actions,
                warnings=warnings,
                kept=report.kept,
            )
            if new is not None:
                writes.append(_FileWrite(slot_path, loaded[0], tomli_w.dumps(new).encode()))

    loaded = _read_toml(profiles_toml)
    if loaded is not None:
        new = plan_profiles(loaded[1], known=known, actions=actions, warnings=warnings)
        if new is not None:
            writes.append(_FileWrite(profiles_toml, loaded[0], tomli_w.dumps(new).encode()))

    if api_env.is_file():
        try:
            original = api_env.read_bytes()
        except OSError as exc:
            # The old env names are simply no longer read, so an api.env this
            # process cannot open is reported, never a reason to block the
            # config conversion.
            warnings.append(f"api.env: not readable here ({exc}); HAL0_TOOLBOX_IMAGE_* not checked")
        else:
            new_text = plan_env(
                original.decode("utf-8"),
                known=known,
                actions=actions,
                warnings=warnings,
            )
            if new_text is not None:
                writes.append(_FileWrite(api_env, original, new_text.encode("utf-8")))

    runner_changes = _plan_preferred_runner(db_path, known, actions, warnings)

    if dry_run or not (writes or runner_changes):
        if report.changed:
            for line in report.lines():
                log.info("migrate.runner_system.plan", job_id=job_id, line=line)
        return report

    try:
        backup = _write_backup(writes, db_path if runner_changes else None)
    except Exception as exc:
        raise RunnerMigrationError(
            f"could not write the runner-migration backup ({exc}); nothing was changed",
            details={"error": str(exc)},
        ) from exc
    report.backup = str(backup)

    done: list[_FileWrite] = []
    try:
        for w in writes:
            _atomic_write(w.path, w.new)
            done.append(w)
        if runner_changes:
            conn = sqlite3.connect(str(db_path))
            try:
                with conn:
                    conn.executemany(
                        "UPDATE model SET preferred_runner = ? WHERE id = ?",
                        [(new, mid) for mid, new in runner_changes],
                    )
            finally:
                conn.close()
    except Exception as exc:
        for w in reversed(done):
            with contextlib.suppress(Exception):
                _atomic_write(w.path, w.original)
        log.error(
            "migrate.runner_system.failed_restored",
            job_id=job_id,
            error=str(exc),
            backup=str(backup),
        )
        raise RunnerMigrationError(
            f"runner-system migration failed and was rolled back: {exc} (backup: {backup})",
            details={"error": str(exc), "backup": str(backup)},
        ) from exc

    for line in report.lines():
        log.warning("migrate.runner_system.applied", job_id=job_id, line=line)
    return report


def restore_backup(archive: Path | str, *, root: Path = Path("/")) -> list[str]:
    """Extract a ``runners-<ts>.tar.gz`` backup over ``root``. Returns the paths.

    The operator's undo for an applied conversion (``hal0 runner-images migrate
    --restore <archive>``). Members are written back to their original
    absolute paths; nothing outside the archive is touched.
    """
    archive = Path(archive)
    restored: list[str] = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if (
                not member.isfile()
                or member.name.startswith("/")
                or ".." in Path(member.name).parts
            ):
                continue
            dest = root / member.name
            src = tar.extractfile(member)
            if src is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(dest, member.mode & 0o7777)
            restored.append(str(dest))
    return restored


__all__ = [
    "RETIRED_RUNNER_KEYS",
    "RunnerMigrationError",
    "RunnerMigrationReport",
    "image_is_hal0_runner",
    "image_is_retired",
    "map_runner_key",
    "migrate_runner_system",
    "plan_env",
    "plan_hal0_toml",
    "plan_profiles",
    "plan_slot",
    "restore_backup",
    "split_image_ref",
]
