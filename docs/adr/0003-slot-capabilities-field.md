# ADR 0003 — Explicit `capabilities` field on slots

- **Status:** Draft
- **Date:** 2026-05-21
- **Drivers:** post-mortem on PR #89 (Test chat dropdown filter) and PR #91 (image models in primary picker)

## Context

Two recent bugs landed in the same place — the UI couldn't tell from a slot record what capability that slot serves, so every picker had to reconstruct the answer locally:

1. **PR #89 — Test chat dropdown.** `/v1/models` returned every loaded upstream (chat, embed, rerank, stt, tts) with no capability tag. The picker defaulted to `data[0]`, which sorted to the embed slot's `nomic-embed-text-v1.5` model, and any send 400ed. The fix did a 2-hop join:

   ```
   /api/capabilities  → catalogs.chat.chat = chat-capable model ids
   /api/slots         → slot.model_id
   /v1/models         → row.owned_by = slot name
   ∩  ⇒ chat-capable slot names
   filter /v1/models by owned_by ∈ chat-capable
   ```

2. **PR #91 — image models in primary picker.** Two compounding bugs (template referenced `slot.model` instead of `slot.model_id`; the inline picker was unfiltered). Fixing the second one only got us a *backend* filter — primary's picker still leaks embed and rerank models because both share `backend=vulkan`. A real "show only chat-capable models for primary" filter still requires the same 2-hop join.

The same shape will keep recurring: the dispatcher, `/v1/models`, the future "test embedding" panel, future slot-aware autocomplete — every caller will need to know which capability a slot serves.

Today the only way to answer "what does this slot serve?" is one of:

- **Slot name heuristic.** `primary` and `nano` ⇒ chat; `embed` ⇒ embed; `embed-rerank` ⇒ rerank; `stt`, `tts` ⇒ voice. Fragile (user-defined slots like `nano` are already off the well-known list), drifts the moment someone adds a `secondary-chat` or `code` slot.
- **Loaded model's capabilities.** Truthful when something is loaded; useless on an empty slot — exactly the state in which the picker needs to filter.
- **The 2-hop join.** Works but every caller pays for it, and the join can be subtly wrong (e.g. `/v1/models` adoption from outside hal0 can have `owned_by` set to the docker container name, not the slot name).

This is the same anti-pattern as `[[hal0_capability_catalog_provider_bug]]`: a row missing a field that every caller has to reconstruct. The cure is the same — put the field on the row.

## Decision

Add a first-class `capabilities` field to the slot data model.

### Schema

In `/etc/hal0/slots/<name>.toml`:

```toml
[slot]
name = "primary"
port = 8081
backend = "vulkan"
provider = "llama-server"
capabilities = ["chat"]      # ← new
```

Multi-capability slots (FLM NPU multiplex) carry the full set:

```toml
[slot]
name = "npu"
backend = "flm"
provider = "flm"
capabilities = ["chat", "embed"]
```

In Python, surfaced on `SlotConfig` as `capabilities: list[str]` (default `[]` for backward compat) and validated against a known set (`{"chat", "embed", "rerank", "stt", "tts", "image"}`, plus `"unknown"` as the explicit "no claim made" sentinel).

### API surface

`GET /api/slots` returns `capabilities` on every slot row, pass-through from the TOML. Empty list ⇒ "this slot makes no capability claim" — UI treats as universal and shows all models.

`POST /api/slots` accepts `capabilities` in the body. If absent, derived from a defaults table by `(backend, provider)`:

| Provider | Default capabilities |
|---|---|
| `llama-server` (backend ∈ {vulkan, rocm, cpu}, no `--embeddings`, no `--reranking`) | `["chat"]` |
| `llama-server` + `--embeddings` in extra_args | `["embed"]` |
| `llama-server` + `--reranking` in extra_args | `["rerank"]` |
| `moonshine` | `["stt"]` |
| `kokoro` | `["tts"]` |
| `comfyui` | `["image"]` |
| `flm` | `["chat", "embed"]` (the multiplex set FLM can serve today) |

