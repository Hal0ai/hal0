# Slot Config Phase 1 (UI reorg) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the slot edit drawer into owner-grouped sections, replace the reasoning checkbox with a two-state pill, remove the "Default for type" checkbox (relocated to a new Settings pane), and make the model dropdown re-filter live from the selected profile — all UI-only, no backend changes.

**Architecture:** Add two presentational primitives (`FieldGroup`, `PillToggle`) to `primitives.jsx`, then restructure `EditSlotDrawer` (`slot-modals.jsx`) to use them. The type-default moves to a new `DefaultSlotsSection` in `settings.jsx` that sets the chosen slot's `default=true` and clears its same-type siblings via the existing `PUT /api/slots/{name}/config`. Verified by Playwright e2e (the correctness gate for `.jsx`).

**Tech Stack:** React (.jsx), TanStack Query, Playwright e2e (`apiMock` + `seedSlots` harness), Vite, `tsc --noEmit`.

**Base branch:** `afk/ui-crud-overhaul` (PR #781). Phase 1 builds directly on #781's editable model dropdown, collapsible Advanced (`details.adv-disclosure`), and non-blocking swap. **Gate: #781 must be merged (or branch from it).** Spec: `docs/superpowers/specs/2026-06-14-slot-config-grouping-mtp-templates-design.md`.

**Verify commands (from the worktree's `ui/` dir; node_modules is symlinked — do NOT install):**
- `npm run typecheck` → exit 0
- `npm run build` → exit 0
- `npx playwright test <spec> --project=chromium` → all pass

---

### Task 1: `FieldGroup` + `PillToggle` primitives

**Files:**
- Modify: `ui/src/dash/primitives.jsx` (add two exported components)
- Modify: `ui/src/dashboard.css` (styles; reuse `.npu-switch` knob pattern at line ~1514)

These are presentational; they are exercised by the consumer e2e in Tasks 2 and 5 (no standalone spec).

- [ ] **Step 1: Add the components to `primitives.jsx`**

```jsx
// FieldGroup — a labeled config section. Groups fields by owner (slot/model/…).
export function FieldGroup({ label, hint, children }) {
  return (
    <div className="field-group">
      <div className="field-group-head">
        <span className="field-group-label">{label}</span>
        {hint && <span className="field-group-hint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

// PillToggle — two-state sliding pill (generalized from slots.jsx NpuSwitch).
// Fixed label; the on/off STATE is shown by the pill, never by a changing label.
export function PillToggle({ on, disabled, label, stateText, onToggle }) {
  return (
    <div className="pill-toggle-row">
      <button
        type="button"
        className="npu-switch"
        role="switch"
        aria-checked={!!on}
        aria-label={label}
        disabled={disabled}
        data-on={on ? "1" : "0"}
        onClick={() => onToggle(!on)}
      >
        <span className="knob" />
      </button>
      {stateText && <span className="pill-toggle-state mono">{stateText}</span>}
    </div>
  );
}
```

- [ ] **Step 2: Add styles to `dashboard.css`** (place near `.npu-switch`, ~line 1514)

```css
.field-group { margin: 18px 0 4px; }
.field-group-head { display: flex; align-items: baseline; gap: 8px; padding: 0 0 6px; border-bottom: 1px solid var(--line-soft); margin-bottom: 8px; }
.field-group-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--fg-4); font-weight: 600; }
.field-group-hint { font-size: 10.5px; color: var(--fg-5); }
.pill-toggle-row { display: flex; align-items: center; gap: 10px; }
.pill-toggle-state { font-size: 12px; color: var(--fg-3); }
```

- [ ] **Step 3: Verify it compiles**

Run: `npm run typecheck && npm run build`
Expected: both exit 0.

- [ ] **Step 4: Commit**

```bash
git add ui/src/dash/primitives.jsx ui/src/dashboard.css
git commit -m "feat(ui): FieldGroup + PillToggle config primitives"
```

---

### Task 2: Reasoning pill (replace the checkbox)

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` (the Thinking `form-row`, ~line 700–735; label flips "Reasoning on/off" at ~731)
- Test: `ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts`

- [ ] **Step 1: Write the failing test** (add to the `describe` block)

```ts
test('C4 — reasoning pill toggles enable_thinking and keeps a fixed label', async ({ page }) => {
  const puts: any[] = []
  await page.route('**/api/slots/primary/config', async (route) => {
    if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await seedSlots(page, [PRIMARY, EMBED]) // PRIMARY enable_thinking:false
  await page.goto('/#slots/primary')

  const row = page.locator('.drawer .form-row', { hasText: 'Reasoning' })
  await expect(row).toBeVisible()
  // Fixed label — the word "Reasoning" is always present, never "Reasoning on/off".
  await expect(row.locator('.form-lbl span').first()).toHaveText('Reasoning')
  const pill = row.locator('button[role="switch"]')
  await expect(pill).toHaveAttribute('aria-checked', 'false')

  await pill.click()
  await expect.poll(() => puts.length).toBeGreaterThan(0)
  expect(puts[0].enable_thinking).toBe(true)
  await expect(pill).toHaveAttribute('aria-checked', 'true')
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx playwright test slot-edit-controls-v3 --project=chromium -g "reasoning pill"`
Expected: FAIL (no `button[role="switch"]` in the Thinking row; label is "Reasoning on/off").

- [ ] **Step 3: Replace the Thinking checkbox with the pill**

In `slot-modals.jsx`, import the primitive: add `PillToggle` to the existing `primitives.jsx` import. Replace the `<label className="checkbox-row">…</label>` block inside the `slot.type === "llm"` Thinking `form-row` (and rename the visible label to "Reasoning") with:

```jsx
<div className="form-row">
  <div className="form-lbl">
    <span>Reasoning</span>
    <span className="sub">Stream reasoning before the answer. Off = faster, direct replies. Applies to the next message.</span>
  </div>
  <div className="form-ctl">
    <PillToggle
      on={thinking}
      disabled={thinkingPending}
      label="Reasoning"
      stateText={thinking ? "On" : "Off"}
      onToggle={async (next) => {
        setThinking(next);
        setThinkingPending(true);
        setSubmitErr(null);
        setThinkingErr(null);
        try {
          await editMut.mutateAsync({ name: slot.name, body: { enable_thinking: next } });
          window.__hal0Toast && window.__hal0Toast(`${slot.name} reasoning ${next ? "on" : "off"} — applies to next message`, "ok");
        } catch (err) {
          setThinking(!next);
          setThinkingErr(err?.message || "reasoning toggle failed");
        } finally {
          setThinkingPending(false);
        }
      }}
    />
    {thinkingErr && <div className="hint" style={{ color: "var(--err)" }}>{thinkingErr}</div>}
  </div>
</div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx playwright test slot-edit-controls-v3 --project=chromium`
Expected: all pass (new test + existing C4/C5/#587).

- [ ] **Step 5: Commit**

```bash
git add ui/src/dash/slot-modals.jsx ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts
git commit -m "feat(slots): reasoning checkbox -> two-state pill with fixed label"
```

---

### Task 3: Remove "Default for type" from the edit drawer

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` (drawer Default `form-row` ~line 672–690; `makeDefault` state ~353; save body `default: makeDefault` ~425)
- Test: `ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts`

Scope: remove from the **edit drawer only**. The create-slot modal's default checkbox stays (creating the first slot of a type as default remains convenient); Task 6 adds the relocation target.

- [ ] **Step 1: Write the failing test**

```ts
test('default-for-type row is gone from the edit drawer and Save omits default', async ({ page }) => {
  const puts: any[] = []
  await page.route('**/api/slots/primary/config', async (route) => {
    if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route('**/api/slots/primary/defaults', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }))
  await seedSlots(page, [PRIMARY, EMBED])
  await page.goto('/#slots/primary')
  await expect(page.locator('.drawer')).toBeVisible()
  await expect(page.locator('.drawer .form-row', { hasText: 'Default for type' })).toHaveCount(0)
  await page.locator('.drawer button:has-text("Save")').click()
  await expect.poll(() => puts.length).toBeGreaterThan(0)
  expect(puts[0]).not.toHaveProperty('default')
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx playwright test slot-edit-controls-v3 --project=chromium -g "default-for-type row is gone"`
Expected: FAIL (row present; Save sends `default`).

- [ ] **Step 3: Remove the row + the save field**

In `slot-modals.jsx`: (a) delete the entire `<div className="form-row">…Default for type…</div>` block in `EditSlotDrawer`; (b) delete the `const [makeDefault, setMakeDefault] = useStateSM(!!slot?.isDefault);` line and its `setMakeDefault(...)` in the re-seed `useEffect`; (c) in `onSaveClick`, change the slot body from `const slotBody = { default: makeDefault };` to `const slotBody = {};` (keep the `if (profileChanged) slotBody.profile = selectedProfile;` line). Leave the create-slot modal's `makeDefault` untouched.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx playwright test slot-edit-controls-v3 slot-drawer-profile-v3 --project=chromium`
Expected: all pass (note: C7c asserts the no-op-profile Save body has no `profile` — still true; it never asserted `default`).

- [ ] **Step 5: Commit**

```bash
git add ui/src/dash/slot-modals.jsx ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts
git commit -m "feat(slots): drop Default-for-type from edit drawer (moves to Settings)"
```

---

### Task 4: Reactive model dropdown — filter by the selected profile

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` (the Model `<select>` block from #781, ~line 595–660; filter uses `slot.backend`)
- Test: `ui/tests/e2e/specs/slot-drawer-profile-v3.spec.ts`

- [ ] **Step 1: Write the failing test** (uses the `chat` container slot, profiles `rocm`/`vulkan`; rocmfp4 models must drop when vulkan is selected)

```ts
test('C7h — model options re-filter from the SELECTED profile, not the persisted one', async ({ page }) => {
  // CHAT_CONTAINER is on a rocm profile; seed an rocmfp4-tagged model + a plain one.
  await page.addInitScript(() => {
    (window as any).__HAL0_TEST_MODELS = [
      { id: 'qwen-fp4', longName: 'qwen-fp4', type: 'llm', tags: ['rocmfp4'] },
      { id: 'qwen-plain', longName: 'qwen-plain', type: 'llm', tags: [] },
    ]
  })
  await page.route('**/api/models', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify([
        { id: 'qwen-fp4', name: 'qwen-fp4', type: 'llm', tags: ['rocmfp4'] },
        { id: 'qwen-plain', name: 'qwen-plain', type: 'llm', tags: [] },
      ]) }))
  await seedSlots(page, [CHAT_CONTAINER])
  await page.goto('/#slots/chat')
  const modelSel = page.locator('.drawer .form-row', { hasText: 'Model' }).locator('select')
  // rocm profile selected → fp4 model present
  await expect(modelSel.locator('option[value="qwen-fp4"]')).toHaveCount(1)
  // switch profile to vulkan → fp4 model filtered out
  await page.locator('.drawer .form-row', { hasText: 'Profile' }).locator('select').selectOption('vulkan')
  await expect(modelSel.locator('option[value="qwen-fp4"]')).toHaveCount(0)
  await expect(modelSel.locator('option[value="qwen-plain"]')).toHaveCount(1)
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx playwright test slot-drawer-profile-v3 --project=chromium -g "C7h"`
Expected: FAIL (filter keyed on `slot.backend`, so changing the Profile select doesn't re-filter).

- [ ] **Step 3: Make the filter depend on `selectedProfile`**

In the Model `<select>` block, replace the `slot.backend` reference in the compatibility filter with the backend of the **selected** profile:

```jsx
// Derive the backend from the SELECTED profile (reactive), falling back to the
// slot's persisted backend when the profile carries none / isn't found.
const selProfileMeta = (profilesQuery.data ?? []).find(p => p.name === selectedProfile);
const selBackend = selProfileMeta?.backend ?? slot.backend;
const compatible = (modelsQuery.data ?? [])
  .map(normalizeApiModel)
  .filter(m =>
    m.type === slot.type &&
    !(Array.isArray(m.tags) && m.tags.includes("rocmfp4") && selBackend !== "rocm")
  );
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx playwright test slot-drawer-profile-v3 --project=chromium`
Expected: all pass (new C7h + existing C7a–C7g).

- [ ] **Step 5: Commit**

```bash
git add ui/src/dash/slot-modals.jsx ui/tests/e2e/specs/slot-drawer-profile-v3.spec.ts
git commit -m "feat(slots): model dropdown re-filters live from the selected profile"
```

---

### Task 5: Regroup the drawer into FieldGroup sections

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` (`EditSlotDrawer` body — wrap existing rows in `FieldGroup`s; SLOT/MODEL/INFERENCE; Advanced stays the `details.adv-disclosure`)
- Test: `ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts`

- [ ] **Step 1: Write the failing test**

```ts
test('drawer fields are grouped under SLOT / MODEL / INFERENCE', async ({ page }) => {
  await seedSlots(page, [PRIMARY, EMBED])
  await page.goto('/#slots/primary')
  await expect(page.locator('.drawer')).toBeVisible()
  for (const label of ['Slot', 'Model', 'Inference']) {
    await expect(page.locator('.field-group-label', { hasText: new RegExp(`^${label}$`, 'i') })).toHaveCount(1)
  }
  // Model dropdown sits inside the MODEL group.
  const modelGroup = page.locator('.field-group', { has: page.locator('.field-group-label', { hasText: /^Model$/i }) })
  await expect(modelGroup.locator('.form-row', { hasText: 'Model' }).locator('select')).toBeVisible()
  // Reasoning pill sits inside the INFERENCE group.
  const infGroup = page.locator('.field-group', { has: page.locator('.field-group-label', { hasText: /^Inference$/i }) })
  await expect(infGroup.locator('.form-row', { hasText: 'Reasoning' })).toBeVisible()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx playwright test slot-edit-controls-v3 --project=chromium -g "grouped under"`
Expected: FAIL (no `.field-group-label` elements yet).

- [ ] **Step 3: Wrap the existing rows in `FieldGroup`s**

Add `FieldGroup` to the `primitives.jsx` import. In `EditSlotDrawer`'s returned JSX, wrap (do not rewrite the inner rows):
- `<FieldGroup label="Slot" hint="this instance">` around the Profile row and any slot-level read-only rows.
- `<FieldGroup label="Model" hint="what it loads">` around the Model `<select>` row and the ctx_size row (move ctx_size out of Advanced into MODEL; keep its restart warning).
- `<FieldGroup label="Inference" hint="behavior">` around the Reasoning pill row. (MTP pill lands here in Phase 2.)
- Keep the existing `<details className="adv-disclosure">` Advanced block (n_gpu_layers / rope_freq_base / extra_args / resolved command) as-is, after the groups.

Net structural change only — no field logic changes.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx playwright test slot-edit-controls-v3 slot-drawer-profile-v3 --project=chromium`
Expected: all pass. (If a prior spec located ctx_size via `details.adv-disclosure summary` click, update it to find ctx_size in the MODEL group — adjust those specs in this step.)

- [ ] **Step 5: Commit**

```bash
git add ui/src/dash/slot-modals.jsx ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts
git commit -m "feat(slots): group edit drawer fields by owner (Slot/Model/Inference)"
```

---

### Task 6: Settings "Default slots" pane

**Files:**
- Modify: `ui/src/dash/settings.jsx` (new `DefaultSlotsSection`; add `defaults` to the section switch ~line 67 and to the nav list)
- Test: `ui/tests/e2e/specs/` — new `settings-default-slots-v3.spec.ts`

No backend change: setting a default = `PUT /config { default: true }` on the chosen slot and `PUT /config { default: false }` on the previously-default sibling of the same type.

- [ ] **Step 1: Write the failing test** (`ui/tests/e2e/specs/settings-default-slots-v3.spec.ts`)

```ts
import { test, expect, type Page } from '../fixtures/apiMock'

async function seedSlots(page: Page, slots: any[]) {
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true, get() { return real },
      set(v) { real = v; if (v && typeof v === 'object') v.slots = slots },
    })
  }, slots)
}

const A = { name: 'primary', type: 'llm', device: 'gpu-rocm', state: 'serving', port: 8092, isDefault: true, enabled: true }
const B = { name: 'backup',  type: 'llm', device: 'gpu-rocm', state: 'ready',   port: 8093, isDefault: false, enabled: true }

test('Default slots pane sets the chosen slot default and clears the prior one', async ({ page }) => {
  const puts: Record<string, any[]> = { primary: [], backup: [] }
  for (const n of ['primary', 'backup']) {
    await page.route(`**/api/slots/${n}/config`, async (route) => {
      if (route.request().method() === 'PUT') puts[n].push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
  }
  await seedSlots(page, [A, B])
  await page.goto('/#settings/defaults')
  const row = page.locator('.default-slot-row', { hasText: 'llm' })
  await expect(row).toBeVisible()
  await row.locator('select').selectOption('backup')
  await expect.poll(() => puts.backup.length).toBeGreaterThan(0)
  expect(puts.backup[0].default).toBe(true)   // new default set
  await expect.poll(() => puts.primary.length).toBeGreaterThan(0)
  expect(puts.primary[0].default).toBe(false)  // prior default cleared
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx playwright test settings-default-slots-v3 --project=chromium`
Expected: FAIL (route `#settings/defaults` and `.default-slot-row` don't exist).

- [ ] **Step 3: Add `DefaultSlotsSection` + wire the nav**

In `settings.jsx`, add the component (uses `useSlots` + `useSlotEdit`; group slots by `type`, render a row per type with ≥2 slots):

```jsx
function DefaultSlotsSection() {
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();
  const slots = slotsQuery.data || [];
  const byType = {};
  for (const s of slots) { (byType[s.type] ||= []).push(s); }
  const types = Object.keys(byType).filter(t => byType[t].length >= 2).sort();

  const setDefault = async (type, name) => {
    const sibs = byType[type] || [];
    const prev = sibs.find(s => s.isDefault && s.name !== name);
    try {
      await editSlot.mutateAsync({ name, body: { default: true } });
      if (prev) await editSlot.mutateAsync({ name: prev.name, body: { default: false } });
      window.__hal0Toast && window.__hal0Toast(`Default ${type} slot → ${name}`, "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Couldn't set default — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="settings-section">
      <h3>Default slots</h3>
      <p className="sub">For each modality with more than one slot, choose which slot serves type-routed requests.</p>
      {types.length === 0 && <p className="hint">No modality has multiple slots yet.</p>}
      {types.map(type => {
        const cur = (byType[type].find(s => s.isDefault) || {}).name || "";
        return (
          <div className="default-slot-row form-row" key={type}>
            <div className="form-lbl"><span>{type}</span></div>
            <div className="form-ctl">
              <select className="input mono" value={cur} disabled={editSlot.isPending}
                onChange={e => { const n = e.target.value; if (n && n !== cur) setDefault(type, n); }}>
                {byType[type].map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

Then wire it into the nav: add `{section === "defaults" && <DefaultSlotsSection />}` alongside the other `section === …` lines (~67), and add a `{ key: "defaults", label: "Default slots" }` entry to the settings nav list (search for where `storage`/`voice`/`secrets` labels are defined and mirror the shape).

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx playwright test settings-default-slots-v3 --project=chromium`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

```bash
npm run typecheck && npm run build && npx playwright test --project=chromium
git add ui/src/dash/settings.jsx ui/tests/e2e/specs/settings-default-slots-v3.spec.ts
git commit -m "feat(settings): Default slots pane (relocated type-default control)"
```

---

## Self-Review

**Spec coverage (Phase 1 rows of the spec's phasing table):**
- FieldGroup grouping → Tasks 1, 5 ✓
- Reasoning pill → Task 2 ✓
- Remove type-default checkbox → Task 3 ✓
- Reactive model dropdown (filter by selected profile) → Task 4 ✓
- Settings "Default slots" pane → Task 6 ✓
- (Phases 2–3: MTP pill, chat templates — separate plans, out of scope here.)

**Placeholder scan:** none — every code/test step carries concrete code. The single "mirror the nav-list shape" in Task 6 Step 3 is a locate-and-copy instruction against an existing pattern, with the exact entry shape given.

**Type/name consistency:** `FieldGroup({label,hint,children})` and `PillToggle({on,disabled,label,stateText,onToggle})` are defined in Task 1 and consumed with those exact props in Tasks 2/5. `selectedProfile`, `profilesQuery`, `normalizeApiModel`, `editMut`, `useSlots`, `useSlotEdit` all already exist in the touched files (#781 base).

**Risk note:** Task 5 may require touching prior specs that located ctx_size via the Advanced disclosure (now in the MODEL group) — handled in Task 5 Step 4.
