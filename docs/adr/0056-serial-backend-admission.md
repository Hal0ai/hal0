# 0056. Serial-backend admission scheduling for single-request-at-a-time slots

## Status

Accepted

## Context

FastFlowLM (FLM) — hal0's FLM provider, `src/hal0/providers/flm.py` — runs on
the NPU and executes **one request at a time** across every model it is
serving, chat and decision alike (FLM docs: "the NPU runs one request at a
time"). hal0's dispatcher (`src/hal0/dispatcher/router.py`) did not know
this: it forwarded concurrent requests straight to the slot's port, so they
queued *inside* FLM where hal0 can't see them.

Two consequences, both observed on a Strix Halo box (hal0 1.7.1, FLM 1.0.12,
slot `flm` = `flm serve qwen3.5:4b --decision d3:lite`):

- The dispatcher's non-streaming read timeout
  (`[dispatcher].direct_read_timeout_s`, default 300s, see #3347) starts
  counting as soon as the request is forwarded — including the hidden queue
  wait. A Hindsight memory-extraction call (non-streaming, ~90s compute)
  stuck behind other traffic could burn its whole timeout budget before FLM
  ever started working on it, triggering a client retry that threw away
  completed NPU work.
- `POST /v1/systemone` (Hindsight's "System One" decision gate, ~5-12s
  compute) arriving mid-chat queued behind the whole chat response (measured
  24.7s behind a 23s chat turn; 118-255s behind a retain backlog), and
  because clients hit the slot port directly (the router 405'd
  `/v1/systemone`), those calls never counted as slot activity —
  `idle_timeout_s` (default 300s) could unload the slot mid-use.

ADR-0053 (`0053-memory-slot-and-chat-priority.md`) already defines a
chat-priority gate for the *memory slot*; GH #3163 lists general scheduling
as deliberately out of scope there, to be picked up later. This ADR is that
follow-up for the *serial-NPU-backend* case specifically — it does not touch
or duplicate ADR-0053's gate, and stays compatible with it.

Course correction during implementation: decision calls do **not** default
to an FLM slot. Hindsight's System One decision gate is normally served by
a separate CPU process (`hs-decide`, `[memory.decision]`, default
`http://127.0.0.1:9178`, model `hs-decide-1-flash`) — independent of
whatever chat backend is running. An FLM slot can *optionally* also serve
decision calls (`[npu].decision = "d3:lite"`), but that is an experimental
alternative, not the default path.

## Decision

1. **Per-slot admission scheduler** (`src/hal0/dispatcher/admission.py`).
   A slot that sets `[slot].max_inflight` (default: unset = unlimited,
   unchanged behavior; FLM/NPU slots are expected to set it to 1) gets a
   priority FIFO ticket queue in front of the forward. A request acquires a
   ticket before `Dispatcher.forward()` does any I/O and releases it on
   completion, error, client disconnect, or stream close. The read-timeout
   clock starts only once the ticket is granted, so queue wait is no longer
   charged against it. Queue-wait itself is bounded by
   `[dispatcher].admission_wait_timeout_s` (default 1800s) — a 503 with
   `Retry-After` beyond that, surfaced as `dispatch.admission_timeout`.
   Two priority classes: 0 (decision, i.e. `POST /v1/systemone`) always
   jumps ahead of 1 (everything else); FIFO within a class. No aging —
   documented as an accepted simplification, not a bug, given decision
   calls are short and infrequent relative to chat.

2. **`POST /v1/systemone` is routed through the hal0 router**, not left as a
   direct-port-only call. Default target: the configured `[memory.decision]`
   hs-decide server, proxied verbatim. If (and only if) a slot sets
   `[npu].decision`, that slot is used instead, lazy-loaded like chat,
   forwarded at priority 0, and counted as slot activity (resets the idle
   timer the same way chat does).

3. **`[npu].decision = "d3:<name>"`** stays available as an **optional,
   experimental** slot setting: when set, hal0 appends `--decision <tag>`
   to the FLM invocation (native and container paths) and exports
   `HAL0_FLM_DECISION`. FLM reserves ~1.8GB extra NPU memory when enabled.
   Most deployments should leave this unset and use `[memory.decision]`.

## Consequences

- Slots without `max_inflight` see zero behavior change — the admission
  acquire is a no-op context manager for them.
- FLM-backed slots with `max_inflight = 1` serialize at the hal0 layer
  instead of inside FLM, so hal0's own timeout accounting reflects actual
  compute time, not hidden queue time.
- `/v1/systemone` clients get consistent idle-timer behavior and a single
  well-known entry point regardless of which backend answers the call.
- Follow-up: queue-depth-aware load shedding / backpressure signaling to
  clients ahead of the hard `admission_wait_timeout_s` cutoff is not
  implemented here; `AdmissionRegistry.status()` exposes queue depth per
  slot/class for a future `/api/slots/{name}` surface to build on.

## References

- ADR-0053: memory-slot and chat priority (the companion/adjacent gate)
- GH #3163: scheduling follow-up scope
- `src/hal0/dispatcher/admission.py`
- `src/hal0/dispatcher/router.py` (`UpstreamCall.priority`, `Dispatcher.forward`)
- `src/hal0/api/routes/v1.py` (`POST /v1/systemone`)
- `src/hal0/providers/flm.py` (`_flm_shadow_role_args`, `HAL0_FLM_DECISION`)