The derivation runs once at slot-create time and the result is written to the TOML — not re-derived on read. That way an operator who *intends* to point a llama-server slot at an embedding-only model can declare `capabilities = ["embed"]` and the dashboard respects it, without the orchestrator second-guessing them every poll.

### Apply-time validation

When a model is loaded into a slot (`SlotManager.load(slot, model_id=X)` or `swap`), the manager checks `slot.capabilities ∩ model.capabilities ≠ ∅`. If empty, the load is rejected with a typed error (`capability.slot_model_capability_mismatch`) carrying both lists, mirroring the existing `capability.illegal_backend_model_pair` from ADR-0002. An empty `slot.capabilities` skips the check (universal-compatible).

### Callers that simplify

| Caller | Before | After |
|---|---|---|
| Dashboard Test chat dropdown (PR #89) | 2-hop join across `/api/capabilities` × `/api/slots` × `/v1/models` | filter slots by `"chat" ∈ capabilities`, intersect with `/v1/models.owned_by` |
| Slots view inline picker (PR #91) | backend filter only, embed/rerank leaks through | filter models by `(model.capabilities ∩ slot.capabilities)` |
| Dispatcher `_route_chat_completion` | finds first chat-ish slot by name pattern | finds first slot with `"chat"` capability in `READY` |
| Future per-capability panels (Test embed, Test rerank) | currently absent; would need their own join | direct slot filter |

## Consequences

### Positive

- Single source of truth for "what this slot is for" — declarative, queryable, and present in the same row as `port`, `backend`, `provider`. Matches the way `_CHILD_TO_SLOT` from ADR-0002 already projects capabilities; this just makes the slot-side of that relation explicit.
- Removes the 2-hop join from every present and future caller. Bug class (capability leak in a picker because some caller forgot to filter) goes away by construction.
- Apply-time validation locks down a class of slot-crash-at-startup bugs (load an embed-only NPU tag into a chat-only slot), with the same typed envelope shape ADR-0002 introduced.
- The default-derivation table is a small, opinionated artefact; it captures the same knowledge that today is duplicated as comments, name-based heuristics, and code-review tribal knowledge.
- Compatible with the capability overlay (ADR-0002): the orchestrator's `_CHILD_TO_SLOT` mapping continues to own the *child → slot* direction; this ADR owns the *slot → capability set* direction. The two close the loop.

### Negative / Tradeoffs

- **Schema migration.** Existing installs' `slots/*.toml` won't carry the field. Handled in two pieces:
  - **Startup backfill.** On first `hal0-api` boot after upgrade, the SlotManager reads each TOML; if `capabilities` is missing, runs the default-derivation table and writes the result back via `write_toml_atomic`. Idempotent. Logs a `slot.capabilities_backfilled` event per slot the first time.
  - **Schema validation tolerates absence.** `SlotConfig.capabilities` defaults to `[]`. Slots stay loadable even if the backfill hasn't run yet — they just don't participate in capability-filtered picker views until backfilled.
- **Operator-edit churn risk.** An operator who edits `extra_args` in TOML to flip `--embeddings` on/off doesn't automatically re-derive `capabilities`. Surfaced via the existing `capabilities migrate` CLI (`src/hal0/cli/capabilities_commands.py`) which gets a new `--reconcile-slot-capabilities` flag.
- **Multi-capability semantics on llama-server.** A llama-server slot can technically serve chat AND embeddings if you pass `--embeddings`, but the upstream's behaviour on a hybrid call is undefined. The derivation table picks ONE capability per llama-server flag set; if an operator really wants both, they declare it manually. Not silently allowed.
- **Adds a TOML field that operators will see.** Worth a one-paragraph mention in the slot TOML docs.

### Risks

- **Backfill race with capability orchestrator.** ADR-0002's `CapabilityOrchestrator.apply()` rewrites slot TOMLs on enable. If backfill and an apply land on the same TOML in the same tick, the apply's write could overwrite the backfilled `capabilities` field. Mitigation: backfill runs *before* the API starts accepting requests (startup hook, not a background task), so the orchestrator's first apply already reads the backfilled value.
- **FLM multiplex slot capabilities will need to update with the model list.** If FLM adds `stt` to its served set (via `flm_served_models()`'s `capabilities` field), the slot's static `capabilities = ["chat", "embed"]` would lag. Either widen the FLM default to include `stt`, or have FLM-backed slots derive on read (the one exception to the "TOML wins" rule). Probably the second; the FLM slot is already special-cased in `SlotCard.vue:isFlmSlot`.

## Alternatives considered

1. **Derived only, no TOML field.** Compute capabilities on every `/api/slots` read from `(provider, backend, extra_args)`. Simpler — no migration, no schema change. Rejected because (a) it can't express "I built this slot for embed even though llama-server *could* do chat" without inventing a sentinel anyway, and (b) the derivation has to run on every poll, which is fine but couples the read path to the heuristic.

2. **Slot-name heuristic in the UI only.** Hard-code `{primary, nano} ⇒ chat`, `{embed} ⇒ embed`, etc. in the Vue components. Rejected because every caller would have to re-encode the table, and user-defined slot names (already supported) trivially break it.

3. **Reuse `capabilities.toml` as the source.** ADR-0002's `capabilities.toml` already maps `(slot, child) → capability`. Could be read back to derive slot capabilities. Rejected because (a) `primary` and `nano` (chat slots) deliberately do NOT have entries in `capabilities.toml` — chat lives outside the capability overlay — and (b) it would invert the direction the overlay was designed for (overlay reads slot state to render UI cards; this ADR needs slot state to *carry* the capability label).

4. **Compute from the currently-loaded model's `model.capabilities`.** Works for non-empty slots, useless when offline. The picker case needs the answer specifically when the slot is offline. Rejected.

## Implementation slice

Not part of this ADR — sketched here so the review can sanity-check the size:

1. `src/hal0/config/schema.py` — add `capabilities: list[str] = []` to `SlotConfig`. Validation list lifted from existing `_CAPABILITY_TO_CHILD` keys plus `"chat"`.
2. `src/hal0/slots/manager.py` — startup backfill (`_backfill_slot_capabilities()`); apply-time validation in `_spawn_locked()` before docker run; thread `capabilities` through `Slot` dataclass and `to_dict()`.
3. `src/hal0/slots/defaults.py` (new) — pure function `default_capabilities(provider, backend, extra_args)` mapping per the table above.
4. `src/hal0/api/routes/slots.py` — surface `capabilities` on the response, accept it on `POST /api/slots`.
5. `src/hal0/cli/capabilities_commands.py` — extend `hal0 capabilities migrate` with a `--reconcile-slot-capabilities` flag.
6. `ui/src/views/Slots.vue`, `ui/src/components/SlotCard.vue`, `ui/src/views/Dashboard.vue` — drop the 2-hop joins.
7. Tests:
   - `tests/slots/test_default_capabilities.py` — derivation table
   - `tests/slots/test_capability_backfill.py` — startup migration is idempotent
   - `tests/slots/test_load_capability_validation.py` — model/slot mismatch raises typed error
   - `tests/api/test_slots_response_shape.py` — `capabilities` field present
   - `ui/tests/e2e/specs/slots-picker.spec.ts` — primary slot's offline picker no longer surfaces embed/rerank/image rows

Estimated 1–2 day implementation slice.

## References

- [ADR 0002 — Capabilities overlay on top of flat slots](./0002-capabilities-overlay.md) — owns the `(capability child → slot)` direction; this ADR owns the inverse.
- PR Hal0ai/hal0#89 — `loadChatModels()` 2-hop join motivating the field.
- PR Hal0ai/hal0#91 — backend-only inline picker filter and the remaining capability leak it exposes.
- `[[hal0_capability_catalog_provider_bug]]` — same shape (missing field on row causes joiner-side bug); also the motivation for this ADR's "every caller pays" framing.
- `[[hal0_orchestrator_drift_bug]]` — drift invariant pattern that the backfill + apply-time validation are modelled on.
