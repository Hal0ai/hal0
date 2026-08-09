# Unified AI Capabilities Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Settings page ("AI Capabilities", `#settings/capabilities`) that owns all TTS/STT/EMBED/RERANK/IMG capability configuration, absorbing the Voice, Image Generation, and NPU pages, adding first-ever UI for the embed/rerank selections, and fixing the rerank slot-name split-brain (`embed-rerank` → canonical `rerank`).

**Architecture:** Stacked `s-panel`s (TTS, STT, Embeddings, Reranking, Image, collapsed NPU anchor) under a status-strip jump nav. A shared `useCapabilitySelection(group, child)` hook replaces the hand-rolled useState/useEffect/dirty loop each old page duplicated; shared presentational helpers (`statusChip`, `PanelHeader`, `PanelFooter`, `EnabledRow`, `ModelRow`) replace the copy-pasted chip/select styles. Backend change is minimal: rename the rerank capability slot to `rerank` (dispatcher already routes there) plus a back-compat `SLOT_ALIASES` entry.

**Tech Stack:** React 18 + @tanstack/react-query (UI), vitest (unit), Playwright (e2e), Python/pydantic backend, pytest.

## Global Constraints

- Decisions locked with the operator (2026-08-09): stacked panels + jump nav (no tabs); memory reranker client keys **stay** on the Memory page (Rerank panel links to them); NPU page **folds in** as a collapsed advanced panel; rerank split-brain fixed in this branch with **`rerank`** as the canonical slot name.
- Old deep links must keep resolving: `#settings/voice`, `#settings/imagegen`, `#settings/npu` → `capabilities` via `SECTION_ALIASES`.
- Panel titles must remain exactly `TTS` and `STT` (e2e locators filter on `.k span` text `/^TTS$/` / `/^STT$/`).
- Panel order on the page (top to bottom): TTS, STT, Embeddings, Reranking, Image generation, NPU anchor. E2e `Model`-row indexes depend on this: TTS=0, STT=1, Embed=2, Rerank=3, Image=4.
- No React hooks inside `.map()` callbacks — `ui/src/dash/__tests__/react-hooks-order.test.mjs` statically scans every file under `ui/src/dash`.
- `FieldInfoIcon` is a window global (`dash/primitives.jsx:1195`) — use it **unimported**, like every existing settings page.
- Conventional Commits; one logical change per commit; run checks before each commit.
- Python tests: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest <paths>` (the HAL0_HOME override kills ~204 `/etc/hal0-perms` pseudo-errors).
- UI unit tests: `cd ui && npm run test:unit`. Hooks-order scan: `node ui/src/dash/__tests__/react-hooks-order.test.mjs`.
- Out of scope (file follow-up issues, do not implement): surfacing `realtime.stt_model/tts_model/tts_voice`; the img device allow-list mismatch (`catalog.py:77` offers only `gpu-vulkan`, seed ships `gpu-rocm`); migrating stale on-disk `embed-rerank.toml` slots (alias covers dispatch; orphan cleanup is operator work); sub-anchor deep links (`#settings/capabilities/tts`).

---

### Task 1: Backend — canonical `rerank` slot name

The orchestrator creates/loads slot `embed-rerank` for the `embed.rerank` selection (`orchestrator.py:71`), but the dispatcher routes `/v1/rerankings` to slot `rerank` (`dispatcher/router.py:120` `_RERANK_DEFAULT = "rerank"`) and the static seed is `installer/etc-hal0/slots/rerank.toml`. Enabling rerank via the API therefore spins a slot nothing routes to. Fix: rename the mapping to `rerank`, alias the old name.

**Files:**
- Modify: `src/hal0/capabilities/orchestrator.py:71` (+ comment refs at `:378`, `:712`)
- Modify: `src/hal0/stacks/apply.py:109`
- Modify: `src/hal0/slots/routing.py:82-87` (`SLOT_ALIASES`)
- Modify: `ui/src/api/mockFixtures.ts:200`
- Test: `tests/capabilities/test_orchestrator_reconciliation.py` (new test), `tests/slots/test_capability_lane_id_keyed.py:263-268` (update)

**Interfaces:**
- Produces: `child_to_slot("embed", "rerank") == "rerank"`; `SLOT_ALIASES["embed-rerank"] == "rerank"`. Task 6 (Rerank panel) and Task 10 (e2e) rely on the selection's `slot` field reading `rerank`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/capabilities/test_orchestrator_reconciliation.py`:

```python
def test_rerank_child_maps_to_canonical_rerank_slot() -> None:
    """embed.rerank must create/load the slot the dispatcher routes to.

    /v1/rerankings dispatches to slot ``rerank`` (router._RERANK_DEFAULT);
    the old ``embed-rerank`` mapping created a slot nothing routed to.
    """
    from hal0.capabilities.orchestrator import _CHILD_TO_SLOT, child_to_slot

    assert _CHILD_TO_SLOT[("embed", "rerank")] == "rerank"
    assert child_to_slot("embed", "rerank") == "rerank"


def test_embed_rerank_alias_resolves_to_rerank() -> None:
    """Back-compat: references to the retired ``embed-rerank`` name resolve."""
    from hal0.slots.routing import SLOT_ALIASES

    assert SLOT_ALIASES["embed-rerank"] == "rerank"


def test_stack_apply_rerank_slot_name_matches_orchestrator() -> None:
    """stacks/apply.py's KEEP-IN-SYNC copy must not drift from the orchestrator."""
    from hal0.capabilities.orchestrator import _CHILD_TO_SLOT
    from hal0.stacks.apply import _CHILD_TO_SLOT_NAME

    assert _CHILD_TO_SLOT_NAME["rerank"] == _CHILD_TO_SLOT[("embed", "rerank")]
```

In `tests/slots/test_capability_lane_id_keyed.py:263-268`, change the `_ensure_slot_exists` call and assertion from `"embed-rerank"` / `embed-rerank.toml` to `"rerank"` / `rerank.toml` (the test exercises resolver no-op behavior, not the name; keep it on the canonical name). Update the comment at `:500` (`# embed-rerank → child "rerank"` → `# rerank → child "rerank"`).

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/capabilities/test_orchestrator_reconciliation.py -k rerank -x`
Expected: FAIL — `assert 'embed-rerank' == 'rerank'`

- [ ] **Step 3: Implement**

`src/hal0/capabilities/orchestrator.py:71`:

```python
    ("embed", "rerank"): "rerank",
```

Update the two docstring mentions of `embed-rerank` (`:378`, `:712`) to say `rerank`.

`src/hal0/stacks/apply.py:109`:

```python
    "rerank": "rerank",
```

`src/hal0/slots/routing.py` — add to `SLOT_ALIASES` (after the `agent-hermes` entry, with a comment):

```python
    # The rerank capability slot was briefly created as ``embed-rerank``
    # while the dispatcher routed /v1/rerankings to ``rerank`` (split-brain,
    # fixed 2026-08). Old references resolve to the canonical name.
    "embed-rerank": "rerank",
```

`ui/src/api/mockFixtures.ts:200`:

```ts
        rerank: capabilityRow('', '', null, 'rerank', 'offline'),
```

- [ ] **Step 4: Run the affected suites**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/capabilities/ tests/slots/test_capability_lane_id_keyed.py tests/api/test_slots_routes.py tests/dispatcher/ -x -q`
Expected: PASS. Also grep for stragglers: `grep -rn "embed-rerank" src/ tests/ ui/src` — remaining hits must be the alias entry, its comment, and historical comments only.

- [ ] **Step 5: Commit**

```bash
git add src/hal0/capabilities/orchestrator.py src/hal0/stacks/apply.py src/hal0/slots/routing.py ui/src/api/mockFixtures.ts tests/capabilities/test_orchestrator_reconciliation.py tests/slots/test_capability_lane_id_keyed.py
git commit -m "fix(capabilities): route embed.rerank to canonical rerank slot"
```

---

### Task 2: Reload-class fallback rows for TTS / image slot keys

`VoicePage`/`ImageGenPage` write `default_voice`, `default_speed`, `default_response_format`, and `[image].*` via `PUT /api/slots/{name}/config` with no apply badge. Classify them in the frontend fallback registry so the unified page's `ApplyBadge` renders truthfully.

**Files:**
- Modify: `ui/src/dash/settings/data/reloadClass.js:47-63` (`RELOAD_CLASS_FALLBACK`)
- Test: `ui/src/dash/__tests__/reload-class-fallback.test.ts` (new)

