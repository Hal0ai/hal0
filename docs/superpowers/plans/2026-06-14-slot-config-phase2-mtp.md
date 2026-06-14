# Slot Config Phase 2 (MTP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a per-slot MTP override (decoupled from profile choice) honored in container flag-building, and surface a capability-gated MTP pill in the slot edit drawer's Inference group.

**Architecture:** `SlotConfig.mtp: bool | None` overrides `profile.mtp` when set. `resolve_profile_flags(profile, mtp_override)` expands `MTP_FLAG_BUNDLE` based on the effective value; `container_spec` passes `slot_cfg.get("mtp")`. The UI shows an MTP `PillToggle` only when the loaded model carries the `mtp` tag and the slot is on a `gpu-rocm` device; toggling writes `mtp` via `PUT /config` and fires the non-blocking restart.

**Tech Stack:** Python (pydantic, pytest), React (.jsx), Playwright e2e.

**Base branch:** `afk/slot-config-phase2-mtp` off `main` (includes Phase 1 #796). Spec: `docs/superpowers/specs/2026-06-14-slot-config-grouping-mtp-templates-design.md`.

**Verify:** backend → `python -m pytest <specific test file> -v` (do NOT run the whole suite — it hangs on lemond health waits per repo lore). UI → `npm run typecheck` (0), `npm run build` (0), `npx playwright test <spec> --project=chromium`.

**Research grounding (why this shape):** MTP helps dense models, hurts MoE (bench: `rocm` 52.8 vs `rocm-mtp` 24.4 tps) and needs an MTP-capable GGUF — hence the capability gate. Bundle retune (`--spec-draft-p-min 0.0`→~0.75) is deliberately out of scope (Task 3 files a bench issue).

---

### Task 1: Backend — per-slot `mtp` override

**Files:**
- Modify: `src/hal0/config/schema.py` (add `SlotConfig.mtp`; extend `resolve_profile_flags`)
- Modify: `src/hal0/providers/container.py` (`_profile_image_and_flags` + `container_spec` thread the override)
- Test: `tests/config/test_mtp_override.py` (new)

- [ ] **Step 1: Write the failing test** (`tests/config/test_mtp_override.py`)

```python
from hal0.config.schema import ProfileConfig, resolve_profile_flags, MTP_FLAG_BUNDLE

def _profile(mtp):
    return ProfileConfig(image="img", flags="-fa on -b 512", mtp=mtp, device_class="gpu", backend="rocm")

def test_override_true_appends_bundle_over_profile_false():
    out = resolve_profile_flags(_profile(False), mtp_override=True)
    assert MTP_FLAG_BUNDLE in out

def test_override_false_drops_bundle_over_profile_true():
    out = resolve_profile_flags(_profile(True), mtp_override=False)
    assert MTP_FLAG_BUNDLE not in out
    assert out == "-fa on -b 512"

def test_override_none_falls_back_to_profile():
    assert MTP_FLAG_BUNDLE in resolve_profile_flags(_profile(True), mtp_override=None)
    assert MTP_FLAG_BUNDLE not in resolve_profile_flags(_profile(False), mtp_override=None)
```

- [ ] **Step 2: Run it, confirm FAIL**

Run: `python -m pytest tests/config/test_mtp_override.py -v`
Expected: FAIL — `resolve_profile_flags()` takes 1 positional arg, no `mtp_override`.

- [ ] **Step 3: Add the field + override**

In `schema.py`, add to `SlotConfig` (near `enable_thinking`):
```python
    mtp: bool | None = Field(
        default=None,
        description=(
            "Per-slot MTP (multi-token-prediction speculative decoding) override. "
            "true → force MTP on; false → force off; None → inherit the profile's "
            "mtp. Only effective on rocmfp4 profiles with an MTP-capable model. "
            "See resolve_profile_flags and MTP_FLAG_BUNDLE."
        ),
    )
```
Change `resolve_profile_flags`:
```python
def resolve_profile_flags(profile: ProfileConfig, mtp_override: bool | None = None) -> str:
    base = profile.flags.strip()
    effective_mtp = mtp_override if mtp_override is not None else profile.mtp
    if effective_mtp:
        return f"{base} {MTP_FLAG_BUNDLE}".strip()
    return base
```

- [ ] **Step 4: Run the test, confirm PASS**

Run: `python -m pytest tests/config/test_mtp_override.py -v`
Expected: 3 passed.

- [ ] **Step 5: Thread the override through the container provider**

In `src/hal0/providers/container.py`, change `_profile_image_and_flags` to accept and honor an override:
```python
def _profile_image_and_flags(profile: Any, mtp_override: bool | None = None) -> tuple[str, str]:
    if mtp_override is not None:
        # Slot override wins over any pre-expanded resolved_flags: recompute
        # from the profile's raw flags (both ResolvedProfile and ProfileConfig
        # expose .flags and .mtp).
        flags = resolve_profile_flags(profile, mtp_override)
    else:
        flags = getattr(profile, "resolved_flags", None)
        if flags is None:
            flags = resolve_profile_flags(profile)
    return str(profile.image), str(flags)
```
In `container_spec` (~line 474), pass the slot's override:
```python
    image, flags_str = _profile_image_and_flags(_resolve_profile(profile_name), slot_cfg.get("mtp"))
```

- [ ] **Step 6: Write + run a container-threading test** (append to `tests/config/test_mtp_override.py`)

```python
from hal0.config.schema import ProfileConfig
from hal0.providers.container import _profile_image_and_flags
from hal0.config.schema import MTP_FLAG_BUNDLE as B

def test_profile_image_and_flags_honors_override():
    p = ProfileConfig(image="img", flags="-fa on", mtp=False, device_class="gpu", backend="rocm")
    _, on = _profile_image_and_flags(p, True)
    assert B in on
    _, off = _profile_image_and_flags(p, None)
    assert B not in off
```
Run: `python -m pytest tests/config/test_mtp_override.py -v` → all pass.

- [ ] **Step 7: Commit**

```bash
git add src/hal0/config/schema.py src/hal0/providers/container.py tests/config/test_mtp_override.py
git commit -m "feat(slots): per-slot mtp override honored in container flag-building"
```

---

### Task 2: UI — capability-gated MTP pill

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` (add an MTP `PillToggle` to the Inference `FieldGroup`)
- Test: `ui/tests/e2e/specs/slot-drawer-profile-v3.spec.ts`

Context: `PillToggle` is a bare global (from Phase 1). `modelsQuery = useModels()` is already in the drawer; find the loaded model via `slot.model_id`. The model object surfaces `tags` (via `normalizeApiModel`'s `...m`). MTP capability = `tags` includes `"mtp"`. Gate also on `String(slot.device).startsWith("gpu-rocm")`. Toggling writes `{ mtp: next }` via `editMut` (`PUT /config`) and fires a non-blocking restart (mirror the existing profile-change pattern in `onSaveClick`: `restartMut.mutate(slot.name, { onError: toast })`).

- [ ] **Step 1: Write the failing tests** (add to the `C7` describe block; seed models via `addInitScript` patching `HAL0_DATA.models` — `page.route` is bypassed for `/api/models` in forced-mock mode; models need `capabilities:['chat']`)

```ts
test('C7i — MTP pill shows only for rocm slot with an MTP-capable model and writes mtp', async ({ page }) => {
  const puts: any[] = []
  await page.route('**/api/slots/chat/config', async (route) => {
    if (route.request().method() === 'PUT') puts.push(JSON.parse(route.request().postData() || '{}'))
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route('**/api/slots/chat/restart', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }))
  await page.addInitScript((models) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true, get() { return real },
      set(v) { real = v; if (v && typeof v === 'object') { v.slots = [CHAT_FIXTURE]; v.models = models } },
    })
  }, [{ id: 'qwen-mtp', name: 'qwen-mtp', capabilities: ['chat'], tags: ['rocmfp4', 'mtp'] }])
  // NOTE: define CHAT_FIXTURE inline above via a serialisable const, OR reuse the
  // existing CHAT_CONTAINER by injecting it the same way the other tests do; ensure
  // CHAT_CONTAINER.device starts with 'gpu-rocm' and model_id === 'qwen-mtp'.
  await page.goto('/#slots/chat')
  const mtpRow = page.locator('.drawer .form-row', { hasText: 'MTP' })
  await expect(mtpRow).toBeVisible()
  await mtpRow.locator('button[role="switch"]').click()
  await expect.poll(() => puts.length).toBeGreaterThan(0)
  expect(puts[0].mtp).toBe(true)
})

