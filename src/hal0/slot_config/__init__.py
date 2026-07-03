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


# ── the shared slot-projection merge primitive (SC-11) ───────────────────────


def merge_slot_config(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Project ``updates`` onto a slot-config ``base`` dict, copy-safe.

    THE one shared "base dict + updates dict → merged dict" mechanic that
    both the store (:meth:`SlotConfigStore._reconciled_slot`) and
    :meth:`hal0.slots.manager.SlotManager.update_config` used to hand-roll
    (SC-11). It does exactly three things and no field-specific reconciliation
    (the store's selection→updates mapping and the manager's device/profile
    coherence stay with their respective callers):

      - shallow-copies ``base`` so the caller's dict is never mutated,
      - runs a ONE-level-deep merge: for a key present as a dict on both
        sides the sub-tables are merged key-by-key (``{**existing, **value}``,
        so sibling ``[model]`` keys like ``default``/``context_size``
        survive); every other key (scalars, lists, dict-vs-scalar) replaces
        wholesale — value wins,
      - folds the legacy ``[model].ctx_size`` alias into the canonical
        ``context_size`` (#585): a fresh ``ctx_size`` wins over any stale
        ``context_size``, then the alias is dropped so exactly one key
        survives on disk.

    Copy-safety is load-bearing: the store diffs ``before`` against ``after``
    and rolls back to ``before`` on a failed commit, so the returned dict must
    never share the ``[model]`` sub-dict with ``base``. Both the merge path
    (which builds a fresh ``{**existing, **value}``) and the fold path (which
    copies the sub-dict before ``pop``) honour that.
    """
    after = dict(base)
    for key, value in updates.items():
        existing = after.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            after[key] = {**existing, **value}
        else:
            after[key] = value

    # #585: fold the legacy [model].ctx_size alias into the canonical
    # context_size. Copy the sub-dict before mutating so ``base`` (the store's
    # ``before`` snapshot) is never touched even when ``updates`` carried no
    # [model] and ``after["model"]`` is still ``base``'s object.
    model = after.get("model")
    if isinstance(model, dict) and "ctx_size" in model:
        model = dict(model)
        model["context_size"] = model.pop("ctx_size")
        after["model"] = model
    return after


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
             selection **iff** the file already exists (slot creation
             carries state.json + port allocation side effects and stays
             with ``SlotManager.create``, so a missing file yields
             ``after == before``). The ``enabled`` flag is always written
             on both enable and disable; the backend/device/provider/
             model/profile fields are reconciled only when the selection
             is enabled.
        """
        caps_path = self._caps_path()
        caps_before = _read_toml_or_none(caps_path)
        caps_after = self._reconciled_capabilities(caps_before, selection)

        slot_path = self._slot_path(selection.slot_name)
        slot_before = _read_toml_or_none(slot_path)
        slot_after = self._reconciled_slot(slot_before, selection)

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
        self, raw_before: dict[str, Any] | None, slot_selection: SlotSelection
    ) -> dict[str, Any] | None:
        """Project a selection onto the existing slot TOML dict.

        The store is the single owner of the slot's ``enabled`` flag (SC-1):

          - ``enabled`` is written UNCONDITIONALLY when the file exists — a
            disable flips ``enabled = false`` (so the request router stops
            resolving the slot) and an enable clears any stale
            ``enabled = false``,
          - the backend/device/provider/model/profile fields are reconciled
            ONLY when the selection is enabled (a pure disable never rewrites
            the model/device siblings),
          - both ``device`` (v0.2 canonical) and ``backend`` (one-release
            legacy alias) written, translated via :mod:`hal0.model_meta`.

        Once the store-specific ``updates`` dict is built, the actual
        projection — the one-level-deep ``[model]`` merge (so sibling keys
        like ``context_size`` survive) and the ``ctx_size`` → ``context_size``
        fold (#585) — is delegated to the shared, copy-safe
        :func:`merge_slot_config` (SC-11), the same primitive
        :meth:`SlotManager.update_config` uses. Copy-safety matters here: the
        returned ``after`` must not mutate ``raw_before`` (the ChangeSet's
        ``before`` snapshot) or a disable-only reconcile could break the
        commit diff / rollback.

        voice.tts engine switch (follow-up to #972): the two TTS engines
        (Kokoro CPU / Qwen3-TTS GPU) live in the SAME ``tts`` slot, selected
        by device. The device alone is not enough to start the right engine —
        the slot's ``profile`` is what ``container._spec_provider_for`` resolves
        to a runtime family. So for the ``tts`` child we also derive and write
        the engine's ``profile`` (cpu → ``tts``, gpu → ``tts-qwen3``); without
        this the slot would keep Kokoro's profile and never spawn Qwen3.
        """
        selection = slot_selection.selection
        if raw_before is None:
            return raw_before

        # ``enabled`` is written UNCONDITIONALLY — this is the single owner of
        # the slot's enablement (SC-1). A disable must flip ``enabled = false``
        # so the request router (``_loaded_slot_from_config``) stops resolving
        # the slot; an enable clears any stale ``enabled = false``.
        updates: dict[str, Any] = {"enabled": selection.enabled}
        if selection.enabled:
            # The backend/device/provider/model/profile reconciliation only
            # applies when the slot is going to run — a pure disable never
            # rewrites the model/device siblings.
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

            # TTS engine switch: derive the slot profile from the picked device
            # so the GPU/CPU choice actually swaps the provider inside the one
            # tts slot.
            tts_profile = self._tts_profile_for(slot_selection)
            if tts_profile is not None:
                updates["profile"] = tts_profile

        # The one-level [model] merge + #585 ctx_size fold is the shared,
        # copy-safe primitive (SC-11) — ``raw_before`` (the ChangeSet's
        # ``before`` snapshot) is never mutated.
        return merge_slot_config(raw_before, updates)

    @staticmethod
    def _tts_profile_for(slot_selection: SlotSelection) -> str | None:
        """Engine profile a voice.tts selection implies, or ``None``.

        Only the ``tts`` child carries an engine switch — every other
        capability leaves the slot's profile untouched (most have none). The
        canonical (device → profile) mapping lives in
        :func:`hal0.capabilities.catalog.tts_profile_for_device`; imported
        lazily here to keep this module import-light (the cycle the module
        docstring guards against).
        """
        if slot_selection.child != "tts":
            return None
        from hal0.capabilities.catalog import tts_profile_for_device

        return tts_profile_for_device(slot_selection.selection.device)


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
    "merge_slot_config",
    "write_slot_toml",
]
