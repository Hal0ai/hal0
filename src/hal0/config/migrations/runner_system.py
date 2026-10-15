"""One-shot conversion of an install onto the fresh runner system (ADR-0028).

The runner registry was rebuilt from scratch: ``standard`` replaced the
``rocmfpx`` default (and its ``vulkanfpx`` alias), the optional ``strix``,
``promptforge`` and ``upstream`` runners were removed, every image moved to
``ghcr.io/hal0ai/hal0-runner-<key>`` pinned in ``manifest.json``, and
ROCmFPX/ROCmFP4/IU4/ActiveFPX GGUFs are no longer loadable. This module moves
an existing install onto that system, once, generically:

==============================================  ==========================================
on-disk state                                   becomes
==============================================  ==========================================
slot ``binary`` = a retired DEFAULT key          ``binary = "standard"``
(rocmfpx, vulkanfpx, vulkan, rocm)
slot ``binary`` = a retired OPTIONAL runner      cleared, loudly (the device picks the
(strix, promptforge, upstream) or no runner     runner)
slot ``image_pin`` / ``image`` (top level or    removed when it names an image hal0
``[slot]``)                                     shipped and retired; any other image (an
                                                operator's own build) is kept and reported
slot whose ``[model].default`` is a fork-only   model unassigned; the rest of the slot
quant (ROCmFPX family, IU4, ActiveFPX)          config is kept; reported as a WARNING
slot ``profile`` naming a removed profile       its replacement (chadrock-moe → moe,
(chadrock-dense, chadrock-moe, promptforge)     chadrock-dense / promptforge → dense)
``[slots].default_images``                      ``rocmfpx`` / ``vulkanfpx`` renamed to
                                                ``standard``; ``vulkan`` / ``rocm``
                                                (inert before), retired optional
                                                runners, unknown keys and retired
                                                images dropped, loudly
profile ``runner``                              as for slot ``binary``
profile ``image`` naming a retired image        removed
removed profile definitions in profiles.toml    dropped, loudly
model ``preferred_runner`` (registry column)    as for slot ``binary``; gone → NULL
``HAL0_TOOLBOX_IMAGE_<KEY>`` in ``api.env``     ``HAL0_RUNNER_IMAGE_<NEWKEY>`` (value
                                                kept verbatim) for a current key or
                                                ``rocmfpx`` / ``vulkanfpx``, deduped
                                                against existing lines; dropped, loudly,
                                                for ``vulkan`` / ``rocm``, a retired
                                                optional runner, an unknown key or a
                                                retired image
==============================================  ==========================================

The runner-image catalogue table is recreated by SQL migration
``011_runner_image_catalogue_v2`` and refilled by the next sync.

Contract:

* **Runs before config validation, never at import.** It reads and writes raw
  TOML / env / SQL, never the pydantic models: a removed runner key in
  ``[slots].default_images`` would otherwise fail config load. It runs first
  in :func:`hal0.updater.updater.run_post_activation_migrations` (the ``hal0
  update`` post-swap step, ``install.sh`` re-runs, and the API's boot-time
  safety net) and in ``hal0 serve`` before the API module is imported.
  Importing :mod:`hal0.api` or running a read-only CLI command never runs it.
* **Each file on its own.** A slot or profile file that does not parse is
  skipped with a WARNING and every other file is still converted.
  ``hal0.toml`` is written first and kept when a later write fails, because it
  is the file that stops ``hal0-api`` from starting; every later write is
  rolled back on failure.
* **Backup first, fail loudly.** Before the first write, the original of every
  file it changes, plus the ``(model id, old preferred_runner)`` pairs it
  changes, goes into ``/var/lib/hal0/backups/runners-<ts>.tar.gz`` (mode 0600,
  directory 0700). No backup, no write. The registry DB itself is never
  copied.
* **Idempotent.** Every rule is "old shape? convert : leave", so a converted
  install plans nothing and writes nothing.
* **Dry run.** ``dry_run=True`` plans and reports without touching disk
  (``hal0 runner-images migrate`` without ``--apply``).
* **Restore is for downgrades.** :func:`restore_backup` puts the owned files
  and the ``preferred_runner`` values back, then leaves a marker so the
  API's own boot-time conversion does not immediately redo the conversion.
  The marker records the hal0 version that wrote it and holds only while that
  version runs. The next ``hal0 update`` or an explicit ``--apply`` converts
  again and clears it. A failed rollback names every file it could not put
  back and the backup to restore from.
"""

from __future__ import annotations

import contextlib
import errno
import grp
import io
import json
import os
import pwd
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import tarfile
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

#: The old system's DEFAULT llama.cpp runner keys. An override or a pin on one
#: of them was an override of the default runner, which is now ``standard``.
RETIRED_DEFAULT_KEYS: dict[str, str] = {
    "rocmfpx": "standard",
    "vulkanfpx": "standard",
    "vulkan": "standard",
    "rocm": "standard",
}

#: Retired OPTIONAL runners. They map to nothing: an image override for one of
#: them named a special build, and turning it into ``standard``'s override
#: would make every default slot run it.
RETIRED_OPTIONAL_KEYS = frozenset({"strix", "promptforge", "upstream"})

