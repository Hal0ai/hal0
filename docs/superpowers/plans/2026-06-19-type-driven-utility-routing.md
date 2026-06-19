# Type-Driven Utility Slot Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a slot card's dashboard placement (main engine grid vs. support footer) derive entirely from its capability `type`, and retire the redundant free-form `group` field so placement can never drift again.

**Architecture:** The inference pane currently splits slots into headline vs. utility rows using a hand-set `group` string that can disagree with the slot's `type` (e.g. a `tts` slot created with the default `group:"chat"` wrongly lands in the main grid). Task 1 rewrites the split to key on `type`. Task 2 removes the now-vestigial `group` field from the create modal, skip-path layout, three incidental readers, and the CLI `add_slot` path. `group` is not a schema field — it rides as a Pydantic `extra:"allow"` passthrough (`src/hal0/config/schema.py:147`) — so removing it needs no migration and no backend test changes; existing on-disk TOMLs with a stray `group` key keep loading and are simply ignored.

**Tech Stack:** Preact/JSX dashboard (`ui/src/dash/`), in-bundle mock data (`VITE_MOCK_HAL0=1`), Playwright e2e (`ui/tests/e2e/`), FastAPI + Pydantic backend (`src/hal0/`).

## Global Constraints

- Work happens in the worktree `/home/halo/dev/hal0-utility-routing` on branch `feat/type-driven-utility-routing` (isolated off `main`; two other sessions share the `fix/hardware-gtt-total-live` checkout). Do not `git checkout`/switch branches in `/home/halo/dev/hal0`.
- The four utility capability types are exactly: `embedding`, `reranking`, `tts`, `transcription`. Image (`image`) is its own pane; LLM (`llm`) is a headline slot.
- NPU and image slots are already cordoned off *before* the utility split (by `devKind(s.device) !== 'npu'` and the image filter); the type split only re-partitions the remaining iGPU/CPU slots.
- Run UI e2e from `ui/`: `npx playwright test specs/<spec>.ts`. Run backend tests from repo root: `pytest <path> -v`.
- End every commit message with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Type-driven zone split in the inference pane

The user-visible fix: a utility-type slot (e.g. the live `test-tts`) drops into the support footer regardless of its `group`. Frontend-only; independently shippable. We encode the footgun directly in the mock by mislabeling the `tts` slot's group, then prove `type` overrides it.

**Files:**
- Modify: `ui/src/dash/inference-pane.jsx` (lines 110-116, 575, 625, 633-639)
- Modify: `ui/src/dash/data.jsx` (the `tts` slot, ~lines 208-219)
- Modify (test): `ui/tests/e2e/specs/inference-pane-v3.spec.ts`

**Interfaces:**
- Produces: module-local `const UTIL_TYPES = new Set(['embedding','reranking','tts','transcription'])` and `function isUtil(s)` in `inference-pane.jsx`, replacing `UTIL_GROUPS` / `isUtilGroup`. No exports change; consumers are all in-file.

- [ ] **Step 1: Mislabel the mock `tts` slot to encode the footgun**

In `ui/src/dash/data.jsx`, the `tts` slot object (name `"tts"`, type `"tts"`) currently has `group: "voice"`. Change it to `group: "chat"` and add a comment so the intent is clear:

```js
      name: "tts",
      type: "tts",
      // …existing fields (port/model/state/device/metrics)…
      group: "chat",   // intentionally MISLABELED: regression fixture — a tts
                       // slot with a non-utility group must STILL route to the
                       // util footer (placement is type-driven, not group-driven).
```

Leave every other field on the slot untouched.

- [ ] **Step 2: Write the failing e2e assertion**

In `ui/tests/e2e/specs/inference-pane-v3.spec.ts`, add a test inside the existing `test.describe('Inference engine pane (/slots · Inference tab)', …)` block. The util zone wraps in `data-testid="infer-util"`; each card (full or mini) renders `data-testid="infer-slot-<name>"`.

