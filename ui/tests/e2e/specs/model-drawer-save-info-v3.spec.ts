/**
 * model-drawer-save-info-v3 — complete model drawer save + compact help.
 *
 * Uses a production-shaped registry row so the PUT contract exercises every
 * field surfaced by ModelDrawer while proving unrelated defaults survive.
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

async function openDrawer(page: import('@playwright/test').Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').first().click()
  await expect(page.getByTestId('model-flags-input')).toBeVisible()
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

    await page.getByTestId('model-name-input').fill('Saved model name')
    await page.getByTestId('type-toggle-coder').click()
    await page.getByTestId('cap-toggle-vision').click()
    await page.getByTestId('backend-toggle-vulkan').click()
    await page.getByTestId('model-mmproj-input').fill('/models/mmproj-saved.gguf')
    await page.getByTestId('model-hfrepo-input').fill('org/saved-repo')
    await page.getByTestId('model-hffile-input').fill('saved-q4.gguf')
    await page.getByTestId('model-template-select').selectOption('rocm-save')
    await page.getByTestId('model-flags-input').fill(PROFILE_FLAGS)
    await page.getByTestId('model-ctx-input').fill('16384')
    await page.getByTestId('model-chat-template').selectOption('chatml')
    await page.getByTestId('cap-mtp-off').click()
    await page.getByTestId('cap-vision-off').click()
    await page.getByTestId('cap-thinking-on').click()
    await page.getByTestId('cap-jinja-off').click()
    await page.getByTestId('model-save').click()

    await expect.poll(() => putBody).not.toBeNull()
    expect(putBody.name).toBe('Saved model name')
    expect(putBody.tags).toEqual(['curated', 'coder'])
    expect(putBody.capabilities).toEqual(['chat', 'vision'])
    expect(putBody.backends).toEqual(['rocm', 'vulkan'])
    expect(putBody.mmproj).toBe('/models/mmproj-saved.gguf')
    expect(putBody.hf_repo).toBe('org/saved-repo')
    expect(putBody.hf_filename).toBe('saved-q4.gguf')
    expect(putBody.defaults).toMatchObject({
      context_size: 16384,
      extra_args: PROFILE_FLAGS,
      profile: 'rocm-save',
      chat_template: 'chatml',
      mtp: false,
      vision: false,
      enable_thinking: true,
      jinja: false,
      rope_freq_base: 1_000_000,
    })
    expect(putBody.defaults).not.toHaveProperty('n_gpu_layers')

    await expect(page.locator('.drawer.open')).toHaveCount(0)
    await expect(page.locator('.hal0-toast')).toContainText('Updated Original model name')
  })

  test('every described label uses hover/focus-only info help', async ({ page }) => {
    await seedProductionModel(page)
    await mockDrawerLookups(page)
    await openDrawer(page)

    const drawer = page.locator('.drawer.open')
    const labels = drawer.locator('.form-lbl')
    await expect(labels).toHaveCount(15)
    await expect(drawer.locator('.form-lbl .sub')).toHaveCount(0)
    await expect(drawer.locator('.form-lbl .field-info-btn')).toHaveCount(15)

    const displayNameLabel = labels.filter({ hasText: 'Display name' })
    const info = displayNameLabel.getByRole('button', { name: 'Info' })
    const popover = displayNameLabel.locator('.field-info-pop')
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