#: The keys whose IMAGE OVERRIDE (``[slots].default_images``,
#: ``HAL0_TOOLBOX_IMAGE_<KEY>``) carries over to ``standard``: the default
#: runner of the release this upgrades from. ``vulkan`` and ``rocm`` overrides
#: were already inert there, so a leftover custom value under them is dropped
#: rather than promoted to every default slot's image.
_OVERRIDE_DEFAULT_KEYS = frozenset({"rocmfpx", "vulkanfpx"})

#: Profiles this release removed (fork-specific flag sets for removed
#: runners), and the generic profile a slot that named one moves to.
RETIRED_PROFILES: dict[str, str] = {
    "chadrock-dense": "dense",
    "chadrock-moe": "moe",
    "promptforge": "dense",
}

#: The exact repositories hal0 shipped and retired (normalized, see
#: :func:`normalize_repo`). A pin to one of them is debris from the old runner
#: system and is removed. An image under any other name or owner is the
#: operator's and is kept.
_RETIRED_REPOS = frozenset(
    {
        "ghcr.io/hal0ai/hal0-combined",
        "ghcr.io/hal0ai/hal0-combined-upstream",
        "ghcr.io/hal0ai/hal0-rocmfpx",
        "localhost/hal0-rocmfpx",
        "ghcr.io/hal0ai/hal0-strix-vulkan",
        "ghcr.io/hal0ai/hal0-promptforge",
        "ghcr.io/hal0ai/hal0-comfyui",
        "ghcr.io/hal0ai/amd-strix-halo-toolboxes",
        "docker.io/kyuz0/amd-strix-halo-toolboxes",
        "docker.io/kyuz0/amd-strix-halo-comfyui",
    }
)
_RETIRED_REPO_PREFIXES = ("ghcr.io/hal0ai/hal0-toolbox-",)
_CUDA_REPO = "ghcr.io/ggml-org/llama.cpp"
#: Floating ggml-org tags the old ``cuda`` default used. Without a digest
#: they name whatever was pushed last, never a fixed image.
_CUDA_FLOATING_TAGS = frozenset({None, "server-cuda", "latest"})

_ENV_OLD_RE = re.compile(
    r"^(?P<export>export\s+)?HAL0_TOOLBOX_IMAGE_(?P<key>[A-Z0-9_]+)=(?P<value>.*)$"
)
_ENV_NEW_RE = re.compile(r"^(?:export\s+)?HAL0_RUNNER_IMAGE_(?P<key>[A-Z0-9_]+)=(?P<value>.*)$")

#: Archive member names of a backup, mapped by :func:`_owned_paths`.
_BACKUP_TOML = "hal0.toml"
_BACKUP_PROFILES = "profiles.toml"
_BACKUP_ENV = "api.env"
_BACKUP_RUNNERS = "preferred_runner.json"
_BACKUP_SLOT_RE = re.compile(r"^slots/(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*\.toml)$")
_BACKUP_MEMBER_MAX = 4 * 1024 * 1024

#: Left by :func:`restore_backup`; the boot-time conversion honours it.
RESTORE_MARKER_NAME = "runner-migration.restored"


class RunnerMigrationError(Hal0Error):
    """The runner-system conversion or a restore failed."""

    code = "config.runner_migration_failed"
    status = 500


@dataclass
class RunnerMigrationReport:
    """What the conversion did (or, on a dry run, would do)."""

    dry_run: bool
    actions: list[str] = field(default_factory=list)
    #: Loud lines an operator must see: unassigned models, dropped pins,
    #: files that could not be read.
    warnings: list[str] = field(default_factory=list)
    #: Operator images left in place (not hal0's to manage). Reported, but
    #: not a change: they do not make the report "changed".
    kept: list[str] = field(default_factory=list)
    #: One ``{"slot", "model", "quant", "remedy"}`` per slot whose model was
    #: unassigned (#2694): the same event as its WARNING line, structured so
    #: ``hal0 update`` can print it instead of leaving it in the journal.
    unassigned: list[dict[str, str]] = field(default_factory=list)
    backup: str | None = None
    #: True when a restore marker made the boot-time conversion stand down.
    skipped_for_restore: bool = False

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
    """The fresh-system key for ``key``; ``None`` when it maps to no runner.

    Only current keys and the old DEFAULT keys map; a retired optional runner
    maps to ``None`` like an unknown key.
    """
    key = key.strip()
    if key in known:
        return key
    return RETIRED_DEFAULT_KEYS.get(key)


def map_override_key(key: str, known: frozenset[str]) -> str | None:
    """The fresh-system key an image OVERRIDE under ``key`` carries over to.

    Stricter than :func:`map_runner_key`: only the previous default runner's
    keys (``rocmfpx``, ``vulkanfpx``) carry an override to ``standard``.
    """
    key = key.strip()
    if key in known:
        return key
    return "standard" if key in _OVERRIDE_DEFAULT_KEYS else None


def _override_precedence(old: str, new: str) -> int:
    """Which override wins when several converge on one key (lower wins).

    The key's own name first, then ``rocmfpx``, then its ``vulkanfpx`` alias:
    the old schema let a canonical-origin override beat its alias whatever
    the TOML order, and the conversion keeps that.
    """
    old = old.strip()
    if old == new:
        return 0
    if old == "rocmfpx":
        return 1
    return 2