```ts
  test('utility-type slots route to the support footer regardless of group', async ({ page }) => {
    await page.goto('/#slots')
    await expect(pane(page)).toBeVisible()
    const util = pane(page).getByTestId('infer-util')
    await expect(util).toBeVisible()
    // embed + rerank (util types) live in the footer…
    await expect(util.getByTestId('infer-slot-embed')).toHaveCount(1)
    await expect(util.getByTestId('infer-slot-rerank')).toHaveCount(1)
    // …and the tts slot does too, EVEN THOUGH its mock group is "chat"
    // (type-driven routing overrides the mislabeled group).
    await expect(util.getByTestId('infer-slot-tts')).toHaveCount(1)
    // it must NOT appear among the headline (full) cards.
    await expect(body(page).locator('.scards.full').getByTestId('infer-slot-tts')).toHaveCount(0)
  })
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd ui && npx playwright test specs/inference-pane-v3.spec.ts -g "route to the support footer"
```
Expected: FAIL — with group-based routing the `tts` slot (group `"chat"`) renders as a headline full card, so `util.getByTestId('infer-slot-tts')` resolves to 0 and the headline assertion finds 1.

- [ ] **Step 4: Rewrite the predicate to key on `type`**

In `ui/src/dash/inference-pane.jsx`, replace the `UTIL_GROUPS` block (lines 110-116):

```js
// Utility (support) slot types — the non-conversational tier that renders as
// the compact mini-card row below the headline chat/agent cards. Placement is
// derived from the slot's capability type, never a hand-set label, so a
// mislabeled slot can't escape its tier. Anything else (llm) is a headline slot;
// image is its own pane.
const UTIL_TYPES = new Set(['embedding', 'reranking', 'tts', 'transcription'])
function isUtil(s) {
  return UTIL_TYPES.has(String(s?.type || '').toLowerCase())
}
```

- [ ] **Step 5: Switch the image filters and the tier split to `type`**

In `ui/src/dash/inference-pane.jsx`, the two identical image-filter lines (575 in `InferenceHeroBand`, 625 in `InferencePane`):

```js
  const nonImg = allSlots.filter((s) => String(s.type) !== 'image')
```
(replacing `(s.group || '') !== 'img'` in both places).

Then update the tier split (lines 633-639):

```js
  // Tier split — headline = the conversational LLM slots; utility = the support
  // slots (embed / rerank / tts / transcription). Keyed off slot.type so the
  // split can't be thrown off by a mislabeled group. This pane is always
  // expanded (no accordion), so the utility tier shows ALL its slots; the live
  // count drives the SubLabel note.
  const headlineRows = rows.filter((r) => !isUtil(r.s))
  const utilRows = rows.filter((r) => isUtil(r.s))
```

- [ ] **Step 6: Run the new test to verify it passes**

```bash
cd ui && npx playwright test specs/inference-pane-v3.spec.ts -g "route to the support footer"
```
Expected: PASS.

- [ ] **Step 7: Run the full inference-pane spec to confirm no regression**

```bash
cd ui && npx playwright test specs/inference-pane-v3.spec.ts
```
Expected: PASS — the existing seed's groups already agree with their types, so headline/util membership is unchanged for every other slot (`primary`/`legacy` headline; `embed`/`rerank` util; `agent`/`stt-npu`/`embed-npu` NPU-cordoned by device; `img` in the image pane).

- [ ] **Step 8: Commit**

```bash
cd /home/halo/dev/hal0-utility-routing
git add ui/src/dash/inference-pane.jsx ui/src/dash/data.jsx ui/tests/e2e/specs/inference-pane-v3.spec.ts
git commit -m "fix(dashboard): route utility slots to the support footer by type, not group

The inference pane split headline vs. utility cards on a hand-set \`group\`
string that can disagree with the slot's type — a tts slot created with the
default group=chat wrongly landed in the main engine grid. Derive the split
(and the image filter) from slot.type instead. Mock fixture mislabels the tts
slot group=chat as a regression guard.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Retire the `group` field

Pure cleanup now that nothing routes on `group`. Removes the create-modal dropdown, the skip-path group sectioning (re-derived from `type`), three incidental readers, and the CLI `add_slot` write. Independently reviewable: a reviewer could accept Task 1's fix while rejecting this cleanup.

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` (state 96 & 110; POST body 160; dropdown 290-304; `EmptySlotCard` 1279 & 1289-1293)
- Modify: `ui/src/dash/slots.jsx` (skip-path `SEEDED` 589-596; `seededByGroup` 632-637; `EmptySlotCard` call 661-668)
- Modify: `ui/src/dash/quickchat-card.jsx` (`isChatCapable` 89-96)
- Modify: `ui/src/dash/connections.jsx` (`slotGroup` 140-148)
- Modify: `ui/src/dash/command-palette.jsx` (keywords 256)
- Modify: `src/hal0/slots/manager.py` (`add_slot` signature 1497; docstring 1516; cfg dict 1545)