**Interfaces:**
- Produces: `reloadClassFor('slot.tts.default_voice'|'slot.tts.default_speed'|'slot.tts.default_response_format'|'slot.image.default_size'|'slot.image.default_steps', {})` → `{apply_class: 'immediate'}`; `reloadClassFor('slot.image.idle_restore_minutes', {})` → `{apply_class: 'service-restart', services: ['hal0-api']}`. Tasks 4, 6, 7 pass these keys to `<ApplyBadge settingsKey=... registry=...>`.

- [ ] **Step 1: Write the failing test**

Create `ui/src/dash/__tests__/reload-class-fallback.test.ts`:

```ts
// The unified AI Capabilities page renders ApplyBadge for per-slot keys the
// backend apply-plan registry can't classify (they aren't Hal0Config paths).
// Lock the fallback classifications so a badge can never silently vanish.
import { describe, expect, it } from 'vitest'
import { reloadClassFor, SERVICE_HAL0_API, SERVICE_SLOTS } from '../settings/data/reloadClass.js'

describe('RELOAD_CLASS_FALLBACK capability keys', () => {
  it.each([
    'slot.tts.default_voice',
    'slot.tts.default_speed',
    'slot.tts.default_response_format',
    'slot.image.default_size',
    'slot.image.default_steps',
  ])('%s is immediate (injected per-request)', (key) => {
    expect(reloadClassFor(key, {})).toEqual({ apply_class: 'immediate', services: [] })
  })

  it('slot.image.idle_restore_minutes needs a hal0-api restart', () => {
    expect(reloadClassFor('slot.image.idle_restore_minutes', {})).toEqual({
      apply_class: 'service-restart',
      services: [SERVICE_HAL0_API],
    })
  })

  it('npu keys stay service-restart on the slots service', () => {
    expect(reloadClassFor('slot.npu.asr', {})).toEqual({
      apply_class: 'service-restart',
      services: [SERVICE_SLOTS],
    })
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ui && npx vitest run src/dash/__tests__/reload-class-fallback.test.ts`
Expected: FAIL — `reloadClassFor('slot.tts.default_voice', {})` returns `null`.

- [ ] **Step 3: Implement**

In `ui/src/dash/settings/data/reloadClass.js`, append inside `RELOAD_CLASS_FALLBACK` after the `slot.npu.asr` row:

```js
  // ── tts request defaults — injected per-request by /v1/audio/speech
  //    (api/routes/v1.py:1398-1404), so a save applies to the very next
  //    request with no restart. ──────────────────────────────────────────────
  'slot.tts.default_voice': { apply_class: 'immediate' },
  'slot.tts.default_speed': { apply_class: 'immediate' },
  'slot.tts.default_response_format': { apply_class: 'immediate' },

  // ── [image] generation defaults — size/steps are per-request fallbacks;
  //    idle_restore_minutes is read once at GpuArbiter construction
  //    (slots/arbiter.py:249, wired api/__init__.py:1619), so it needs the
  //    API service bounced. ──────────────────────────────────────────────────
  'slot.image.default_size': { apply_class: 'immediate' },
  'slot.image.default_steps': { apply_class: 'immediate' },
  'slot.image.idle_restore_minutes': { apply_class: 'service-restart', services: [SERVICE_HAL0_API] },
```