def _gone(key: str) -> str:
    """Why ``key`` maps to nothing, for a warning."""
    key = key.strip()
    if key in RETIRED_OPTIONAL_KEYS:
        return f"the optional {key!r} runner was removed"
    if key in RETIRED_DEFAULT_KEYS:
        return f"a {key!r} override was already inert before this release"
    return f"{key!r} names no runner"


def split_image_ref(ref: str) -> tuple[str, str | None, str | None]:
    """``repo[:tag][@digest]`` → ``(repo, tag, digest)``."""
    body, _, digest = ref.strip().partition("@")
    head, sep, tail = body.rpartition(":")
    if sep and "/" not in tail:
        return head, tail, digest or None
    return body, None, digest or None


def normalize_repo(repo: str) -> str:
    """``repo`` with the registry spelled out, lowercased.

    ``kyuz0/x`` and ``index.docker.io/kyuz0/x`` are ``docker.io/kyuz0/x``; a
    single component is ``docker.io/library/<name>``.
    """
    parts = [p for p in repo.strip().lower().split("/") if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"docker.io/library/{parts[0]}"
    first = parts[0]
    if first in {"index.docker.io", "registry-1.docker.io"}:
        parts[0] = "docker.io"
    elif "." not in first and ":" not in first and first != "localhost":
        parts.insert(0, "docker.io")
    return "/".join(parts)


def image_is_retired(ref: str) -> bool:
    """Does ``ref`` name an image the old runner system shipped?

    True for the exact repositories hal0 published or built and has retired
    (``hal0-combined``, ``-upstream``, ``hal0-rocmfpx``, ``hal0-strix-vulkan``,
    ``hal0-promptforge``, ``hal0-comfyui``, ``hal0-toolbox-*``, hal0's
    ``amd-strix-halo-toolboxes`` mirror, and the two kyuz0 Strix Halo images),
    and for the floating ggml-org ``server-cuda`` tag the old ``cuda`` default
    used. Everything else, including an operator's own build under any of
    those names at another owner, is not.
    """
    repo, tag, digest = split_image_ref(ref)
    norm = normalize_repo(repo)
    if not norm:
        return False
    if norm in _RETIRED_REPOS or norm.startswith(_RETIRED_REPO_PREFIXES):
        return True
    return norm == _CUDA_REPO and digest is None and tag in _CUDA_FLOATING_TAGS


def image_is_hal0_runner(ref: str) -> bool:
    """Does ``ref`` name one of the current runner packages?"""
    repo, _tag, _digest = split_image_ref(ref)
    norm = normalize_repo(repo)
    return norm.startswith("ghcr.io/hal0ai/hal0-runner-") or norm == _CUDA_REPO


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
    unassigned: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """The converted slot TOML, or ``None`` when nothing changes.

    A slot whose model is unassigned also gets one entry in ``unassigned``
    (when given): ``{"slot", "model", "quant", "remedy"}``.
    """
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
                    f"slot {name}: runtime cleared ({_gone(binary)}); the device picks the runner"
                )
                changed = True
            elif new != binary:
                table["binary"] = new
                actions.append(f"slot {name}: runtime {binary!r} -> {new!r}")
                changed = True
        profile = table.get("profile")
        if isinstance(profile, str) and profile.strip() in RETIRED_PROFILES:
            replacement = RETIRED_PROFILES[profile.strip()]
            table["profile"] = replacement
            actions.append(f"slot {name}: profile {profile!r} (removed) -> {replacement!r}")
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
            from hal0.slot_config import unassigned_model_remedy

            model["default"] = ""
            remedy = unassigned_model_remedy(name)
            warnings.append(
                f"slot {name}: model {model_id!r} is a {quant} build no runner can load; "
                f"unassigned it (the slot keeps its settings). Fix: {remedy}."
            )
            if unassigned is not None:
                unassigned.append(
                    {"slot": name, "model": model_id, "quant": quant, "remedy": remedy}
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
    """The converted ``hal0.toml``, or ``None`` when nothing changes.

    A current key wins over a renamed one: ``standard`` set explicitly beats a
    leftover ``rocmfpx`` entry.
    """
    slots = _as_table(raw.get("slots"))
    images = _as_table(slots.get("default_images")) if slots is not None else None
    if not images:
        return None
    converted: dict[str, Any] = {}
    changed = False

    def _rank(item: tuple[Any, Any]) -> int:
        new_key = map_override_key(str(item[0]), known)
        return _override_precedence(str(item[0]), new_key) if new_key else 3

    ordered = sorted(images.items(), key=_rank)
    for key, ref in ordered:
        new = map_override_key(str(key), known)
        if new is None:
            warnings.append(
                f"[slots].default_images: dropped {key!r} = {ref!r} ({_gone(str(key))})"
            )
            changed = True
            continue
        if not isinstance(ref, str) or not ref.strip() or image_is_retired(ref):
            warnings.append(
                f"[slots].default_images: dropped {key!r} = {ref!r} (a retired hal0 image)"
            )
            changed = True
            continue
        if new in converted:
            warnings.append(
                f"[slots].default_images: dropped {key!r} = {ref!r} "
                f"(another entry already sets {new!r})"
            )
            changed = True
            continue
        if kept is not None and not image_is_hal0_runner(ref):
            kept.append(f"[slots].default_images: custom image {new!r} = {ref!r}")
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
    for name in [n for n in out["profile"] if n in RETIRED_PROFILES]:
        del out["profile"][name]
        warnings.append(
            f"profile {name}: removed (hal0 no longer ships it); slots that named it "
            f"move to {RETIRED_PROFILES[name]!r}"
        )
        changed = True
    for name, prof in out["profile"].items():
        if not isinstance(prof, dict):
            continue
        runner = prof.get("runner")
        if isinstance(runner, str) and runner.strip():
            new = map_runner_key(runner, known)
            if new is None:
                del prof["runner"]
                warnings.append(f"profile {name}: runtime cleared ({_gone(runner)})")
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


def _env_value(raw: str) -> str:
    return raw.strip().strip("'\"")


def plan_env(
    text: str,
    *,
    known: frozenset[str],
    actions: list[str],
    warnings: list[str],
) -> str | None:
    """``api.env`` with ``HAL0_TOOLBOX_IMAGE_*`` converted, or ``None``.

    A converted line never duplicates a ``HAL0_RUNNER_IMAGE_<KEY>`` the file
    already sets, nor another converted line. Among old lines converging on
    one key, the precedence the old schema applied wins regardless of line
    order (:func:`_override_precedence`: the current key, then ``ROCMFPX``,
    then its ``VULKANFPX`` alias); an identical duplicate is dropped quietly,
    a conflicting one loudly.
    """
    lines = text.splitlines(keepends=True)
    present: dict[str, str] = {}
    for line in lines:
        match = _ENV_NEW_RE.match(line.rstrip("\r\n"))
        if match is not None:
            present.setdefault(match.group("key"), _env_value(match.group("value")))

    preexisting = frozenset(present)
    # The old line each new key keeps, chosen by precedence, not file order.
    winners: dict[str, tuple[int, int]] = {}
    for index, line in enumerate(lines):
        match = _ENV_OLD_RE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        old = match.group("key").lower()
        new = map_override_key(old, known)
        value = _env_value(match.group("value"))
        if new is None or not value or image_is_retired(value):
            continue
        rank = (_override_precedence(old, new), index)
        if new.upper() not in winners or rank < winners[new.upper()]:
            winners[new.upper()] = rank
    for key, (_rank, index) in winners.items():
        if key not in present:
            match = _ENV_OLD_RE.match(lines[index].rstrip("\r\n"))
            assert match is not None
            present[key] = _env_value(match.group("value"))
    claimed = {index for _rank, index in winners.values()}

    out: list[str] = []
    written: set[str] = set()
    changed = False
    for index, line in enumerate(lines):
        match = _ENV_OLD_RE.match(line.rstrip("\r\n"))
        if match is None:
            out.append(line)
            continue
        changed = True
        old_key = match.group("key")
        old_name = f"HAL0_TOOLBOX_IMAGE_{old_key}"
        raw_value = match.group("value").strip()
        value = _env_value(raw_value)
        new = map_override_key(old_key.lower(), known)
        if new is None:
            warnings.append(f"api.env: dropped {old_name}={value} ({_gone(old_key.lower())})")
            continue
        if not value or image_is_retired(value):
            warnings.append(f"api.env: dropped {old_name}={value} (a retired hal0 image)")
            continue
        new_key = new.upper()
        new_name = f"HAL0_RUNNER_IMAGE_{new_key}"
        if new_key in preexisting or index not in claimed or new_key in written:
            if present[new_key] == value:
                actions.append(f"api.env: dropped {old_name} (duplicates {new_name})")
            else:
                warnings.append(
                    f"api.env: dropped {old_name}={value} ({new_name} is already set to "
                    f"{present[new_key]})"
                )
            continue
        written.add(new_key)
        # The value is written back exactly as the operator quoted it: an
        # EnvironmentFile or a shell reads quoted and unquoted values
        # differently.
        out.append(f"{match.group('export') or ''}{new_name}={raw_value}\n")
        actions.append(f"api.env: {old_name} -> {new_name}")
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


def _shards_of(extra: object) -> tuple[str, ...]:
    """The further files of a split model, from the registry ``extra`` JSON blob."""
    if not isinstance(extra, str) or not extra:
        return ()
    try:
        meta = json.loads(extra)
    except ValueError:
        return ()
    shards = meta.get("shards") if isinstance(meta, dict) else None
    if not isinstance(shards, list):
        return ()
    return tuple(str(p) for p in shards if p)


def _fork_quant_lookup(db_path: Path) -> Callable[[str], str | None]:
    """``model id -> fork-only quant label | None``, read-only against the DB."""
    from hal0.registry.detect import unsupported_gguf_contents, unsupported_quant

    rows: dict[str, tuple[str, str, str, tuple[str, ...]]] = {}
    if db_path.is_file():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cols = _db_columns(conn, "model")
                if {"id", "path"} <= cols:
                    quant_col = "quant" if "quant" in cols else "NULL"
                    hf_col = "hf_filename" if "hf_filename" in cols else "NULL"
                    # ``extra`` is the metadata JSON blob; split models list
                    # their further files under ``shards``.
                    extra_col = "extra" if "extra" in cols else "NULL"
                    for mid, path, quant, hf, extra in conn.execute(
                        f"SELECT id, path, {quant_col}, {hf_col}, {extra_col} FROM model"
                    ):
                        rows[str(mid)] = (
                            str(path or ""),
                            str(quant or ""),
                            str(hf or ""),
                            _shards_of(extra),
                        )
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RunnerMigrationError(
                f"cannot read the model registry at {db_path}: {exc}",
                details={"path": str(db_path)},
            ) from exc

    def lookup(model_id: str) -> str | None:
        # File and quant evidence only: the registry id is the operator's
        # free choice and can name a fork family while the file is plain.
        path, quant, hf, shards = rows.get(model_id, ("", "", "", ()))
        by_name = unsupported_quant(quant, Path(path).name, hf)
        if by_name or not path:
            return by_name
        # A fork build renamed to an innocuous name still declares tensor
        # types only a fork can read. The launch guard refuses it, so the
        # slot must not keep it, and it checks every shard of a split model
        # too. A missing, unreadable or truncated file is simply not refused
        # by content: the migration never fails on it.
        for candidate in (path, *shards):
            try:
                found = unsupported_gguf_contents(candidate)
            except Exception:  # a bad file must not fail the migration
                continue
            if found:
                return found
        return None

    return lookup


@dataclass(frozen=True)
class _RunnerChange:
    model_id: str
    old: str
    new: str | None


def _plan_preferred_runner(
    db_path: Path, known: frozenset[str], actions: list[str], warnings: list[str]
) -> list[_RunnerChange]:
    """Leftover ``preferred_runner`` values to rename or clear."""
    if not db_path.is_file():
        return []
    changes: list[_RunnerChange] = []
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
                changes.append(_RunnerChange(str(mid), str(value), new))
                if new is None:
                    warnings.append(f"model {mid}: cleared preferred runner ({_gone(str(value))})")
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


def _set_preferred_runners(db_path: Path, pairs: Iterable[tuple[str, str | None]]) -> None:
    """``UPDATE model SET preferred_runner`` for each ``(model id, value)``, one transaction."""
    conn = sqlite3.connect(str(db_path))
    try:
        with conn:
            conn.executemany(
                "UPDATE model SET preferred_runner = ? WHERE id = ?",
                [(value, mid) for mid, value in pairs],
            )
    finally:
        conn.close()


# ── IO ────────────────────────────────────────────────────────────────────────


@dataclass
class _FileWrite:
    path: Path
    member: str
    original: bytes
    new: bytes


def _read_toml(path: Path, warnings: list[str]) -> tuple[bytes, dict[str, Any]] | None:
    """``(bytes, parsed)``; ``None`` when absent or unreadable (with a WARNING)."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        warnings.append(f"{path}: not readable ({exc}); skipped, not converted")
        return None
    try:
        return data, tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        warnings.append(
            f"{path}: does not parse ({exc}); skipped, not converted. Fix it, then "
            "re-run `hal0 runner-images migrate --apply`"
        )
        return None


def _refuse_symlink_dir(directory: Path) -> None:
    st = os.lstat(directory)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RunnerMigrationError(
            f"{directory} is not a plain directory; refusing to write into it",
            details={"path": str(directory)},
        )


def _replace_file(path: Path, data: bytes, *, default_mode: int = 0o640) -> None:
    """Replace ``path`` atomically without following a symlink at any step.

    The temp file is created ``O_EXCL | O_NOFOLLOW`` next to ``path``, given
    the existing file's mode and (when allowed) owner through its descriptor,
    and renamed over ``path``. A symlink at ``path`` is replaced, never
    followed.
    """
    _refuse_symlink_dir(path.parent)
    try:
        st: os.stat_result | None = os.lstat(path)
    except FileNotFoundError:
        st = None
    if st is not None and stat.S_ISREG(st.st_mode):
        mode, uid, gid = st.st_mode & 0o7777, st.st_uid, st.st_gid
    else:
        parent = os.lstat(path.parent)
        mode, uid, gid = default_mode, parent.st_uid, parent.st_gid
    tmp = path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            with contextlib.suppress(PermissionError, OSError):
                os.fchown(f.fileno(), uid, gid)
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


#: The mode the ownership table declares for ``<var_lib>/backups``
#: (:mod:`hal0.install.perms`, #2657): setgid, group-writable, so root-run
#: tools and the ``User=hal0`` service can both add restore points.
BACKUPS_DIR_MODE = 0o2775


def _backups_dir() -> Path:
    """The shared backups directory, writable by the current user or an error.

    The directory is shared between root-run tools and hal0-api
    (``User=hal0``); :mod:`hal0.install.perms` declares it ``hal0:hal0 2775``.
    A directory this call creates is chmodded to that mode (``mkdir``'s mode is
    masked by the umask). An existing directory is left alone when it is
    already writable (#2558). When it is not (#2657: a root-run tool under
    umask 022 birthed it ``root:hal0 2755``), it is repaired to ``2775`` when
    the current user owns it or is root; otherwise this raises a
    :class:`PermissionError` that names the directory, its owner and mode, and
    the one-line remedy, because no retry can succeed until an operator acts.
    Each archive is ``0600``.
    """
    from hal0.config import paths

    backup_dir = paths.var_lib() / "backups"
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        backup_dir.mkdir(mode=0o700)
    except FileExistsError:
        _refuse_symlink_dir(backup_dir)
    else:
        os.chmod(backup_dir, BACKUPS_DIR_MODE)
        return backup_dir
    if os.access(backup_dir, os.W_OK | os.X_OK):
        return backup_dir
    st = os.stat(backup_dir)
    euid = os.geteuid()
    if euid in (0, st.st_uid):
        with contextlib.suppress(OSError):
            if euid == 0:
                # Hand the group back to the parent's (hal0 on a packaged
                # install) so the service user gains write, not just root.
                os.chown(backup_dir, -1, os.stat(backup_dir.parent).st_gid)
            os.chmod(backup_dir, BACKUPS_DIR_MODE)
        if os.access(backup_dir, os.W_OK | os.X_OK):
            return backup_dir
        st = os.stat(backup_dir)
    raise PermissionError(errno.EACCES, _backups_dir_remedy(backup_dir, st))


def _backups_dir_remedy(backup_dir: Path, st: os.stat_result) -> str:
    """Why ``backup_dir`` is unusable and the one-line fix (#2657)."""
    owner = _id_name(pwd.getpwuid, st.st_uid, "pw_name")
    group = _id_name(grp.getgrgid, st.st_gid, "gr_name")
    me = _id_name(pwd.getpwuid, os.geteuid(), "pw_name")
    fix = f"sudo chmod 2775 {backup_dir}"
    if group != "hal0":
        fix = f"sudo chgrp hal0 {backup_dir} && {fix}"
    return (
        f"backups directory {backup_dir} is not writable by {me} "
        f"(owner {owner}:{group}, mode {stat.S_IMODE(st.st_mode):04o}); "
        f"fix: {fix} (or sudo hal0 doctor perms --fix), then restart hal0-api"
    )


def _id_name(lookup: Callable[[int], Any], ident: int, attr: str) -> str:
    try:
        return str(getattr(lookup(ident), attr))
    except KeyError:
        return str(ident)


def _write_backup(writes: Iterable[_FileWrite], runner_changes: list[_RunnerChange]) -> Path:
    """Write the restore point: owned files by role, plus the old preferred runners."""
    backup_dir = _backups_dir()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for w in writes:
            info = tarfile.TarInfo(name=w.member)
            info.size = len(w.original)
            info.mode = 0o600
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(w.original))
        if runner_changes:
            payload = json.dumps(
                [{"id": c.model_id, "preferred_runner": c.old} for c in runner_changes],
                indent=2,
            ).encode()
            info = tarfile.TarInfo(name=_BACKUP_RUNNERS)
            info.size = len(payload)
            info.mode = 0o600
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(payload))
    n = 0
    while True:  # never overwrite an earlier restore point
        suffix = f"-{n}" if n else ""
        target = backup_dir / f"runners-{stamp}{suffix}.tar.gz"
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


