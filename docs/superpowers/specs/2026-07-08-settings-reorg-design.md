# Settings Reorganization — Design Spec

**Date**: 2026-07-08
**Status**: approved
**Touches**: `ui/src/dash/settings.jsx` only

## Problem

The dashboard Settings view has accumulated cruft:

- **General** is buried at position 8 of 10, containing only telemetry + a locked-theme stub ("not available in this release").
- **Memory graph settings** (`[memory.graph]` — extraction slot, enabled toggle, LLM timeout) are invisible in the dashboard; only configurable via `hal0 memory graph enable/disable` CLI.
- **Memory reranker settings** (`[memory.embedding]`) are buried in the Advanced grab-bag.
- **Advanced** is an undifferentiated flat list: slots runtime, dispatcher, memory engine, reranker, activity log — all mixed together.
- **NPU** has a dead slot-picker dropdown (only one NPU slot ever exists on a real box).
- **Default slots** is a separate top-level nav item instead of living under Slots.

## Design

### New sidebar order

| # | Section | Source |
|---|---------|--------|
| 1 | General | `[telemetry]`, API version, `[meta]` |
| 2 | Slots | `[slots]` runtime + default slot pickers (was Default slots) |
| 3 | NPU | FLM knobs only (npu.toml via `PUT /api/slots/{name}/config`) |
| 4 | Memory | `[memory]` engine + `[memory.graph]` + `[memory.embedding]` |
| 5 | Voice | Unchanged |
| 6 | Image-gen | Unchanged |
| 7 | Storage | Unchanged |
| 8 | Secrets | Unchanged |
| 9 | Updates | Unchanged |
| 10 | Advanced | `[dispatcher]` + `[activity]` only |
| 11 | About | Unchanged |

### General (position 1)

- Keep: anonymous telemetry toggle (`[telemetry].enabled`)
- Add: hal0 version (read-only, from `/api/version`)
- Add: schema version (read-only, from `[meta].schema_version`)
- Drop: Theme "dark · locked" stub row
- Drop: "density / accent customization is not available" description
- New description: "Platform identity and privacy."

### Slots (position 2, new section)

Absorbs two things into one view:

**Sub-panel 1: Slot defaults** — moved from current "Default slots" nav item. For each slot type with ≥2 slots, pick which is the default. Unchanged logic, same `PUT /api/slots/{name}/config { default: true/false }` calls.

**Sub-panel 2: Runtime** — moved from Advanced. Same fields, same `PUT /api/settings` deep-merge:

| Field | TOML key | Type |
|-------|----------|------|
| Max slots | `slots.max_slots` | Number |
| Port range start | `slots.port_range_start` | Number |
| Port range end | `slots.port_range_end` | Number |
| Idle timeout | `slots.idle_timeout_s` | Number (0 = never evict) |
| Evict pressure | `slots.evict_pressure_mb` | Number |
| Publish host | `slots.publish_host` | Text |

### NPU (position 3)

FLM-specific knobs only. Drops the slot picker dropdown.

- Context size — `[model].context_size` in npu.toml (number, ≥512)
- Load embeddings — `[npu].embed` (toggle)
- Load ASR — `[npu].asr` (toggle)
- Occupancy — read-only from `/api/npu/occupancy`

If `npuSlots.length === 1`, use it directly (no selector). If 0, show "no NPU slot configured" hint. `useSlotConfig(selName)` and `doSave` logic unchanged — writes via `PUT /api/slots/{name}/config`.

### Memory (position 4, new section)

Three sub-panels:

**Engine**

| Field | TOML key | Type |
|-------|----------|------|
| Engine | `memory.engine` | Dropdown: `hindsight` / `pgvector` |

Saved via `PUT /api/settings { memory: { engine } }`. Requires hal0-api restart (existing apply-plan registry handles the badge).

**Graph extraction** — currently CLI-only; surfaced to dashboard for the first time.

| Field | TOML key | Type |
|-------|----------|------|
| Enabled | `memory.graph.enabled` | Toggle |
| Extraction slot | `memory.graph.extraction_slot` | Dropdown (live enabled llm slots) |
| LLM timeout | `memory.graph.llm_timeout_s` | Number (30–3600) |

Saved via `PUT /api/memory/graph { enabled, extraction_slot, llm_timeout_s }` (existing endpoint in `routes/memory.py`). The extraction slot dropdown validates against the live slot set — server-side validator already rejects invalid slot names.

**Reranker** — moved from Advanced, unchanged.

| Field | TOML key | Type |
|-------|----------|------|
| Rerank model | `memory.embedding.rerank_model` | Text |
| Gateway URL | `memory.embedding.rerank_gateway_url` | Text |
| Connect timeout | `memory.embedding.rerank_connect_timeout_s` | Number |
| Read timeout | `memory.embedding.rerank_read_timeout_s` | Number |

### Advanced (position 10)

Shrinks to just `[dispatcher]` and `[activity]`:

- Prefetch timeout (`dispatcher.prefetch_timeout_s`)
- Prefetch parallel cap (`dispatcher.prefetch_parallel_cap`)
- Activity enabled (`activity.enabled`)
- Activity retention (`activity.retention_days`)
- Activity max rows (`activity.max_rows`)

Same `ADV_GROUPS` / `AdvRow` rendering — just the groups array shrinks.

### Image-gen (position 6)

No changes. Already comprehensive: enable toggle + engine selector + model picker (capability slot `img.img`), default size/steps/idle-restore (`[image]` slot TOML), and a live status chip. All `ImageGenConfig` schema fields are surfaced. Just slides from position 5 to 6.

### Other unchanged sections

Voice, Storage, Secrets, Updates, About — no changes, just slide to new positions.

## Implementation plan

Single file: `ui/src/dash/settings.jsx`.

1. Update `sections` array with new order and labels
2. Rewrite `GeneralSection` — drop theme stub, add version rows
3. Create `SlotsSection` — combine DefaultSlotsSection body + Slots runtime group from Advanced
4. Simplify `NpuSection` — remove slot selector, keep single-slot path
5. Create `MemorySection` — engine dropdown + graph extraction toggle/slot/timeout + reranker fields
6. Trim `AdvancedSection` `ADV_GROUPS` — remove slots and memory groups
7. Remove old `DefaultSlotsSection` export/case from `section === "defaults"`
8. Wire new sections to `{section === "slots" && <SlotsSection />}` and `{section === "memory" && <MemorySection />}`

No backend changes. Every setting already has an API endpoint.

## Spec self-review

- No placeholders, TODOs, or incomplete sections.
- Sidebar order, section contents, and TOML keys are all internally consistent.
- Scope is focused: one file, one view, no backend changes.
- No ambiguous requirements — every section has a concrete field list and save strategy.
