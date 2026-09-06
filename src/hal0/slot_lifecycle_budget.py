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

#: Wall-clock bound on the ``systemctl restart`` that actually (re)spawns a
#: slot's Quadlet-generated unit (#1869/#1870) — the client-observable job
#: window BEFORE the post-spawn ``/health`` poll even begins. Mirrored by
#: ``hal0.providers.container._UNIT_START_TIMEOUT_S``.
#:
#: Before #1869 this call had NO bound at all: a ``systemctl restart`` that
#: never returns (crossed with a wedged podman/netavark call) blocked the
#: calling thread — and every client budget derived from this module —
#: forever, with nothing printed (#1870). Set equal to
#: :data:`HEALTH_TIMEOUT_S`: a wedged restart gets no more patience than the
#: phase that follows it.
UNIT_START_TIMEOUT_S = HEALTH_TIMEOUT_S

#: Wall-clock bound on the three fast, should-be-sub-second seam calls that
#: precede ``systemctl restart`` in the unit-start sequence: the Quadlet
#: write, ``daemon-reload``, and ``reset-failed`` (#1869). Mirrors
#: ``hal0.providers.container._UNIT_STOP_TIMEOUT_S`` x 3 — like
#: :data:`EVICTION_UNLOAD_ALLOWANCE`, this is a policy allowance rather than a
#: value container.py imports FROM here: none of the three legitimately needs
#: more than an instant (a local file write, a systemd unit-cache reload, a
#: state-clear), so the bound exists purely to turn "wedged forever" into
#: "wedged for at most 20s each", not to give real work room.
UNIT_ADMIN_CALLS_ALLOWANCE_S = 60.0

#: Wall-clock bound on the RAW ``/completion`` output-sanity probe — the one a
#: ``type=llm`` load runs after ``/health`` converges (#1922 — the gate that
#: turns "the port answers" into "the model produces language"). Consumed by
#: ``hal0.slots.output_sanity.probe``; deliberately small, because it asks for
#: a dozen greedy tokens from an already-warm server, so anything near this
#: bound is itself the failure the gate reports.
OUTPUT_SANITY_TIMEOUT_S = 20.0

#: Wall-clock bound on the CHAT fallback, which only runs when the raw probe's
#: answer was wrong. Wider on purpose: that request asks for
#: ``output_sanity.SANITY_CHAT_MAX_TOKENS`` (256) rather than a dozen, because
#: a reasoning model spends its first tokens inside ``<think>`` and a tight cap
#: fails working slots. 256 tokens inside 45s is ~6 tok/s — under the slowest
#: warm lane we ship (CPU), so a slow-but-correct model is judged on its
#: answer instead of on the clock.
OUTPUT_SANITY_CHAT_TIMEOUT_S = 45.0

#: Both gate budgets on a ``device="cpu"`` slot, which is a different machine
#: from the accelerated lane the two constants above are sized for.
#:
#: The 20s/45s pair assumes a GPU that decodes a dozen greedy tokens in about
#: a second. The fleet's own numbers say the CPU lane is two orders of
#: magnitude away from that: the ``v1.0.0-rc.5`` validation measured ct151
#: (8 cores, 16 GB, no GPU passthrough) at 0.12 tok/s for a 0.8B model under
#: exactly the contention a load happens in, and one brain reply took 3m42s.
#: A no-GPU box is not an exotic case either — ``install.profile_derive.
#: derive_device`` resolves EVERY seeded slot to ``cpu`` there, and the brain
#: slot then binds the unquantized F16 variant precisely because the box is
#: slow.
#:
#: Set to :data:`HEALTH_TIMEOUT_S` rather than to a fresh literal: it is the
#: wall clock the same box already gets to answer ``/health`` after spawning,
#: so the gate asks for no more patience than the phase before it. That is
#: 0.067 tok/s for the raw probe's dozen tokens — under the slowest thing the
#: fleet has measured — and it also covers the chat fallback, whose 256-token
#: ask is where a reasoning model on a CPU box would otherwise be judged on
#: an answer it had no room to write.
#:
#: Consumed via ``hal0.slots.output_sanity.probe_budget_s``, which returns it
#: for a cpu-backed slot and ``None`` (module defaults) for everything else.
OUTPUT_SANITY_CPU_TIMEOUT_S = HEALTH_TIMEOUT_S