# ── restore marker ────────────────────────────────────────────────────────────


def restore_marker() -> Path:
    from hal0.config import paths

    return paths.var_lib() / RESTORE_MARKER_NAME


def _running_version() -> str:
    """The exact installed release (``current/VERSION``), nightly stamp included.

    ``hal0.__version__`` carries only the base version on a nightly wheel, so
    two nightlies of one base would share a marker.
    """
    from hal0.updater.updater import installed_version

    return str(installed_version())


def _restore_marker_active() -> bool:
    """Is there a restore marker written under the hal0 version running now?

    The marker records the version that ran the restore. It holds only while
    that same version runs: once another release is installed (the
    downgrade, or a later upgrade), it is stale and ignored.
    """
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


# ── entry point ───────────────────────────────────────────────────────────────


def migrate_runner_system(
    *,
    dry_run: bool = False,
    job_id: str | None = None,
    honour_restore_marker: bool = False,
) -> RunnerMigrationReport:
    """Convert this install onto the fresh runner system. See the module docstring.

    ``honour_restore_marker`` is for the boot-time callers (``hal0 serve`` and
    the API's safety net): after an operator restored a backup, they stand
    down instead of converting again. ``hal0 update`` and an explicit
    ``--apply`` pass ``False``, convert, and clear the marker.

    Raises :class:`RunnerMigrationError` on a failed write, after rolling back
    every write except an already converted ``hal0.toml``.
    """
    from hal0.config import paths
    from hal0.config.loader import hal0_config_file_lock
    from hal0.slot_config import slot_write_lock

    if honour_restore_marker and _restore_marker_active():
        report = RunnerMigrationReport(dry_run=True, skipped_for_restore=True)
        log.warning(
            "migrate.runner_system.skipped_after_restore",
            job_id=job_id,
            marker=str(restore_marker()),
            remedy="run `hal0 runner-images migrate --apply` (or `hal0 update`) to convert again",
        )
        return report

    with contextlib.ExitStack() as locks:
        # Serialize against the API's own config writers (a live hal0-api may be
        # serving a settings PUT while `hal0 update` runs this). Only when the
        # config tree exists: a fresh box has nothing to convert or lock.
        if paths.etc().is_dir():
            locks.enter_context(hal0_config_file_lock(paths.hal0_toml()))
            if paths.slots_config_dir().is_dir():
                locks.enter_context(slot_write_lock())
        return _migrate(dry_run=dry_run, job_id=job_id)


