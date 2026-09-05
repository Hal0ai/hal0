# ADR-0023: Canonical LLM roles (`agent`/`utility`) + Hindsight-native memory extraction

## Status

**ACCEPTED.** Shipped in `v0.8.0-beta.3` (2026-06-23), per
`CHANGELOG.md`: "Canonical LLM roles + Hindsight-native memory extraction
(ADR-0023)." Unlike the other reconstructed ADRs in this tree, no
pre-cutover copy of this one exists in git history — it was authored
after `docs/internal/adr/` became gitignored (#627/#638) and lived only
on the authoring machine. This page is reconstructed from source
comments and the CHANGELOG entry alone, not from the original document.

## Context

Two related pieces of naming and routing debt had accumulated by
mid-2026:

1. The slot-routing vocabulary used `chat`/`primary`/`role` inconsistently
   across the resolver, the seeded slot TOMLs, and the dashboard, with no
   single canonical anchor name for "the default capable LLM."
2. The memory subsystem's graph-extraction routing (`[memory.graph]`)
   still carried config fields (`route`, `upstream`) and a reranker
   config shaped for the retired cognee engine, even though Hindsight had
   become the sole memory engine.

## Decision

**Two canonical LLM roles replace the old ad hoc names, and Hindsight
becomes the platform memory engine with graph extraction routed through
the normal slot-alias machinery.**

- **`agent`** is the canonical default/anchor slot — every `hal0/<slot>`
  fallback chain ends in `agent`
  (`src/hal0/normalize/resolver.py:13-19`, `DEFAULT_CHAINS`). `chat` and
  `primary` are retired as slot/role names.
- **`utility`** is the cheap-helper role, seeded on every install, and is
  never the fallback for general chat — only for its own targeted uses
  (`resolver.py:29`).
- **Slot routing key is the slot `name`, not a separate `role` field.**
  The legacy `role` field on `SlotConfig` is gone; identity IS the
  routing key for `hal0/<slot>` aliases
  (`CHANGELOG.md`, `v0.7.x` "Slot routing key is now the slot `name`, not
  `role` (ADR-0023 §2.1)"). One escape hatch: the special name `npu`
  additionally matches any slot with `device == "npu"`, so the NPU trio's
  chat edge answers `hal0/npu` regardless of its literal slot name.
- **`hal0/<slot>` generalizes to any enabled LLM slot**, not just the
  three advertised virtual names, so an operator-chosen slot (e.g. a
  memory-extraction slot) is addressable without a hardcoded resolver
  entry (`resolver.py:41-46`, ADR-0023 §2).
- **Hindsight is the platform memory engine; the cognee wrapper is
  gone.** `[memory.graph].extraction_slot` names a local, enabled LLM
  slot (resolved via the same `hal0/<slot>` alias machinery) that
  Hindsight's graph builder uses; `route`/`upstream` config fields from
  the cognee era are retired (`src/hal0/config/schema.py:2606-2951`,
  `MemoryGraphConfig`, `MemoryEmbeddingConfig`). Hindsight embeds
  server-side with its own bundled model, so hal0 no longer pins a
  separate embedding model for memory.
- **`extraction_slot` propagates to `hindsight-api` as environment**,
  resolved by the dispatcher to that slot's live model
  (`src/hal0/memory/extraction_env.py:1,14`).

## Consequences

- One naming vocabulary (`agent`/`utility`, plus any operator-named slot)
  replaces three overlapping ones (`chat`/`primary`/`role`), reducing the
  places a slot's identity could disagree with its routing.
- Memory graph extraction is reporting-only from Hindsight's perspective
  (`src/hal0/memory/hindsight_provider.py:479`) but routes through the
  same live-slot resolution every other virtual model name uses — no
  parallel routing table to keep in sync.
- Every `route: self._extraction_slot` field left in
  `src/hal0/memory/provider.py` / `hindsight_provider.py` /
  `pgvector_provider.py` is explicitly marked a deprecated mirror kept
  for backward-compatible API responses, not a second source of truth.
- This ADR's scope touches an unusually large number of call sites
  (dispatcher, resolver, CLI, config schema, UI) because it retired two
  overlapping naming systems at once; that breadth is why it is cited
  from more files than any other ADR in this tree.

## References

- `CHANGELOG.md`, `v0.8.0-beta.3` — "Canonical LLM roles + Hindsight-native
  memory extraction (ADR-0023)"; `v0.7.x` — "Slot routing key is now the
  slot `name`, not `role` (ADR-0023 §2.1)"
- `src/hal0/normalize/resolver.py:13-46` — `_ANCHOR_NAME`, `DEFAULT_CHAINS`,
  the `hal0/<slot>` generalization
- `src/hal0/config/schema.py:2602-2951` — `MemoryGraphConfig`,
  `MemoryEmbeddingConfig`
- `src/hal0/memory/extraction_env.py:1,14,91` — extraction-slot env
  propagation to `hindsight-api`
- `src/hal0/memory/__init__.py:222` — Hindsight as the platform engine
- `src/hal0/dispatcher/_capability_resolve.py:28,156,239,248` — rule-9
  fallback to the `agent` anchor