**Interfaces:**
- Consumes: `isUtil` / `UTIL_TYPES` from Task 1 (no direct call, but the type-driven routing is the reason `group` is now dead).
- Produces: a module-local `SECTION_FOR_TYPE` map in `slots.jsx` mapping `type → 'chat'|'embed'|'voice'|'img'` for the skip-path layout. `EmptySlotCard` loses its `group` prop. `add_slot` loses its `group` keyword arg.

- [ ] **Step 1: Re-grep for any `slot.group` readers added since planning**

```bash
cd /home/halo/dev/hal0-utility-routing
grep -rn "\.group\b\|[\"']group[\"']" ui/src --include=*.jsx --include=*.js | grep -v node_modules
grep -rn "\bgroup\b" src/hal0/slots/manager.py
```
Expected: only the sites listed in this task plus unrelated matches (`extras.jsx` log groups, `activity-log.jsx` ARIA `role="group"`, `arbiter.py`/`manager.py:2167,2171` image *exclusive group*). If a new slot-rollup `group` reader appears, convert it to `type` the same way before continuing.

- [ ] **Step 2: Remove the `group` dropdown, state, and POST field from the create modal**

In `ui/src/dash/slot-modals.jsx`:
- Delete the state declaration (line 96): `const [group, setGroup] = useStateSM(defaults.group || "chat");`
- Delete the reset line inside the `open` effect (line 110): `setGroup(defaults.group || "chat");`
- Delete the `group,` line from the POST `body` object (line 160).
- Delete the entire `<div className="form-row">` for the Group select (lines 290-304, the block containing `<span>Group</span>` … `</select>`).

- [ ] **Step 3: Drop `group` from `EmptySlotCard`**

In `ui/src/dash/slot-modals.jsx`, change the signature (line 1279) from `function EmptySlotCard({ name, type, group, device, onConfigure })` to `function EmptySlotCard({ name, type, device, onConfigure })`, and delete the group chip line (1292): `<span className="chip">{group}</span>`. The `type` chip (1290) already shows the real category.

- [ ] **Step 4: Re-derive the skip-path sections from `type` in `slots.jsx`**

In `ui/src/dash/slots.jsx`, replace the `SEEDED` array (lines 589-596) — drop each `group:` key — and add a type→section map just above it:

```js
  // Seeded slot identities for the skip-path empty layout. Section membership
  // is derived from the capability type (mirrors the live pane's type-driven
  // split), so seeded cards never disagree with their type.
  const SECTION_FOR_TYPE = {
    llm: "chat", embedding: "embed", reranking: "embed",
    transcription: "voice", tts: "voice", image: "img",
  };
  const SEEDED = [
    { name: "primary", type: "llm",           device: "gpu-rocm" },
    { name: "embed",   type: "embedding",     device: "gpu-rocm" },
    { name: "rerank",  type: "reranking",     device: "gpu-rocm" },
    { name: "stt",     type: "transcription", device: "cpu"      },
    { name: "tts",     type: "tts",           device: "cpu"      },
    { name: "img",     type: "image",         device: "gpu-rocm" },
  ];
```

Then change `seededByGroup` (lines 632-637) to bucket by derived section:

```js
    const seededByGroup = {
      chat:  SEEDED.filter(s => SECTION_FOR_TYPE[s.type] === "chat"),
      embed: SEEDED.filter(s => SECTION_FOR_TYPE[s.type] === "embed"),
      voice: SEEDED.filter(s => SECTION_FOR_TYPE[s.type] === "voice"),
      img:   SEEDED.filter(s => SECTION_FOR_TYPE[s.type] === "img"),
    };
```

And in the `EmptySlotCard` call (lines 661-668), drop the `group={c.group}` prop and the `group: c.group` key from the `openCreatePrefilled` argument:

```js
                      <EmptySlotCard
                        key={c.name}
                        name={c.name}
                        type={c.type}
                        device={c.device}
                        onConfigure={() => openCreatePrefilled({ name: c.name, type: c.type, device: c.device })}
                      />
```

- [ ] **Step 5: Drop the vestigial `group` fallback in `quickchat-card.jsx`**

