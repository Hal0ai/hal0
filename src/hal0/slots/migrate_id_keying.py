"""One-shot M5 migration: name-keyed slot artefacts → id-keyed (rework §3.1).

Increment-A landed the ``slot`` identity table + the non-destructive boot fold
(:meth:`hal0.slots.manager.SlotManager.fold_identity`, which only *populates*
rows + port claims from the existing name-keyed TOMLs). This module is the
*destructive* one-shot that flips the on-disk layout itself so every operator-
visible surface is addressed by the stable ``slot_id`` instead of the mutable
name:

  ``/etc/hal0/slots/<name>.toml``           → ``/etc/hal0/slots/<id>.toml``
  ``/var/lib/hal0/slots/<name>/state.json`` → ``/var/lib/hal0/slots/<id>/state.json``
  ``hal0-slot@<name>.service``              → ``hal0-slot@<id>.service``
  ``hal0-slot-<name>`` (podman container)   → ``hal0-slot-<id>``

Design contract (per the P3 spec §3.1 / §3.3):

* **Idempotent.** The marker is the TOML's ``id`` field living at ``<id>.toml``.
  A re-run — including on a half-migrated tree (TOML moved but state.json not,
  or vice-versa) — rolls forward to the same byte-identical result.
* **File-rename only.** state.json stays a file on disk (it does NOT move into
  SQLite here — that is the later P3-runtime-db lane, migration 006). The
  migrator's job is to hand runtime-db a fully id-keyed layout to consume.
* **Ops are injected.** The systemd unit + podman container renames go through
  a :class:`SlotArtifactOps` seam so the pure filesystem migration is unit-
  testable without touching systemd/podman. The live implementation
  (:class:`SubprocessSlotArtifactOps`) is deploy-only and best-effort — the
  live unit/container rename is held for on-hardware smoke.

This module is intentionally NOT wired into the api lifespan: running it flips
the layout the (still name-keyed) runtime reads, so it is invoked deliberately
during the P3 downtime window, not automatically on every boot.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import tomllib
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from hal0.slot_config import write_slot_toml
from hal0.slots.identity import SlotIdentityStore

log = logging.getLogger("hal0.slots.migrate_id_keying")


# ── ops seam (systemd unit + podman container renames) ───────────────────────


class SlotArtifactOps(Protocol):
    """The non-filesystem side effects of the migration.

    Split behind a protocol so the filesystem migration is testable without a
    live systemd/podman; the live implementation is deploy-only.
    """

    def rename_unit(self, old_name: str, new_id: int) -> None: ...
    def rename_container(self, old_name: str, new_id: int) -> None: ...


@dataclass
class RecordingSlotArtifactOps:
    """Test / dry-run double: records the requested renames, does nothing."""

    unit_renames: list[tuple[str, int]] = field(default_factory=list)
    container_renames: list[tuple[str, int]] = field(default_factory=list)

    def rename_unit(self, old_name: str, new_id: int) -> None:
        self.unit_renames.append((old_name, new_id))

    def rename_container(self, old_name: str, new_id: int) -> None:
        self.container_renames.append((old_name, new_id))


class SubprocessSlotArtifactOps:
    """Deploy-only live ops: rename the systemd unit + podman container.

    Best-effort and HELD FOR ON-HARDWARE SMOKE — no CI/unit coverage here
    (there is no systemd/podman in the test env). Routes systemctl verbs
    through :class:`hal0.system.seam.SystemCtlSeam` (the same seam the
    container provider uses) so a hal0-service-user install stays unprivileged.
    """

    def __init__(self) -> None:  # pragma: no cover - deploy-only
        import subprocess

        from hal0.system.seam import SystemCtlSeam

        self._subprocess = subprocess
        self._seam = SystemCtlSeam()
        self._systemd_dir = Path("/etc/systemd/system")

    def rename_unit(self, old_name: str, new_id: int) -> None:  # pragma: no cover
        old_unit = self._systemd_dir / f"hal0-slot@{old_name}.service"
        new_unit = self._systemd_dir / f"hal0-slot@{new_id}.service"
        # Stop + disable the old instance, then move the unit file across,
        # rewriting the container name + SyslogIdentifier tokens inside it.
        self._seam.systemctl("systemctl", "stop", old_unit.name, check=False)
        self._seam.systemctl("systemctl", "disable", old_unit.name, check=False)
        if old_unit.exists():
            text = old_unit.read_text().replace(f"hal0-slot-{old_name}", f"hal0-slot-{new_id}")
            self._seam.write_unit(new_unit, text)
            self._seam.remove_unit(old_unit)
        self._seam.systemctl("systemctl", "daemon-reload", check=False)
        self._seam.systemctl("systemctl", "enable", new_unit.name, check=False)

    def rename_container(self, old_name: str, new_id: int) -> None:  # pragma: no cover
        self._subprocess.run(
            ["podman", "rename", f"hal0-slot-{old_name}", f"hal0-slot-{new_id}"],
            check=False,
            capture_output=True,
        )


# ── report ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotMigration:
    """One slot that was migrated name → id in this run."""

    name: str
    slot_id: int


@dataclass(frozen=True)
class MigrationReport:
    migrations: list[SlotMigration]
    #: ids of slots already id-keyed on entry (skipped, but their state.json is
    #: still reconciled for partial-migration roll-forward).
    skipped_ids: list[int]


# ── TOML shape helpers (preserve the on-disk ``[slot]`` table) ────────────────


def _read_raw_toml(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("slot.migrate_toml_unreadable", extra={"path": str(path), "error": str(exc)})
        return None


def _slot_table(raw: dict[str, Any]) -> dict[str, Any]:
    """The table the flat slot fields live in — the ``[slot]`` sub-table when
    present (the on-disk shape), else the top level (already-flat configs)."""
    tbl = raw.get("slot")
    return tbl if isinstance(tbl, dict) else raw


def _toml_id(raw: dict[str, Any]) -> int | None:
    val = _slot_table(raw).get("id")
    return int(val) if isinstance(val, int) and not isinstance(val, bool) else None


# ── state.json move ──────────────────────────────────────────────────────────


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".hal0-state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _migrate_state(data_dir: Path, name: str, slot_id: int) -> bool:
    """Move ``<name>/state.json`` → ``<id>/state.json``, rewriting the ``name``
    field to the canonical label and adding a top-level ``slot_id``.

    Returns True when a move happened; a no-op (returns False) when the
    name-keyed state.json is absent (already migrated, or never persisted).
    """
    if name == str(slot_id):
        return False  # degenerate: a slot literally named as its id
    src = data_dir / name / "state.json"
    if not src.exists():
        return False
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("slot.migrate_state_unreadable", extra={"path": str(src), "error": str(exc)})
        return False
    data["name"] = name
    data["slot_id"] = slot_id
    _write_json_atomic(data_dir / str(slot_id) / "state.json", data)
    src.unlink()
    with contextlib.suppress(OSError):
        (data_dir / name).rmdir()  # drop the now-empty name-keyed dir
    return True


# ── the migration ─────────────────────────────────────────────────────────────


def migrate_slot_id_keying(
    *,
    identity: SlotIdentityStore,
    config_dir: Path,
    data_dir: Path,
    ops: SlotArtifactOps,
    seeded_names: Collection[str] = (),
) -> MigrationReport:
    """Migrate every name-keyed slot artefact under *config_dir* / *data_dir*
    to id-keyed. Idempotent; safe to re-run on a partially-migrated tree.

    Slots are processed in deterministic (TOML-stem-sorted) order so the
    assigned ids are stable across a fresh run.
    """
    seeded = set(seeded_names)
    migrations: list[SlotMigration] = []
    skipped_ids: list[int] = []

    for toml_path in sorted(config_dir.glob("*.toml")):
        if toml_path.name.startswith("."):
            continue
        raw = _read_raw_toml(toml_path)
        if raw is None:
            continue
        slot_tbl = _slot_table(raw)
        name = str(slot_tbl.get("name") or toml_path.stem)
        existing_id = _toml_id(raw)

        # Already id-keyed (the idempotency marker): its id field matches its
        # filename. Still reconcile a possibly-stranded name-keyed state.json
        # (crash between the TOML move and the state move — §3.3 roll-forward).
        if existing_id is not None and toml_path.stem == str(existing_id):
            _migrate_state(data_dir, name, existing_id)
            skipped_ids.append(existing_id)
            continue

        # Name-keyed (or half-migrated: id stamped but file still <name>.toml).
        # Reuse the identity row when one already exists (fold / prior run);
        # only mint a new id for a genuinely-unseen slot.
        row = identity.get_by_name(name)
        if row is None:
            device = str(slot_tbl.get("device") or "")
            row = identity.create(
                name=name,
                slot_type=str(slot_tbl.get("type") or "llm"),
                device=device,
                coresident_group="npu-flm-trio" if device == "npu" else None,
                is_seed=name in seeded,
                enabled=bool(slot_tbl.get("enabled", True)),
            )
        slot_id = row.id

        # Write the id-keyed TOML (id + preserved name), then drop the old file.
        slot_tbl["id"] = slot_id
        slot_tbl.setdefault("name", name)
        write_slot_toml(config_dir / f"{slot_id}.toml", raw)
        if toml_path.name != f"{slot_id}.toml":
            with contextlib.suppress(OSError):
                toml_path.unlink()

        _migrate_state(data_dir, name, slot_id)

        # systemd unit + podman container renames (deploy-only when live).
        ops.rename_unit(name, slot_id)
        ops.rename_container(name, slot_id)

        migrations.append(SlotMigration(name=name, slot_id=slot_id))
        log.info("slot.migrated_id_keyed", extra={"slot": name, "id": slot_id})

    log.info(
        "slot.id_keying_migration_done",
        extra={"migrated": len(migrations), "skipped": len(skipped_ids)},
    )
    return MigrationReport(migrations=migrations, skipped_ids=sorted(skipped_ids))


__all__ = [
    "MigrationReport",
    "RecordingSlotArtifactOps",
    "SlotArtifactOps",
    "SlotMigration",
    "SubprocessSlotArtifactOps",
    "migrate_slot_id_keying",
]