#: What the gate can cost ONE load, worst case: the raw probe, then the chat
#: fallback. A load that passes on the raw probe pays only the first.
#:
#: Charged at the CPU lane's budget because a client timeout must cover the
#: slowest slot the server might be converging, and the client is handed a
#: slot name — not its device — by every lifecycle verb. An accelerated slot
#: spends 65s of this and returns early.
OUTPUT_SANITY_LOAD_ALLOWANCE_S = 2 * OUTPUT_SANITY_CPU_TIMEOUT_S

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

#: Worst wall-clock a single ``SlotManager.load`` can hold the slot lock —
#: every phase it runs between acquiring and releasing, summed. Keep this
#: derivation exhaustive: it is the number BOTH the per-load charge and the
#: lock-wait allowance below are built from, so a phase added to ``load``
#: without a line here silently re-creates #1832 (client reports a timeout on
#: an op the server is still converging).
#:
#:     30   pre-spawn terminate — the drifted re-converge (#1224), the ERROR
#:          retry and the stale in-flight branches each stop the unit in place
#:          before re-spawning
#:     90   3 x 30, ``preload_evict.admit`` unloading eviction candidates in
#:          series (EVICTION_UNLOAD_ALLOWANCE)
#:     60   the Quadlet write + daemon-reload + reset-failed that precede the
#:          spawn (UNIT_ADMIN_CALLS_ALLOWANCE_S, #1869) — bounded seam calls
#:          now that (before #1869) had no bound at all
#:    180   the ``systemctl restart`` that spawns the unit (UNIT_START_TIMEOUT_S,
#:          #1869/#1870) — likewise unbounded before this fix
#:    180   the post-spawn ``/health`` poll (HEALTH_TIMEOUT_S)
#:    360   the output-sanity gate (#1922), at its CPU-lane budget: 180 raw
#:          probe + 180 chat fallback (OUTPUT_SANITY_LOAD_ALLOWANCE_S = 2 x
#:          OUTPUT_SANITY_CPU_TIMEOUT_S). An accelerated slot spends 20 + 45
#:          instead, but the client cannot know which it is asking about
#:     30   the failed gate's teardown, which runs before the ERROR stamp and
#:          therefore still inside the lock
#:    ---
#:    930
LOAD_LOCK_HOLD_S = (
    TERMINATE_TIMEOUT_S
    + EVICTION_UNLOAD_ALLOWANCE * TERMINATE_TIMEOUT_S
    + UNIT_ADMIN_CALLS_ALLOWANCE_S
    + UNIT_START_TIMEOUT_S
    + HEALTH_TIMEOUT_S
    + OUTPUT_SANITY_LOAD_ALLOWANCE_S
    + TERMINATE_TIMEOUT_S
)

#: Every lifecycle verb takes the per-slot lock (``SlotManager._lock``), so a
#: request can sit queued behind whatever is already converging that slot
#: before doing any of its own work. Charged once per request, at the cost of
#: the worst thing that can hold the lock — a full load, start to finish.
LOCK_WAIT_ALLOWANCE_S = LOAD_LOCK_HOLD_S

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

    A load charges :data:`LOAD_LOCK_HOLD_S` — every phase it runs while
    holding the lock, itemised at that constant; an unload charges one
    terminate; the whole request additionally charges one
    :data:`LOCK_WAIT_ALLOWANCE_S` per lock-acquiring phase, for queueing behind
    an in-flight op on the same slot.
    """
    per_slot = loads * LOAD_LOCK_HOLD_S
    per_slot += unloads * TERMINATE_TIMEOUT_S
    # The lock allowance is charged per lock-acquiring phase, per slot. A
    # compound verb does NOT hold one lock: ``SlotManager.restart`` releases
    # after ``unload`` and reacquires inside ``load``, so another queued op can
    # win the gap and be waited on twice. A fan-out sweep likewise takes each
    # slot's lock separately.
    lock_waits = max(loads + unloads, 1) * LOCK_WAIT_ALLOWANCE_S
    total = (per_slot + lock_waits) * max(int(slots), 1)
    return round(total * OVERHEAD_FACTOR, 1)
