# Type-driven utility slot routing

**Date:** 2026-06-19
**Branch:** `feat/type-driven-utility-routing` (worktree off `main`)
**Status:** approved design

## Problem

On the inference dashboard, slot cards are split into two zones:

- **Main engine grid** — conversational LLM cards (chat / agent), full-size with metrics.
- **Support footer** ("utility · embed · rerank · voice") — compact mini-cards for the non-conversational tier.

Which zone a card lands in is decided by a free-form `group` field, not by the
slot's capability `type`:

```js
// ui/src/dash/inference-pane.jsx:113-116
const UTIL_GROUPS = new Set(['embed', 'rerank', 'tts', 'stt', 'voice'])
function isUtilGroup(group) {
  return UTIL_GROUPS.has(String(group || '').toLowerCase())
}
// :638-639
const headlineRows = rows.filter((r) => !isUtilGroup(r.s.group))
const utilRows     = rows.filter((r) =>  isUtilGroup(r.s.group))
```

`group` is a separate dropdown in the create modal (`chat / embed / voice /
img / custom`, slot-modals.jsx:296), set independently of **Type**, defaulting
to `"chat"` in the modal (slot-modals.jsx:96) and `"custom"` in the backend
(manager.py:1516,1545). The backend stores it verbatim — it is **not** derived
from `type`.

**Symptom:** a slot created with `Type=tts` but the default `group="chat"`
(e.g. the live `test-tts` slot) renders in the main engine grid instead of the
support footer. A slot's nature already lives in `type`; the layout decision was
wired to a second, hand-set field that can disagree with it. Two fields that
should never diverge, can.

## Goal

A slot's dashboard placement is derived **entirely** from its capability
`type`. The redundant `group` field is retired so placement can never drift
again.

## Non-goals

- **Seeding iGPU utility slots for all four capabilities (rejected).** Embed and
  rerank already seed to the iGPU (rerank → `gpu-vulkan`, embed →
  `gpu-vulkan/rocm`; `installer/etc-hal0/slots/*.toml`). TTS (kokoro) and STT
  (FastFlowLM) have no iGPU runtime in this stack — the non-NPU alternative for
  STT is CPU whisper, not the iGPU; TTS is CPU-only. Seeding dormant iGPU
  tts/stt slots would add clutter for zero new capability. Out of scope.
- No backend routing / `default_slot_for` changes. `group` is a UI-only rollup;
  request routing already keys on `type` + `default` (manager.py:1432-1448) and
  is unaffected.

## Design

### 1. Type-driven split (ui/src/dash/inference-pane.jsx)

Replace the `group`-keyed predicate with a `type`-keyed one:

```js
const UTIL_TYPES = new Set(['embedding', 'reranking', 'tts', 'transcription'])
const isUtil = (s) => UTIL_TYPES.has(String(s.type || '').toLowerCase())
```

- `headlineRows` = `!isUtil(s)` and not image → chat/agent LLM cards (main grid)
- `utilRows`     = `isUtil(s)` → support footer
- The two image filters (inference-pane.jsx:575, 625) switch from
  `(s.group || '') !== 'img'` to `String(s.type) !== 'image'`.

Effect: any utility-type slot lands in the footer regardless of how it was
created. The existing `test-tts` slot moves down automatically — no migration,
no user action.

### 2. Retire the `group` field

- **Create modal (ui/src/dash/slot-modals.jsx):** remove the `group` dropdown
  (296), the `group` / `setGroup` state (96, 110), and `group` from the POST
  body (160).
- **EmptySlotCard (slot-modals.jsx:1279, 1292):** the placeholder chip shows
  `type` instead of `group` (type is now the real category). Drop the `group`
  prop.
- **Skip-path seeded list (ui/src/dash/slots.jsx:588-596):** drop the `group`
  key from each `SEEDED` entry; callers that pre-fill the modal pass `type`
  only.
- **Backend (src/hal0/slots/manager.py):** `add_slot` stops requiring and
  writing `group` (1497, 1516, 1545). Serialization drops the `group` key.
  Loading a TOML that still carries a stray `group` key is tolerated (ignored),
  so existing on-disk slots upgrade cleanly.

### 2a. Other `slot.group` readers (full inventory)

Every remaining reader already prefers `type`; `group` is a vestigial fallback
in each, so retiring it is a removal, not a rewrite:

| File:line | Current use | Disposition |
|---|---|---|
| `inference-pane.jsx:575,625,638,639` | zone split + img filter | rewritten to `type` (§1) |
| `slot-modals.jsx:96,110,160,296` | modal state + dropdown + POST | removed (§2) |
| `slot-modals.jsx:1279,1292` | EmptySlotCard chip | show `type` (§2) |
| `slots.jsx:633-636,666,667` | skip-path SEEDED grouping | drop `group`, derive from `type` (§2) |
| `quickchat-card.jsx:89-95` | `isChatCapable`: `g==='chat'` OR `t==='llm'` | drop the `g==='chat'` fallback; `type==='llm'` already covers it |
| `connections.jsx:140-148` | `slotGroup`: type-first, group as last resort (146) | drop line 146 fallback; every real type maps before it |
| `command-palette.jsx:256` | search keyword `${s.group||""}` | drop the `group` term |

**Not affected** (different `group` concept, left untouched): `extras.jsx`
(log-line groups), `activity-log.jsx` (ARIA `role="group"`), and
`manager.py:2167,2171` (the image *exclusive group* / idle-restore mechanism,
keyed on `type==='image'`/device, not the slot rollup attribute).

### 3. Compatibility

- Old slot TOMLs and `state.json` may carry `group` — these are ignored on
  load, never written back. No migration step required.
- Seeded slot catalog (`SEEDED_SLOTS`, manager.py:70) is unchanged; only the
  per-slot `group` attribute is dropped.

## Data flow

```
create modal (type, profile, model, device)   ← no `group`
        │  POST /api/slots
        ▼
manager.add_slot(...)                          ← no `group` written
        │  slot serialized with `type`
        ▼
GET /api/slots → UI
        │
inference-pane: isUtil(s) keyed on s.type
        ├─ true  → support footer (MiniCard)
        └─ false → main engine grid (SlotCard) / image pane (type==='image')
```

## Testing

- **Backend:** update `tests/config/test_profiles.py`,
  `tests/api/test_profiles_route.py`, and any slot-create test asserting
  `group`; add a test that `add_slot` ignores/omits `group` and that a TOML
  carrying a stray `group` loads without error.
- **Frontend e2e:** add a case to the inference-pane util-zone spec asserting a
  `tts`-type slot with a non-utility group (or no group) renders in `utilRows`,
  not the main grid. Update existing specs that assert the `group` chip on empty
  cards to assert `type`.

## Risks

- All current `slot.group` readers are inventoried in §2a; each already keys on
  `type` with `group` as a fallback, so the change is low-risk. Re-grep
  `\.group` / `"group"` / `'group'` across `ui/` and `tests/` during
  implementation to catch anything added since.
- Two live sessions share the `fix/hardware-gtt-total-live` checkout; this work
  is isolated in the `feat/type-driven-utility-routing` worktree off `main` to
  avoid colliding with their uncommitted changes.