Note `reloadClassFor` normalizes `services` to `[]` when absent — the test expects that shape. If it instead returns entries verbatim, adjust the immediate-key expectation to `{ apply_class: 'immediate' }` spread accordingly (match the function, don't change it).

- [ ] **Step 4: Run tests**

Run: `cd ui && npx vitest run src/dash/__tests__/reload-class-fallback.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/dash/settings/data/reloadClass.js ui/src/dash/__tests__/reload-class-fallback.test.ts
git commit -m "feat(ui): classify tts/image per-slot keys in reload-class fallback"
```

---

### Task 3: Shared capability-selection infrastructure

The hook + pure helpers + presentational shared bits every panel composes.

**Files:**
- Create: `ui/src/dash/settings/pages/capabilities/selection-pure.js`
- Create: `ui/src/dash/settings/pages/capabilities/useCapabilitySelection.js`
- Create: `ui/src/dash/settings/pages/capabilities/shared.jsx`
- Test: `ui/src/dash/__tests__/capability-selection-pure.test.ts` (new)

**Interfaces:**
- Produces (consumed by Tasks 4–8):
  - `rowId(m)` → string; `resolveProvider(catalogItems, model, selection)` → string
  - `useCapabilitySelection(group, child, {withProvider?})` → `{ capsQuery, applyCapability, selection, catalogItems, model, setModel, enabled, setEnabled, provider, setProvider, dirty, reset, save(extraBody?), status, resolvedProvider, loading, errored }`
  - `shared.jsx`: `selStyle`, `inputStyle(width)`, `statusChip(status)`, `PanelHeader({title, info, chip})`, `PanelFooter({dirty, onReset, onSave, disabled, saving, label})`, `EnabledRow({enabled, setEnabled})`, `ModelRow({items, value, onChange, placeholder, emptyHint})`

- [ ] **Step 1: Write the failing pure-helper test**

Create `ui/src/dash/__tests__/capability-selection-pure.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { resolveProvider, rowId } from '../settings/pages/capabilities/selection-pure.js'

describe('rowId', () => {
  it('prefers id, then model_id, then the bare value', () => {
    expect(rowId({ id: 'a', model_id: 'b' })).toBe('a')
    expect(rowId({ model_id: 'b' })).toBe('b')
    expect(rowId('bare')).toBe('bare')
  })
})

describe('resolveProvider (#1470 semantics)', () => {
  const catalog = [{ id: 'kokoro-v1', provider: 'kokoro' }]
  const selection = { model: 'qwen3-tts', provider: 'qwen3tts' }

  it('prefers the catalog row for the model being edited', () => {
    expect(resolveProvider(catalog, 'kokoro-v1', selection)).toBe('kokoro')
  })
  it('falls back to the saved selection provider only while ids match', () => {
    expect(resolveProvider(catalog, 'qwen3-tts', selection)).toBe('qwen3tts')
    expect(resolveProvider(catalog, 'something-else', selection)).toBe('')
  })
  it('empty model resolves to empty provider', () => {
    expect(resolveProvider(catalog, '', selection)).toBe('')
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ui && npx vitest run src/dash/__tests__/capability-selection-pure.test.ts`
Expected: FAIL — module `selection-pure.js` not found.

- [ ] **Step 3: Create `selection-pure.js`**

```js
// Pure helpers for capability-selection editing — no React, no API imports,
// so vitest can exercise them without a DOM.

// Id of a catalog picker row (rows are objects; legacy fixtures were bare strings).
export function rowId(m) {
  return m.id || m.model_id || m
}

// #1470: resolve the provider for the model id currently being edited.
// Prefer the catalog row (covers an unsaved local change); fall back to the
// persisted selection's provider only while the local id still matches what's
// saved. This keys engine-specific copy instead of hardcoding Kokoro facts.
export function resolveProvider(catalogItems, model, selection) {
  const row = catalogItems.find(m => rowId(m) === model)
  return row?.provider
    || (model && model === (selection?.model || "") ? selection?.provider : "") || ""
}
```

- [ ] **Step 4: Run the pure test — PASS expected**

Run: `cd ui && npx vitest run src/dash/__tests__/capability-selection-pure.test.ts`

- [ ] **Step 5: Create `useCapabilitySelection.js`**

```js
// Shared per-(group, child) capability-selection editing over
// GET /api/capabilities + POST /api/capabilities/{group}/{child}.
// Every panel on the AI Capabilities page composes this instead of the
// ~40-line useState/useEffect/dirty loop VoicePage and ImageGenPage each
// hand-rolled (P3-ui consolidation).
import { useState, useEffect } from 'react'
import { useCapabilities, useCapabilityApply } from '@/api/hooks/useCapabilities'
import { resolveProvider } from './selection-pure.js'

export function useCapabilitySelection(group, child, { withProvider = false } = {}) {
  const capsQuery = useCapabilities()
  const applyCapability = useCapabilityApply()
  const caps = capsQuery.data
  const selection = caps?.selections?.[group]?.[child] || {}
  const catalogItems = caps?.catalogs?.[group]?.[child] || []

  const [model, setModel] = useState("")
  const [enabled, setEnabled] = useState(false)
  const [provider, setProvider] = useState("")

  useEffect(() => {
    if (selection.model != null) setModel(selection.model || "")
    if (selection.enabled != null) setEnabled(!!selection.enabled)
    if (withProvider && selection.provider != null) setProvider(selection.provider || "")
  }, [selection.model, selection.enabled, selection.provider, withProvider])

  const dirty = model !== (selection.model || "")
    || enabled !== !!selection.enabled
    || (withProvider && provider !== (selection.provider || ""))

  const reset = () => {
    setModel(selection.model || "")
    setEnabled(!!selection.enabled)
    if (withProvider) setProvider(selection.provider || "")
  }

  // Persist via capability apply. `extraBody` lets a panel piggyback fields
  // (none today); provider rides only when the panel opted in and set one.
  const save = async (extraBody = {}) => {
    const body = { model, enabled, ...extraBody }
    if (withProvider && provider) body.provider = provider
    await applyCapability.mutateAsync({ slot: group, child, body })
  }

  return {
    capsQuery, applyCapability, selection, catalogItems,
    model, setModel, enabled, setEnabled, provider, setProvider,
    dirty, reset, save,
    status: selection.status || "offline",
    resolvedProvider: resolveProvider(catalogItems, model, selection),
    loading: capsQuery.isLoading,
    // #1467: gate Save on isError, not just isLoading — a failed probe must
    // not allow saving against unknown live state.
    errored: capsQuery.isError,
  }
}
```

- [ ] **Step 6: Create `shared.jsx`**

```jsx
// AI Capabilities page — shared presentational helpers. Replaces the
// statusChip / select-style copies VoicePage and ImageGenPage each carried
// (they were duplicated verbatim, flagged in the P3-ui split).
// FieldInfoIcon is a window global (dash/primitives.jsx) — used unimported,
// same as every settings page.
import { SRow } from '../../shared/SRow.jsx'
import { rowId } from './selection-pure.js'

export const selStyle = { fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px" }
export const inputStyle = (width) => ({ ...selStyle, width })

export function statusChip(st) {
  const color = st === "ready" || st === "serving" ? "var(--ok)" : st === "starting" || st === "warming" ? "var(--warn)" : "var(--fg-4)"
  return <span className="chip mono" style={{borderColor: color, color, fontSize: 10, padding: "1px 6px"}}>{st}</span>
}

export function PanelHeader({ title, info, chip, onToggle, open }) {
  return (
    <div
      className="s-row"
      style={{paddingBottom: 4, borderBottom: "1px solid var(--line)", cursor: onToggle ? "pointer" : undefined}}
      onClick={onToggle}
    >
      <div className="k">
        {onToggle && <span className="mono" style={{marginRight: 6, color: "var(--fg-4)"}}>{open ? "▾" : "▸"}</span>}
        <span>{title}</span>
        <FieldInfoIcon description={info} />
      </div>
      <div className="v">{chip}</div>
    </div>
  )
}

export function PanelFooter({ dirty, onReset, onSave, disabled, saving, label }) {
  return (
    <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
      {dirty && <button className="btn ghost sm" onClick={onReset}>Reset</button>}
      <button className="btn sm" disabled={disabled} onClick={onSave}>{saving ? "Saving…" : label}</button>
    </div>
  )
}

export function EnabledRow({ enabled, setEnabled }) {
  return <SRow k="Enabled" v={
    <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
  } />
}

export function ModelRow({ items, value, onChange, placeholder, emptyHint }) {
  return <SRow k="Model" v={
    items.length > 0 ? (
      <select value={value} onChange={e => onChange(e.target.value)} style={selStyle}>
        <option value="">— unset —</option>
        {items.map(m => <option key={rowId(m)} value={rowId(m)}>{rowId(m)}</option>)}
      </select>
    ) : (
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        className="mono" style={inputStyle(260)} />
    )
  } sub={items.length === 0 ? emptyHint : undefined} />
}
```

- [ ] **Step 7: Verify hooks-order scan + unit suite stay green**

Run: `node ui/src/dash/__tests__/react-hooks-order.test.mjs && cd ui && npm run test:unit`
Expected: PASS (new files scanned, no hook-in-map violations).

- [ ] **Step 8: Commit**

```bash
git add ui/src/dash/settings/pages/capabilities/ ui/src/dash/__tests__/capability-selection-pure.test.ts
git commit -m "feat(ui): shared capability-selection hook and panel primitives"
```

---

### Task 4: TTS and STT panels

Port both panels out of `VoicePage.jsx` (`ui/src/dash/settings/pages/inference/VoicePage.jsx`) onto the new infrastructure. Behavior parity is the requirement — same endpoints, same #1467/#1470 semantics, same copy. Do **not** delete `VoicePage.jsx` yet (Task 9 does, after the shell switches over).

**Files:**
- Create: `ui/src/dash/settings/pages/capabilities/SttPanel.jsx`
- Create: `ui/src/dash/settings/pages/capabilities/TtsPanel.jsx`

**Interfaces:**
- Consumes: Task 3 hook + shared; `useSlotEdit`, `useSlotConfig`, `useSlotVoices` from `@/api/hooks/useSlots`; `ApplyBadge` from `../../shared/ApplyBadge.jsx`.
- Produces: `<SttPanel registry={...} />`, `<TtsPanel registry={...} />` — each renders one `.s-panel`; panel titles exactly `STT` / `TTS`. Task 8 mounts them inside anchored wrappers.

- [ ] **Step 1: Create `SttPanel.jsx`**

```jsx
// STT — voice.stt capability slot. Ported from VoicePage (top panel).
// Deliberately absent: STT silence thresholds (no such endpoint param).
// Language support is engine-specific — copy keyed on the resolved provider
// (moonshine is English-only), never stated as blanket truth (#1470).
import { SRow } from '../../shared/SRow.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow } from './shared.jsx'

export function SttPanel() {
  const sel = useCapabilitySelection('voice', 'stt')
  const p = sel.resolvedProvider

  const doSave = async () => {
    try {
      await sel.save()
      window.__hal0Toast && window.__hal0Toast("STT settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`STT save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  return (
    <div className="s-panel">
      <PanelHeader title="STT" info="speech-to-text · voice.stt slot" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. moonshine-base)"
        emptyHint="no installed STT models — install one in the Models view" />
      <SRow k="Language" sub={
          p === "moonshine"
            ? "moonshine is English-only; the /v1/audio/transcriptions language param is accepted but ignored"
            : p
              ? `${p} — language support is engine-specific; check its docs before relying on the language param`
              : "select an STT model to see its language support"
        } mono v={<span style={{color: "var(--fg-4)"}}>{p === "moonshine" ? "English" : "engine-dependent"}</span>} />
      <SRow k="NPU mode" sub="device npu serves whisper from the FLM trio ([npu].asr on the anchor slot) — tune it in the NPU anchor panel below" mono
        v={<span style={{color: "var(--fg-4)"}}>{sel.selection.device === "npu" ? "FLM trio" : "—"}</span>} />
      <PanelFooter dirty={sel.dirty} onReset={sel.reset} onSave={doSave}
        disabled={!sel.dirty || sel.loading || sel.errored || sel.applyCapability.isPending}
        saving={sel.applyCapability.isPending} label="Save STT" />
    </div>
  )
}
```

- [ ] **Step 2: Create `TtsPanel.jsx`**

Port the VoicePage TTS block (`VoicePage.jsx:211-322`) verbatim onto the hook. The `KOKORO_VOICES` list moves here unchanged (`VoicePage.jsx:32-42`). Full component:

```jsx
// TTS — voice.tts capability slot + request defaults. Ported from VoicePage.
// Model/enabled persist via capability apply; default_voice / default_speed /
// default_response_format persist via PUT /api/slots/tts/config and are
// injected per-request by /v1/audio/speech (immediate — see the
// slot.tts.* rows in reloadClass.js RELOAD_CLASS_FALLBACK).
// Voice picker prefers the live GET /api/slots/tts/voices list; the Kokoro
// seed pack is only the cold-slot fallback when the provider is kokoro (#1470).
import { useState, useEffect } from 'react'
import { useSlotEdit, useSlotConfig, useSlotVoices } from '@/api/hooks/useSlots'
import { SRow } from '../../shared/SRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow, selStyle, inputStyle } from './shared.jsx'

