# ADR 0002 — Capabilities overlay on top of flat slots

- **Status:** Accepted (shipped — `src/hal0/capabilities/`, commits `78c46c2`, `39adaf7`, `b90a569`)
- **Date:** 2026-05-20

## Context

The slot layer (`src/hal0/slots/`) models one process per named slot
(`primary`, `embed`, `stt`, `tts`, `img`, …). It is the right primitive
for the runtime — one TOML, one systemd unit, one health probe — but it
has been an awkward operator surface for two reasons:

1. **Multiple slots cooperate to deliver one user-visible "capability."**
   Embed + rerank are two slots but one mental concept. STT + TTS are
   two slots but one Voice card. The dashboard kept growing per-slot
   editor screens that asked the operator to know that "yes, you want
   `bge-reranker-v2-m3-q4_k_m`, and yes, you have to set `extra_args =
   --reranking`, and the port has to avoid colliding with `embed`."
2. **The model-picker had no way to enforce a `(backend, model)` legal
   set.** Nothing stopped a user from binding an FLM-only chat tag to a
   GGUF-only slot, or a llama.cpp GGUF to `backend=npu`. The slot would
   start, the upstream would crash, and the only error available was the
   provider's `Model not found` from inside the container.

## Decision

Add a thin overlay layer — `src/hal0/capabilities/` — that does NOT
replace the slot schema. The slot layer remains the runtime primitive.
The overlay owns three things:

1. **A capability/child → slot bridge.** Hard-coded mapping in
   `orchestrator.py:_CHILD_TO_SLOT`: `embed.embed → embed`,
   `embed.rerank → embed-rerank`, `voice.stt → stt`, `voice.tts → tts`,
   `img.img → img`. Children may auto-create their slot on first enable
   (`_ensure_slot_exists`).
2. **A model-first catalog.** `catalog.py:models_for_capability()`
   returns one entry per model id with a `backends` list of legal
   choices, so the picker can offer model → backend (narrowed)
   rather than the prior flat (model × backend) grid that allowed
   illegal pairs.
3. **A persisted selection layer.** `/etc/hal0/capabilities.toml`
   carries one `CapabilitySelection` per `(slot, child)` tuple.
   `CapabilityOrchestrator.apply()` reconciles the underlying slot
   TOML on every enabled apply (drift invariant — capabilities.toml
   and slots/*.toml can disagree if a prior apply failed mid-flight or
   the operator hand-edited either file).

The HTTP surface is mounted at `/api/capabilities`
(`src/hal0/api/routes/capabilities.py`). Operator tooling lives at
`hal0 capabilities migrate` for cleaning stale selections after the
catalog reshape.

## Consequences

### Positive

- Dashboard cards (Embed, Voice, Img + NPU backend rollup) match
  operator mental model; raw slot screens still exist for advanced
  use but are no longer the first thing a new user sees.
- Per-`(backend, model)` validation (`capability.illegal_backend_model_pair`)
  prevents a class of slot-crash-at-start-up bugs by surfacing the
  failure at apply time with a typed envelope.
- The drift invariant means a half-applied selection is recoverable
  by re-issuing the same apply — the orchestrator always rewrites the
  underlying slot TOML when the selection is enabled, not just when it
  differs from the prior selection.
- The overlay is thin enough (~700 LoC across catalog + orchestrator +
  config) to delete if the slot layer ever absorbs the capability
  concept directly.

### Negative / Tradeoffs

- Two configuration sources of truth (`capabilities.toml` +
  `slots/*.toml`). The drift invariant pays the cost in the
  orchestrator; manual edits are still allowed but the operator is
  expected to run `hal0 capabilities migrate` if they edit
  `capabilities.toml` directly with bad data.
- Hard-coded `_CHILD_TO_SLOT` table — adding a capability requires a
  code change, not just a config edit. Intentional: the runtime slot
  bridge is part of the contract the dashboard renders against.
- NPU multiplex (one `flm` process serving multiple children) is
  deferred. Each NPU child currently spawns its own slot.

### Risks

- `_ensure_slot_exists` allocates ports from `8081..8099`; pool
  exhaustion surfaces as `capability.apply_failed` rather than a
  silent collision, but operators with many manually-created slots
  may hit this.
- The catalog probes the FLM toolbox image (`docker image inspect`)
  on every `GET /api/capabilities`. Cheap but not free.
