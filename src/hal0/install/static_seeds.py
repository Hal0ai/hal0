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

import re
import shutil
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from hal0.config import paths

if TYPE_CHECKING:
    from hal0.config.schema import HardwareInfo

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

#: Seed name → :func:`hal0.install.profile_derive.derive_device` capability for
#: the llama.cpp-backed lifecycle seeds (#2023). Every one of these ships
#: ``device = "gpu-rocm"`` in its TOML — the reference platform — but a
#: verbatim copy on a kfd-less box hands the slot a device the load-time gate
#: (``_gpu.require_kfd_for_gpu_slot``) can only refuse, so seeding routes them
#: through the SAME derivation ladder ``hal0 setup --auto`` uses.
#:
#: ``agent``/``brain`` map to the **chat** capability: they are the chat
#: capability's slots (ADR-0023; ``setup_command._SETUP_SLOTS`` /
#: ``_BRAIN_SLOT``). ``derive_device``'s own ``"agent"`` capability is the
#: NPU-only chat lane and returns ``None`` on every non-NPU box — mapping the
#: seed name straight through would skip derivation entirely.
#:
#: Deliberately absent: ``flm`` (npu), ``tts`` (kokoro/cpu), ``img``
#: (ComfyUI) and ``qwen3tts`` — non-llama runtimes with their own device
#: logic. Their seed device ships verbatim, whatever the host. For the two
#: that ship ``gpu-rocm`` (``img``, ``qwen3tts``) verbatim is a CONTRACT, not
#: an omission (#2067): both runner images are ROCm-only builds
#: (``RUNNER_IMAGES[...].supported_backends == ("rocm",)``), so a kfd-less box
#: has no CPU or Vulkan lane to derive instead. The seed lands as an inactive
#: tile (no ``[model].default`` pin, #1369 — nothing autoloads), and first use
#: refuses loudly via ``_gpu.require_kfd_for_gpu_slot``'s ``rocm`` lane,
#: naming the absent ``/dev/kfd`` and how to forward it.
LLAMA_SEED_CAPABILITIES: dict[str, str] = {
    "agent": "chat",
    "brain": "chat",
    "coder": "coder",
    "embed": "embed",
    "rerank": "rerank",
    "utility": "utility",
}

#: The top-level ``device = "..."`` assignment in a seed TOML. The seeds keep
#: ``device`` above the ``[model]`` table, so the first match is always the
#: slot-level field.
_DEVICE_LINE = re.compile(r'(?m)^(device[ \t]*=[ \t]*)"[^"]*"')


def _resolve_hardware_info() -> HardwareInfo | None:
    """Best-effort hardware fact for seed-device derivation, or ``None``.

    Prefers the stored probe fact (``/etc/hal0/hardware.json``, written by
    ``hal0 setup``) when it exists; otherwise runs a live light probe — the
    fresh-install case, where install.sh's seed loop lands BEFORE ``hal0
    setup --auto`` writes the fact. ``None`` (nothing resolvable) means the
    caller keeps the verbatim seed devices: fail-soft, a wrong ROCm label
    refuses loudly at load time with its remedy, and seeding must never
    abort over a probe.
    """
    try:
        if paths.hardware_json().exists():
            from hal0.config.loader import load_hardware_info

            return load_hardware_info()
    except Exception as exc:
        log.warning("install.seed_device_hw_fact_unreadable", error=str(exc))
    try:
        from hal0.hardware.probe import HardwareProbe

        return HardwareProbe().probe()
    except Exception as exc:
        log.warning("install.seed_device_probe_failed", error=str(exc))
        return None


def derive_seed_device(name: str, hw: HardwareInfo) -> str | None:
    """Host-derived device for a llama.cpp-backed seed; ``None`` = verbatim.

    Same ladder as ``hal0 setup --auto`` (#1888/#1923/#1948 rulings intact):
    usable ROCm (kfd + compute-capable / strix-halo) → ``gpu-rocm``; else a
    Vulkan-capable GPU *and* a runner image that serves the Vulkan lane →
    ``gpu-vulkan``; else ``cpu``. ``npu_opt_in`` is pinned False — static
    seeding never claims the NPU (the ``flm`` seed carries that lane).
    """
    capability = LLAMA_SEED_CAPABILITIES.get(name)
    if capability is None:
        return None
    from hal0.install.profile_derive import derive_device

    return derive_device(capability, hw, npu_opt_in=False)


