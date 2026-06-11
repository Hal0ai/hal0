"""SlotConfigStore — one reconciled truth for capability + slot config (issue #697).

Two files describe what a capability child should run:

  - ``/etc/hal0/capabilities.toml`` — the operator's selection per
    (slot, child), written by the dashboard's capability cards.
  - ``/etc/hal0/slots/<name>.toml`` — the underlying slot config the
    providers actually spawn from.

Historically they were reconciled by an unconditional rewrite buried in
``CapabilityOrchestrator.apply()``; a half-finished apply, a manual
edit, or a migration seed could leave the two disagreeing (the
2026-05-20 production drift). This module replaces that hidden rewrite
with an explicit, observable, reversible step:

  - :meth:`SlotConfigStore.apply` is **compute-only** — it produces a
    :class:`ChangeSet` (before/after snapshots of the on-disk config)
    and writes NOTHING.
  - :meth:`SlotConfigStore.commit` writes ``after`` atomically (per
    file via :func:`hal0.config.loader.write_toml_atomic`, with
    roll-back to ``before`` if a later file write fails).
  - :meth:`SlotConfigStore.revert` restores ``before``.

The invariant the test-suite pins: after ``commit`` disk equals
``cs.after``; after ``revert`` disk equals ``cs.before``; a failed
mid-commit leaves disk at ``before`` — never half-reconciled.

Device→backend translation is **imported from** :mod:`hal0.model_meta`
(:func:`canonical_device`, :func:`device_to_legacy_backend`) — this
module re-derives no classification logic.

:func:`write_slot_toml` is the single low-level write path for
``slots/*.toml``; every writer (SlotManager.create/update_config, the
installer's pick-default seed, the model-delete cascade) routes its
bytes through it.
"""

from __future__ import annotations

import contextlib
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hal0.config import paths
from hal0.config.loader import write_toml_atomic
from hal0.model_meta import canonical_device, device_to_legacy_backend

if TYPE_CHECKING:
    from hal0.capabilities.config import CapabilitySelection

# NOTE: hal0.capabilities.* is imported lazily inside the store methods.
# This module is imported by hal0.slots.manager (the write_slot_toml
# re-point), and a module-level import of hal0.capabilities would close
# the cycle capabilities.__init__ → orchestrator → dispatcher →
# slots.manager → slot_config. Keeping this module import-light breaks
# that loop while the orchestrator (which imports both) stays the
# composition point.

log = logging.getLogger(__name__)


# ── the single low-level slots/*.toml write path ─────────────────────────────


def write_slot_toml(path: Path | str, data: dict[str, Any]) -> None:
    """Atomically write a slot TOML.

    THE byte-level write path for ``/etc/hal0/slots/*.toml`` — thin
    wrapper over :func:`write_toml_atomic` kept as a named seam so the
    writer inventory stays greppable (issue #697). Raises ``OSError`` on
    filesystem failure and ``TypeError`` on non-TOML-encodable values,
    same as the underlying writer.
    """
    write_toml_atomic(Path(path), data)


# ── ChangeSet ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FileState:
    """One file's snapshot inside a :class:`ChangeSet`.

    ``data is None`` means "the file does not exist" — committing it
    is a no-op, reverting to it unlinks the file.
    """

    path: Path
    data: dict[str, Any] | None


@dataclass(frozen=True)
class ChangeSet:
    """Before/after snapshots of the on-disk slot config.

    ``before`` and ``after`` are parallel tuples (same paths, same
    order). Produced by :meth:`SlotConfigStore.apply`; consumed by
    :meth:`SlotConfigStore.commit` / :meth:`SlotConfigStore.revert`.
    """

    before: tuple[FileState, ...]
    after: tuple[FileState, ...]

    @property
    def changed(self) -> bool:
        """True when committing this ChangeSet would alter any file."""
        return any(b.data != a.data for b, a in zip(self.before, self.after, strict=True))


@dataclass(frozen=True)
class SlotSelection:
    """The input to :meth:`SlotConfigStore.apply`.

    Carries the merged (post-validation) selection the orchestrator
    computed for one capability child, plus the addressing needed to
    reconcile the underlying slot file.
    """

    slot: str
    child: str
    slot_name: str
    selection: CapabilitySelection


# ── store ────────────────────────────────────────────────────────────────────


