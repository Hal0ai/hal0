"""Slot port allocation + conflict rejection.

Extracted from ``routes/slots.py`` (P3-routers §J) so the route layer is a thin
request→service→envelope shell over the central port registry (:mod:`hal0.ports`).

MERGE TARGET: rework §11.2 PortAuthority will absorb this module — the
PortAuthority PR owns the merge. Per that coordination the names + signatures
here are kept exactly as the route layer had them (``routes/slots`` re-exports
the underscore-named originals so ``routes/ports``, ``capabilities/orchestrator``
and the test-suite keep resolving them); do NOT reshape this API.

Interface contract:

    slot_port_range() -> tuple[int, int]
        The configured slot auto-allocation pool (hal0.toml ``[slots]`` or the
        schema defaults). Falls back to the pool constants on read failure.
    collect_port_claims(start, end, slot_snapshots=None) -> list
        Every known claim in the pool via :func:`hal0.ports.collect_claims`.
    next_free_slot_port(start=None, end=None, slot_snapshots=None) -> int
        Next free port in the configured range; raises ``BadRequest``
        (``slot.no_free_port``) when the pool is exhausted.
    reject_port_conflict(port, owner_slot, slot_snapshots=None) -> None
        Raises ``BadRequest`` (``slot.port_conflict``) when an explicitly
        requested port is already owned by another slot/listener.
"""

from __future__ import annotations

from hal0.api.middleware.error_codes import BadRequest


def slot_port_range() -> tuple[int, int]:
    """Resolve the slot port pool: hal0.toml ``[slots]`` or the schema pool.

    ``SlotsConfig.port_range_start/end`` default to ``_SLOT_PORT_MIN`` ..
    ``_SLOT_PORT_POOL_END`` (8081..8099) — the AUTO-ALLOCATION pool, kept
    deliberately below ComfyUI's 8188 so fresh slots never squat on it.
    Per-slot ``port`` validation still allows up to ``_SLOT_PORT_MAX``
    (8200) for explicit operator choices. An operator ``[slots]``
    ``port_range_start/end`` in hal0.toml narrows/moves/widens the pool.
    Falls back to the pool constants when hal0.toml is unreadable.
    """
    from hal0.config.schema import _SLOT_PORT_MIN, _SLOT_PORT_POOL_END

    try:
        from hal0.config.loader import load_hal0_config

        slots_cfg = load_hal0_config().slots
        return int(slots_cfg.port_range_start), int(slots_cfg.port_range_end)
    except Exception:
        return _SLOT_PORT_MIN, _SLOT_PORT_POOL_END


def collect_port_claims(start: int, end: int, slot_snapshots: list[dict] | None = None):
    """Every known claim in the pool via the central registry (hal0.ports).

    Config TOMLs alone are NOT the truth — runtime rows can claim ports no
    TOML mentions (FLM-trio virtual ports), and something else may already
    be listening. See :mod:`hal0.ports` for the full source list.
    """
    from hal0.config.paths import slots_config_dir
    from hal0.ports import collect_claims

    return collect_claims(
        slots_dir=slots_config_dir(),
        pool=(start, end),
        slot_snapshots=slot_snapshots,
        reserved={8080: "api"},
    )


def next_free_slot_port(
    start: int | None = None,
    end: int | None = None,
    slot_snapshots: list[dict] | None = None,
) -> int:
    """Return the next free port in the configured slot range (#275 bug 2).

    Free = unclaimed by ANY registry source: slot configs (incl. disabled
    slots), runtime slot rows, reserved ports, and live listeners — see
    :mod:`hal0.ports`. The bounds default to the configured pool
    (hal0.toml ``[slots] port_range_start/end``); callers with the live
    config in hand may thread the bounds explicitly to avoid a disk read.
    """
    from hal0.ports import next_free

    if start is None or end is None:
        cfg_start, cfg_end = slot_port_range()
        start = cfg_start if start is None else start
        end = cfg_end if end is None else end

    port = next_free(collect_port_claims(start, end, slot_snapshots), start, end)
    if port is not None:
        return port
    raise BadRequest(
        f"no free port in {start}-{end} (all slots occupied)",
        code="slot.no_free_port",
    )


def reject_port_conflict(
    port: int, owner_slot: str, slot_snapshots: list[dict] | None = None
) -> None:
    """409-style 400 when an explicitly requested port is already owned."""
    from hal0.ports import claimed_by_other

    start, end = slot_port_range()
    lo, hi = min(start, port), max(end, port)
    claims = collect_port_claims(lo, hi, slot_snapshots)
    others = claimed_by_other(claims, port, f"slot:{owner_slot}")
    if others:
        raise BadRequest(
            f"port {port} is already claimed by {', '.join(sorted(others))} — "
            "omit 'port' to auto-assign a free one (see GET /api/ports)",
            code="slot.port_conflict",
            details={"port": port, "owners": sorted(others)},
        )
