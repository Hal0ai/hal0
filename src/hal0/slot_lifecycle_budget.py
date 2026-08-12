"""One source of truth for how long a slot lifecycle call can block (#1832).

The slot lifecycle endpoints (``load`` / ``unload`` / ``restart`` / ``swap``,
and the fan-out ``/api/updates/restart-slots``) hold the HTTP response open
until the server-side state machine converges. Every client of those
endpoints — the CLI, the MCP admin bridge — therefore needs a read timeout
derived from the *server's* worst case, not a hand-picked number that a later
server-side retune silently invalidates.

That is exactly how #1832 came back: the first fix hardcoded 220s against a
210s server floor (180s health poll + one 30s terminate), leaving ~5% margin
and no allowance at all for the sequential unloads
:func:`hal0.slots.preload_evict.admit` performs *inside* the load path. Two
evicted candidates already push a plain load past 240s, and the CLI reports
failure on a load that in fact succeeded.

This module owns the primitive budgets, and the server modules import them
from here rather than declaring their own:

  - :data:`HEALTH_TIMEOUT_S` — :func:`hal0.providers.container._await_health`
  - :data:`TERMINATE_TIMEOUT_S` — :attr:`hal0.slots.manager.SlotManager._terminate_timeout_s`

Deliberately dependency-free (stdlib only, no ``hal0`` imports) so the CLI can
import it without dragging in the slot manager.
"""

from __future__ import annotations

#: Wall-clock bound on the post-spawn ``/health`` poll. Mirrored by
#: ``hal0.providers.container._HEALTH_TIMEOUT_S``.
HEALTH_TIMEOUT_S = 180.0

#: Wall-clock bound on a single container stop. Mirrored by
#: ``hal0.slots.manager.SlotManager._terminate_timeout_s``.
TERMINATE_TIMEOUT_S = 30.0

#: Sequential unloads a single ``load`` may perform before it spawns
#: anything: ``preload_evict.admit`` awaits ``host.unload(candidate)`` once per
#: evicted candidate, in series, inside the load path. The true count is
#: bounded only by the number of resident slots, so a client budget has to pick
#: an allowance; three covers every fleet box we ship (chat + code + embedding
#: resident at once) with the margin factor on top.
EVICTION_UNLOAD_ALLOWANCE = 3

#: Multiplier over the summed server budget covering what the server's own
#: timeouts do not: podman/systemd fork + image resolution, JSON encode, the
#: request/response hop, and the poll interval overshoot on each bound above.
OVERHEAD_FACTOR = 1.15


def slot_lifecycle_timeout_s(*, loads: int = 1, unloads: int = 1, slots: int = 1) -> float:
    """Client read timeout that clears the server's worst case, in seconds.

    ``loads`` / ``unloads``: how many of each the endpoint performs per slot —
    ``load`` is ``loads=1, unloads=0``; ``unload`` is ``loads=0, unloads=1``;
    ``restart`` and ``swap`` are unload-then-load, so both.

    ``slots``: how many slots the endpoint iterates over in one request
    (``/api/updates/restart-slots`` loops ``sm.restart`` over every drifted
    slot). Clamped to at least 1.

    A load charges the health poll plus :data:`EVICTION_UNLOAD_ALLOWANCE`
    terminates for the evictions ``preload_evict.admit`` may run before the
    spawn; an unload charges one terminate.
    """
    per_slot = loads * (HEALTH_TIMEOUT_S + EVICTION_UNLOAD_ALLOWANCE * TERMINATE_TIMEOUT_S)
    per_slot += unloads * TERMINATE_TIMEOUT_S
    return round(per_slot * max(int(slots), 1) * OVERHEAD_FACTOR, 1)