// Remsky Kokoro-FastAPI af_bella default. Full list from kokoro-v1 pack.
// No backend API exposes the voice list — hardcoded against the upstream.
// See: https://github.com/remsky/Kokoro-FastAPI#voices
const KOKORO_VOICES = [
  { id: "af_bella",   label: "Bella (af) — American female, warm" },
  { id: "af_sarah",   label: "Sarah (af) — American female, clear" },
  { id: "af_nicole",  label: "Nicole (af) — American female" },
  { id: "am_adam",    label: "Adam (am) — American male" },
  { id: "am_michael", label: "Michael (am) — American male" },
  { id: "bf_emma",    label: "Emma (bf) — British female" },
  { id: "bf_isabella",label: "Isabella (bf) — British female" },
  { id: "bm_george",  label: "George (bm) — British male" },
  { id: "bm_lewis",   label: "Lewis (bm) — British male" },
];

export function TtsPanel({ registry }) {
  const sel = useCapabilitySelection('voice', 'tts')
  const ttsSlotCfgQuery = useSlotConfig("tts")
  const ttsVoicesQuery = useSlotVoices("tts")
  const editSlot = useSlotEdit()
  const ttsCfg = ttsSlotCfgQuery.data || {}

  const [voice, setVoice] = useState("")
  const [speed, setSpeed] = useState("")
  const [format, setFormat] = useState("")

  useEffect(() => {
    if (ttsCfg.default_voice != null) setVoice(String(ttsCfg.default_voice))
    if (ttsCfg.default_speed != null) setSpeed(String(ttsCfg.default_speed))
    if (ttsCfg.default_response_format != null) setFormat(String(ttsCfg.default_response_format))
  }, [ttsCfg.default_voice, ttsCfg.default_speed, ttsCfg.default_response_format])

  const origVoice = ttsCfg.default_voice ? String(ttsCfg.default_voice) : ""
  const origSpeed = ttsCfg.default_speed != null ? String(ttsCfg.default_speed) : ""
  const origFormat = ttsCfg.default_response_format ? String(ttsCfg.default_response_format) : ""
  const speedNum = parseFloat(speed)
  const speedValid = speed.trim() === "" || (!isNaN(speedNum) && speedNum >= 0.25 && speedNum <= 4)
  const dirty = sel.dirty || voice !== origVoice || speed !== origSpeed || format !== origFormat

  const isKokoro = sel.resolvedProvider === "kokoro"
  const isQwen3 = sel.resolvedProvider === "qwen3tts"

  const doSave = async () => {
    try {
      await sel.save()
      // Only the changed defaults; empty string clears back to the engine's
      // own default (null on the wire; /v1/audio/speech skips null/empty).
      const patch = {}
      if (voice !== origVoice) patch.default_voice = voice || null
      if (speed !== origSpeed) patch.default_speed = speed.trim() === "" ? null : speedNum
      if (format !== origFormat) patch.default_response_format = format || null
      if (Object.keys(patch).length > 0) {
        await editSlot.mutateAsync({ name: "tts", body: patch })
      }
      window.__hal0Toast && window.__hal0Toast("TTS settings saved — applies to the next /v1/audio/speech request", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`TTS save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  const resetAll = () => { sel.reset(); setVoice(origVoice); setSpeed(origSpeed); setFormat(origFormat) }

  const liveVoices = ttsVoicesQuery.data?.source === "live" ? (ttsVoicesQuery.data.voices || []) : []
  const kokoroish = !sel.model || isKokoro
  const options = liveVoices.length > 0
    ? liveVoices.map(v => {
        const seed = KOKORO_VOICES.find(k => k.id === v)
        return { id: v, label: seed ? seed.label : v }
      })
    : (kokoroish ? KOKORO_VOICES : null)
  const srcNote = liveVoices.length > 0
    ? "voices reported live by the tts slot"
    : (kokoroish ? "bundled voices (Kokoro v1) · slot offline — list is the seed pack" : "model-specific voice id")
  const defaultVoiceLabel = kokoroish
    ? "— use engine default (af_bella) —"
    : isQwen3 ? "— use engine default (Ryan) —" : "— use engine default —"

  return (
    <div className="s-panel">
      <PanelHeader title="TTS" info="text-to-speech · voice.tts slot" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. kokoro-v1)"
        emptyHint="no installed TTS models — install one in the Models view" />
      <SRow k="Default voice" sub={`applied when /v1/audio/speech omits the voice param · ${srcNote}`}
        actions={<ApplyBadge settingsKey="slot.tts.default_voice" registry={registry} />} v={
        options ? (
          <select value={voice} onChange={e => setVoice(e.target.value)} style={selStyle}>
            <option value="">{defaultVoiceLabel}</option>
            {voice && !options.some(o => o.id === voice) && (
              <option value={voice}>{voice} (saved)</option>
            )}
            {options.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
        ) : (
          <input value={voice} onChange={e => setVoice(e.target.value)} placeholder="empty = engine default"
            className="mono" style={inputStyle(220)} />
        )
      } />
      <SRow k="Default speed"
        sub={`applied when the request omits speed · ${(isKokoro || isQwen3) ? "the engine clamps to 0.5–2.0" : "clamp range is engine-specific"} · empty = engine default (1.0)`}
        actions={<ApplyBadge settingsKey="slot.tts.default_speed" registry={registry} />} v={
        <input type="number" min={0.25} max={4} step={0.05} value={speed}
          onChange={e => setSpeed(e.target.value)} placeholder="1.0"
          className="mono" style={{...inputStyle(100), border: `1px solid ${speedValid ? "var(--line)" : "var(--err)"}`}} />
      } />
      <SRow k="Default format" sub="applied when the request omits response_format · empty = engine default (mp3)"
        actions={<ApplyBadge settingsKey="slot.tts.default_response_format" registry={registry} />} v={
        <select value={format} onChange={e => setFormat(e.target.value)} style={selStyle}>
          <option value="">— engine default (mp3) —</option>
          {["mp3", "wav", "opus", "flac", "pcm"].map(f => <option key={f} value={f}>{f}</option>)}
        </select>
      } />
      <SRow k="Sample rate" sub={
          isKokoro ? "fixed by the Kokoro engine — not configurable"
            : isQwen3 ? "set by the loaded Qwen3-TTS model at startup — not configurable"
            : "not configurable — determined by the active engine"
        } mono v={<span style={{color: "var(--fg-4)"}}>{isKokoro ? "24 kHz" : "engine-dependent"}</span>} />
      <PanelFooter dirty={dirty} onReset={resetAll} onSave={doSave}
        disabled={!dirty || !speedValid || sel.loading || sel.errored || sel.applyCapability.isPending || editSlot.isPending}
        saving={sel.applyCapability.isPending || editSlot.isPending} label="Save TTS" />
    </div>
  )
}
```

- [ ] **Step 3: Verify hooks-order scan passes**

Run: `node ui/src/dash/__tests__/react-hooks-order.test.mjs`
Expected: PASS (`options.map` / `KOKORO_VOICES.find` contain no hooks).

- [ ] **Step 4: Commit**

```bash
git add ui/src/dash/settings/pages/capabilities/SttPanel.jsx ui/src/dash/settings/pages/capabilities/TtsPanel.jsx
git commit -m "feat(ui): port TTS and STT panels onto shared capability infra"
```

---

### Task 5: Embeddings and Reranking panels (new UI)

First-ever settings surface for `embed.embed` and `embed.rerank`. The API has always shipped `catalogs.embed.{embed,rerank}` + `selections.embed.{embed,rerank}` in the same shape voice/img use — no backend work needed beyond Task 1.

**Files:**
- Create: `ui/src/dash/settings/pages/capabilities/EmbedPanel.jsx`
- Create: `ui/src/dash/settings/pages/capabilities/RerankPanel.jsx`

**Interfaces:**
- Consumes: Task 3 infra; Task 1's canonical `rerank` slot name (the selection's `.slot` field).
- Produces: `<EmbedPanel />`, `<RerankPanel />` — one `.s-panel` each, titles `Embeddings` / `Reranking`.

- [ ] **Step 1: Create `EmbedPanel.jsx`**

```jsx
// Embeddings — embed.embed capability slot (llama-server --embedding profile,
// seed default qwen3-embedding-0-6b-q8-0). First settings surface for this
// selection: /api/capabilities always shipped catalogs/selections for it, but
// no page consumed them before the AI Capabilities unification.
// Note: Hindsight (memory) embeds server-side with its own bundled model —
// this slot serves /v1/embeddings API traffic (RAG, external clients) only.
import { SRow } from '../../shared/SRow.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow } from './shared.jsx'

export function EmbedPanel() {
  const sel = useCapabilitySelection('embed', 'embed')

  const doSave = async () => {
    try {
      await sel.save()
      window.__hal0Toast && window.__hal0Toast("Embeddings settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Embeddings save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  return (
    <div className="s-panel">
      <PanelHeader title="Embeddings" info="embed.embed slot · llama-server --embedding" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. qwen3-embedding-0-6b-q8-0)"
        emptyHint="no installed embedding models — install one in the Models view" />
      <SRow k="Serves" sub="memory (Hindsight) embeds with its own bundled model — this slot serves API/RAG traffic" mono
        v={<span style={{color: "var(--fg-4)"}}>/v1/embeddings</span>} />
      <PanelFooter dirty={sel.dirty} onReset={sel.reset} onSave={doSave}
        disabled={!sel.dirty || sel.loading || sel.errored || sel.applyCapability.isPending}
        saving={sel.applyCapability.isPending} label="Save embeddings" />
    </div>
  )
}
```

- [ ] **Step 2: Create `RerankPanel.jsx`**

```jsx
// Reranking — embed.rerank capability slot (llama-server --reranking profile,
// seed default bge-reranker-v2-m3-q4_k_m, canonical slot name `rerank`).
// First settings surface for this selection (see EmbedPanel note).
// The memory recall reranker CLIENT ([memory.embedding].rerank_*) is a
// separate concern and stays on the Memory page — linked below.
import { SRow } from '../../shared/SRow.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow } from './shared.jsx'

export function RerankPanel() {
  const sel = useCapabilitySelection('embed', 'rerank')

  const doSave = async () => {
    try {
      await sel.save()
      window.__hal0Toast && window.__hal0Toast("Reranking settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Reranking save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  return (
    <div className="s-panel">
      <PanelHeader title="Reranking" info="embed.rerank selection · rerank slot · llama-server --reranking" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. bge-reranker-v2-m3)"
        emptyHint="no installed reranking models — install one in the Models view" />
      <SRow k="Serves" sub="public route /v1/rerankings is rewritten to llama-server's native /v1/rerank" mono
        v={<span style={{color: "var(--fg-4)"}}>/v1/rerank</span>} />
      <SRow k="Memory recall reranker" sub="the memory subsystem's second-pass reranker client is configured separately" v={
        <a href="#settings/memory" className="mono" style={{fontSize: 11, color: "var(--accent)"}}>Memory settings →</a>
      } />
      <PanelFooter dirty={sel.dirty} onReset={sel.reset} onSave={doSave}
        disabled={!sel.dirty || sel.loading || sel.errored || sel.applyCapability.isPending}
        saving={sel.applyCapability.isPending} label="Save reranking" />
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/dash/settings/pages/capabilities/EmbedPanel.jsx ui/src/dash/settings/pages/capabilities/RerankPanel.jsx
git commit -m "feat(ui): embeddings and reranking capability panels"
```

---

### Task 6: Image generation panel

Port `ImageGenPage.jsx` (both panels merged into one panel with a defaults sub-section) onto the shared infra, with ApplyBadges on the `[image]` keys.

**Files:**
- Create: `ui/src/dash/settings/pages/capabilities/ImagePanel.jsx`

**Interfaces:**
- Consumes: Task 3 infra; Task 2 keys `slot.image.*`; `useSlots`, `useSlotEdit`, `useSlotConfig` from `@/api/hooks/useSlots`.
- Produces: `<ImagePanel registry={...} />` — one `.s-panel`, title `Image generation`.

- [ ] **Step 1: Create `ImagePanel.jsx`**

Port `ImageGenPage.jsx:18-213` with these exact deltas — selection state → `useCapabilitySelection('img', 'img', { withProvider: true })`; the two panels merge (capability rows, then a `Generation defaults` divider row, then the three default rows); status chip / footer / model row → shared components; `[image]` rows gain `actions={<ApplyBadge settingsKey="slot.image.default_size" … />}` (and `default_steps`, `idle_restore_minutes` respectively). Full component:

```jsx
// Image generation — img.img capability slot (ComfyUI engine) + [image]
// generation defaults on the img slot TOML (#599 ImageGenConfig). Ported from
// ImageGenPage. default_size/default_steps are per-request fallbacks
// (immediate); idle_restore_minutes feeds the GpuArbiter at construction
// (service-restart hal0-api) — badges come from reloadClass.js fallback rows.
// Workflows, queue, and inventory live on the ComfyUI pane, not here.
import { useState, useEffect } from 'react'
import { useSlots, useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import { SRow } from '../../shared/SRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow, selStyle, inputStyle } from './shared.jsx'

const DEF_SIZE = "1024x1024"
const DEF_STEPS = "0"
const DEF_IDLE = "60"

export function ImagePanel({ registry }) {
  const sel = useCapabilitySelection('img', 'img', { withProvider: true })
  const slotsQuery = useSlots()
  const editSlot = useSlotEdit()

  // Discover the img slot so the [image] read/write targets a real slot.
  const imgSlotName =
    (slotsQuery.data || []).find(s => s.name === "img" || s.type === "image" || s.group === "img")?.name || null
  const imgCfgQuery = useSlotConfig(imgSlotName)
  const imgCfgImage = (imgCfgQuery.data?.image) || {}

  const origSize = imgCfgImage.default_size != null ? String(imgCfgImage.default_size) : DEF_SIZE
  const origSteps = imgCfgImage.default_steps != null ? String(imgCfgImage.default_steps) : DEF_STEPS
  const origIdle = imgCfgImage.idle_restore_minutes != null ? String(imgCfgImage.idle_restore_minutes) : DEF_IDLE

  const [size, setSize] = useState(DEF_SIZE)
  const [steps, setSteps] = useState(DEF_STEPS)
  const [idle, setIdle] = useState(DEF_IDLE)

  useEffect(() => {
    const img = imgCfgQuery.data?.image || {}
    setSize(img.default_size != null ? String(img.default_size) : DEF_SIZE)
    setSteps(img.default_steps != null ? String(img.default_steps) : DEF_STEPS)
    setIdle(img.idle_restore_minutes != null ? String(img.idle_restore_minutes) : DEF_IDLE)
  }, [imgCfgQuery.data])

  const defaultsDirty = !!imgSlotName && (size !== origSize || steps !== origSteps || idle !== origIdle)
  const dirty = sel.dirty || defaultsDirty

  const doSave = async () => {
    try {
      await sel.save()
      if (defaultsDirty) {
        // Coerce to ImageGenConfig field types (steps/idle non-negative ints).
        await editSlot.mutateAsync({ name: imgSlotName, body: { image: {
          default_size: size.trim() || DEF_SIZE,
          default_steps: Math.max(0, parseInt(steps, 10) || 0),
          idle_restore_minutes: Math.max(0, parseInt(idle, 10) || 0),
        } } })
      }
      window.__hal0Toast && window.__hal0Toast("Image generation settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Image generation save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  const resetAll = () => { sel.reset(); setSize(origSize); setSteps(origSteps); setIdle(origIdle) }

  return (
    <div className="s-panel">
      <PanelHeader title="Image generation" info="img.img slot · ComfyUI engine" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <SRow k="Engine" sub="provider for the img slot" v={
        <select value={sel.provider} onChange={e => sel.setProvider(e.target.value)} style={selStyle}>
          <option value="">— auto —</option>
          <option value="comfyui">comfyui</option>
        </select>
      } />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. sdxl-turbo-fp16)"
        emptyHint="no installed image models — install one in the Models view" />

      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k">
          <span>Generation defaults</span>
          <FieldInfoIcon description="img slot [image] table · applied when a /v1/images request omits the param" />
        </div>
        <div className="v">
          {imgSlotName
            ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{imgSlotName}</span>
            : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>no img slot</span>}
        </div>
      </div>
      <SRow k="Default size" sub="Output resolution as WxH (e.g. 1024x1024)"
        actions={<ApplyBadge settingsKey="slot.image.default_size" registry={registry} />} v={
        <input value={size} onChange={e => setSize(e.target.value)} placeholder={DEF_SIZE}
          disabled={!imgSlotName} className="mono" style={inputStyle(140)} />
      } />
      <SRow k="Default steps" sub="Sampler steps · 0 = use the model-class default"
        actions={<ApplyBadge settingsKey="slot.image.default_steps" registry={registry} />} v={
        <input type="number" min={0} value={steps} onChange={e => setSteps(e.target.value)} placeholder={DEF_STEPS}
          disabled={!imgSlotName} className="mono" style={inputStyle(100)} />
      } />
      <SRow k="Idle restore" sub="Minutes of img inactivity before the GPU arbiter restores LLM slots · 0 = manual only"
        actions={<ApplyBadge settingsKey="slot.image.idle_restore_minutes" registry={registry} />} v={
        <input type="number" min={0} value={idle} onChange={e => setIdle(e.target.value)} placeholder={DEF_IDLE}
          disabled={!imgSlotName} className="mono" style={inputStyle(100)} />
      } />
      {!imgSlotName && (
        <div className="s-row" style={{padding: "6px 12px"}}>
          <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>No img slot configured — create one in the Slots view to edit generation defaults.</span>
        </div>
      )}
      <PanelFooter dirty={dirty} onReset={resetAll} onSave={doSave}
        disabled={!dirty || sel.loading || sel.errored || sel.applyCapability.isPending || editSlot.isPending}
        saving={sel.applyCapability.isPending || editSlot.isPending} label="Save image generation" />
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/dash/settings/pages/capabilities/ImagePanel.jsx
git commit -m "feat(ui): image generation panel with apply badges"
```

---

### Task 7: NPU anchor panel (collapsed advanced)

Port `NpuPage.jsx` into a collapsed-by-default panel. This closes the page's own TODO (`NpuPage.jsx:99-107`): the hardcoded amber `⟳ restart` chip becomes a real `ApplyBadge` — the fallback registry already classifies `slot.model.context_size`, `slot.npu.embed`, `slot.npu.asr` (`reloadClass.js:51-53`).

**Files:**
- Create: `ui/src/dash/settings/pages/capabilities/NpuAnchorPanel.jsx`

**Interfaces:**
- Consumes: Task 3 shared (`PanelHeader` with `onToggle`/`open`, `PanelFooter`, `inputStyle`); `useSlots`, `useSlotEdit`, `useSlotConfig`, `useNpuOccupancy` hooks; `ApplyBadge`.
- Produces: `<NpuAnchorPanel registry={...} />` — one `.s-panel`, title `NPU anchor (FLM trio)`, body hidden until expanded; renders `null`-body hint when no npu-device slot exists.

- [ ] **Step 1: Create `NpuAnchorPanel.jsx`**

Port `NpuPage.jsx:19-163` with these deltas — wrap body in `open` state (default `false`, header toggles); the three hardcoded style objects → shared `inputStyle`; the amber chip on Context size → `<ApplyBadge settingsKey="slot.model.context_size" registry={registry} />`; add ApplyBadges to the two toggles (`slot.npu.embed`, `slot.npu.asr`); occupancy strip moves inside the expanded body; page-level `<h2>`/`desc` dropped (the panel's info icon carries the FLM copy). All hooks stay at the top of the component (NOT behind the `open` conditional — the hooks-order scan and React both require unconditional hook calls). Full component:

```jsx
// NPU anchor (FLM trio) — advanced panel, collapsed by default. Ported from
// NpuPage. One FLM process serves chat + embed + ASR on the XDNA2 NPU:
//   [model].context_size → HAL0_FLM_CTX → --ctx-len
//   [npu].embed → HAL0_FLM_LOAD_EMBED → --embed 1
//   [npu].asr   → HAL0_FLM_LOAD_ASR   → --asr 1
// All service-restart (slot bounce). The old hardcoded amber chip is now a
// real ApplyBadge (closes the NpuPage TODO / spec Risk #2 anti-pattern) —
// slot.model.context_size / slot.npu.* are classified in RELOAD_CLASS_FALLBACK.
// The [npu].asr/.embed booleans are also written by the NPU pane pills and
// the slot drawer; this panel is the settings-side writer of the same keys.
import { useState, useEffect } from 'react'
import { useSlots, useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { SRow } from '../../shared/SRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { PanelHeader, PanelFooter, inputStyle } from './shared.jsx'

const DEF_CTX = "16384"

export function NpuAnchorPanel({ registry }) {
  const [open, setOpen] = useState(false)
  const slotsQuery = useSlots()
  const editSlot = useSlotEdit()
  const occQuery = useNpuOccupancy()

  const npuSlots = (slotsQuery.data || []).filter(s => s.device === "npu")
  const npuName = npuSlots.length > 0 ? npuSlots[0].name : null
  const cfgQuery = useSlotConfig(npuName)
  const cfg = cfgQuery.data || {}
  const liveCtx = cfg.model?.context_size
  const liveNpu = cfg.npu || {}

  const origCtx = liveCtx != null ? String(liveCtx) : DEF_CTX
  const origAsr = !!liveNpu.asr
  const origEmbed = !!liveNpu.embed

  const [ctx, setCtx] = useState(DEF_CTX)
  const [asr, setAsr] = useState(false)
  const [embed, setEmbed] = useState(false)
  useEffect(() => {
    setCtx(liveCtx != null ? String(liveCtx) : DEF_CTX)
    setAsr(!!liveNpu.asr)
    setEmbed(!!liveNpu.embed)
  }, [cfgQuery.data])

  const ctxNum = parseInt(ctx, 10)
  const ctxValid = /^\d+$/.test(ctx.trim()) && ctxNum >= 512
  const dirty = !!npuName && (ctx !== origCtx || asr !== origAsr || embed !== origEmbed)

  const doSave = async () => {
    if (!npuName || !ctxValid) return
    try {
      await editSlot.mutateAsync({ name: npuName, body: { model: { context_size: ctxNum }, npu: { asr, embed } } })
      window.__hal0Toast && window.__hal0Toast("NPU settings saved — restart the slot to apply", "warn")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  const occ = occQuery.data

  return (
    <div className="s-panel">
      <PanelHeader
        title="NPU anchor (FLM trio)"
        info={npuName ? `${npuName} · device=npu · one FLM process multiplexes chat + embed + ASR` : "advanced · FastFlowLM on the AMD XDNA2 NPU"}
        chip={npuName
          ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{npuName}</span>
          : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>no NPU slot</span>}
        onToggle={() => setOpen(o => !o)} open={open}
      />
      {open && !npuName && !slotsQuery.isPending && (
        <div className="s-row" style={{padding: "6px 12px"}}>
          <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
            No NPU slot configured. Create a slot with device npu in the Slots view (or run hal0 setup with NPU opt-in) to tune FLM here.
          </span>
        </div>
      )}
      {open && npuName && (
        <>
          <SRow k="Context size" sub="FLM --ctx-len (tokens) · larger = more KV cache on the NPU"
            actions={<ApplyBadge settingsKey="slot.model.context_size" registry={registry} />} v={
            <input type="number" min={512} step={512} value={ctx}
              onChange={e => setCtx(e.target.value)} placeholder={DEF_CTX}
              className="mono" style={{...inputStyle(120), borderColor: ctxValid || !ctx ? "var(--line)" : "var(--err)"}} />
          } />
          <SRow k="Load embeddings" sub="Serve /v1/embeddings from the FLM trio (--embed 1) · mirrors device=npu on the Embeddings selection"
            actions={<ApplyBadge settingsKey="slot.npu.embed" registry={registry} />} v={
            <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
              <input type="checkbox" checked={embed} onChange={e => setEmbed(e.target.checked)} style={{accentColor: "var(--accent)"}} />
              <span>{embed ? "enabled" : "disabled"}</span>
            </label>
          } />
          <SRow k="Load ASR" sub="Serve /v1/audio/transcriptions from the FLM trio (--asr 1) · mirrors device=npu on the STT selection"
            actions={<ApplyBadge settingsKey="slot.npu.asr" registry={registry} />} v={
            <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
              <input type="checkbox" checked={asr} onChange={e => setAsr(e.target.checked)} style={{accentColor: "var(--accent)"}} />
              <span>{asr ? "enabled" : "disabled"}</span>
            </label>
          } />
          {occ?.present && (
            <>
              <SRow k="Occupancy" mono v={
                <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: occ.cols_used > 0 ? "var(--ok)" : "var(--fg-4)", borderColor: occ.cols_used > 0 ? "var(--ok)" : "var(--line)"}}>
                  {occ.cols_used}/{occ.cols_total} cols
                </span>
              } />
              <SRow k="Peak" mono v={`${occ.tops_peak} TOPS · ${occ.tiles} tiles (${occ.rows}×${occ.cols})`} />
              {(occ.slots || []).map(s => (
                <SRow key={s.name} k={s.name} sub={s.model || "—"} mono
                  v={<>
                    <span style={{color: s.state === "serving" || s.state === "ready" ? "var(--ok)" : "var(--fg-4)"}}>{s.state}</span>
                    <span style={{color: "var(--fg-4)"}}> · {s.cols?.length || 0} cols{s.gb != null ? ` · ${s.gb} GB` : ""}</span>
                  </>} />
              ))}
            </>
          )}
          <PanelFooter dirty={dirty} onReset={() => { setCtx(origCtx); setAsr(origAsr); setEmbed(origEmbed) }}
            onSave={doSave} disabled={!dirty || !ctxValid || editSlot.isPending}
            saving={editSlot.isPending} label="Save NPU settings" />
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify hooks-order scan passes**

Run: `node ui/src/dash/__tests__/react-hooks-order.test.mjs`
Expected: PASS (`occ.slots.map` renders SRow only — no hooks in the map callback).

- [ ] **Step 3: Commit**

```bash
git add ui/src/dash/settings/pages/capabilities/NpuAnchorPanel.jsx
git commit -m "feat(ui): fold NPU anchor into capabilities as collapsed panel with real apply badges"
```

---

### Task 8: Status strip and CapabilitiesPage assembly

**Files:**
- Create: `ui/src/dash/settings/pages/capabilities/StatusStrip.jsx`
- Create: `ui/src/dash/settings/pages/capabilities/CapabilitiesPage.jsx`

**Interfaces:**
- Consumes: all Task 4–7 panels; `useCapabilities`; `useSettingsClient` from `../../data/settingsClient.js` (its returned `registry` is the merged apply-plan lookup).
- Produces: `<CapabilitiesPage />` — the page component Task 9 wires into the shell. Anchored wrappers with DOM ids `cap-tts`, `cap-stt`, `cap-embed`, `cap-rerank`, `cap-img`, `cap-npu`.

- [ ] **Step 1: Create `StatusStrip.jsx`**

```jsx
// Jump-nav status strip: one chip per capability, scroll-links to its panel.
// Reads the same useCapabilities() query the panels use (react-query dedupes).
import { useCapabilities } from '@/api/hooks/useCapabilities'

const CHIPS = [
  { id: "cap-tts",    label: "TTS",    group: "voice", child: "tts" },
  { id: "cap-stt",    label: "STT",    group: "voice", child: "stt" },
  { id: "cap-embed",  label: "Embed",  group: "embed", child: "embed" },
  { id: "cap-rerank", label: "Rerank", group: "embed", child: "rerank" },
  { id: "cap-img",    label: "Image",  group: "img",   child: "img" },
]

function dotColor(sel) {
  const st = sel?.status || "offline"
  if (st === "ready" || st === "serving") return "var(--ok)"
  if (st === "starting" || st === "warming") return "var(--warn)"
  return "var(--fg-4)"
}

export function StatusStrip() {
  const capsQuery = useCapabilities()
  const selections = capsQuery.data?.selections || {}
  const jump = (id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })
  return (
    <div className="s-panel" style={{display: "flex", flexWrap: "wrap", gap: 8, padding: "8px 12px", marginBottom: 12}}>
      {CHIPS.map(c => {
        const sel = selections[c.group]?.[c.child]
        return (
          <button key={c.id} className="chip mono" onClick={() => jump(c.id)}
            title={sel ? `${sel.status || "offline"}${sel.slot ? ` · slot ${sel.slot}` : ""}` : "status unknown"}
            style={{fontSize: 11, padding: "2px 10px", cursor: "pointer", background: "transparent", display: "inline-flex", alignItems: "center", gap: 6}}>
            <span style={{width: 7, height: 7, borderRadius: "50%", background: dotColor(sel), display: "inline-block"}} />
            {c.label}
          </button>
        )
      })}
      <button className="chip mono" onClick={() => jump("cap-npu")}
        style={{fontSize: 11, padding: "2px 10px", cursor: "pointer", background: "transparent", color: "var(--fg-3)"}}>
        NPU ▾
      </button>
    </div>
  )
}
```

- [ ] **Step 2: Create `CapabilitiesPage.jsx`**

```jsx
// MODELS & INFERENCE ▸ AI Capabilities — the unified settings surface for
// every non-chat capability slot: TTS, STT, embeddings, reranking, image
// generation, plus the collapsed NPU anchor. Absorbs the former Voice /
// Image Generation / NPU pages (their #settings/<id> deep links resolve here
// via SECTION_ALIASES). Chat models stay on Loaded Models; the memory recall
// reranker client stays on Memory (linked from the Reranking panel).
import { useCapabilities } from '@/api/hooks/useCapabilities'
import { useSettingsClient } from '../../data/settingsClient.js'
import { StatusStrip } from './StatusStrip.jsx'
import { TtsPanel } from './TtsPanel.jsx'
import { SttPanel } from './SttPanel.jsx'
import { EmbedPanel } from './EmbedPanel.jsx'
import { RerankPanel } from './RerankPanel.jsx'
import { ImagePanel } from './ImagePanel.jsx'
import { NpuAnchorPanel } from './NpuAnchorPanel.jsx'

export function CapabilitiesPage() {
  const capsQuery = useCapabilities()
  const { registry } = useSettingsClient()

  return (
    <div className="s-section">
      <h2>AI Capabilities</h2>
      <p className="desc">Speech, embeddings, reranking, and image generation. Chat models live in Loaded Models.</p>

      {capsQuery.isError && (
        <div className="err">{capsQuery.error?.message || "Could not load capabilities — Save is disabled until the probe succeeds"}</div>
      )}

      <StatusStrip />
      <div id="cap-tts" style={{marginBottom: 12}}><TtsPanel registry={registry} /></div>
      <div id="cap-stt" style={{marginBottom: 12}}><SttPanel /></div>
      <div id="cap-embed" style={{marginBottom: 12}}><EmbedPanel /></div>
      <div id="cap-rerank" style={{marginBottom: 12}}><RerankPanel /></div>
      <div id="cap-img" style={{marginBottom: 12}}><ImagePanel registry={registry} /></div>
      <div id="cap-npu"><NpuAnchorPanel registry={registry} /></div>
    </div>
  )
}
```

Before committing, confirm `useSettingsClient()` is exported from `../../data/settingsClient.js` and returns `{ registry }` without needing `{schema: true}` (it does — `settingsClient.js:52,67`); if the hook requires arguments, call it as `useSettingsClient({})`.

- [ ] **Step 3: Build + scans**

Run: `cd ui && npm run build && node src/dash/__tests__/react-hooks-order.test.mjs && npm run test:unit`
Expected: build succeeds; scans and unit suite PASS. (`CHIPS.map` in StatusStrip has no hooks.)

- [ ] **Step 4: Commit**

```bash
git add ui/src/dash/settings/pages/capabilities/StatusStrip.jsx ui/src/dash/settings/pages/capabilities/CapabilitiesPage.jsx
git commit -m "feat(ui): assemble unified AI Capabilities settings page"
```

---

### Task 9: Nav rewiring — replace Voice/ImageGen/NPU with AI Capabilities

**Files:**
- Modify: `ui/src/dash/settings/SettingsNav.jsx:20-27` (NAV_GROUPS), `:51-59` (SECTION_ALIASES)
- Modify: `ui/src/dash/settings/SettingsShell.jsx:27-29` (imports), `:53-55` (cases)
- Modify: `ui/src/dash/command-palette.jsx:281-292`
- Delete: `ui/src/dash/settings/pages/inference/VoicePage.jsx`, `ImageGenPage.jsx`, `NpuPage.jsx` (directory becomes empty — remove it)

**Interfaces:**
- Consumes: `CapabilitiesPage` from Task 8.
- Produces: route id `capabilities`; aliases `voice|imagegen|npu → capabilities`. Task 10's e2e depends on the aliases.

- [ ] **Step 1: Update `SettingsNav.jsx`**

Replace the three MODELS & INFERENCE entries:

```js
  {
    title: "MODELS & INFERENCE",
    items: [
      { id: "slots", label: "Loaded Models" },
      { id: "modeldefaults", label: "Model Defaults" },
      { id: "capabilities", label: "AI Capabilities" },
    ],
  },
```

Extend `SECTION_ALIASES`:

```js
export const SECTION_ALIASES = {
  general: "overview",
  health: "overview",
  library: "slots",
  backend: "hardware",
  runtimes: "hardware",
  hwtuning: "hardware",
  about: "updates",
  // Unified AI Capabilities page (2026-08) absorbed three pages:
  voice: "capabilities",
  imagegen: "capabilities",
  npu: "capabilities",
};
```

- [ ] **Step 2: Update `SettingsShell.jsx`**

Replace the three imports with:

```js
import { CapabilitiesPage } from './pages/capabilities/CapabilitiesPage.jsx'
```

Replace the three switch cases with:

```js
      case "capabilities": return <CapabilitiesPage />;
```

- [ ] **Step 3: Update the command palette**

In `ui/src/dash/command-palette.jsx`, replace the `set-voice` / `set-imagegen` / `set-npu` entries with one, and add the two entries the list is missing today (`memory`, `agents` — it's hand-mirrored and already stale):

```js
    { id: "set-capabilities", label: "AI Capabilities",  route: "settings/capabilities", sub: "TTS, STT, embeddings, reranking, image gen · NPU anchor" },
    { id: "set-memory",    label: "Memory",               route: "settings/memory",   sub: "engine, reranker client, graph" },
    { id: "set-agents",    label: "Agent Chat",           route: "settings/agents",   sub: "brain chat · slot routing" },
```

- [ ] **Step 4: Delete the absorbed pages**

```bash
git rm ui/src/dash/settings/pages/inference/VoicePage.jsx ui/src/dash/settings/pages/inference/ImageGenPage.jsx ui/src/dash/settings/pages/inference/NpuPage.jsx
```

Then verify nothing still imports them: `grep -rn "VoicePage\|ImageGenPage\|NpuPage" ui/src` — expected: no hits.

- [ ] **Step 5: Build + unit suite**

Run: `cd ui && npm run build && npm run test:unit && node src/dash/__tests__/react-hooks-order.test.mjs`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/src/dash/settings/SettingsNav.jsx ui/src/dash/settings/SettingsShell.jsx ui/src/dash/command-palette.jsx
git commit -m "feat(ui): route AI Capabilities page, alias voice/imagegen/npu, sync palette"
```

---

### Task 10: E2e specs — update the two existing, add coverage for embed/rerank

**Files:**
- Modify: `ui/tests/e2e/specs/voice-page-provider-copy-v3.spec.ts`
- Modify: `ui/tests/e2e/specs/capability-catalog-pickers-v3.spec.ts`

**Interfaces:**
- Consumes: alias routing (Task 9), panel titles/order (Global Constraints), mock fixtures (`nomic-embed-text-v1.5`, `bge-reranker-v2-m3`, rerank slot name `rerank` from Task 1).

- [ ] **Step 1: Update `voice-page-provider-copy-v3.spec.ts`**

- Every `await expect(page.locator('.settings-content h2').first()).toHaveText('Voice')` → `toHaveText('AI Capabilities')` (lines 72, 96, 120 area). The `page.goto('/#settings/voice')` calls stay — they now prove the alias resolves.
- Panel locators (`.s-panel` filtered by `.k span` text `/^TTS$/` / `/^STT$/`) survive unchanged.
- The `Language` row locator (`:114`) survives.

- [ ] **Step 2: Update `capability-catalog-pickers-v3.spec.ts`**

- h2 assertions → `'AI Capabilities'` for both the `/#settings/voice` and `/#settings/imagegen` gotos.
- Model-row indexes: the page now renders five `Model` rows in order TTS(0), STT(1), Embed(2), Rerank(3), Image(4). Rework the row selection to scope by panel instead of index — the pattern already used for panels:

```ts
const panelModelRow = (title: RegExp) =>
  page.locator('.s-panel')
    .filter({ has: page.locator('.k span', { hasText: title }) })
    .locator('.s-row')
    .filter({ has: page.locator('.k span', { hasText: /^Model$/ }) })
```

Use `panelModelRow(/^STT$/)`, `panelModelRow(/^TTS$/)`, `panelModelRow(/^Image generation$/)` in place of `modelRows.nth(...)`. The imagegen test drops its separate `goto('/#settings/imagegen')` h2 assertion (`'Image Generation'`) in favor of the unified h2 + scoped panel.
- Append two new tests in the same describe block, mirroring the existing picker assertions against the default `mockFixtures.ts` catalog:

```ts
  test('embed panel lists the embed catalog', async ({ page }) => {
    await page.goto('/#settings/capabilities')
    await expect(page.locator('.settings-content h2').first()).toHaveText('AI Capabilities')
    const row = panelModelRow(/^Embeddings$/)
    await expect(row.locator('select option', { hasText: 'nomic-embed-text-v1.5' })).toHaveCount(1)
  })

  test('rerank panel lists the rerank catalog and links memory settings', async ({ page }) => {
    await page.goto('/#settings/capabilities')
    const panel = page.locator('.s-panel').filter({ has: page.locator('.k span', { hasText: /^Reranking$/ }) })
    await expect(panel.locator('select option', { hasText: 'bge-reranker-v2-m3' })).toHaveCount(1)
    await expect(panel.locator('a[href="#settings/memory"]')).toHaveCount(1)
  })
```

If these tests use per-spec `page.route` fixture overrides rather than the default mocks, mirror the override pattern of the neighbouring tests in the same file and assert against the ids that override ships.

- [ ] **Step 3: Run the two specs**

Run: `cd ui && npm run test:e2e -- voice-page-provider-copy-v3 capability-catalog-pickers-v3`
Expected: PASS. (First run may need `npm run test:e2e:install`.)

- [ ] **Step 4: Commit**

```bash
git add ui/tests/e2e/specs/voice-page-provider-copy-v3.spec.ts ui/tests/e2e/specs/capability-catalog-pickers-v3.spec.ts
git commit -m "test(e2e): cover unified capabilities page incl. embed/rerank panels"
```

---

### Task 11: Memory page cross-link + changelog

**Files:**
- Modify: `ui/src/dash/settings/pages/data/MemoryPage.jsx` (MemoryRerankerPanel header)
- Modify: `CHANGELOG.md` (Unreleased section, matching existing heading conventions)

- [ ] **Step 1: Add the cross-link**

In `MemoryRerankerPanel`'s header row (the `s-row` containing `<span>Reranker</span>`), add to the right side:

```jsx
        <div className="v">
          <a href="#settings/capabilities" className="mono" style={{fontSize: 11, color: "var(--accent)"}}>rerank slot →</a>
        </div>
```

(The header `div.k` stays as-is; this adds the missing `div.v`.) The Rerank panel already links back (Task 5).

- [ ] **Step 2: Changelog entry**

Add under the unreleased/next heading, following the file's existing bullet style:

```markdown
- Settings: new unified **AI Capabilities** page (TTS, STT, embeddings, reranking, image generation, NPU anchor) replaces the Voice / Image Generation / NPU pages; old `#settings/voice|imagegen|npu` links redirect. First UI for the embed/rerank capability selections.
- Fixed: enabling the rerank capability now creates/loads the `rerank` slot the dispatcher actually routes `/v1/rerankings` to (was `embed-rerank`, which nothing routed to); `embed-rerank` resolves as an alias.
```

- [ ] **Step 3: Docs sweep**

Run: `grep -rn "settings/voice\|settings/imagegen\|settings/npu\|Voice page\|Image Generation page" docs/ --include='*.mdx' --include='*.md' -l`
For each hit (if any), update the reference to the AI Capabilities page. If none — done.

- [ ] **Step 4: Full check + commit**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/ -x -q` and `cd ui && npm run test:unit && npm run build`
Expected: PASS.

```bash
git add ui/src/dash/settings/pages/data/MemoryPage.jsx CHANGELOG.md docs/
git commit -m "docs(ui): cross-link memory reranker, changelog for unified capabilities"
```

---

## Verification (whole branch)

1. `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/ -q` — green.
2. `cd ui && npm run test:unit && node src/dash/__tests__/react-hooks-order.test.mjs && npm run build` — green.
3. `cd ui && npm run test:e2e -- voice-page-provider-copy-v3 capability-catalog-pickers-v3` — green.
4. Manual smoke (dev server or deploy box): `#settings/voice` lands on AI Capabilities; status strip chips scroll; Embed/Rerank panels render catalogs; NPU panel expands; ApplyBadges show `live` on TTS defaults and `⟳ restart hal0-api` on Idle restore.
5. `grep -rn "embed-rerank" src/ tests/ ui/src` — only the alias + comments remain.

## Follow-ups to file as issues (not in this branch)

- Surface `realtime.stt_model` / `realtime.tts_model` / `realtime.tts_voice` (registered, immediate-class, no UI) or document them as realtime-lane-only.
- img device allow-list mismatch: `catalog.py:77` offers only `gpu-vulkan`; seed + `_ensure_slot_exists` use `gpu-rocm`.
- Stale `_settings_apply.py:84-90` comment referencing the removed `memory.embedding.model` field.
- Backend apply-plan rows for capability selections (badges for enable/model/device changes are frontend-silent today).
- Sub-anchor deep links (`#settings/capabilities#cap-tts`) if palette entries per capability are wanted.