def _plan_files(
    report: RunnerMigrationReport, known: frozenset[str], fork_quant_of: Callable[[str], str | None]
) -> tuple[_FileWrite | None, list[_FileWrite]]:
    """``(hal0.toml write, every other file write)``; each file planned on its own."""
    from hal0.config import paths

    actions, warnings = report.actions, report.warnings
    toml_write: _FileWrite | None = None
    rest: list[_FileWrite] = []

    hal0_toml = paths.hal0_toml()
    loaded = _read_toml(hal0_toml, warnings)
    if loaded is not None:
        new = plan_hal0_toml(
            loaded[1], known=known, actions=actions, warnings=warnings, kept=report.kept
        )
        if new is not None:
            toml_write = _FileWrite(hal0_toml, _BACKUP_TOML, loaded[0], tomli_w.dumps(new).encode())

    slots_dir = paths.slots_config_dir()
    if slots_dir.is_dir():
        for slot_path in sorted(slots_dir.glob("*.toml")):
            if _BACKUP_SLOT_RE.match(f"slots/{slot_path.name}") is None:
                warnings.append(f"{slot_path}: unexpected slot file name; skipped")
                continue
            loaded = _read_toml(slot_path, warnings)
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
                unassigned=report.unassigned,
            )
            if new is not None:
                rest.append(
                    _FileWrite(
                        slot_path, f"slots/{slot_path.name}", loaded[0], tomli_w.dumps(new).encode()
                    )
                )

    profiles_toml = paths.profiles_toml()
    loaded = _read_toml(profiles_toml, warnings)
    if loaded is not None:
        new = plan_profiles(loaded[1], known=known, actions=actions, warnings=warnings)
        if new is not None:
            rest.append(
                _FileWrite(profiles_toml, _BACKUP_PROFILES, loaded[0], tomli_w.dumps(new).encode())
            )

    api_env = paths.api_env()
    if api_env.is_file():
        try:
            original = api_env.read_bytes()
            text = original.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # The old env names are simply no longer read, so an api.env this
            # process cannot open is reported, never a reason to block the
            # config conversion.
            warnings.append(f"api.env: not readable here ({exc}); HAL0_TOOLBOX_IMAGE_* not checked")
        else:
            new_text = plan_env(text, known=known, actions=actions, warnings=warnings)
            if new_text is not None:
                rest.append(_FileWrite(api_env, _BACKUP_ENV, original, new_text.encode("utf-8")))
    return toml_write, rest


