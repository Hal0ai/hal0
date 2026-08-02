# Slot autoload + eviction priority — design

Date: 2026-08-02
Branch: `h0/slot-auto-load-priority`

## Problem

Slots start by themselves on host boot and API restart, with no visible setting that
says so. Today "activation" is implicit: a slot whose TOML has a non-empty
`[model].default` gets a generated systemd unit with `WantedBy=hal0.target`, so it is
pulled up at boot, and `_maybe_adopt_running_slot()` re-adopts it on API restart.
Binding a model to a slot silently opts it into boot start — surprising and sometimes
undesirable.

Separately, eviction (pressure and pre-load) picks victims in pure LRU order with an
opt-in `lru=true` gate. There is no way to say "unload this slot before that one".

## Decisions (operator-approved 2026-08-02)

1. `autoload` migrates as `true` for existing slots with a bound model; new slots
   default `false`.
2. `priority` is an integer 0–100, default 50; lower evicts first, LRU breaks ties.
3. Priority replaces the `lru=true` opt-in gate. All non-pinned slots become
   evictable; `pinned` remains the absolute never-evict override.

## Design

### 1. `autoload` — explicit boot-start setting

- New field `SlotConfig.autoload: bool` (per-slot TOML, `/etc/hal0/slots/*.toml`).
  Default `false` for newly created slots.
- **Migration shim:** when loading a slot TOML that has no `autoload` key, derive
  `autoload = bool(model.default)`. The derived value is written back on the next
  config save. Upgrading changes no behavior until the operator flips toggles.
- **Mechanism:** the systemd unit generator emits `WantedBy=hal0.target` only when
  `autoload=true`. With `autoload=false` the unit still exists (manual load, swap,
  and API-driven start keep working) but nothing starts it at boot.
- **Adoption unchanged:** `_maybe_adopt_running_slot()` still adopts a running unit
  regardless of `autoload`. The flag governs starting; it never stops or kills
  running work.
- Net effect: "model bound" and "starts on boot" are decoupled. The only reason a
  slot starts by itself is an explicit `autoload = true`.

### 2. `priority` — eviction ordering

- New field `SlotConfig.priority: int`, range 0–100 inclusive, default 50.
  Out-of-range values are rejected at config load and by the API (422).
- **Victim ordering everywhere victims are picked:** sort candidates by
  `(priority ascending, last_used ascending)` — lowest priority first, oldest-idle
  first within equal priority. Applied in:
  - `SlotReaper.pressure_evict_once()` (free-RAM-floor pressure eviction)
  - `preload_evict.admit()` (synchronous evict-to-fit before a load)
- **Hard protections unchanged:** `pinned` slots and the default anchor slots
  (`agent`, `utility`, `npu`) are never evicted. `priority=100` is *not* pinned —
  it is merely evicted last.
- **`lru=true` gate removed:** every non-pinned, non-serving slot is an eviction
  candidate. The old `lru` key is still accepted in TOML but ignored, with a
  deprecation warning logged once per slot at config load.
- **Ordering only, no hard block:** a low-priority load may still evict a
  higher-priority slot when pressure requires it; only `pinned` and anchor status
  block eviction.
- Idle-TTL eviction is untouched — it is time-based and already configurable
  per slot via `idle_timeout_s`.

### 3. API and UI surface

- `GET /api/slots` includes `autoload` and `priority` per slot.
- `PUT /api/slots/{name}/config` accepts both fields; `priority` validated 0–100.
- Slot drawer (`ui/src/dash/slot-modals.jsx`, `EditSlotDrawer`):
  - "Auto-load on start" toggle in the Model group (next to the model/default
    fields it modifies the meaning of).
  - "Eviction priority" number input (0–100) with hint text
    "lower unloads first".
  - Create modal: both fields present with defaults (`autoload=false`,
    `priority=50`).

## Error handling

- `priority` outside 0–100 in TOML → config load error naming the slot and field.
- `priority` outside 0–100 via API → 422 with field-level message.
- Deprecated `lru` key → warning log, value ignored, not an error.

## Testing

- Schema: defaults (`autoload=false`, `priority=50`), range validation, TOML
  round-trip.
- Migration shim: TOML without `autoload` + bound model → `true`; without model →
  `false`; explicit value wins; write-back on save.
- Generator: `WantedBy=hal0.target` present iff `autoload=true`.
- Reaper: pressure eviction picks lowest priority first, LRU within equal
  priority, skips pinned/anchors/serving; `lru` flag no longer gates.
- preload_evict: same ordering; deprecation warning emitted once for `lru`.
- API: fields round-trip through GET/PUT, 422 on out-of-range priority.

## Out of scope

- Per-slot boot ordering / dependency between autoloaded slots.
- Priority influence on idle TTL.
- Any change to adoption semantics on API restart.
