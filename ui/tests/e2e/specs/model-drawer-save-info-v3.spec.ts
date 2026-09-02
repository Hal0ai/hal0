/**
 * model-drawer-save-info-v3 — complete model drawer save + compact help.
 *
 * Uses a production-shaped registry row so the PUT contract exercises every
 * field surfaced by ModelDrawer while proving unrelated defaults survive.
 *
 * Option A drawer (Task 8, PR-3): the editable "Backends" chip-toggle row is
 * retired (spec-hw-slot-ownership §1/§8 — the model is device-agnostic) in
 * favor of an "Engine" select (`model-provider-select`, writes `provider`)
 * plus a read-only "Runs on" readout; the four always-on tri-state
 * (mtp/thinking/jinja/vision) rows collapse into the overrides ledger
 * (`model-cap-override-add` → `model-cap-override-add-{id}-on|off`); and
 * stamping a profile goes through the "⤵ Seed from profile…" button
 * (`model-seed-profile-open` → `model-seed-profile-option-{name}`), which
 * POSTs /api/models/{id}/seed-profile instead of copying flags client-side.
 *
 * The seed-profile server route shipped in #2198 (e31a451b, merged to main)
 * — mockSeedProfile below stays regardless, because this whole harness is
 * mock-driven (apiMock.ts: page.route stubs, Phase A/HAL0_DATA-seeded), the
 * same reason mockDrawerLookups mocks /api/profiles and /api/chat-templates
 * despite both existing server-side too.
 */
import { test, expect } from '../fixtures/apiMock'

const MODEL_ID = 'qwen3.6-27b-mtp'
const PROFILE_FLAGS = '--cache-type-k q8_0'

const PRODUCTION_MODEL = {
  name: 'Original model name',
  tags: ['curated'],
  capabilities: ['chat'],
  backends: ['rocm'],
  mmproj: null,
  hf_repo: 'org/original-repo',
  hf_filename: 'original.gguf',
  defaults: {
    rope_freq_base: 1_000_000,
    n_gpu_layers: 99,
  },
}

async function seedProductionModel(page: import('@playwright/test').Page) {
  await page.addInitScript(({ id, model }) => {
    window.addEventListener('DOMContentLoaded', () => {
      const target = (window as any).HAL0_DATA?.models?.find((row: any) => row.id === id)
      if (target) {
        Object.assign(target, model)
        // Remove legacy display aliases so normalizeApiModel derives the same
        // values it would from this production registry shape.
        delete target.longName
        delete target.repo
        delete target.labels
      }
    })
  }, { id: MODEL_ID, model: PRODUCTION_MODEL })
}

async function mockDrawerLookups(page: import('@playwright/test').Page) {
  await page.route('**/api/profiles', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{
        name: 'rocm-save',
        image: 'ghcr.io/hal0ai/hal0-rocmfp4',
        flags: PROFILE_FLAGS,
        resolved_flags: PROFILE_FLAGS,
        mtp: true,
        intent: 'Save contract fixture',
        device_class: 'gpu',
        backend: 'rocm',
      }]),
    }),
  )
  await page.route('**/api/chat-templates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 'auto', label: 'Auto (GGUF embedded)' },
        { id: 'chatml', label: 'ChatML' },
      ]),
    }),
  )
}

/** Route is live on main (#2198, e31a451b) — mocked because this harness is
 * mock-driven, not because the server doesn't have it. */
async function mockSeedProfile(page: import('@playwright/test').Page) {
  const requests: any[] = []
  await page.route(`**/api/models/${MODEL_ID}/seed-profile`, async (route) => {
    const body = route.request().postDataJSON()
    requests.push(body)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: MODEL_ID,
        defaults: { profile: body.profile, extra_args: PROFILE_FLAGS },
      }),
    })
  })
  return requests
}

async function stampFromProfile(page: import('@playwright/test').Page, name: string) {
  await page.getByTestId('model-seed-profile-open').click()
  await page.getByTestId(`model-seed-profile-option-${name}`).click()
}

