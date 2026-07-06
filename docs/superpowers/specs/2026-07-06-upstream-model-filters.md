## Problem Statement

An operator wants to curate which upstream models appear in hal0's model picker. Today the only control is `advertise_models = true/false` per upstream — all-or-nothing. Setting it to `false` hides every model from that provider, even the ones the operator wants to see. Setting it to `true` floods the picker (e.g. 300+ OpenRouter models) and makes discovery sluggish.

The operator should be able to say "from OpenRouter, show me Anthropic and Google models, plus a handful of specific ones — nothing else."

## Solution

Add optional per-upstream model filters to `upstreams.toml`. An operator can configure **allowlists** (explicit model IDs), **include** patterns (glob or prefix), and **exclude** patterns. Filters apply at the `/v1/models` aggregation layer only — excluded models remain dispatchable by explicit name but don't appear in the discovery catalog.

## User Stories

1. As an operator, I want to allowlist specific OpenRouter models (e.g. `anthropic/claude-sonnet-4`, `google/gemini-2.5-pro`) so that only curated models appear in the TUI model picker without losing dispatch access to the rest.
2. As an operator, I want to include all models matching a prefix pattern (e.g. `anthropic/*`, `google/*`) so that I can curate by provider without listing every model individually.
3. As an operator, I want a combined allowlist + pattern-include approach so that I can say "show me all Anthropic and Google models, plus these 3 specific DeepSeek ones."
4. As an operator, I want to exclude specific models or patterns (e.g. `*:free`, `nvidia/*`) so that I can hide low-quality or sampled models without blocking an entire upstream.
5. As an operator, I want exclude to take priority over include so that I can say "all Anthropic models EXCEPT claude-3-haiku."
6. As an operator, I want models excluded from the catalog to remain dispatchable by explicit name so that sub-agent configs using `model: "openrouter/anthropic/claude-sonnet-4"` still work.
7. As an operator, I want the same filter mechanism to work on all upstream kinds — remote (OpenRouter, MiniMax, LiteLLM) and slot-backed — so the control surface is consistent.
8. As an operator, I want to configure filters in `upstreams.toml` (the same place I already configure upstreams) and have them take effect immediately on hal0-api restart, matching the existing hot-reload-or-restart contract.
9. As an operator, I want model discovery to remain fast even when only a subset of models is advertised, because the full upstream catalog is still fetched (for dispatch) but filtered before surfacing.
10. As an operator, I want clear feedback when a filter config is invalid (typo in a glob, empty list), surfaced as a startup warning that doesn't block API launch.

## Implementation Decisions

- **Config shape**: `UpstreamEntry` gains an optional `model_filters` table with three lists: `models` (exact IDs), `include` (glob patterns), `exclude` (glob patterns). All three are optional; omitting the entire table means "no filter, advertise everything" (current behavior).
- **Filter semantics**: A model ID is advertised when (1) it matches at least one include OR is in the models allowlist, AND (2) it does NOT match any exclude pattern. An empty or absent include + models means "no include filter — everything included unless excluded." Exclude always overrides include. Exact-specified models are OR'd with include patterns; both are gated by exclude.
- **Glob matching**: Use Python's `fnmatch` (already in the stdlib) for pattern matching. Patterns support `*` (any chars) and `?` (single char). No regex — operators don't need that complexity for model IDs.
- **Performance**: Filters are applied after the upstream model list is fetched (same fetch that populates the dispatch cache). No additional network calls. The filter is a simple in-memory match — O(n × p) where n is model count and p is pattern count, both small.
- **Schema validation**: Filter fields are validated at config-load time — empty lists are permitted (they mean "nothing"), invalid field names are rejected by Pydantic's `extra = "forbid"` (or similar), and the glob patterns don't need runtime validation beyond being non-null strings.
- **Dispatch unaffected**: The `upstreams.fetch_models()` call that populates the dispatch passthrough cache is NOT filtered — it still fetches the full catalog. Only the `/v1/models` handler passes each model ID through the filter before appending to the response. Excluded models remain in the dispatch cache and are accessible by explicit name.
- **Provider panel compatibility**: The `/api/providers` management endpoint that reads and writes upstreams.toml must round-trip the new filter fields — adding them to the schema, displaying them in the UI, and preserving them on save.
- **ADR reference**: This extends ADR-0023 (slot aliases in /v1/models) to cover remote upstreams — the alias entries already curate the slot-surface; model filters curate the remote-surface, making the entire catalog operator-controllable.

## Testing Decisions

- **What makes a good test**: Test the filter logic in isolation (given a list of model IDs and a filter config, assert the surviving list). Test the `/v1/models` handler integration (configure an upstream with filters, verify the response omits excluded models). Do NOT test dispatch behavior — dispatch is explicitly unfiltered.
- **Units**: `tests/config/test_upstream_filters.py` — schema validation (valid/invalid shapes), `fnmatch` behavior on realistic OpenRouter model ID patterns, priority rules (exclude over include, models + include interaction).
- **Integration**: `tests/api/test_v1_models_filters.py` — mock an upstream returning a known model list, apply filters, assert `/v1/models` response matches. Test the no-filter default (existing behavior preserved). Test empty filter = pass-all.
- **Prior art**: The existing `tests/api/test_v1_slot_alias_models.py` tests follow the same pattern (direct unit tests on the helper, plus a handler integration test). The `tests/config/test_loader.py` tests validate schema shapes. Follow both patterns.

## Out of Scope

- **Dispatch-time filtering** — excluded models are still dispatchable by explicit name. Filtering at dispatch time is a separate feature (operator-level access control for sub-agents) and is not addressed here.
- **Hermes/picker-side filtering** — this PRD controls the API catalog. Agent-side model curation (e.g. Hermes's built-in model picker filtering) is a separate concern and uses the API catalog as its input.
- **Dynamic filter updates without restart** — filters take effect on hal0-api restart, matching the existing upstreams.toml contract. Hot-reload of filter changes is not in this PRD but could be added later if the upstreams reload story is broadened.
- **Per-agent filters** — filters apply per-upstream, not per-agent. A future PRD could layer per-agent visibility on top of this (e.g. "Hermes sees OpenRouter; Browser Agent does not").

## Further Notes

- This was motivated by the 2026-07-06 session where we set `advertise_models = false` on OpenRouter as a workaround after fixing the cold-slot visibility bug (PR #1153). The workaround is effective but loses all OpenRouter models from the catalog — operators need a finer-grained control.
- The `minimax` upstream could also benefit from this (currently showing 8 models when the operator might only want 2-3).
- The pattern is analogous to the capability orchestrator's `selections` table in `capabilities.toml` — each capability is enabled/disabled and configured per-selection. Upstream model filters bring the same curation paradigm to the chat model surface.
