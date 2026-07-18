"""Intent-oriented DEEP interface for the slot module (REWORK.md §E, board #4).

Where :class:`hal0.slots.manager.SlotManager` exposes a *wide* verb surface
(``load`` / ``unload`` / ``swap`` / ``status`` / ``create`` / ``update_config`` /
``delete`` / ``state_stream`` / …) each mapping 1:1 to a CLI subcommand or an API
route, :class:`SlotInterface` is the *narrow, deep* counterpart: four intent
verbs keyed by the slot's **stable id**, each hiding a cluster of mechanism the
wide surface leaks.

    inspect(slot_id)            -> SlotSnapshot        (one read, every source)
    apply(slot_id, desired)     -> SlotSnapshot        (reconcile to intent)
    delete(slot_id)             -> None                (compose the delete path)
    subscribe()                 -> AsyncIterator[...]  (the state-change stream)

What the interface HIDES (all of it lives in the manager already; this module
orchestrates, it does not reimplement):

  * state-machine transitions          (``_transition`` / ``load`` / ``unload``)
  * two-way drift reconciliation       (``status`` unit-vs-record adoption)
  * port claims                        (``PortAuthority.held_by``)
  * unit generation + runtime launch   (``_spawn_locked`` via ``load``)
  * eviction / idle demotion           (reaper — surfaced only as IDLE state)
  * failure watching                   (watchdog — surfaced as ERROR + message)
  * model resolution                   (``_resolve_servable_model`` /
                                        ``_resolve_model_info``)

DESIGN — why a facade object, not more methods on SlotManager
-------------------------------------------------------------
The intent verbs must *compose* a dozen manager internals, several private
(``_identity_row``, ``_resolve_model_info``, ``_port_authority``). A sibling
module reaching across those underscores would be brittle; free functions would
need the manager threaded through every call. A thin facade *bound to one
manager* keeps the composition inside the ``slots`` package, gives the four
verbs a home that can't collide with the wide surface (the manager already has a
name-keyed ``delete`` — the facade's id-keyed ``delete`` is a distinct product),
and reads as the module's deep API: ``manager.interface.apply(id, desired)``.

The facade is **id-keyed on purpose**: the deep interface speaks the stable
identity (``slot_id``), not the mutable name-label. It therefore requires an
identity store to be wired (the API lifespan wires one; a bare ``SlotManager()``
does not). ``slot_id_to_name`` is the single id→name chokepoint.

ADDITIVE (HARD REQ #1): every existing manager method keeps its exact signature.
Collapsing the wide surface into these four verbs is a later increment under
expiring-shim discipline (REWORK.md principle 6) — NOT done here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hal0.slots._cfg_helpers import _model_default
from hal0.slots.state import DISPATCHABLE_STATES, SlotState, SlotStateRecord

if TYPE_CHECKING:
    from hal0.slots.manager import SlotManager

# A slot is "loaded" (has a live runtime the interface must tear down before an
# unload/delete, or leave alone on an idempotent re-apply) exactly in the
# dispatchable ready-set. Reuse the ONE canonical definition (finding DR-8) so
# this module never re-declares the literal.
_LOADED_STATES = DISPATCHABLE_STATES


@dataclass(frozen=True, slots=True)
class SlotSnapshot:
    """Typed, one-shot inspection of a slot — HARD REQ #2.

    Assembled from the manager's scattered read paths in a single ``inspect``
    call: the persisted+reconciled runtime state (``status``), the on-disk
    launch config (``get_config``), the authoritative port claim
    (``PortAuthority.held_by``), the resolved model metadata
    (``_resolve_model_info``), and the last failure message. One call replaces
    "read state.json, then re-parse the TOML, then ask the authority, then hit
    the registry" scattered across today's callers.
    """

    slot_id: int
    name: str
    state: SlotState
    #: On-disk launch config (read-only view; the TOML the unit renders from).
    config: dict[str, Any]
    #: Effective port the snapshot advertises (claim if authoritative, else the
    #: state-record / config port).
    port: int
    #: Authoritative live port claim from :class:`PortAuthority`, or ``None``
    #: when no authority is wired (bare manager) or the slot holds no claim.
    port_claim: int | None
    #: Model id the slot is currently assigned / running.
    model_id: str | None
    #: Resolved model metadata (registry dump + FLM tag), ``{}`` when unresolvable.
    model_resolution: dict[str, Any]
    backend: str | None
    #: Last failure message — populated only while the slot is in ERROR.
    last_failure: str | None
    last_used_at: float | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe projection (mirrors :meth:`Slot.as_dict` conventions)."""
        return {
            "id": self.slot_id,
            "name": self.name,
            "state": self.state.value,
            "config": self.config,
            "port": self.port,
            "port_claim": self.port_claim,
            "model_id": self.model_id,
            "model_resolution": self.model_resolution,
            "backend": self.backend,
            "last_failure": self.last_failure,
            "last_used_at": self.last_used_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class DesiredSlotState:
    """Declarative target a slot is reconciled toward — HARD REQ #3.

    A desired state is *intent*, not commands: it names WHERE the slot should
    be, and ``apply`` computes the transitions to get there from wherever it is.

    Fields:
      * ``loaded`` — the lifecycle target. ``True`` = a live, dispatchable
        runtime (READY/SERVING/IDLE all satisfy it); ``False`` = OFFLINE.
      * ``model`` — desired model id, or ``None`` to retain the current
        assignment. A change on a live slot is reconciled by a hot ``swap``.
      * ``config`` — materialized non-model launch fields (a partial slot-TOML
        update: ``device`` / ``profile`` / ``image`` / ``[server]`` / …), or
        ``None`` for no change. Applied through ``update_config`` (the one
        apply engine), so device/profile coherence and the NPU/default guards
        run exactly as for a direct edit.

    Idempotence (HARD REQ #3): applying the state a slot is already in is a
    no-op — the config diff is empty, the model matches, and the lifecycle
    target is already satisfied, so ``apply`` issues zero transitions.

    FLAGS-own narrowing (docs/rework/hal0-specs/spec-flags-ownership.md §4) —
    NOT implemented here, documented so the schema's future shape is legible:
    under FLAGS-own a slot collapses to ``(slot_id, name-label, model ref,
    port [authority], lifecycle state)``. Launch flags attach to the *model*,
    not the slot; ``profile``/``device``/backend selection ride the model's
    materialized tune + runner registry. When that lands, the free-form
    ``config`` dict narrows to just the model ref (already ``.model`` here) plus
    the name-label — the remaining launch fields become model-owned or
    authority-computed, and passing an authority-managed field (port, unit
    name, derived backend) in ``config`` will be rejected at the seam rather
    than silently written. ``.loaded`` and ``.model`` are already FLAGS-own
    shaped; only ``.config`` shrinks.
    """

    loaded: bool
    model: str | None = None
    config: dict[str, Any] | None = None


class SlotInterface:
    """Deep, id-keyed intent surface bound to one :class:`SlotManager`.

    Constructed lazily via ``SlotManager.interface``; holds no state of its own
    beyond the manager back-reference — every read and mutation delegates to the
    manager's existing internals.
    """

    __slots__ = ("_m",)

    def __init__(self, manager: SlotManager) -> None:
        self._m = manager

    # ── id-keyed entry ────────────────────────────────────────────────────────

    def _name(self, slot_id: int) -> str:
        """Resolve an opaque slot id to its current name (raises SlotNotFound).

        The single id→name chokepoint for the whole interface; every verb goes
        through it so the deep surface is uniformly keyed on stable identity.
        """
        return self._m.slot_id_to_name(slot_id)

    # ── inspect ───────────────────────────────────────────────────────────────

    async def inspect(self, slot_id: int) -> SlotSnapshot:
        """One typed snapshot assembling every scattered read for *slot_id*."""
        name = self._name(slot_id)
        # Runtime state (persisted + reconciled against unit reality).
        snap = await self._m.status(name)
        # On-disk launch config (synthesized from state.json for in-memory slots).
        config = await self._m.get_config(name)
        # Resolved model metadata (registry dump + FLM tag).
        model_resolution = await self._m._resolve_model_info(snap.model_id)
        # Authoritative port claim, if a PortAuthority is wired.
        port_claim = self._port_claim(slot_id, name)
        # Last failure surfaces only while the slot is wedged in ERROR.
        last_failure = (
            snap.metadata.get("message") if snap.state == SlotState.ERROR else None
        ) or None
        return SlotSnapshot(
            slot_id=slot_id,
            name=name,
            state=snap.state,
            config=config,
            port=port_claim if port_claim is not None else snap.port,
            port_claim=port_claim,
            model_id=snap.model_id,
            model_resolution=model_resolution,
            backend=snap.backend,
            last_failure=last_failure,
            last_used_at=snap.last_used_at,
            metadata=snap.metadata,
        )

    def _port_claim(self, slot_id: int, name: str) -> int | None:
        """The slot's live authoritative port claim, or ``None``."""
        authority = self._m._port_authority
        if authority is None:
            return None
        # A device=npu shadow shares its anchor's claim via the coresident
        # group; carry the row's group so held_by resolves the shared port.
        row = self._m._identity_row(name)
        grp = getattr(row, "coresident_group", None) if row is not None else None
        try:
            return authority.held_by(slot_id, coresident_group=grp)
        except Exception:  # pragma: no cover - defensive; a claim read must not
            return None  # break inspect

    # ── apply ─────────────────────────────────────────────────────────────────

    async def apply(self, slot_id: int, desired: DesiredSlotState) -> SlotSnapshot:
        """Reconcile the slot toward *desired*, orchestrating existing paths.

        Order: materialize declared launch config first (so a subsequent load /
        restart renders the unit from the new TOML), then converge the lifecycle
        target. Every mutation is an existing manager verb — this method issues
        no transition of its own.
        """
        name = self._name(slot_id)
        current_cfg = await self._m.get_config(name)

        # 1. Materialize declared non-model launch fields (idempotent: skip when
        #    the update would change nothing). update_config is the one apply
        #    engine — coherence + NPU/default guards ride along.
        config_changed = False
        if desired.config and self._would_change(current_cfg, desired.config):
            await self._m.update_config(name, desired.config)
            config_changed = True

        # 2. Converge the lifecycle target.
        state = self._m.state(name)
        loaded = state in _LOADED_STATES
        current_model = _model_default(current_cfg)
        model_changes = desired.model is not None and desired.model != current_model

        if desired.loaded:
            if not loaded:
                if state == SlotState.ERROR:
                    # A wedged unit must not go through the graceful load path —
                    # restart force-clears it, then swap re-points the model if
                    # the desired model differs from the persisted default.
                    await self._m.restart(name)
                    if model_changes:
                        await self._m.swap(name, desired.model)  # type: ignore[arg-type]
                else:
                    await self._m.load(name, model_id=desired.model)
            elif model_changes:
                # Live slot, model drift → hot swap (unload+load onto new model,
                # adopting its preferred profile/runner).
                await self._m.swap(name, desired.model)  # type: ignore[arg-type]
            elif config_changed:
                # Live slot, launch-field drift → reload to materialize it.
                await self._m.restart(name)
            # else: already converged → no-op (idempotent path).
        elif loaded:
            await self._m.unload(name)
        # else: already offline → no-op.

        return await self.inspect(slot_id)

    @staticmethod
    def _would_change(current: dict[str, Any], updates: dict[str, Any]) -> bool:
        """True when applying *updates* over *current* would alter any field.

        Mirrors ``update_config``'s one-level-deep merge for nested TOML tables
        so the idempotence check matches what the write would actually do.
        """
        for k, v in updates.items():
            cur = current.get(k)
            if isinstance(v, dict) and isinstance(cur, dict):
                if any(cur.get(sk) != sv for sk, sv in v.items()):
                    return True
            elif cur != v:
                return True
        return False

    # ── delete ────────────────────────────────────────────────────────────────

    async def delete(self, slot_id: int, *, force: bool = False) -> None:
        """Delete the slot behind *slot_id* — HARD REQ #4.

        Composes the existing delete path (unload if live → port release →
        identity-row drop → unit/TOML/state removal). ``force`` carries through
        the manager's seeded/pinned guard exactly as the name-keyed ``delete``.
        """
        name = self._name(slot_id)
        await self._m.delete(name, force=force)

    # ── subscribe ─────────────────────────────────────────────────────────────

    def subscribe(self) -> AsyncIterator[SlotStateRecord]:
        """The slot state-change stream — HARD REQ #5.

        A thin wrap over the manager's existing fan-out ``state_stream`` (each
        subscriber gets its own buffered queue); no new bus is introduced. Every
        transition across ALL slots is delivered as a :class:`SlotStateRecord`.
        """
        return self._m.state_stream()
