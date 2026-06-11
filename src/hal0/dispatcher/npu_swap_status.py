"""NPU trio chat-model swap-in-progress detection — PR-20.

When the operator picks a new NPU chat model in the dashboard, the
underlying FLM trio must:

  1. Persist the new model on the ``device=npu, type=llm`` slot's TOML.
  2. Issue a ``/v1/load`` against Lemonade with the new model name.
  3. Lemonade evicts the previous FLM process and spins up a new one.
  4. The NEW model name appears in ``GET /v1/health.loaded[]``.

Steps (2)→(4) take ~14s on Strix Halo (cold NPU + ASR + embed warm-up
all together; see plan §5.3, ADR-0009). For the dashboard to render a
"swap incoming" banner + spinner, hal0 needs a single signal that says
"this is the swap window".

The signal we publish:

  - capabilities.toml — actually here we read the *slot TOMLs* — has a
    ``device=npu, type=llm, enabled=true`` slot whose ``model.default``
    is NOT currently listed in Lemonade's ``loaded[]`` AND there IS at
    least one other ``recipe=flm`` entry in ``loaded[]`` (i.e., the
    OLD trio chat is still serving while the new one warms up).

If no FLM is currently loaded at all (``loaded[]`` has no ``recipe=flm``
entries), this is NOT a swap — it's a fresh first load. The dashboard
already handles fresh loads via the slot lifecycle dot; we only surface
the banner when the trio is actually mid-transition.

This module is *pure*: no caching, no side effects. The status endpoint
calls :func:`compute_npu_swap_status` once per request; the helper reads
the configured NPU LLM slot list from the SlotManager and the loaded
list from the LemonadeClient.

Per ADR-0008 §5 + plan §5.3, only one NPU LLM slot may be enabled at a
time. PR-11's :meth:`hal0.slots.manager.SlotManager._check_npu_exclusivity`
guards the *write* path; this module observes the *runtime* state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from hal0.dispatcher._npu_common import is_container_npu_cfg
from hal0.lemonade.errors import LemonadeError
from hal0.slots.state import SlotState

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NpuSwapStatus:
    """Snapshot of the NPU trio swap state.

    Attributes:
        in_progress: True iff the configured NPU LLM model is NOT in
            Lemonade's ``loaded[]`` AND a different FLM-recipe entry IS
            loaded (the previous chat model still serving).
        from_model: The model_name of the currently-loaded FLM chat
            entry, if any (the "from" side of the swap). ``None`` when
            no FLM is loaded at all.
        to_model: The model_name configured on the enabled NPU LLM
            slot (the "to" side of the swap). ``None`` when no NPU
            LLM slot is enabled, or when its ``model.default`` is empty.
    """

    in_progress: bool
    from_model: str | None
    to_model: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_progress": self.in_progress,
            "from_model": self.from_model,
            "to_model": self.to_model,
        }


def _flm_loaded_entries(health: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull every ``recipe=flm`` entry out of /v1/health.

    Accepts both ``loaded`` and ``all_models_loaded`` keys — same forward-
    compat dance as :class:`hal0.dispatcher.flm_trio.FLMTrioRouter` —
    and de-dupes by ``backend_url`` (some Lemonade versions emit the
    chat under both keys).
    """
    seen_urls: set[str] = set()
    out: list[dict[str, Any]] = []
    for key in ("loaded", "all_models_loaded"):
        entries = health.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("recipe") != "flm":
                continue
            url = entry.get("backend_url")
            url_key = url if isinstance(url, str) else ""
            if url_key and url_key in seen_urls:
                continue
            if url_key:
                seen_urls.add(url_key)
            out.append(entry)
    return out


