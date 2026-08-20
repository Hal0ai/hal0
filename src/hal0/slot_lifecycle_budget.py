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

#: Wall-clock bound on ONE output-sanity completion — the probe a ``type=llm``
#: load runs after ``/health`` converges (#1922 — the gate that turns "the
#: port answers" into "the model produces language"). Consumed by
#: ``hal0.slots.output_sanity.probe``; deliberately small, because the probe
#: asks for a dozen greedy tokens from an already-warm server, so anything
#: near this bound is itself the failure the gate reports.
OUTPUT_SANITY_TIMEOUT_S = 20.0

#: Probes a single load can pay for in the worst case. A wrong answer on the
#: raw ``/completion`` endpoint buys one retry through
#: ``/v1/chat/completions`` (a template-dependent instruct model can answer
#: one well and the other poorly), so the failing path costs two budgets. The
#: happy path costs one.
OUTPUT_SANITY_PROBES_PER_LOAD = 2

#: Sequential unloads a single ``load`` may perform before it spawns
#: anything: ``preload_evict.admit`` awaits ``host.unload(candidate)`` once per
#: evicted candidate, in series, inside the load path. The true count is
#: bounded only by the number of resident slots, so a client budget has to pick
#: an allowance; three covers every fleet box we ship (chat + code + embedding
#: resident at once) with the margin factor on top.
#:
#: CAVEAT — this is the one constant in this module with no server-side source.
#: ``HEALTH_TIMEOUT_S`` and ``TERMINATE_TIMEOUT_S`` are the values the server
#: itself imports from here, so retuning either propagates to every client and
#: the parity test in ``tests/cli/test_slot_commands.py`` fails if someone
#: re-declares a literal. ``preload_evict`` has no equivalent bound to import:
#: nothing caps how many candidates ``admit`` evicts, so this number is a policy
#: choice and a server change could invalidate it silently. The slack is
#: currently large — ten resident slots evicting in series cost
#: ``HEALTH_TIMEOUT_S + 10 * TERMINATE_TIMEOUT_S`` = 480s against a 621s load
#: budget, because the per-phase lock allowance and ``OVERHEAD_FACTOR`` sit on
#: top of the three charged here. Deriving it per call would mean a
#: ``GET /api/slots`` before every lifecycle verb for a bound that is still an
#: estimate; bounding it server-side is the real fix (#1869).
EVICTION_UNLOAD_ALLOWANCE = 3

#: Every lifecycle verb takes the per-slot lock (``SlotManager._lock``), so a
#: request can sit queued behind whatever is already converging that slot
#: before doing any of its own work. Charged once per request, at the cost of
#: the worst thing that can hold the lock — a full load, evictions included,
#: not just its health poll.
LOCK_WAIT_ALLOWANCE_S = HEALTH_TIMEOUT_S + EVICTION_UNLOAD_ALLOWANCE * TERMINATE_TIMEOUT_S

#: Slots one ``POST /api/stacks/{slug}/apply`` may converge in a single request.
#: ``StackApplyEngine.converge`` walks the stack's entries sequentially
#: (``load`` / ``swap`` / ``restart``, apply.py:472/475/481) and then unloads
#: every running slot the stack does not claim (apply.py:541), so the true count
#: is ``len(stack.entries) + len(residual slots)``. The MCP bridge is handed a
#: slug, not a slot list, and would need an extra ``GET /api/stacks/{slug}``
#: round trip to count — so, like :data:`EVICTION_UNLOAD_ALLOWANCE`, this is a
#: documented policy allowance rather than a derived bound. Six covers every
#: stack we ship and every hand-built one seen on the fleet; a larger stack
#: degrades to the pre-existing behaviour (client gives up, server finishes),
#: which is strictly better than the 30s generic default that could not even
#: cover one unload.
STACK_APPLY_SLOT_ALLOWANCE = 6

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

    A load charges the health poll, the output-sanity probes that follow it
    (:data:`OUTPUT_SANITY_PROBES_PER_LOAD` x :data:`OUTPUT_SANITY_TIMEOUT_S`), plus
    :data:`EVICTION_UNLOAD_ALLOWANCE` terminates for the evictions
    ``preload_evict.admit`` may run before the spawn; an unload charges one
    terminate; the whole request additionally charges one
    :data:`LOCK_WAIT_ALLOWANCE_S` for queueing behind an in-flight op on the
    same slot.
    """
    per_slot = loads * (
        HEALTH_TIMEOUT_S
        + OUTPUT_SANITY_PROBES_PER_LOAD * OUTPUT_SANITY_TIMEOUT_S
        + EVICTION_UNLOAD_ALLOWANCE * TERMINATE_TIMEOUT_S
    )
    per_slot += unloads * TERMINATE_TIMEOUT_S
    # The lock allowance is charged per lock-acquiring phase, per slot. A
    # compound verb does NOT hold one lock: ``SlotManager.restart`` releases
    # after ``unload`` and reacquires inside ``load``, so another queued op can
    # win the gap and be waited on twice. A fan-out sweep likewise takes each
    # slot's lock separately.
    lock_waits = max(loads + unloads, 1) * LOCK_WAIT_ALLOWANCE_S
    total = (per_slot + lock_waits) * max(int(slots), 1)
    return round(total * OVERHEAD_FACTOR, 1)