def _migrate(*, dry_run: bool, job_id: str | None) -> RunnerMigrationReport:
    from hal0.config import paths

    known = _known_runner_keys()
    report = RunnerMigrationReport(dry_run=dry_run)
    db_path = paths.db_path()

    try:
        fork_quant_of = _fork_quant_lookup(db_path)
        runner_changes = _plan_preferred_runner(db_path, known, report.actions, report.warnings)
    except RunnerMigrationError as exc:
        # An unreadable registry only costs the model checks; the config
        # files still convert.
        report.warnings.append(f"{exc}; model checks skipped")
        fork_quant_of, runner_changes = (lambda _mid: None), []

    toml_write, rest = _plan_files(report, known, fork_quant_of)
    writes = ([toml_write] if toml_write else []) + rest

    if dry_run or not (writes or runner_changes):
        if report.changed:
            for line in report.lines():
                log.info("migrate.runner_system.plan", job_id=job_id, line=line)
        if not dry_run:
            _clear_restore_marker()
        return report

    try:
        backup = _write_backup(writes, runner_changes)
    except Exception as exc:
        raise RunnerMigrationError(
            f"could not write the runner-migration backup ({exc}); nothing was changed",
            details={"error": str(exc)},
        ) from exc
    report.backup = str(backup)

    # hal0.toml on its own: it is what stops hal0-api from starting, so it is
    # kept even when a later write fails.
    if toml_write is not None:
        try:
            _replace_file(toml_write.path, toml_write.new)
        except Exception as exc:
            raise RunnerMigrationError(
                f"could not convert {toml_write.path} ({exc}); nothing was changed "
                f"(backup: {backup})",
                details={"error": str(exc), "backup": str(backup)},
            ) from exc

    done: list[_FileWrite] = []
    try:
        for w in rest:
            _replace_file(w.path, w.new)
            done.append(w)
        if runner_changes:
            _set_preferred_runners(db_path, [(c.model_id, c.new) for c in runner_changes])
    except Exception as exc:
        not_restored: list[str] = []
        for w in reversed(done):
            try:
                _replace_file(w.path, w.original)
            except Exception as restore_exc:
                not_restored.append(f"{w.path} ({restore_exc})")
        kept_toml = " (hal0.toml stays converted)" if toml_write is not None else ""
        log.error(
            "migrate.runner_system.failed",
            job_id=job_id,
            error=str(exc),
            backup=str(backup),
            not_restored=not_restored,
        )
        if not_restored:
            raise RunnerMigrationError(
                f"runner-system migration failed ({exc}) and these files could NOT be "
                f"restored: {', '.join(not_restored)}. Stop hal0-api and run "
                f"`hal0 runner-images migrate --restore {backup}`",
                details={"error": str(exc), "backup": str(backup), "not_restored": not_restored},
            ) from exc
        raise RunnerMigrationError(
            f"runner-system migration failed and was rolled back{kept_toml}: {exc} "
            f"(backup: {backup})",
            details={"error": str(exc), "backup": str(backup)},
        ) from exc

    _clear_restore_marker()
    for line in report.lines():
        log.warning("migrate.runner_system.applied", job_id=job_id, line=line)
    return report