def _enabled_npu_llm_slot(slot_configs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the (at most one) enabled NPU LLM slot config, or None.

    PR-11's exclusivity validation guarantees at most one such slot
    exists on disk; we walk the list defensively anyway and return the
    first match. A multi-match would itself be a bug, surfaced upstream
    by the validator the next time the operator saves.
    """
    for cfg in slot_configs:
        if cfg.get("device") != "npu":
            continue
        if cfg.get("type") != "llm":
            continue
        if cfg.get("enabled") is False:
            continue
        return cfg
    return None


def _slot_model_default(slot_cfg: dict[str, Any]) -> str:
    """Pull ``model.default`` out of a slot config dict."""
    model_section = slot_cfg.get("model")
    if isinstance(model_section, dict):
        default = model_section.get("default")
        if isinstance(default, str):
            return default
    return ""


def compute_npu_swap_status(
    slot_configs: list[dict[str, Any]],
    health: dict[str, Any] | None,
) -> NpuSwapStatus:
    """Decide whether an NPU chat-model swap is in progress.

    Pure function: every input is an explicit argument so the caller
    can mock both the slot TOML state and the /v1/health response.

    Args:
        slot_configs: The list of slot config dicts (as returned by
            :func:`hal0.api.routes.slots._all_slot_configs` or similar).
            Only the ``device``, ``type``, ``enabled`` and ``model``
            keys are read.
        health: Parsed ``GET /v1/health`` body, or ``None`` when the
            probe failed. ``None`` is treated as "no FLM loaded" — same
            as a successful probe that returned an empty ``loaded[]``.

    Returns:
        :class:`NpuSwapStatus` snapshot. ``in_progress=True`` only when
        all of:

          - An enabled NPU LLM slot exists.
          - Its ``model.default`` is set.
          - At least one ``recipe=flm`` entry is loaded.
          - That entry's ``model_name`` is *different* from the slot's
            configured ``model.default``.

        Any other combination (no NPU slot; matching model already
        loaded; no FLM loaded at all) yields ``in_progress=False``.
        ``from_model`` / ``to_model`` are populated when known, even
        when ``in_progress=False`` — the dashboard uses them to render
        the "current model" string under the chat sub-row.
    """
    npu_slot = _enabled_npu_llm_slot(slot_configs)
    to_model = _slot_model_default(npu_slot) if npu_slot is not None else None
    if to_model == "":
        to_model = None

    flm_entries = _flm_loaded_entries(health) if isinstance(health, dict) else []
    from_model: str | None = None
    for entry in flm_entries:
        name = entry.get("model_name")
        if isinstance(name, str) and name:
            from_model = name
            break

    # Not configured, or nothing to swap to → not a swap.
    if to_model is None:
        return NpuSwapStatus(in_progress=False, from_model=from_model, to_model=None)

    # Configured + nothing loaded → fresh first load, not a swap.
    if from_model is None:
        return NpuSwapStatus(in_progress=False, from_model=None, to_model=to_model)

    # Configured + already loaded the SAME model → steady state.
    if from_model == to_model:
        return NpuSwapStatus(in_progress=False, from_model=from_model, to_model=to_model)

    # Configured + an FLM is loaded + that FLM is a DIFFERENT model →
    # swap window.
    return NpuSwapStatus(in_progress=True, from_model=from_model, to_model=to_model)


#: SlotState values that indicate a container NPU slot is mid-transition
#: (model swap in progress: container restarting/loading new model).
_TRANSITIONAL_STATES: frozenset[str] = frozenset(
    {
        SlotState.PULLING.value,
        SlotState.STARTING.value,
        SlotState.WARMING.value,
        SlotState.UNLOADING.value,
    }
)


async def _container_npu_swap_status(
    slot_configs: list[dict[str, Any]],
    slot_manager: Any,
) -> NpuSwapStatus | None:
    """Return swap status from container slot state, or None if not applicable.

    Returns a :class:`NpuSwapStatus` when the enabled NPU LLM slot is a
    container slot (Phase A); returns ``None`` to signal "fall through to
    the lemond path" when no container NPU slot is found or any accessor
    raises.

    Transitional states (PULLING/STARTING/WARMING/UNLOADING) map to
    ``in_progress=True`` (a model swap = container restart). Settled
    states (READY/SERVING) map to ``in_progress=False``. IDLE/OFFLINE/ERROR
    are settled states that map to ``in_progress=False`` (consistent with
    the lemond path: no swap signalled when the slot is down or idle).

    The ``to_model`` is the slot's ``model.default`` (the configured target).
    The ``from_model`` is ``None`` in the container path — there is no
    "previously loaded" signal from a container (unlike the lemond path
    where the old FLM child is still serving). This mirrors the lemond
    path's "fresh first load" semantics: the dashboard shows the banner
    but cannot name the outgoing model.
    """
    npu_slot_cfg = _enabled_npu_llm_slot(slot_configs)
    if npu_slot_cfg is None:
        return None
    if not is_container_npu_cfg(npu_slot_cfg):
        return None

    to_model = _slot_model_default(npu_slot_cfg) or None

    try:
        slot = await slot_manager.status(npu_slot_cfg.get("name") or "npu")
        state_val = slot.state.value
    except Exception as exc:
        log.debug(
            "npu_swap.container_status_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        # Can't read state → treat as settled (no swap).
        return NpuSwapStatus(in_progress=False, from_model=None, to_model=to_model)

    in_progress = state_val in _TRANSITIONAL_STATES
    return NpuSwapStatus(in_progress=in_progress, from_model=None, to_model=to_model)


async def fetch_npu_swap_status(
    slot_configs: list[dict[str, Any]],
    lemonade_client: Any,
    *,
    slot_manager: Any | None = None,
) -> NpuSwapStatus:
    """Async wrapper that probes ``/v1/health`` and runs the pure helper.

    Phase A: when the enabled NPU LLM slot is a container slot, swap
    detection reads the slot's lifecycle state instead of diffing Lemonade's
    ``/v1/health`` loaded list (a swap = container restart, so state
    transitions signal the swap window directly). The lemond diff path is
    kept as fallback for non-container (Lemonade-managed) NPU slots and will
    be removed in Phase E.

    Catches :class:`hal0.lemonade.errors.LemonadeError` and any other
    exception from the probe, degrading to ``health=None``. The
    dashboard NEVER wants a swap-status 503 to cascade — when lemond
    is unreachable, the global ``lemond-offline`` banner already covers
    the surface, and the swap banner should fall back to "no swap" so
    the operator isn't told a swap is in progress while the daemon is
    down.
    """
    # Phase A: container path — slot state drives swap signal.
    if slot_manager is not None:
        container_status = await _container_npu_swap_status(slot_configs, slot_manager)
        if container_status is not None:
            return container_status

    # Legacy lemond diff path — non-container NPU slots (Phase E removes this).
    health: dict[str, Any] | None = None
    if lemonade_client is not None:
        try:
            probe = await lemonade_client.health()
            if isinstance(probe, dict):
                health = probe
        except LemonadeError as exc:
            log.debug(
                "npu_swap.health_unavailable",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "npu_swap.health_unexpected_error",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
    return compute_npu_swap_status(slot_configs, health)


__all__ = [
    "NpuSwapStatus",
    "compute_npu_swap_status",
    "fetch_npu_swap_status",
]