/** Set an override via the ledger's "+ Override…" menu (cap id + on/off). */
async function setCapOverride(
  page: import('@playwright/test').Page,
  id: string,
  value: 'on' | 'off',
) {
  await page.getByTestId('model-cap-override-add').click()
  await page.getByTestId(`model-cap-override-add-${id}-${value}`).click()
}

async function openDrawer(page: import('@playwright/test').Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').first().click()
  await expect(page.getByTestId('model-tune-raw-toggle')).toBeVisible()
}

test.describe('Model drawer — complete save and compact field help', () => {
  test('successful PUT carries every surfaced field, preserves unrelated defaults, and closes with feedback', async ({ page }) => {
    await seedProductionModel(page)
    await mockDrawerLookups(page)
    const seedRequests = await mockSeedProfile(page)

    let putBody: any = null
    await page.route(`**/api/models/${MODEL_ID}`, async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = route.request().postDataJSON()
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ id: MODEL_ID, ...putBody }),
        })
      }
      return route.fallback()
    })

    await openDrawer(page)

    // model-drawer-2 Task 3: name edits ride the inline title editor now.
    await page.getByTestId('model-title-edit').click()
    await page.getByTestId('model-title-input').fill('Saved model name')
    await page.getByTestId('model-title-input').press('Enter')
    // Backends is retired (spec-hw-slot-ownership §1/§8) — Engine is the
    // write path now; "Runs on" is derived/read-only and not editable here.
    await page.getByTestId('model-provider-select').selectOption('flm')
    await page.getByTestId('model-mmproj-input').fill('/models/mmproj-saved.gguf')
    await page.getByTestId('model-hfrepo-input').fill('org/saved-repo')
    await page.getByTestId('model-hffile-input').fill('saved-q4.gguf')
    // Stamp now round-trips through POST /api/models/{id}/seed-profile.
    await stampFromProfile(page, 'rocm-save')
    // model-drawer-2 Task 4: the flags textarea is the raw view behind
    // `model-tune-raw-toggle` — the stamped STRING is what this asserts, so it
    // reads it there.
    await page.getByTestId('model-tune-raw-toggle').click()
    await expect(page.getByTestId('model-flags-input')).toHaveValue(PROFILE_FLAGS)
    expect(seedRequests).toEqual([{ profile: 'rocm-save' }])
    await page.getByTestId('model-ctx-input').fill('16384')
    await page.getByTestId('model-chat-template').selectOption('chatml')
    // Overrides ledger replaces the four always-on TypedCapSeg rows.
    await setCapOverride(page, 'mtp', 'off')
    await setCapOverride(page, 'thinking', 'on')
    await setCapOverride(page, 'jinja', 'off')
    await setCapOverride(page, 'vision', 'off')
    await expect(page.getByTestId('model-cap-override-mtp')).toContainText('off')
    await expect(page.getByTestId('model-cap-override-thinking')).toContainText('on')
    await expect(page.getByTestId('model-cap-override-jinja')).toContainText('off')
    await expect(page.getByTestId('model-cap-override-vision')).toContainText('off')
    // Every cap now has an override — the "+ Override…" button disappears
    // (CapOverrideAdd renders null once `remaining` is empty).
    await expect(page.getByTestId('model-cap-override-add')).toHaveCount(0)
    await page.getByTestId('model-save').click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody.name).toBe('Saved model name')
    // Type-tag chips are retired — saves never touch tags.
    expect(putBody).not.toHaveProperty('tags')
    expect(putBody.capabilities).toBeUndefined()
    // Backends is a dead write path (models_service.py drops a client-sent
    // `backends` key silently) — the drawer never sends it; provider is the
    // real write instead.
    expect(putBody).not.toHaveProperty('backends')
    expect(putBody.provider).toBe('flm')
    expect(putBody.mmproj).toBe('/models/mmproj-saved.gguf')
    expect(putBody.hf_repo).toBe('org/saved-repo')
    expect(putBody.hf_filename).toBe('saved-q4.gguf')
    expect(putBody.defaults).toMatchObject({
      context_size: 16384,
      extra_args: PROFILE_FLAGS,
      profile: 'rocm-save',
      chat_template: 'chatml',
      mtp: false,
      enable_thinking: true,
      jinja: false,
      vision: false,
      rope_freq_base: 1_000_000,
    })
    // n_gpu_layers is the sunset key (spec-hw-slot-ownership §2). It rides as an
    // explicit `null` rather than an omitted key since #1413: the server merges
    // `defaults` one level deep now, so an absent key KEEPS the stored value —
    // omitting it would round-trip the sunset value instead of unsetting it.
    expect(putBody.defaults).toHaveProperty('n_gpu_layers')
    expect(putBody.defaults.n_gpu_layers).toBeNull()

    await expect(page.locator('.drawer.open')).toHaveCount(0)
    // The seed-profile step above raises its own "Seeded …" toast first —
    // both can be on screen at once, so scope to the one Save raised.
    await expect(page.locator('.hal0-toast').last()).toContainText('Updated Original model name')
  })

  test('every described label uses hover/focus-only info help', async ({ page }) => {
    await seedProductionModel(page)
    await mockDrawerLookups(page)
    await openDrawer(page)

    const drawer = page.locator('.drawer.open')
    const labels = drawer.locator('.form-lbl')
    // 9 rows (was 13 pre-Task-8): Option A's header meta move relocated
    // "Default for {type}" off the row list entirely (−1) and folded the
    // "Modality" row into a renamed "Capabilities" row in place (±0 — the
    // modality tag left for the header, the readout chips stayed); the four
    // always-on tri-state rows (mtp/thinking/jinja/vision) collapsed into one
    // "Overrides" ledger row (−4 +1 = −3); and the single editable "Backends"
    // row split into "Engine" + read-only "Runs on" (−1 +2 = +1).
    // 13 − 1 − 3 + 1 = 10, then model-drawer-2 Task 3 moved "Display name"
    // off the row list onto the header's inline title editor (−1) = 9.
    await expect(labels).toHaveCount(9)
    await expect(drawer.locator('.form-lbl .sub')).toHaveCount(0)
    await expect(drawer.locator('.form-lbl .field-info-btn')).toHaveCount(9)

    // Display name no longer has a form row (Task 3) — MMProj is a plain
    // text-input row with the same FieldInfoIcon shape, so it stands in for
    // the generic hover/focus-only info-help behaviour asserted below.
    const mmprojLabel = labels.filter({ hasText: 'MMProj' })
    const info = mmprojLabel.getByRole('button', { name: 'Info' })
    // #1683: the popup portals to document.body (so overflow:hidden panels
    // can't clip it), so it's no longer a DOM descendant of the label — find
    // it via the button's aria-describedby instead of a row-scoped locator.
    const popupId = await info.getAttribute('aria-describedby')
    // useId() ids contain colons (e.g. ":r0:"), invalid in a raw #id CSS
    // selector — use an attribute selector, which doesn't need escaping.
    const popover = page.locator(`[id="${popupId}"]`)
    await expect(popover).toHaveCount(1)
    await expect(popover).toBeHidden()

    // Pointer activation does not focus the button, so moving away immediately
    // after the click cannot leave the description pinned open.
    await info.click()
    await expect(info).not.toBeFocused()
    await page.mouse.move(0, 0)
    await expect(popover).toBeHidden()

    // Pointer hover reveals the description and leaving the icon hides it.
    await info.hover()
    await expect(popover).toBeVisible()
    await page.mouse.move(0, 0)
    await expect(popover).toBeHidden()

    // Keyboard focus provides the same transient help without a click.
    await info.focus()
    await expect(popover).toBeVisible()
    await page.keyboard.press('Tab')
    await expect(popover).toBeHidden()
  })
})