test('C7j — MTP pill hidden when the model is not MTP-capable', async ({ page }) => {
  await page.addInitScript((models) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true, get() { return real },
      set(v) { real = v; if (v && typeof v === 'object') { v.slots = [CHAT_FIXTURE]; v.models = models } },
    })
  }, [{ id: 'qwen-plain', name: 'qwen-plain', capabilities: ['chat'], tags: ['rocmfp4'] }])
  await page.goto('/#slots/chat')
  await expect(page.locator('.drawer')).toBeVisible()
  await expect(page.locator('.drawer .form-row', { hasText: 'MTP' })).toHaveCount(0)
})
```
The implementer must resolve `CHAT_FIXTURE`/`CHAT_CONTAINER` injection concretely (match how C7h seeds slots+models) and ensure the fixture's `device` is `gpu-rocm*` and `model_id` matches the seeded model. If `CHAT_CONTAINER`'s model_id can't be controlled inline, build a small local fixture object. Report what you used.

- [ ] **Step 2: Run, confirm FAIL** (`npx playwright test slot-drawer-profile-v3 --project=chromium -g "C7i|C7j"`) — no MTP row exists.

- [ ] **Step 3: Implement the pill** in `EditSlotDrawer`, inside the Inference `FieldGroup` (after the Reasoning pill row):

```jsx
{(() => {
  const cur = slot.model_id || slot.model || "";
  const m = (modelsQuery.data ?? []).map(normalizeApiModel).find(x => x.id === cur);
  const mtpCapable = Array.isArray(m?.tags) && m.tags.includes("mtp");
  const isRocm = String(slot.device || "").startsWith("gpu-rocm");
  if (!mtpCapable || !isRocm) return null;
  const mtpOn = slot.mtp === true;
  return (
    <div className="form-row">
      <div className="form-lbl">
        <span>MTP</span>
        <span className="sub">Multi-token speculative decoding — dense models only (MoE runs slower). Restarts the container.</span>
      </div>
      <div className="form-ctl">
        <PillToggle
          on={mtpOn}
          disabled={saving}
          label="MTP"
          stateText={mtpOn ? "On" : "Off"}
          onToggle={async (next) => {
            setSubmitErr(null);
            try {
              await editMut.mutateAsync({ name: slot.name, body: { mtp: next } });
              restartMut.mutate(slot.name, {
                onError: (err) => window.__hal0Toast && window.__hal0Toast(`MTP restart failed — ${err?.message || "see logs"}`, "err"),
              });
              window.__hal0Toast && window.__hal0Toast(`${slot.name} MTP ${next ? "on" : "off"} — restarting in the background`, "info");
            } catch (err) {
              setSubmitErr(err?.message || "MTP toggle failed");
            }
          }}
        />
      </div>
    </div>
  );
})()}
```

- [ ] **Step 4: Confirm pass + no regressions** (`npx playwright test slot-drawer-profile-v3 slot-edit-controls-v3 --project=chromium`), then `npm run typecheck` (0), `npm run build` (0).

- [ ] **Step 5: Commit**

```bash
git add ui/src/dash/slot-modals.jsx ui/tests/e2e/specs/slot-drawer-profile-v3.spec.ts
git commit -m "feat(slots): capability-gated MTP pill in the edit drawer"
```

---

### Task 3 (controller): file the MTP bundle retune bench issue

Not code. After Tasks 1–2 land, file a GitHub issue: retune `MTP_FLAG_BUNDLE` (`--spec-draft-p-min 0.0`→~0.75, `--spec-draft-n-max 4`→5 for dense) behind a Strix Halo bench, citing the research. Tag it `perf`/`bench`.

---

## Self-Review

**Spec coverage (Phase 2 row):** per-slot `mtp` override + flag-merge → Task 1 ✓; capability-gated pill → Task 2 ✓; bench retune ticket → Task 3 ✓. Model capability via `tags` (deferring GGUF-metadata detection) — noted in spec as acceptable.

**Placeholder scan:** none. The one judgment call (CHAT fixture injection in Task 2 Step 1) is explicitly delegated with the constraints (device `gpu-rocm*`, `model_id` matches seeded model) and a report-back requirement.

**Type/name consistency:** `resolve_profile_flags(profile, mtp_override=None)` defined in Task 1 Step 3 and consumed in Step 5; `PillToggle` / `modelsQuery` / `normalizeApiModel` / `editMut` / `restartMut` / `saving` / `setSubmitErr` all exist in the Phase-1 drawer.
