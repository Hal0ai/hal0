# ADR-0053: Dedicated memory slot and chat-priority gate

## Status

Accepted 2026-11-04 for v1.9.0. Amends ADR-0023's utility seed and memory-role
policy. Implementation is staged through H0-191; publishing this decision does
not certify the later gate, seeding or hardware acceptance.

## Context

Memory extraction, consolidation and reflect can occupy a serving model while
a user waits for chat. The utility seed mixes this work with auxiliary tasks.
A cancellation spike found that an ordinary forward could continue generating
after its client disconnected, whereas closing a streaming forward stopped the
tested generation promptly. A provider-level test also found that Hindsight
handles a long 429 Retry-After as a deferral instead of ordinary retry failure.
Those findings motivate the design; they do not prove full-path cancellation or
real retain/task accounting.

## Decision

- Fresh installs will seed an empty memory slot and profile, with no model bound
  automatically, and retire the utility seed. `hal0/memory` resolves through
  `(memory, agent)`, so fresh configurations start shared. Existing extraction
  targets, utility slots and custom upstreams persist.
- `[memory.graph].extraction_slot` defaults to `"memory"`. Mode is `dedicated`
  only when it targets a model-bound memory slot without an upstream override;
  `agent` or an unbound memory target is `shared`; other slots and upstreams
  are `custom`. Mode follows configuration and binding, never readiness.
  Custom setups remain read-only until an operator explicitly changes them.
- New Open WebUI title/tag tasks will target agent. Hermes compaction will follow
  configured dedicated/shared mode rather than readiness. Preserve custom setups.
- Shared tagged memory will wait for all non-memory traffic plus a 30-second
  quiet gap. The per-request hold budget is derived from half the configured
  Hindsight LLM timeout. A 300-second starvation threshold admits one protected
  request and resets on admission. A model-bound dedicated memory slot bypasses
  the shared gate. Identity comes only from request state tagged when the caller
  uses `hal0/memory`, before slot/upstream rewriting.
- `[memory.priority]` owns `enabled` (fresh default true), `quiet_gap_s` (30) and
  `max_defer_s` (300). Migration v4 fills only missing values: the old utility
  extraction default and priority false, including a partially present priority
  table without `enabled`. Preserve all explicit settings.
- Tagged memory will stream upstream; hal0 will assemble an ordinary response,
  preserving schema, structured content, usage, finish reasons and errors, and
  close the upstream stream on cancellation. Deferral/preemption will reply 429
  with Retry-After strictly above 60 seconds and the effective upstream maximum
  backoff. Preserve operator retries/timeouts; add no 8-retry/4-hour override.
- Memory tuning stays profile-owned (32768 context, thinking off), placement
  slot-owned (proposed port 8091, subject to collision checks), per ADR-0029.
- This design supersedes H0-70's competing second-target proposal. No
  `hal0/memory-background` alias. Independent consolidation budgets, batching,
  scheduling and mental-model refresh remain follow-up scope in GH #3163.

## Implementation and acceptance

H0-194 supplies the canonical chain, defaults, request identity, status mode,
priority schema and v4 transform. Seeding, auxiliary tasks, scheduling, streaming
assembly and their UI are later stages. H0-198 owns the final documentation and
merged-code evidence sweep of this same ADR. H0-195 must also change the
installer's minimal config seed to the current schema version: a new file
stamped v1 would immediately receive v4's upgrade-only utility/priority-off
values during post-activation migrations. Fresh-install acceptance remains
outstanding until that installer stage lands.

A starvation-protected generation can delay chat for its own duration. Requeue
delay can extend observed waiting beyond the admission threshold; 300 seconds is
not a completion guarantee. Measure protected-admission latency separately.

Before accepting the complete feature, run a real Hindsight retain through hal0
and the backend, measure retry counters and Retry-After scheduling, demonstrate
eventual completion under sustained chat, measure cancellation on CPU and GPU,
and test response schema/usage/errors and both installation paths. No under-two-
second full-path claim follows from the spike. Migration and installer merges
belong to Alex; hardware acceptance remains required.

## Source ownership

- `src/hal0/normalize/resolver.py`: canonical slot chains and binding selection.
- `src/hal0/api/routes/v1.py` and `src/hal0/dispatcher/lane_pin.py`: request identity.
- `src/hal0/config/schema.py`: defaults and priority configuration.
- `src/hal0/config/migrations/v4.py`: preservation of upgrade settings.
- `src/hal0/api/routes/memory.py`: the graph status mode.
- `src/hal0/memory/extraction_env.py`: the engine alias and rendered limits.
