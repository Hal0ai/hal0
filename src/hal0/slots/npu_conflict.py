"""Pure rule for NPU exclusion between FLM and per-op ggml-xdna slots (#3666).

The FLM anchor and a per-op xdna slot each need the whole NPU, so they cannot
run together. A fused xdna slot time-slices beside FLM and never conflicts.
Nothing here touches the manager, the filesystem state or the network: callers
pass in a snapshot and get a verdict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hal0.slots import activation
from hal0.slots.npu.trio import is_npu_trio_shadow
from hal0.slots.state import LIVE_COUNTERPART_STATES, SlotState

XdnaPath = Literal["fused", "per-op"]
NpuRole = Literal["flm", "per-op"]

_FUSED_LAYER_KEY = "GGML_XDNA_FUSED_LAYER"

#: A holder in one of these states has a load of its own still running. It
#: counts as ``pending``: the opt-in never unloads around an in-flight load.
NPU_IN_FLIGHT_STATES: frozenset[SlotState] = frozenset(
    {SlotState.PULLING, SlotState.STARTING, SlotState.WARMING}
)

#: States in which a peer holds, or is about to take, the NPU: the live set
#: shared with the fabric-clock guard, plus PULLING (a load that will start).
NPU_HOLDING_STATES: frozenset[SlotState] = LIVE_COUNTERPART_STATES | {SlotState.PULLING}


def xdna_launch_env(cfg: Mapping[str, Any]) -> dict[str, str]:
    """The env an xdna slot launches with: defaults < ``[server].env`` < ``[xdna].env``.

    Same merge order as ``ContainerProvider.load_sync``. The per-op
    ``GGML_XDNA_FUSED_LAYER`` default is applied by the caller, after this.
    """
    from hal0.runners.knobs import screen_xdna_env, xdna_env_defaults

    env: dict[str, Any] = dict(xdna_env_defaults())
    server = cfg.get("server")
    server_env = server.get("env") if isinstance(server, Mapping) else None
    if isinstance(server_env, Mapping):
        env.update(server_env)
    xdna = cfg.get("xdna")
    xdna_env = xdna.get("env") if isinstance(xdna, Mapping) else None
    if isinstance(xdna_env, Mapping):
        screen_xdna_env(xdna_env)
        env.update(xdna_env)
    return env


def effective_xdna_path(env: Mapping[str, str], model_path: str | Path | None) -> XdnaPath:
    """Fused or per-op as the launch will actually run, not the bare model fit.

    ``GGML_XDNA_FUSED_LAYER=0`` in the launch env forces per-op. A missing or
    unreadable model is per-op: the fail-safe side, since per-op excludes FLM.
    """
    # ``[xdna].env`` values are not type-normalised on write, so an integer 0
    # (TOML or JSON) must force per-op as the string "0" does.
    if str(env.get(_FUSED_LAYER_KEY)).strip() == "0":
        return "per-op"
    if not model_path:
        return "per-op"
    from hal0.slots.model_fit import xdna_model_fit_cached

    try:
        fit = xdna_model_fit_cached(model_path)
    except (OSError, ValueError):
        return "per-op"
    return "fused" if fit.xdna_path == "fused" else "per-op"


@dataclass(frozen=True)
class NpuPeer:
    """One other slot, as the rule sees it."""

    name: str
    cfg: Mapping[str, Any]
    state: SlotState
    pinned: bool
    xdna_path: XdnaPath | None


@dataclass(frozen=True)
class NpuConflict:
    """A refused NPU claim: who holds the NPU and what the opt-in would unload."""

    holders: tuple[str, ...]
    holder_engine: Literal["flm", "xdna"]
    unloads: tuple[str, ...]
    pinned: bool
    pending: bool
    reason: str


def npu_role(cfg: Mapping[str, Any], xdna_path: XdnaPath | None) -> NpuRole | None:
    """``"flm"`` for an FLM anchor, ``"per-op"`` for a per-op xdna slot, else ``None``."""
    d = dict(cfg)
    if activation.claims_npu_anchor(d):
        return "flm"
    if activation.is_xdna_slot(d) and xdna_path == "per-op":
        return "per-op"
    return None


def roles_conflict(a: NpuRole | None, b: NpuRole | None) -> bool:
    """True only for the pair {flm, per-op}."""
    return {a, b} == {"flm", "per-op"}


def find_npu_conflict(
    name: str,
    cfg: Mapping[str, Any],
    xdna_path: XdnaPath | None,
    peers: Sequence[NpuPeer],
    pending: Mapping[str, NpuRole],
) -> NpuConflict | None:
    """The conflict ``name`` would cause by loading, or ``None``.

    ``peers`` are the other slots; only those in :data:`NPU_HOLDING_STATES`
    hold the NPU. ``pending`` maps slot name to ``"flm"`` or ``"per-op"`` for
    loads reserved but not yet visible. The verdict is ``pending`` when any
    holder is reserved or in :data:`NPU_IN_FLIGHT_STATES`; UNLOADING is not.
    """
    if is_npu_trio_shadow(dict(cfg)):
        return None
    mine = npu_role(cfg, xdna_path)
    if mine is None:
        return None

    live = [
        p
        for p in peers
        if p.name != name
        and p.state in NPU_HOLDING_STATES
        and roles_conflict(mine, npu_role(p.cfg, p.xdna_path))
    ]
    pend = [n for n, role in pending.items() if n != name and roles_conflict(mine, role)]
    if not live and not pend:
        return None

    holder_role: NpuRole = "per-op" if mine == "flm" else "flm"
    engine: Literal["flm", "xdna"] = "flm" if holder_role == "flm" else "xdna"
    holders = tuple(p.name for p in live) + tuple(
        n for n in pend if n not in {p.name for p in live}
    )
    unloads: list[str] = []
    for holder in holders:
        if holder not in unloads:
            unloads.append(holder)
        if holder_role == "flm":
            for p in peers:
                if p.cfg.get("served_by") == holder and p.name not in unloads:
                    unloads.append(p.name)
    # Any in-flight holder makes the whole conflict pending: the opt-in must
    # never unload around a load still running, published or reserved.
    is_pending = bool(pend) or any(p.state in NPU_IN_FLIGHT_STATES for p in live)
    what = "FLM anchor" if holder_role == "flm" else "per-op xdna slot"
    reason = f"{name} needs the whole NPU, which {what} {', '.join(holders)} holds" + (
        " (load in flight)" if is_pending else ""
    )
    return NpuConflict(
        holders=holders,
        holder_engine=engine,
        unloads=tuple(unloads),
        pinned=any(p.pinned for p in live),
        pending=is_pending,
        reason=reason,
    )