# ── restore ───────────────────────────────────────────────────────────────────


def _api_is_active() -> bool:
    """Is ``hal0-api`` running? ``False`` when systemd cannot be asked."""
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return False
    try:
        proc = subprocess.run(
            [systemctl, "is-active", "--quiet", "hal0-api"],
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _trusted_archive_owners() -> set[int]:
    """root, the caller, and the hal0 service user (``hal0-api`` writes backups).

    Safe because :func:`_read_archive` accepts only the members this migration
    owns, whoever wrote the archive.
    """
    owners = {0, os.getuid()}
    try:
        import pwd

        owners.add(pwd.getpwnam("hal0").pw_uid)
    except (KeyError, ImportError):
        pass
    return owners


def _check_archive(archive: Path) -> None:
    """Refuse an archive another user could have planted or edited."""
    try:
        st = os.lstat(archive)
    except OSError as exc:
        raise RunnerMigrationError(f"cannot read {archive}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise RunnerMigrationError(f"{archive} is not a regular file; refusing to restore it")
    if st.st_uid not in _trusted_archive_owners():
        raise RunnerMigrationError(
            f"{archive} is owned by uid {st.st_uid}, not root, you or hal0; refusing to restore it"
        )
    if st.st_mode & 0o022:
        raise RunnerMigrationError(f"{archive} is group- or world-writable; refusing to restore it")


def _owned_paths(member: str) -> Path | None:
    """The file a backup member restores to; ``None`` for anything else."""
    from hal0.config import paths

    fixed = {
        _BACKUP_TOML: paths.hal0_toml(),
        _BACKUP_PROFILES: paths.profiles_toml(),
        _BACKUP_ENV: paths.api_env(),
    }
    if member in fixed:
        return fixed[member]
    match = _BACKUP_SLOT_RE.match(member)
    if match is not None:
        return paths.slots_config_dir() / match.group("name")
    return None


def _read_archive(archive: Path) -> tuple[dict[Path, bytes], list[tuple[str, str]]]:
    """``({destination: bytes}, [(model id, preferred_runner)])``, all validated.

    Every member must be a regular file this migration owns; anything else
    refuses the whole archive before a single write.
    """
    files: dict[Path, bytes] = {}
    runners: list[tuple[str, str]] = []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    raise RunnerMigrationError(
                        f"{archive}: member {member.name!r} is not a regular file; refusing"
                    )
                if member.size > _BACKUP_MEMBER_MAX:
                    raise RunnerMigrationError(f"{archive}: member {member.name!r} is too large")
                src = tar.extractfile(member)
                data = src.read() if src is not None else b""
                if member.name == _BACKUP_RUNNERS:
                    rows = json.loads(data.decode("utf-8"))
                    if not isinstance(rows, list):
                        raise RunnerMigrationError(f"{archive}: {member.name} is not a list")
                    for row in rows:
                        if not (
                            isinstance(row, dict)
                            and isinstance(row.get("id"), str)
                            and isinstance(row.get("preferred_runner"), str)
                        ):
                            raise RunnerMigrationError(
                                f"{archive}: {member.name} has a malformed row"
                            )
                        runners.append((row["id"], row["preferred_runner"]))
                    continue
                dest = _owned_paths(member.name)
                if dest is None:
                    raise RunnerMigrationError(
                        f"{archive}: unknown member {member.name!r}; this is not a "
                        "runner-migration backup, refusing"
                    )
                files[dest] = data
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise RunnerMigrationError(f"cannot read {archive}: {exc}") from exc
    return files, runners


def restore_backup(archive: Path | str) -> list[str]:
    """Put a ``runners-<ts>.tar.gz`` backup back. Returns what it restored.

    For a downgrade: it writes back only the files the conversion owns
    (``hal0.toml``, ``slots/*.toml``, ``profiles.toml``, ``api.env``) and the
    old ``preferred_runner`` values, through ``UPDATE`` statements, never a
    database copy. It refuses an archive not owned by root or the caller, a
    group- or world-writable one, any member it does not own, and to run
    while ``hal0-api`` is active. It takes the config and slot locks, and
    leaves a marker so the API's boot-time conversion does not redo the
    conversion; ``hal0 update`` or ``--apply`` converts again.
    """
    from hal0.config import paths
    from hal0.config.loader import hal0_config_file_lock
    from hal0.slot_config import slot_write_lock

    archive = Path(archive)
    _check_archive(archive)
    if _api_is_active():
        raise RunnerMigrationError(
            "hal0-api is running; stop it first (`sudo systemctl stop hal0-api`) so it "
            "does not convert or overwrite the restored files"
        )
    files, runners = _read_archive(archive)

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
        if runners and db_path.is_file():
            _set_preferred_runners(db_path, runners)
            restored.append(f"{db_path} (preferred_runner for {len(runners)} model(s))")

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
    "RETIRED_DEFAULT_KEYS",
    "RETIRED_OPTIONAL_KEYS",
    "RunnerMigrationError",
    "RunnerMigrationReport",
    "image_is_hal0_runner",
    "image_is_retired",
    "map_runner_key",
    "migrate_runner_system",
    "normalize_repo",
    "plan_env",
    "plan_hal0_toml",
    "plan_profiles",
    "plan_slot",
    "restore_backup",
    "restore_marker",
    "split_image_ref",
]