def apply_derived_seed_devices(
    names: Collection[str],
    *,
    slots_dir: Path | None = None,
    hw: HardwareInfo | None = None,
) -> dict[str, str]:
    """Rewrite freshly seeded llama.cpp slots' ``device`` to the host-derived one.

    The single derivation pass both seeding paths share (#2023):
    :func:`seed_static_slots` calls it after its copy loop, and install.sh's
    bash seed loop calls it via ``python -m hal0.install.static_seeds`` over
    the slots it just copied. Only names in :data:`LLAMA_SEED_CAPABILITIES`
    with an existing ``<name>.toml`` are considered — pass ONLY freshly seeded
    names; an operator's existing file must never reach this function.

    The hardware fact is resolved lazily (first file that actually carries a
    ``device`` line) so converged/no-op passes cost nothing. Unresolvable
    hardware keeps every file verbatim. Returns ``{name: device}`` for the
    files actually rewritten.
    """
    dest_dir = slots_dir if slots_dir is not None else paths.slots_config_dir()
    resolved = hw
    rewritten: dict[str, str] = {}
    for name in names:
        if name not in LLAMA_SEED_CAPABILITIES:
            continue
        dest = dest_dir / f"{name}.toml"
        try:
            text = dest.read_text(encoding="utf-8")
        except OSError:
            # Tombstoned / pre-existing names never got a fresh copy — skip.
            continue
        if _DEVICE_LINE.search(text) is None:
            continue
        if resolved is None:
            resolved = _resolve_hardware_info()
            if resolved is None:
                log.warning(
                    "install.seed_device_derivation_skipped",
                    detail="hardware unresolvable — seeded slots keep their shipped device",
                )
                return rewritten
        device = derive_seed_device(name, resolved)
        if device is None:
            continue
        new_text, n = _DEVICE_LINE.subn(rf'\g<1>"{device}"', text, count=1)
        if n == 0 or new_text == text:
            continue
        dest.write_text(new_text, encoding="utf-8")
        rewritten[name] = device
        log.info("install.seed_device_derived", slot=name, device=device)
    return rewritten


def seed_static_slots(
    *,
    installer_root: Path | None = None,
    slots_dir: Path | None = None,
    existing_names: Collection[str] = (),
    hw: HardwareInfo | None = None,
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

    ``hw`` (#2023): hardware fact for the seed-device derivation applied to
    the llama.cpp-backed seeds after copying (see
    :func:`apply_derived_seed_devices`). ``None`` resolves it lazily — and
    only when a freshly copied seed actually carries a ``device`` line — via
    :func:`_resolve_hardware_info`; unresolvable hardware keeps the verbatim
    copy (fail-soft).
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
    if seeded:
        # #2023: freshly copied llama.cpp seeds carry the reference platform's
        # device = "gpu-rocm" verbatim — derive the device this host can
        # actually load, exactly like `hal0 setup --auto` would have. Only the
        # names copied THIS call are touched; existing/operator files above
        # were skipped and stay untouched.
        apply_derived_seed_devices(seeded, slots_dir=dest_dir, hw=hw)
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


def _main(argv: list[str] | None = None) -> int:
    """``python -m hal0.install.static_seeds`` — install.sh's derivation pass.

    install.sh's "Container slot seeds" bash loop copies the seed TOMLs
    verbatim (curated profiles/ports must land byte-for-byte), then invokes
    this over the names it just copied so the llama.cpp seeds get the same
    host-derived device every other provisioning path uses (#2023). Exit 1
    (hardware unresolvable — nothing rewritten) lets install.sh print its
    fail-soft warning; unknown / non-llama names are skipped silently.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m hal0.install.static_seeds",
        description="Derive host-appropriate devices for freshly seeded slot TOMLs.",
    )
    parser.add_argument(
        "--slots-dir",
        type=Path,
        default=None,
        help="Slot config dir (default: the platform slots config dir).",
    )
    parser.add_argument("names", nargs="+", help="Freshly seeded slot names.")
    args = parser.parse_args(argv)

    hw = _resolve_hardware_info()
    if hw is None:
        print(
            "seed-device derivation: hardware unresolvable — seeded slots keep their shipped device"
        )
        return 1
    rewritten = apply_derived_seed_devices(args.names, slots_dir=args.slots_dir, hw=hw)
    for name, device in sorted(rewritten.items()):
        print(f"seeded {name} slot: device derived -> {device}")
    if not rewritten:
        print("seeded slot devices already match this host — nothing rewritten")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via install.sh
    import sys

    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "LLAMA_SEED_CAPABILITIES",
    "STATIC_SEED_SLOTS",
    "add_seed_tombstone",
    "apply_derived_seed_devices",
    "clear_seed_tombstone",
    "derive_seed_device",
    "read_seed_tombstones",
    "seed_static_slots",
]
