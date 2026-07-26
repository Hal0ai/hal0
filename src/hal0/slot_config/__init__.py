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

Device normalization is **imported from** :mod:`hal0.model_meta`
(:func:`canonical_device`) — this module re-derives no classification
logic.

:func:`write_slot_toml` is the single low-level write path for
``slots/*.toml``; every writer (SlotManager.create/update_config, the
installer's pick-default seed, the model-delete cascade) routes its
bytes through it.
"""

from __future__ import annotations

import contextlib
import logging
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hal0.config import paths
from hal0.config.loader import write_toml_atomic
from hal0.config.locking import file_lock
from hal0.model_meta import canonical_device

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

    Cross-process serialization is the CALLER's job: wrap the whole
    read-modify-write (not just this write) in :func:`slot_write_lock`.
    """
    write_toml_atomic(Path(path), data)


@contextlib.contextmanager
def slot_write_lock(slots_dir: Path | str | None = None) -> Iterator[None]:
    """Hold the coarse cross-process lock for ALL slots/*.toml writes.

    Historically :meth:`SlotConfigStore.transaction` guarded only
    ``capabilities.toml``, so slot-TOML read-modify-writes from the API
    (``SlotManager.update_config`` / ``create`` / ``_persist_model_default``),
    the CLI, and the stacks apply engine could interleave and drop each
    other's change. Every slot-TOML RMW now opts into this ONE advisory
    lock — a single lock file (``<slots_dir>.lock``, i.e. the sibling of
    the slots config dir) covering every slot file. Deliberately coarse:
    slot writes are rare, tiny, and a per-file lock would buy nothing but
    ordering headaches.

    Same re-entrancy contract as :func:`hal0.config.locking.file_lock`
    (per-thread re-entrant; serializing across threads and processes).
    IMPORTANT: do not ``await`` anything that can actually suspend while
    holding it — a second coroutine on the same thread would observe the
    thread-local re-entrancy depth and walk straight in.

    ``slots_dir`` overrides the default ``paths.slots_config_dir()`` for
    stores constructed against a custom directory (tests).
    """
    base = Path(slots_dir) if slots_dir is not None else paths.slots_config_dir()
    with file_lock(base):
        yield


def fold_ctx_size_alias(cfg_dict: dict[str, Any]) -> None:
    """Fold the legacy ``[model].ctx_size`` alias into the canonical
    ``context_size`` (#585) — THE single normalization point.

    The dashboard slot-edit panel writes ``ctx_size``; the load path reads
    ``context_size``. Persisting both lets them silently diverge. A fresh
    ``ctx_size`` (the operator's latest write) wins over any stale
    ``context_size``, then the alias is dropped so exactly one key survives
    on disk. No-op when ``ctx_size`` is absent.

    Copy-safe on the nested table: the ``[model]`` sub-dict is copied
    before mutation and rebound onto ``cfg_dict``, so callers that share
    the sub-dict with a "before" snapshot (the store's ChangeSet) are
    never corrupted. ``cfg_dict`` itself is mutated in place.

    Used by :func:`merge_slot_config` (which covers
    ``SlotManager.update_config`` and the store) and by
    ``SlotManager.create`` — previously three separate copies of this fold
    existed and could drift.
    """
    model = cfg_dict.get("model")
    if isinstance(model, dict) and "ctx_size" in model:
        model = dict(model)
        model["context_size"] = model.pop("ctx_size")
        cfg_dict["model"] = model


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
      - treats an explicit ``None`` value in ``updates`` as **delete the key**
        (top level and one level deep). TOML has no null — ``tomli_w`` raises
        ``TypeError`` on a ``None`` value, so "set to null" could never be
        persisted anyway; absent IS null on this surface. This is what lets
        ``PUT /config {"mtp": null}`` return a slot to MTP AUTO (the tri-state
        default) instead of 500ing in the TOML writer,
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
        if value is None:
            # None = delete: remove the key so the TOML writer never sees a
            # null (tomli_w TypeError). Deleting a missing key is a no-op.
            after.pop(key, None)
        elif isinstance(existing, dict) and isinstance(value, dict):
            sub = {**existing, **value}
            # Same None-deletes rule one level deep ({"server": {"extra_args": null}}).
            after[key] = {k: v for k, v in sub.items() if v is not None}
        else:
            after[key] = value

    # #585: fold the legacy [model].ctx_size alias into the canonical
    # context_size via the ONE shared fold (:func:`fold_ctx_size_alias`).
    # The fold copies the sub-dict before mutating so ``base`` (the store's
    # ``before`` snapshot) is never touched even when ``updates`` carried no
    # [model] and ``after["model"]`` is still ``base``'s object.
    fold_ctx_size_alias(after)
    return after


# ── boundary validation for slot-config writes ───────────────────────────────

#: Top-level keys that are NOT declared on ``SlotConfig`` (they round-trip
#: through its ``extra="allow"``) but are documented, actively-read parts of
#: the slot-TOML surface. The unknown-key validator tolerates exactly these;
#: anything else at the top level is treated as a typo.
#:
#:   - ``type``           — slot kind (llm/embedding/…), read all over manager/routing.
#:   - ``default``        — SC-4 per-type default flag, read by default_slot_for.
#:   - ``lru``            — pressure-eviction eligibility (#903).
#:   - ``default_voice`` / ``default_language`` — TTS engine extras written by
#:     the dashboard voice settings (PUT /api/slots/tts/config).
#:   - ``slot``           — the nested on-disk [slot] table shape (hoisted on load).
TOLERATED_SLOT_CONFIG_KEYS: frozenset[str] = frozenset(
    # image_pin and binary are declared SlotConfig fields but the running backend may
    # predate them — tolerate so the drawer Save always works.
    {"type", "default", "lru", "default_voice", "default_language", "slot", "image_pin", "binary"}
)

#: Extra keys tolerated inside specific sub-tables, keyed by sub-table name.
#: ``[model].ctx_size`` is the documented legacy alias of ``context_size``
#: (#585) — accepted on write, folded by :func:`fold_ctx_size_alias`.
_TOLERATED_SUBTABLE_KEYS: dict[str, frozenset[str]] = {
    "model": frozenset({"ctx_size"}),
}


def _known_field_names(model_cls: Any) -> set[str]:
    """Field names + aliases a pydantic model accepts — derived dynamically.

    Reading ``model_fields`` (not a hardcoded list) means a field another
    change adds to the schema (e.g. ``ServerConfig.env``) passes validation
    automatically, with no second list to keep in sync.
    """
    names: set[str] = set()
    for name, field in model_cls.model_fields.items():
        names.add(name)
        alias = getattr(field, "alias", None)
        if alias:
            names.add(alias)
    return names


def unknown_slot_config_keys(payload: dict[str, Any]) -> list[str]:
    """Dotted paths of payload keys the slot-config schema does not know.

    ``SlotConfig`` and its sub-models are ``extra="allow"`` so a typo'd key
    (``ctx_sizee``, ``enabeld``) round-trips to disk silently and the
    intended setting never takes effect. This walks ``payload`` (a partial
    slot-config write body: PUT /config, PATCH /defaults wrapped under
    ``model``, POST create) against the pydantic field sets of
    ``SlotConfig`` / ``ModelConfig`` / ``ServerConfig`` / ``NpuConfig`` /
    ``ImageGenConfig`` and returns the paths of unknown keys, sorted.

    Tolerated beyond the declared fields:
      - documented legacy aliases: ``[model].ctx_size``, top-level
        ``provider`` / ``runtime`` (declared deprecated fields), a
        *string* ``image`` (per-slot container-image override). NOTE
        (P2-device): ``backend`` is no longer a declared ``SlotConfig``
        field nor tolerated here — a write payload carrying it is now
        flagged as unknown; ``device`` is the sole persisted truth.
      - the actively-read extras in :data:`TOLERATED_SLOT_CONFIG_KEYS`,
      - ``extra`` tables at any level (verbatim provider passthrough by
        contract — never validated).

    Known fields are derived from ``model_fields`` dynamically, so schema
    additions (e.g. ``[server].env``) pass without touching this module.
    """
    from hal0.config.schema import (
        ImageGenConfig,
        ModelConfig,
        NpuConfig,
        ServerConfig,
        SlotConfig,
    )

    sub_models: dict[str, Any] = {
        "model": ModelConfig,
        "server": ServerConfig,
        "npu": NpuConfig,
        "image": ImageGenConfig,
        "image_gen": ImageGenConfig,
    }
    top_known = _known_field_names(SlotConfig) | TOLERATED_SLOT_CONFIG_KEYS
    unknown: list[str] = []

    def _check_subtable(prefix: str, table: dict[str, Any], model_cls: Any, name: str) -> None:
        known = _known_field_names(model_cls) | _TOLERATED_SUBTABLE_KEYS.get(name, frozenset())
        for key in table:
            if key not in known:
                unknown.append(f"{prefix}{key}")

    def _check_top(prefix: str, mapping: dict[str, Any]) -> None:
        for key, value in mapping.items():
            if key not in top_known:
                unknown.append(f"{prefix}{key}")
                continue
            if key == "extra":
                continue  # verbatim passthrough by contract
            if key == "slot" and isinstance(value, dict):
                # Nested on-disk [slot] table — same vocabulary as top level.
                for sub_key in value:
                    if sub_key not in top_known or sub_key == "slot":
                        unknown.append(f"{prefix}slot.{sub_key}")
                continue
            model_cls = sub_models.get(key)
            if model_cls is not None and isinstance(value, dict):
                _check_subtable(f"{prefix}{key}.", value, model_cls, key)
            # A non-dict ``image`` is the legacy string container-image
            # override — tolerated as-is (parked under extra on load).

    _check_top("", payload)
    return sorted(set(unknown))


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

    # ── cross-process serialized RMW (SC-10) ─────────────────────────────────

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold the capabilities.toml cross-process lock for the body.

        Every capabilities.toml writer (this store, the orchestrator seed,
        the CLI migrate, the schema auto-migration) serializes on the SAME
        advisory lock — the sibling ``<capabilities.toml>.lock`` — so a
        concurrent read-modify-write from the API and the CLI cannot
        interleave and drop each other's change (SC-10). The lock is
        re-entrant within a thread, so a caller already inside ``transaction``
        may nest a locked leaf writer without self-deadlocking.

        The store's commit also writes ``slots/*.toml``, so the body
        additionally holds the coarse slot-TOML write lock
        (:func:`slot_write_lock`) — a stack apply / capability apply and a
        concurrent ``SlotManager.update_config`` (which takes only the slot
        lock) serialize instead of interleaving their slot-file writes.
        Lock order is always capabilities → slots; no slot-lock holder ever
        acquires the capabilities lock, so the ordering cannot invert.
        """
        with file_lock(self._caps_path()), slot_write_lock(self._slots_dir):
            yield

    def apply_and_commit(self, selection: SlotSelection) -> ChangeSet:
        """Atomically read, reconcile and write ``selection`` under the lock.

        The authoritative ``before`` snapshot (``apply``) and the ``after``
        write (``commit``) happen inside one held lock so the whole
        read-modify-write is a single critical section. Locking ``commit``
        alone would NOT fix the lost update — the stale ``before`` would
        already have been read outside the lock. Callers that need an earlier
        ChangeSet for a dry-run/telemetry display should recompute the
        committed one here, under the lock.
        """
        with self.transaction():
            cs = self.apply(selection)
            self.commit(cs)
            return cs

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
        the engine's ``profile`` (cpu → ``kokoro``, gpu → ``qwen3-tts``); without
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
            # The device/provider/model/profile reconciliation only
            # applies when the slot is going to run — a pure disable never
            # rewrites the model/device siblings.
            slot_device = canonical_device(selection.device)
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
    "TOLERATED_SLOT_CONFIG_KEYS",
    "ChangeSet",
    "FileState",
    "SlotConfigStore",
    "SlotSelection",
    "fold_ctx_size_alias",
    "merge_slot_config",
    "slot_write_lock",
    "unknown_slot_config_keys",
    "write_slot_toml",
]
