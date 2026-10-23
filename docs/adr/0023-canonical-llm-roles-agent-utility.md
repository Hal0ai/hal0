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
  (`src/hal0/normalize/resolver.py`, `_ANCHOR_NAME` / `DEFAULT_CHAINS`). `chat` and
  `primary` are retired as slot/role names.
- **`utility`** is the cheap-helper role, seeded on every install, and is
  never the fallback for general chat — only for its own targeted uses
  (`DEFAULT_CHAINS["hal0/utility"]` in `resolver.py`).
- **Slot routing key is the slot `name`, not a separate `role` field.**
  The legacy `role` field on `SlotConfig` is gone; identity IS the
  routing key for `hal0/<slot>` aliases
  (`CHANGELOG.md`, `v0.9.5` "Slot routing key is now the slot `name`, not
  `role` (ADR-0023 §2.1)"). One escape hatch: the special name `npu`
  additionally matches any slot with `device == "npu"`, so the NPU trio's
  chat edge answers `hal0/npu` regardless of its literal slot name.
- **`hal0/<slot>` generalizes to any enabled LLM slot**, not just the
  three advertised virtual names, so an operator-chosen slot (e.g. a
  memory-extraction slot) is addressable without a hardcoded resolver
  entry (`_chain_for` in `resolver.py`, ADR-0023 §2).
- **Hindsight is the platform memory engine; the cognee wrapper is
  gone.** `[memory.graph].extraction_slot` names a local, enabled LLM
  slot (resolved via the same `hal0/<slot>` alias machinery) that
  Hindsight's graph builder uses; `route`/`upstream` config fields from
  the cognee era are retired (`src/hal0/config/schema.py`,
  `MemoryGraphConfig` at `:2605`, `MemoryEmbeddingConfig` at `:2930`). Hindsight embeds
  server-side with its own bundled model, so hal0 no longer pins a
  separate embedding model for memory.
- **`extraction_slot` propagates to `hindsight-api` as environment**,
  resolved by the dispatcher to that slot's live model
  (`src/hal0/memory/extraction_env.py`, `apply_extraction_slot`).

### Amendment (2026-10, #2741): bound chain entries load on demand

A chain entry with a bound model is loaded on demand. `resolve_chain`
walks the chain in order and takes the first entry whose slot has a bound
model, whether that model is loaded or not. When it is not loaded, the
result carries `needs_load=True` and the matched slot name, and the
`/v1` route pins the request to that slot so the backend-aware load
wakes it. The `agent` anchor is the fallback only when no chain entry
has a model-bound slot (for example a model-less `utility` on a fresh
install). Each substitution is logged at INFO as `resolver.fallback`
with `requested`, `chain`, `resolved_slot` and `reason`
(`not_loaded_loading` or `no_model_bound_using_anchor`).

Before this amendment a chain entry counted only when its model was
already loaded. A cold `utility` (or `brain`, `npu`, or any operator-named
`hal0/<slot>`) therefore fell through to `agent`. That cold-loaded the
largest model on the box for every Hindsight extraction call meant for
the cheap helper.

### Amendment (2026-10, #2919): the memory engine calls `hal0/memory`

`hal0/memory` is a fourth reserved virtual name. hindsight-api is pointed
at it and nothing else: the drop-in hal0 writes
(`src/hal0/memory/extraction_env.py`) pins
`HINDSIGHT_API_LLM_MODEL=hal0/memory` and carries only the LLM timeout and
the extraction limits. hal0's `/v1` resolves the alias from
`[memory.graph]` on every call (`_normalize_chat_body` in
`src/hal0/api/routes/v1.py`, `memory_engine_target` in `extraction_env.py`):

- with `extraction_model` unset it behaves exactly as `hal0/<extraction_slot>`
  did — the §2 generalised chain `(<slot>, agent)`, same lane pin, same
  on-demand load;
- with `extraction_model` set to an upstream model id (one `/v1/models`
  lists for a remote, non-pinned upstream — OpenRouter, MiniMax, …) the
  request carries that id as-is, with no slot lookup and no lane pin, and
  the dispatcher routes it to its upstream or answers `dispatch.no_route`;
  it never falls through to the `agent` anchor.

Before this amendment the drop-in named `hal0/<extraction_slot>`, so every
slot change rewrote it and restarted the engine, and an upstream target
was impossible without changing the root-side allow-list in
`installer/wrappers/hal0-systemctl` (which only admits `hal0/…` values).
Now switching the engine between a local slot and an upstream model is a
`hal0.toml` write that the hot path reads from `app.state.hal0_config`,
applied on the engine's next call; the drop-in changes, and the engine
restarts, only for a timeout or limit change. A pre-amendment drop-in
still naming `hal0/<slot>` reads as drift and is rewritten once on the
next enabled graph PUT.

The engine still runs through hal0's `/v1` with hal0's upstream auth, so
an upstream target adds no credential to the engine, and hal0's upstream
filters and audit apply to its calls. Per-operation targets (retain vs
reflect vs mental-model refresh, which hindsight-api supports through
`HINDSIGHT_API_<OP>_LLM_*`) are deliberately not configured here; #2919
tracks them.

`GET /api/memory/graph/status` reports `target` (what the alias resolves
to), `available_models` (the pickable upstream ids) and `engine_llm`: the
provider, base URL and model the unit really loads, overlaying
`/etc/hal0/hindsight-llm.env` on the unit's merged `Environment=` the way
systemd does. That file is read after every `Environment=` line, drop-ins
included, so PROVIDER/BASE_URL/MODEL lines an operator adds there beat
hal0's target silently; the status names the file and the keys instead of
hiding it (#2918).

## Consequences

- One naming vocabulary (`agent`/`utility`, plus any operator-named slot)
  replaces three overlapping ones (`chat`/`primary`/`role`), reducing the
  places a slot's identity could disagree with its routing.
- Memory graph extraction is reporting-only from Hindsight's perspective
  (`src/hal0/memory/hindsight_provider.py:479`) but routes through the
  same live-slot resolution every other virtual model name uses — no
  parallel routing table to keep in sync.
- The deprecated `route` mirror of `extraction_slot` that
  `src/hal0/memory/provider.py` / `hindsight_provider.py` /
  `pgvector_provider.py` once emitted in `GET /api/memory/graph/status` has
  been removed; `extraction_slot` is the only field.
- This ADR's scope touches an unusually large number of call sites
  (dispatcher, resolver, CLI, config schema, UI) because it retired two
  overlapping naming systems at once; that breadth is why it is cited
  from more files than any other ADR in this tree.

## References

- `CHANGELOG.md`, `v0.8.0-beta.3` — "Canonical LLM roles + Hindsight-native
  memory extraction (ADR-0023)"; `v0.9.5` — "Slot routing key is now the
  slot `name`, not `role` (ADR-0023 §2.1)"
- `src/hal0/normalize/resolver.py` — `_ANCHOR_NAME`, `DEFAULT_CHAINS`,
  the `hal0/<slot>` generalization
- `src/hal0/config/schema.py` — `MemoryGraphConfig` (`:2605`),
  `MemoryEmbeddingConfig` (`:2930`)
- `src/hal0/memory/extraction_env.py` — extraction-slot env
  propagation to `hindsight-api`
- `src/hal0/memory/__init__.py:222` — Hindsight as the platform engine
- `src/hal0/dispatcher/_capability_resolve.py:28,156,239,248` — rule-9
  fallback to the `agent` anchor
- After the 1.3.0 upgrade floor no legacy main-slot names (`chat`, `primary`) are recognised anywhere: Hermes's main model and its auxiliary role map both take `agent`, else the first ready LLM slot in pool order, and log the fallback (#2635).