class SlotConfigStore:
    """Deep module owning capabilities.toml + slots/*.toml as one truth.

    Stateless between calls — every ``apply`` re-reads disk so the
    snapshots are honest even when other writers (CLI migrations,
    SlotManager lifecycle) touched the files in between.
    """

    def __init__(
        self,
        *,
        capabilities_path: Path | None = None,
        slots_dir: Path | None = None,
    ) -> None:
        # Resolved lazily so a HAL0_HOME set after construction (the
        # test fixture pattern) still lands in the right place.
        self._capabilities_path = Path(capabilities_path) if capabilities_path else None
        self._slots_dir = Path(slots_dir) if slots_dir else None

    # ── path helpers ─────────────────────────────────────────────────────────

    def _caps_path(self) -> Path:
        from hal0.capabilities.config import capabilities_toml_path

        return self._capabilities_path or capabilities_toml_path()

    def _slot_path(self, slot_name: str) -> Path:
        base = self._slots_dir or paths.slots_config_dir()
        return base / f"{slot_name}.toml"

    # ── apply (compute-only) ─────────────────────────────────────────────────

    def apply(self, selection: SlotSelection) -> ChangeSet:
        """Compute the reconciled post-state for ``selection``. Writes NOTHING.

        Returns a :class:`ChangeSet` over exactly two files, in commit
        order:

          1. ``capabilities.toml`` — the persisted selection for
             ``(selection.slot, selection.child)`` replaced with
             ``selection.selection``, serialised through the canonical
             :func:`capabilities_toml_payload` shape.
          2. ``slots/<slot_name>.toml`` — reconciled against the
             selection **iff** the selection is enabled AND the file
             already exists (slot creation carries state.json + port
             allocation side effects and stays with
             ``SlotManager.create``). Otherwise ``after == before``.
        """
        caps_path = self._caps_path()
        caps_before = _read_toml_or_none(caps_path)
        caps_after = self._reconciled_capabilities(caps_before, selection)

        slot_path = self._slot_path(selection.slot_name)
        slot_before = _read_toml_or_none(slot_path)
        slot_after = self._reconciled_slot(slot_before, selection.selection)

        return ChangeSet(
            before=(
                FileState(path=caps_path, data=caps_before),
                FileState(path=slot_path, data=slot_before),
            ),
            after=(
                FileState(path=caps_path, data=caps_after),
                FileState(path=slot_path, data=slot_after),
            ),
        )

    # ── commit / revert ──────────────────────────────────────────────────────

    def commit(self, cs: ChangeSet) -> None:
        """Write ``cs.after`` to disk.

        Each file write is atomic (tmpfile + fsync + rename). Whole-set
        atomicity is approximated by roll-back: if a later write fails,
        every file already written is restored to its ``before``
        snapshot and the original exception re-raised — disk is never
        left half-reconciled.
        """
        written: list[FileState] = []
        for before, after in zip(cs.before, cs.after, strict=True):
            if before.data == after.data:
                continue
            try:
                _write_state(after)
            except BaseException:
                for prior in reversed(written):
                    with contextlib.suppress(OSError):
                        _write_state(prior)
                raise
            written.append(before)

    def revert(self, cs: ChangeSet) -> None:
        """Restore every file in ``cs`` to its ``before`` snapshot."""
        for before in cs.before:
            _write_state(before)

    # ── reconciliation ───────────────────────────────────────────────────────

    def _reconciled_capabilities(
        self, raw_before: dict[str, Any] | None, selection: SlotSelection
    ) -> dict[str, Any]:
        """Fold ``selection`` into the capabilities file's canonical shape."""
        from hal0.capabilities.config import (
            CapabilityConfig,
            capabilities_toml_payload,
        )

        cfg = (
            CapabilityConfig.model_validate(raw_before)
            if raw_before is not None
            else CapabilityConfig()
        )
        cfg.selections.setdefault(selection.slot, {})[selection.child] = selection.selection
        return capabilities_toml_payload(cfg)

    def _reconciled_slot(
        self, raw_before: dict[str, Any] | None, selection: CapabilitySelection
    ) -> dict[str, Any] | None:
        """Project an enabled selection onto the existing slot TOML dict.

        Mirrors the semantics of the pre-#697 rewrite-through-
        ``SlotManager.update_config`` path exactly:

          - reconcile only when the selection is enabled (pure disable
            never rewrote the slot file) and the file exists,
          - one-level deep merge for the nested ``[model]`` table so
            sibling keys (``context_size`` etc.) survive,
          - both ``device`` (v0.2 canonical) and ``backend`` (one-release
            legacy alias) written, translated via :mod:`hal0.model_meta`,
          - the ``ctx_size`` → ``context_size`` alias folded (#585) so
            the two keys can't diverge on disk.
        """
        if raw_before is None or not selection.enabled:
            return raw_before

        updates: dict[str, Any] = {}
        slot_backend = device_to_legacy_backend(selection.device)
        slot_device = canonical_device(selection.device)
        if slot_backend:
            # Deprecated field, kept for one release — see ADR-0006 §7.
            updates["backend"] = slot_backend
        if slot_device:
            updates["device"] = slot_device
        if selection.provider:
            updates["provider"] = selection.provider
        if selection.model:
            updates["model"] = {"default": selection.model}
        if not updates:
            return raw_before

        after = dict(raw_before)
        for key, value in updates.items():
            existing = after.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                after[key] = {**existing, **value}
            else:
                after[key] = value

        # #585 parity with SlotManager.update_config: fold the legacy
        # [model].ctx_size alias into the canonical context_size key.
        model = after.get("model")
        if isinstance(model, dict) and "ctx_size" in model:
            model = dict(model)
            model["context_size"] = model.pop("ctx_size")
            after["model"] = model
        return after


# ── file-state IO ────────────────────────────────────────────────────────────


def _read_toml_or_none(path: Path) -> dict[str, Any] | None:
    """Read a TOML file as a raw dict; ``None`` when it doesn't exist."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return None


def _write_state(fs: FileState) -> None:
    """Materialise one snapshot: write its data, or unlink when absent."""
    if fs.data is None:
        fs.path.unlink(missing_ok=True)
        return
    write_toml_atomic(fs.path, fs.data)


__all__ = [
    "ChangeSet",
    "FileState",
    "SlotConfigStore",
    "SlotSelection",
    "write_slot_toml",
]
