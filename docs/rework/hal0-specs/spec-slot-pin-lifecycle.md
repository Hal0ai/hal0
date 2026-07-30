# Slot-pin-lifecycle — the operator pin is the residency control, and the only one

> Ratified 2026-07-29 (user directive, pin/drawer-audit session). Living spec
> for the §21.10 operator pin as it stands after #1368 (#1367). Does **not**
> supersede a prior spec — §21.10 defined the pin as an *eviction exemption*
> for a fixed anchor set; this documents its promotion to a first-class,
> per-slot, operator-writable field with a UI surface, and the resulting
> retirement of `SlotConfig.enabled` (staged separately, see §7).
>
> Related: `spec-hw-slot-ownership` (what a slot owns), `spec-flags-ownership`
> §4 (what a slot no longer owns).

## 1. Decision

**Residency has exactly one operator control: `pinned`.**

A pin answers one question — *may the orchestrator evict this slot to reclaim
memory?* — and nothing else. It is not an on/off switch, not a health signal,
and not an activation flag. A pinned slot is exempt from automatic eviction
(idle-TTL and memory-pressure alike) and refuses the destructive verbs without
an explicit override.

Rationale (one-owner rule, as in `spec-hw-slot-ownership §1`): residency
previously had *two* writers with overlapping meaning — the `pinned` exemption
and the `enabled` flag — and the drawer surfaced only the second. Operators
reaching for "keep this slot resident" flipped `enabled`, which is not that
control. Collapsing to one field means the question has one answer and the UI
can only express the real thing.

## 2. Effective-pin resolution

The pin is a **tri-state on disk** — `true`, `false`, absent — and the absent
case is *not* the same as `false`:

```
explicit SlotConfig.pinned key present  →  that value wins, both directions
key absent (or config unreadable)       →  default anchor set applies
```

`hal0.slots.reaper.is_pinned(canonical_name, cfg)` is the single resolver:

```python
if cfg is not None and cfg.get("pinned") is not None:
    return bool(cfg["pinned"])
return canonical_name in _PINNED_BY_DEFAULT
```

`_PINNED_BY_DEFAULT = frozenset({"agent", "utility", "npu"})` — the anchors that
must stay resident on a fresh install because the box is unusable without them.

Two properties are load-bearing and easy to lose in a refactor:

- **Key presence, not truthiness, decides.** `cfg.get("pinned") is not None`
  distinguishes "authored `false`" from "unset". A plain `if cfg.get("pinned")`
  would make an authored `false` indistinguishable from absent, and the anchors
  would become un-un-pinnable — the exact bug #1367 filed.
- **The resolver takes the raw TOML dict**, not a parsed `SlotConfig`. A pydantic
  model with a defaulted field cannot report key absence.

`SlotManager.is_pinned(slot_name)` resolves the alias first, then delegates. A
missing or unreadable config is treated as "not pinned beyond the default set" —
fail-open, matching the reaper's eviction-timeout contract, because an
unreadable config must not silently make a slot immortal.

## 3. Guarded verbs

| Verb | Guard | Bypass |
|---|---|---|
| automatic idle-TTL eviction | skipped when pinned | — (never overridable) |
| automatic pressure eviction | skipped when pinned | — (never overridable) |
| manual unload (`POST /api/slots/{name}/unload`) | `409 slot.pinned` | `?force=true` |
| delete (`DELETE /api/slots/{name}`) | `409 slot.pinned` | `?force=true` |

The two automatic paths have **no** override by design: a pin exists precisely to
survive the orchestrator's own judgement. The two operator-initiated paths do,
because an operator who types the slot name and passes `force` has stated intent
the orchestrator hasn't.

## 4. Payload lift

`GET /api/slots` lifts the **effective** pin onto every entry:

```python
entry["pinned"] = reaper_is_pinned(name, cfg)
```

Effective, not raw: the list is what the dashboard renders, so a fresh install's
`utility` must read `pinned: true` from the anchor set without the client
knowing the anchor set exists, and without a per-slot `/config` round-trip.

Consequence worth stating explicitly: **`entry.pinned` is a resolved boolean,
never tri-state.** A client cannot distinguish "explicitly pinned" from
"anchor-pinned" from this payload, and should not try to — the distinction is a
storage detail, and the write path (§5) collapses it anyway.

## 5. UI surface

The slot drawer header carries the toggle (`ui/src/dash/slot-modals.jsx`):

- reads `slot.pinned === true` — the lifted effective value
- writes instant-apply `PUT /api/slots/{name}/config { pinned }`, outside the
  batched Save, so it is persisted the moment it is flipped
- labels **Pinned / Unpinned** — the operator-facing name of the actual concept

Because the write always sends an explicit boolean, flipping the toggle on an
anchor authors the key and thereby *leaves* the default set permanently. That is
intended: an operator who un-pins `utility` means it, and the anchor default
should not silently reassert on the next read.

The toggle deliberately does **not** gate on the guarded verbs — it is not a
confirmation dialog. Unload/delete keep their own 409 + `?force=true` handling.

## 6. Verification

- Unit (`reaper.is_pinned`): explicit `true` pins a non-anchor; explicit `false`
  un-pins an anchor; absent key falls through to the anchor set; unreadable
  config falls back to the anchor set.
- Route: unload and delete both 409 `slot.pinned` on a pinned slot and succeed
  with `?force=true`, including **the un-pinned-anchor path** (an anchor with
  `pinned = false` must delete without `force`).
- Payload: `GET /api/slots` reports `pinned: true` for a fresh-install anchor
  with no key on disk.
- UI: the drawer header renders Pinned/Unpinned from the lifted value and writes
  `{pinned}` — `ui/tests/e2e/specs/slot-pin-toggle-v3.spec.ts`.

## 7. Relationship to `enabled` (staged)

`SlotConfig.enabled` is being removed. The pin took over the only job operators
were actually using `enabled` for, and what remains of it duplicates a signal the
system can derive:

- **residency** → `pinned` (this spec)
- **activation** → **model presence**. A `type: llm` slot with a model bound is
  active; one with no model is not. This generalizes the rule the NPU trio
  already followed, and removes a flag that could contradict reality (a slot
  marked `enabled = true` with no model cannot serve; one marked `false` with a
  live healthy container *is* serving).

Removal is staged separately on `feat/remove-slot-enabled` — no PR open at time
of writing; it touches `slot-status.js`, `slots.jsx`, `slot_view`, routing, the
capability orchestrator and a wide band of specs, so it lands on its own.

Until it does, `enabled` remains readable and writable through
`PUT /api/slots/{name}/config` and is still honoured by the backend (an explicit
`enabled: false` write unloads a live slot). **It no longer has a drawer
surface** — #1368 replaced that toggle with the pin. Treat `enabled` as
deprecated: do not add new readers.
