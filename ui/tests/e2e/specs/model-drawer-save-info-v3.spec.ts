/**
 * model-drawer-save-info-v3 — complete model drawer save + compact help.
 *
 * Uses a production-shaped registry row so the PUT contract exercises every
 * field surfaced by ModelDrawer while proving unrelated defaults survive.
 *
 * ADR-0029 drawer: the model carries FILE FACTS only. The editable "Backends"
 * chip row is retired in favor of an "Engine" select (`model-provider-select`,
 * writes `provider`) plus a read-only "Runs on" readout; the file facts are the
 * chat template and the overrides ledger (`model-cap-override-add` →
 * `model-cap-override-add-{mtp|jinja}-on|off`), and the native context window is
 * shown read-only. The flag editor, seed-from-profile, context input and the
 * Thinking/Vision overrides moved to the profile, so the PUT body carries ONLY
 * `defaults.{chat_template, mtp, jinja, tokenizer_repo}` — the server answers a
 * tuning key with a 422.
 */
import { test, expect } from '../fixtures/apiMock'
import { pickRichOption } from '../fixtures/richSelect'

const MODEL_ID = 'qwen3.6-27b-mtp'

const PRODUCTION_MODEL = {
  name: 'Original model name',
  tags: ['curated'],
  capabilities: ['chat'],
  backends: ['rocm'],
  mmproj: null,
  hf_repo: 'org/original-repo',
  hf_filename: 'original.gguf',
  defaults: {
    tokenizer_repo: 'org/original-tok',
    // Tuning keys a pre-ADR-0029 row may still carry. The drawer must never
    // echo them back (the server 422s every one).
    extra_args: '-fa on --temp 0.4',
    context_size: 8192,
    profile: 'rocm-save',
    enable_thinking: true,
    vision: false,
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
  await expect(page.getByTestId('model-chat-template')).toBeVisible()
}

/**
 * model-drawer-2 Task 9: MMProj / HF repo / HF filename / Paths live behind a
 * disclosure that rests closed. The apiMock catch-all answers the lazy
 * POST /api/models/inspect with `{}` — no variants, so MMProj degrades to the
 * free-text path input this spec's save contract fills.
 */
async function openSource(page: import('@playwright/test').Page) {
  await page.getByTestId('model-source-disclosure').click()
  await expect(page.getByTestId('model-source-paths')).toBeVisible()
}

test.describe('Model drawer — complete save and compact field help', () => {
  test('successful PUT carries every surfaced field, preserves unrelated defaults, and closes with feedback', async ({ page }) => {
    await seedProductionModel(page)
    await mockDrawerLookups(page)

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
    // Task 6: the native <select> became a RichSelect (rich-select.jsx).
    await pickRichOption(page.getByTestId('model-provider-select'), 'flm')
    await openSource(page)
    await page.getByTestId('model-mmproj-input').fill('/models/mmproj-saved.gguf')
    await page.getByTestId('model-hfrepo-input').fill('org/saved-repo')
    await page.getByTestId('model-hffile-input').fill('saved-q4.gguf')
    // ADR-0029: no flags, context or seed controls on the model any more.
    for (const id of [
      'model-flags-input',
      'model-tune-raw-toggle',
      'model-seed-profile-open',
      'model-ctx-input',
    ]) {
      await expect(page.getByTestId(id)).toHaveCount(0)
    }
    // The native window is a read-only fact.
    await expect(page.getByTestId('model-ctx-native')).toBeVisible()
    await pickRichOption(page.getByTestId('model-chat-template'), 'chatml')
    // Overrides ledger replaces the four always-on TypedCapSeg rows.
    await setCapOverride(page, 'mtp', 'off')
    await setCapOverride(page, 'jinja', 'off')
    await expect(page.getByTestId('model-cap-override-mtp')).toContainText('off')
    await expect(page.getByTestId('model-cap-override-jinja')).toContainText('off')
    // Thinking and vision are profile tunes now — never offered here.
    await expect(page.getByTestId('model-cap-override-thinking')).toHaveCount(0)
    await expect(page.getByTestId('model-cap-override-vision')).toHaveCount(0)
    // Both file facts now have an override — the "+ Override…" button
    // disappears (CapOverrideAdd renders null once `remaining` is empty).
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
    // Facts only: the edited facts plus the one this drawer does not render.
    expect(putBody.defaults).toEqual({
      chat_template: 'chatml',
      mtp: false,
      jinja: false,
      tokenizer_repo: 'org/original-tok',
    })
    expect(JSON.stringify(putBody)).not.toMatch(
      /extra_args|context_size|enable_thinking|rope_freq_base|n_gpu_layers|"vision"|"profile"/,
    )

    await expect(page.locator('.drawer.open')).toHaveCount(0)
    await expect(page.locator('.hal0-toast').last()).toContainText('Updated Original model name')
  })

  test('every described label uses hover/focus-only info help', async ({ page }) => {
    await seedProductionModel(page)
    await mockDrawerLookups(page)
    await openDrawer(page)

    const drawer = page.locator('.drawer.open')
    const labels = drawer.locator('.form-lbl')
    // model-drawer-2 Task 9: the three Source rows (MMProj / HF repo / HF
    // filename) moved behind the Source disclosure and a read-only "Paths" row
    // joined them there, so the RESTING drawer shows 9 − 3 = 6 rows and the
    // opened disclosure adds 4 back for 10. Both counts are asserted, because
    // the rule under test ("every described label carries the info icon, and
    // nothing carries a .sub subtitle") has to hold on either side of it.
    await expect(labels).toHaveCount(6)
    await openSource(page)
    // Resting rows: Chat template, File facts (the overrides ledger),
    // Capabilities, Native context (read-only, replaces the editable Context
    // size), Engine, Runs on. The disclosure adds MMProj, HF repo, HF filename
    // and the read-only Paths row.
    await expect(labels).toHaveCount(10)
    await expect(drawer.locator('.form-lbl .sub')).toHaveCount(0)
    await expect(drawer.locator('.form-lbl .field-info-btn')).toHaveCount(10)

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
