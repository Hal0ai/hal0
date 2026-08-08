"""Static slot-config seeds shipped in ``installer/etc-hal0/slots/``.

``install.sh``'s "Container slot seeds" loop copies these files
(``flm``/``tts``/``rerank``/``utility``/``img``/``agent``/``brain``)
into ``/etc/hal0/slots/`` on a FRESH install only — bash, so it runs
before hal0's own venv exists. That loop never re-runs on ``hal0
update``: an existing box upgrading past a release that adds a new
seed (e.g. ``brain``, added alongside the dashboard steward) never
grows the file. This module is the same copy-if-absent logic, callable
from Python, so the hal0-api lifespan can close that gap the way it
already does for persona seeding (:func:`hal0.agents.personas.seed_default_personas`).

Keep :data:`STATIC_SEED_SLOTS` in sync with install.sh's
``for seed_slot in ...`` line by hand — bash and Python don't share a
source here, mirroring how ``setup_command._SETUP_SLOTS`` and
``routes.installer._SLOT_META`` already hand-mirror each other.
"""

from __future__ import annotations

import shutil
from collections.abc import Collection
from pathlib import Path

import structlog

from hal0.config import paths

log = structlog.get_logger(__name__)

#: Slot names with a static seed TOML in installer/etc-hal0/slots/.
#: MUST mirror install.sh's:
#:   for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do
STATIC_SEED_SLOTS: tuple[str, ...] = (
    "flm",
    "tts",
    "rerank",
    "utility",
    "img",
    "agent",
    "brain",
    "qwen3tts",
    "coder",
    "embed",
)


def seed_static_slots(
    *,
    installer_root: Path | None = None,
    slots_dir: Path | None = None,
    existing_names: Collection[str] = (),
) -> list[str]:
    """Copy any missing static seed TOML into the slots config dir.

    Idempotent and non-destructive: an existing ``<name>.toml`` — an
    operator edit, a seed from a prior run, or a slot the operator
    created by hand under that name — is left untouched. Returns the
    slot names actually seeded this call (empty on a converged box).

    ``existing_names`` (P3-runtime-db inc3 — the seed split-brain fix) is the
    set of slot names the identity store already tracks. A slot migrated to
    id-keying has NO ``<name>.toml`` on disk (it lives at ``<id>.toml``), so
    the file-existence check alone would re-copy the seed and split-brain the
    slot (a fresh ``brain.toml`` beside the migrated ``143.toml``). A slot is
    therefore "already known" — and skipped — when its name is in
    ``existing_names`` OR a name-keyed ``<name>.toml`` still exists (the pre-
    identity fresh box). Defaults to empty, so every existing caller / test
    keeps the pure file-existence behaviour.

    ``installer_root`` defaults to :data:`hal0.agents.hermes_provision.
    REPO_ROOT_FOR_INSTALLER`, the same dev/editable-vs-FHS probe every
    other installer-tree reader uses (imported lazily to dodge an
    import cycle at module load); ``slots_dir`` defaults to
    :func:`hal0.config.paths.slots_config_dir`. Both are injectable for
    tests.
    """
    if installer_root is None:
        from hal0.agents.hermes_provision import REPO_ROOT_FOR_INSTALLER

        installer_root = REPO_ROOT_FOR_INSTALLER
    dest_dir = slots_dir if slots_dir is not None else paths.slots_config_dir()
    src_dir = installer_root / "installer" / "etc-hal0" / "slots"
    known = set(existing_names)

    tombstoned = read_seed_tombstones(slots_dir=dest_dir)
    seeded: list[str] = []
    for name in STATIC_SEED_SLOTS:
        dest = dest_dir / f"{name}.toml"
        # "Already known" = the identity store tracks it (id-keyed, no
        # <name>.toml) OR a name-keyed file still exists (pre-migration box).
        # A tombstoned name is an operator DELETION — honour it instead of
        # resurrecting the slot on every boot (the pre-tombstone behaviour
        # that made seeded slots effectively undeletable).
        if name in known or name in tombstoned or dest.exists():
            continue
        src = src_dir / f"{name}.toml"
        if not src.is_file():
            log.warning("install.static_seed_missing", slot=name, src=str(src))
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        dest.chmod(0o644)
        seeded.append(name)
        log.info("install.static_seed_applied", slot=name, dest=str(dest))
    return seeded


# ── Seed tombstones — "the operator deleted this seed on purpose" ────────────
#
# Deleting a slot removes its TOML and identity row, which made a static seed
# indistinguishable from "new seed this release" on the next boot — the seeding
# pass re-created it, so a seeded slot could never actually be removed (the
# delete guard papered over that with a hard refusal). A tombstone records the
# deletion; creating a slot under the name again (operator create, or the
# capability orchestrator materialising it on re-enable) clears it.

_TOMBSTONE_FILE = ".seed-tombstones"


def _tombstone_path(slots_dir: Path | None = None) -> Path:
    base = slots_dir if slots_dir is not None else paths.slots_config_dir()
    return base / _TOMBSTONE_FILE


def read_seed_tombstones(*, slots_dir: Path | None = None) -> frozenset[str]:
    """Names the operator deleted and that seeding must not resurrect."""
    try:
        raw = _tombstone_path(slots_dir).read_text()
    except OSError:
        return frozenset()
    return frozenset(line.strip() for line in raw.splitlines() if line.strip())


def add_seed_tombstone(name: str, *, slots_dir: Path | None = None) -> None:
    """Record that seed ``name`` was deliberately deleted (idempotent).

    Read-modify-write under the cross-process slot-TOML lock: two concurrent
    deletes (CLI + API) each rewriting the whole file would otherwise lose one
    entry and the lost deletion resurrects on the next seeding pass.
    """
    from hal0.slot_config import slot_write_lock

    with slot_write_lock():
        current = set(read_seed_tombstones(slots_dir=slots_dir))
        if name in current:
            return
        current.add(name)
        path = _tombstone_path(slots_dir)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(f"{n}\n" for n in sorted(current)))
        except OSError as exc:  # read-only config dir — deletion still proceeds
            log.warning("install.seed_tombstone_write_failed", slot=name, error=str(exc))


def clear_seed_tombstone(name: str, *, slots_dir: Path | None = None) -> None:
    """Forget a deletion — the slot exists again under this name (idempotent)."""
    from hal0.slot_config import slot_write_lock

    with slot_write_lock():
        current = set(read_seed_tombstones(slots_dir=slots_dir))
        if name not in current:
            return
        current.discard(name)
        path = _tombstone_path(slots_dir)
        try:
            if current:
                path.write_text("".join(f"{n}\n" for n in sorted(current)))
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover — defensive
            log.warning("install.seed_tombstone_clear_failed", slot=name, error=str(exc))


__all__ = [
    "STATIC_SEED_SLOTS",
    "add_seed_tombstone",
    "clear_seed_tombstone",
    "read_seed_tombstones",
    "seed_static_slots",
]