In `ui/src/dash/quickchat-card.jsx`, `isChatCapable` (lines 89-96). `type === 'llm'` already covers every chat slot, so the `g === 'chat'` branch is dead. Remove the `g` line and the branch:

```js
function isChatCapable(slot) {
  if (slot._synthetic) return false
  const t = (slot.type ?? '').toLowerCase()
  const chatType = t === 'llm' || t === 'chat'
  const liveState = slot.state === 'serving' || slot.state === 'ready'
  return chatType && liveState
}
```

- [ ] **Step 6: Drop the `group` fallback in `connections.jsx`**

In `ui/src/dash/connections.jsx`, `slotGroup` (lines 140-148) already maps every real `type` before the `group` fallback at line 146. Remove that line so the function is type-only:

```js
function slotGroup(s) {
  const t = s.type
  if (t === 'llm') return 'chat'
  if (t === 'embedding' || t === 'reranking') return 'embed'
  if (t === 'image') return 'img'
  if (t === 'tts' || t === 'transcription') return 'voice'
  return 'chat'
}
```

- [ ] **Step 7: Drop `group` from the command-palette search keywords**

In `ui/src/dash/command-palette.jsx` line 256, remove the `s.group` term:

```js
      keywords: `${s.type} ${s.device}`,
```

- [ ] **Step 8: Remove `group` from the CLI `add_slot` path**

In `src/hal0/slots/manager.py`:
- Delete the `group: str = "custom",` parameter from the `add_slot` signature (line 1497).
- Delete the `group:` line from the docstring Args block (line 1516).
- Delete the `"group": group,` entry from the `cfg` dict (line 1545).

The API create route (`src/hal0/api/routes/slots.py:create_slot`) calls `sm.create(name, body)` directly and does not reference `group`, so it needs no change — once the modal (Step 2) stops sending `group`, new slots simply omit it.

- [ ] **Step 9: Run the backend slot tests**

```bash
cd /home/halo/dev/hal0-utility-routing && pytest tests/slots/test_manager.py -v
```
Expected: PASS. The `add_slot` callers in this file (lines 196, 214, 221, 239, 245, 247, 264) pass no `group=` argument, and no test asserts the slot `group` field (all `group` matches in the suite are `coresident_group`/`cgroup`).

- [ ] **Step 10: Run the affected UI e2e specs**

```bash
cd ui && npx playwright test specs/inference-pane-v3.spec.ts specs/slots-v3.spec.ts specs/slots-wireup-v3.spec.ts specs/settings-default-slots-v3.spec.ts
```
Expected: PASS. If any spec asserts the removed Group `<select>` or an `EmptySlotCard` group chip, update that assertion to drop the group expectation (the field no longer exists) — do not re-add `group`.

- [ ] **Step 11: Final sweep — no live `slot.group` readers remain**

```bash
cd /home/halo/dev/hal0-utility-routing
grep -rn "\.group\b\|setGroup\|defaults.group\|c.group\|s.group" ui/src --include=*.jsx --include=*.js | grep -v node_modules
```
Expected: only `extras.jsx` (log-line groups) and `activity-log.jsx` (ARIA role) remain — no slot-rollup `group` readers.

- [ ] **Step 12: Commit**

```bash
cd /home/halo/dev/hal0-utility-routing
git add ui/src/dash/slot-modals.jsx ui/src/dash/slots.jsx ui/src/dash/quickchat-card.jsx ui/src/dash/connections.jsx ui/src/dash/command-palette.jsx src/hal0/slots/manager.py
git commit -m "refactor(slots): retire the redundant slot \`group\` field

Placement and routing are now derived entirely from slot.type (see prior
commit). Remove the free-form \`group\`: the create-modal dropdown, the
skip-path sectioning (re-derived from type), the vestigial group fallbacks in
quickchat/connections/command-palette, and the CLI add_slot write. \`group\`
was never a schema field (extra=allow passthrough), so on-disk TOMLs carrying
it still load and are ignored — no migration needed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- After both tasks, run `graphify update .` from the worktree root to refresh the knowledge graph (AST-only, no API cost), per repo convention.
- Do not seed iGPU utility slots (the original Idea A): embed/rerank already default to the iGPU, and tts (kokoro) / stt (FastFlowLM) have no iGPU runtime — explicitly out of scope.
