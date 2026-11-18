/**
 * providers-v3 — Models → Providers, driven in a real browser against the mock API.
 *
 * The tab (`#models/providers`) lists one section per remote provider with a switch per model, a
 * detail panel on the right and an "Add provider" modal. The provider routes are answered by
 * `installProviders` (tests/e2e/fixtures/providers.ts), which serves the same builders and
 * mutation fixtures the unit tests pin to src/hal0/api/routes/providers.py and keeps what a spec
 * changes. Mock set: corp (not converted yet), minimax (7 rows), openai (key missing),
 * openrouter (450 rows), hermes.coder (managed by an agent, read-only).
 *
 * Every test also fails on an unexpected console error or uncaught page error. A test that forces
 * an HTTP failure allows the browser's own "Failed to load resource" line for it, nothing else.
 */
import type { Locator, Page } from '@playwright/test'

import { test, expect } from '../fixtures/apiMock'
import { installProviders, watchErrors, type ProvidersCtl } from '../fixtures/providers'

const HTTP_NOISE = [/Failed to load resource/]

type Env = { ctl: ProvidersCtl; errors: ReturnType<typeof watchErrors> }

async function open(
  page: Page,
  allow: RegExp[] = [],
  hash = '#models/providers',
  opts: { switchedOff?: string[]; keyless?: boolean } = {},
): Promise<Env> {
  const errors = watchErrors(page, allow)
  const ctl = await installProviders(page, opts)
  await page.goto('/' + hash)
  await expect(page.getByTestId('providers-tab')).toBeVisible()
  await expect(page.getByTestId('prov-head-minimax')).toBeVisible()
  return { ctl, errors }
}

const head = (page: Page, name: string) => page.getByTestId(`prov-head-${name}`)
const sw = (page: Page, label: string) => page.getByRole('switch', { name: label, exact: true })
const toast = (page: Page, text: string | RegExp) => page.locator('.hal0-toast', { hasText: text })

async function expand(page: Page, name: string) {
  await head(page, name).click()
  await expect(head(page, name)).toHaveAttribute('aria-expanded', 'true')
}

/** Open the Add provider dialog from the Models header. */
async function addModal(page: Page) {
  await page.getByTestId('mdl-add').click()
  const modal = page.getByTestId('add-provider-modal')
  await expect(modal).toBeVisible()
  return modal
}

async function noHorizontalScroll(page: Page, scope: Locator | null = null) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(over, 'page scrolls sideways').toBeLessThanOrEqual(0)
  if (scope) {
    const inner = await scope.evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(inner, 'container scrolls sideways').toBeLessThanOrEqual(0)
  }
}

test.describe('Providers tab', () => {
  test('lists every provider as a section, collapsed until one is picked', async ({ page }) => {
    const { errors } = await open(page)
    for (const n of ['corp', 'minimax', 'openai', 'openrouter', 'hermes.coder']) {
      await expect(head(page, n)).toBeVisible()
      await expect(head(page, n)).toHaveAttribute('aria-expanded', 'false')
    }
    await expect(page.getByTestId('models-tab-providers')).toContainText('5')
    await expect(head(page, 'minimax')).toContainText('2 of 7 on')
    await expect(head(page, 'openai')).toContainText('no key')
    await expect(page.getByTestId('provider-detail')).toContainText('Select a provider')
    expect(errors.seen).toEqual([])
  })

  test('switching a model on sends the change, shows a toast and moves the count', async ({ page }) => {
    const { ctl, errors } = await open(page)
    await expand(page, 'minimax')
    await expect(page.getByTestId('prov-section-minimax')).toContainText('MiniMax-M2.5')
    await sw(page, 'Switch on minimax/MiniMax-M2.5').click()
    await expect(toast(page, 'minimax/MiniMax-M2.5 switched on')).toBeVisible()
    await expect(sw(page, 'Switch off minimax/MiniMax-M2.5')).toHaveAttribute('aria-checked', 'true')
    await expect(head(page, 'minimax')).toContainText('3 of 7 on')
    const patches = ctl.callsTo('PATCH', /\/models\/MiniMax-M2\.5$/)
    expect(patches).toHaveLength(1)
    expect(patches[0].body).toEqual({ enabled: true })
    expect(errors.seen).toEqual([])
  })

  test('a model id with slashes keeps them as path separators in the PATCH', async ({ page }) => {
    const { ctl, errors } = await open(page)
    await expand(page, 'openrouter')
    await sw(page, 'Switch off openrouter/qwen/qwen3-coder').click()
    await expect(toast(page, 'openrouter/qwen/qwen3-coder switched off')).toBeVisible()
    const patches = ctl.callsTo('PATCH', /\/api\/upstreams\/openrouter\/models\//)
    expect(patches).toHaveLength(1)
    expect(patches[0].path).toBe('/api/upstreams/openrouter/models/qwen/qwen3-coder')
    expect(patches[0].body).toEqual({ enabled: false })
    expect(errors.seen).toEqual([])
  })

  test('the 450-row provider shows 25 available rows and "Show 25 more"', async ({ page }) => {
    const { errors } = await open(page)
    await expand(page, 'openrouter')
    const off = page.getByTestId('prov-off-openrouter')
    await expect(off.locator('.prov-row')).toHaveCount(25)
    await expect(off.getByRole('button', { name: 'Show 25 more' })).toBeVisible()
    await off.getByRole('button', { name: 'Show 25 more' }).click()
    await expect(off.locator('.prov-row')).toHaveCount(50)
    expect(errors.seen).toEqual([])
  })

  test('search finds a deep row of the 450-row provider with every section collapsed', async ({ page }) => {
    const { errors } = await open(page)
    await expect(head(page, 'openrouter')).toHaveAttribute('aria-expanded', 'false')
    await page.getByPlaceholder('Search models across providers').fill('model-440')
    const row = page.locator('[data-testid^="prov-model-openrouter-"]', { hasText: 'model-440' })
    await expect(row).toHaveCount(1)
    await expect(head(page, 'openrouter')).toContainText('1 match')
    // The other providers have nothing to show for this query.
    await expect(head(page, 'minimax')).toContainText('no match')
    expect(errors.seen).toEqual([])
  })

  test('a one-letter search draws exactly 25 available rows of the big provider and says how many more', async ({ page }) => {
    const { errors } = await open(page)
    await page.getByPlaceholder('Search models across providers').fill('a')
    const off = page.getByTestId('prov-off-openrouter')
    await expect(off).toBeVisible()
    await expect(off.locator('.prov-row')).toHaveCount(25)
    await expect(off.locator('.prov-more span')).toHaveText(/^\d+ more available$/)
    // No provider ever draws more than one page of available rows for the query.
    for (const n of ['minimax', 'openai']) {
      const sec = page.getByTestId(`prov-off-${n}`)
      if (await sec.count()) expect(await sec.locator('.prov-row').count()).toBeLessThanOrEqual(25)
    }
    expect(errors.seen).toEqual([])
  })

  test('while searching the chevron shows what is open and a header click selects the provider', async ({ page }) => {
    const { errors } = await open(page)
    await page.getByPlaceholder('Search models across providers').fill('qwen')
    const chev = head(page, 'openrouter').locator('.prov-chev')
    await expect(head(page, 'openrouter')).toHaveAttribute('aria-expanded', 'true')
    await expect(chev).toHaveText('▾')
    await head(page, 'openrouter').click()
    // Still open (the search keeps it open) and now the selected provider.
    await expect(head(page, 'openrouter')).toHaveAttribute('aria-expanded', 'true')
    await expect(head(page, 'openrouter')).toHaveClass(/sel/)
    await expect(page.getByTestId('prov-detail-provider')).toContainText('openrouter')
    // Clearing the search returns to the remembered state: this provider was selected, so it opens.
    await page.getByPlaceholder('Search models across providers').fill('')
    await expect(head(page, 'minimax')).toHaveAttribute('aria-expanded', 'false')
    await expect(head(page, 'openrouter')).toHaveAttribute('aria-expanded', 'true')
    expect(errors.seen).toEqual([])
  })

  test('a provider added before per-model switches offers "Choose models" and converts', async ({ page }) => {
    const { errors } = await open(page)
    await expand(page, 'corp')
    await expect(page.getByTestId('prov-banner-convert-corp')).toContainText('Added before per-model switches')
    await expect(sw(page, 'Switch off corp/corp-chat-large')).toBeDisabled()
    await page.getByTestId('prov-banner-convert-corp').getByRole('button', { name: 'Choose models' }).click()
    await expect(toast(page, 'converted')).toBeVisible()
    await expect(page.getByTestId('prov-banner-convert-corp')).toHaveCount(0)
    await expect(sw(page, 'Switch off corp/corp-chat-large')).toBeEnabled()
    await expect(head(page, 'corp')).toContainText('2 of 2 on')
    expect(errors.seen).toEqual([])
  })

  test('a managed provider is read-only: no switch can be flipped', async ({ page }) => {
    const { errors } = await open(page)
    await expand(page, 'hermes.coder')
    await expect(page.getByTestId('prov-banner-managed-hermes.coder')).toContainText('Read-only')
    const switches = page.getByTestId('prov-section-hermes.coder').getByRole('switch')
    expect(await switches.count()).toBeGreaterThan(0)
    for (let i = 0; i < (await switches.count()); i += 1) await expect(switches.nth(i)).toBeDisabled()
    await expect(page.getByTestId('prov-detail-provider')).toContainText('Managed providers cannot be edited')
    expect(errors.seen).toEqual([])
  })

  test('picking a model opens its details; editing a price does not carry over to the next model', async ({ page }) => {
    const { errors } = await open(page)
    await expand(page, 'minimax')
    await page.getByRole('button', { name: 'Show details of minimax/MiniMax-M3', exact: true }).click()
    const detail = page.getByTestId('prov-detail-model')
    await expect(detail).toContainText('minimax/MiniMax-M3')
    await detail.getByRole('button', { name: 'Edit' }).click()
    const priceIn = detail.getByLabel(/Price in/)
    await priceIn.fill('9.99')
    await expect(page.getByTestId('prov-edit')).toBeVisible()
    await page.getByRole('button', { name: 'Show details of minimax/MiniMax-M2.5', exact: true }).click()
    await expect(page.getByTestId('prov-call-name')).toHaveText('minimax/MiniMax-M2.5')
    await expect(page.getByTestId('prov-edit')).toHaveCount(0)
    // Back on the first model the editor is closed and the price was never saved.
    await page.getByRole('button', { name: 'Show details of minimax/MiniMax-M3', exact: true }).click()
    await expect(page.getByTestId('prov-edit')).toHaveCount(0)
    await expect(page.getByTestId('prov-detail-model')).not.toContainText('9.99')
    expect(errors.seen).toEqual([])
  })

  test('a failed switch shows the mapped message and the switch goes back', async ({ page }) => {
    const { ctl, errors } = await open(page, HTTP_NOISE)
    await expand(page, 'minimax')
    ctl.failPatch = { status: 500, message: 'boom' }
    const off = sw(page, 'Switch on minimax/MiniMax-M2.5')
    await off.click()
    await expect(toast(page, 'Could not change the model. Try again.')).toBeVisible()
    await expect(sw(page, 'Switch on minimax/MiniMax-M2.5')).toHaveAttribute('aria-checked', 'false')
    await expect(head(page, 'minimax')).toContainText('2 of 7 on')
    expect(errors.seen).toEqual([])
  })

  test('a refused switch on a managed provider names the reason', async ({ page }) => {
    const { ctl, errors } = await open(page, HTTP_NOISE)
    await expand(page, 'minimax')
    ctl.failPatch = { status: 409, code: 'upstream.protected', message: 'managed' }
    await sw(page, 'Switch on minimax/MiniMax-M2.5').click()
    await expect(toast(page, 'managed by an agent and cannot be changed here')).toBeVisible()
    expect(errors.seen).toEqual([])
  })

  test('a failed Remove shows an error and the provider stays', async ({ page }) => {
    const { ctl, errors } = await open(page, HTTP_NOISE)
    await head(page, 'openai').click()
    ctl.failDelete = { status: 500, message: 'disk full' }
    const detail = page.getByTestId('prov-detail-provider')
    await detail.getByRole('button', { name: 'Remove' }).click()
    await page.getByRole('button', { name: 'Remove provider' }).click()
    await expect(toast(page, 'Could not remove openai. Try again.')).toBeVisible()
    await expect(head(page, 'openai')).toBeVisible()
    expect(errors.seen).toEqual([])
  })

  test('Remove deletes the provider and selects another', async ({ page }) => {
    const { ctl, errors } = await open(page)
    await head(page, 'openai').click()
    await page.getByTestId('prov-detail-provider').getByRole('button', { name: 'Remove' }).click()
    await page.getByRole('button', { name: 'Remove provider' }).click()
    await expect(toast(page, 'openai removed')).toBeVisible()
    await expect(head(page, 'openai')).toHaveCount(0)
    expect(ctl.callsTo('DELETE', /\/api\/upstreams\/openai$/)).toHaveLength(1)
    expect(errors.seen).toEqual([])
  })

  test('"Add key" on a provider with no key stores it under the provider\'s own variable', async ({ page }) => {
    const { ctl, errors } = await open(page)
    await expand(page, 'openai')
    await page.getByTestId('prov-banner-key-openai').getByRole('button', { name: 'Add key' }).click()
    const editor = page.getByTestId('prov-key-editor')
    await expect(editor).toBeVisible()
    await editor.locator('input[type=password]').fill('sk-test-0123456789')
    await editor.getByRole('button', { name: 'Save key' }).click()
    await expect(toast(page, 'Key saved for openai')).toBeVisible()
    const saves = ctl.callsTo('POST', /\/api\/providers\/openai\/credentials$/)
    expect(saves).toHaveLength(1)
    expect(saves[0].body).toEqual({ key: 'OPENAI_API_KEY', value: 'sk-test-0123456789' })
    expect(errors.seen).toEqual([])
  })
})

test.describe('Add provider', () => {
  test('tile, key, a failed then a successful check, two models, add: the new provider is selected', async ({ page }) => {
    const { ctl, errors } = await open(page, HTTP_NOISE)
    ctl.probes = ['denied', 'ok']
    const modal = await addModal(page)
    await modal.getByRole('button', { name: /^DeepSeek/ }).click()
    await expect(modal.getByLabel('Name')).toHaveValue('deepseek')
    await modal.getByLabel('API key').fill('sk-live-abcdef123456')

    await modal.getByRole('button', { name: 'Check connection' }).click()
    await expect(modal.getByTestId('addprov-result')).toContainText('The provider rejected the key')
    await expect(modal.getByTestId('addprov-result')).toContainText('Nothing was saved')
    await expect(page.getByRole('button', { name: /^Add provider/ })).toBeDisabled()

    await modal.getByRole('button', { name: 'Check connection' }).click()
    await expect(modal.getByTestId('addprov-result')).toContainText('Reachable')
    await expect(modal.getByTestId('addprov-result')).toContainText('2 models offered')
    await modal.getByRole('checkbox', { name: 'qwen/qwen3-coder' }).check()
    await modal.getByRole('checkbox', { name: 'openai/text-embedding-3-small' }).check()
    await page.getByRole('button', { name: 'Add provider · 2 on' }).click()

    await expect(page.getByTestId('add-provider-modal')).toHaveCount(0)
    await expect(toast(page, 'deepseek added · 2 models on')).toBeVisible()
    await expect(head(page, 'deepseek')).toHaveClass(/sel/)
    await expect(head(page, 'deepseek')).toContainText('2 of 2 on')
    await expect(page.getByTestId('prov-detail-provider')).toContainText('deepseek')

    const created = ctl.callsTo('POST', /^\/api\/upstreams$/)
    expect(created).toHaveLength(1)
    expect(created[0].body.name).toBe('deepseek')
    expect(created[0].body.catalog_id).toBe('deepseek')
    expect(created[0].body.auth_value_env).toBe('DEEPSEEK_API_KEY')
    expect(created[0].body.models.map((m: any) => [m.id, m.enabled])).toEqual([
      ['qwen/qwen3-coder', true],
      ['openai/text-embedding-3-small', true],
    ])
    const creds = ctl.callsTo('POST', /\/credentials$/)
    expect(creds).toHaveLength(1)
    expect(creds[0].path).toBe('/api/providers/deepseek/credentials')
    expect(creds[0].body).toEqual({ key: 'DEEPSEEK_API_KEY', value: 'sk-live-abcdef123456' })
    // The key is only ever in the two request bodies: never a URL, never page text.
    expect(ctl.calls.filter((c) => c.path.includes('sk-live'))).toEqual([])
    expect(await page.evaluate(() => document.body.innerText.includes('sk-live'))).toBe(false)
    expect(errors.seen).toEqual([])
  })

  test('a failed key save keeps the dialog open with the server message, and retry works', async ({ page }) => {
    const { ctl, errors } = await open(page, HTTP_NOISE)
    const modal = await addModal(page)
    await modal.getByRole('button', { name: /^DeepSeek/ }).click()
    await modal.getByLabel('API key').fill('sk-live-abcdef123456')
    await modal.getByRole('button', { name: 'Check connection' }).click()
    await expect(modal.getByTestId('addprov-result')).toContainText('Reachable')
    await modal.getByRole('checkbox', { name: 'qwen/qwen3-coder' }).check()

    ctl.failCredentials = { status: 400, code: 'secret.invalid', message: 'the store refused this key' }
    await page.getByRole('button', { name: 'Add provider · 1 on' }).click()
    await expect(modal).toBeVisible()
    await expect(modal).toContainText('the store refused this key')
    await expect(page.getByText('Provider added, but the key was not saved.')).toBeVisible()
    // The provider exists even though the dialog is still open.
    expect(ctl.callsTo('POST', /^\/api\/upstreams$/)).toHaveLength(1)

    ctl.failCredentials = null
    await page.getByRole('button', { name: 'Try saving the key again' }).click()
    await expect(page.getByTestId('add-provider-modal')).toHaveCount(0)
    await expect(head(page, 'deepseek')).toHaveClass(/sel/)
    expect(ctl.callsTo('POST', /\/credentials$/)).toHaveLength(2)
    expect(ctl.callsTo('POST', /^\/api\/upstreams$/)).toHaveLength(1)
    expect(errors.seen).toEqual([])
  })

  test('the dialog cannot be dismissed while the save is in flight', async ({ page }) => {
    const { ctl, errors } = await open(page)
    const modal = await addModal(page)
    await modal.getByRole('button', { name: /^DeepSeek/ }).click()
    await modal.getByLabel('API key').fill('sk-live-abcdef123456')
    await modal.getByRole('button', { name: 'Check connection' }).click()
    await expect(modal.getByTestId('addprov-result')).toContainText('Reachable')
    await modal.getByRole('checkbox', { name: 'qwen/qwen3-coder' }).check()

    ctl.holdCreate = true
    await page.getByRole('button', { name: 'Add provider · 1 on' }).click()
    await expect(page.getByRole('button', { name: 'Adding…' })).toBeDisabled()
    await page.keyboard.press('Escape')
    await page.mouse.click(5, 5)
    await expect(page.getByRole('button', { name: 'Close' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await expect(modal).toBeVisible()
    ctl.release()
    await expect(page.getByTestId('add-provider-modal')).toHaveCount(0)
    await expect(head(page, 'deepseek')).toHaveClass(/sel/)
    expect(errors.seen).toEqual([])
  })

  test('Esc closes an untouched dialog; a dirty one asks first', async ({ page }) => {
    const { errors } = await open(page)
    const modal = await addModal(page)
    await page.keyboard.press('Escape')
    await expect(modal).toHaveCount(0)
    const again = await addModal(page)
    await again.getByRole('button', { name: /^DeepSeek/ }).click()
    await page.keyboard.press('Escape')
    await expect(page.getByText('Discard unsaved changes?').first()).toBeVisible()
    expect(errors.seen).toEqual([])
  })
})

test.describe('Old addresses and the Endpoints tab', () => {
  test('#models/upstream lands on Providers', async ({ page }) => {
    const { errors } = await open(page, [], '#models/upstream')
    await expect.poll(() => page.evaluate(() => window.location.hash)).toBe('#models/providers')
    await expect(page.getByTestId('models-tab-providers')).toHaveAttribute('aria-selected', 'true')
    expect(errors.seen).toEqual([])
  })

  test('#slots/endpoints?focus=upstream lands on Providers', async ({ page }) => {
    const { errors } = await open(page, [], '#slots/endpoints?focus=upstream')
    await expect.poll(() => page.evaluate(() => window.location.hash)).toBe('#models/providers')
    expect(errors.seen).toEqual([])
  })

  test('Slots → Endpoints keeps the local endpoints and links to Providers', async ({ page }) => {
    const errors = watchErrors(page)
    await installProviders(page)
    await page.goto('/#slots/endpoints')
    const note = page.getByTestId('endpoints-providers-note')
    await expect(note).toContainText('Remote providers moved to Models → Providers')
    await expect(page.getByText('Upstream providers')).toHaveCount(0)
    await note.getByRole('link', { name: 'Open Providers' }).click()
    await expect.poll(() => page.evaluate(() => window.location.hash)).toBe('#models/providers')
    await expect(page.getByTestId('providers-tab')).toBeVisible()
    expect(errors.seen).toEqual([])
  })
})

test.describe('Keyboard', () => {
  test('Tab from the search box reaches the first provider in eleven stops, Enter expands it, Space flips a switch, Esc closes the modal', async ({ page }) => {
    const { ctl, errors } = await open(page)
    // From the toolbar search box, Tab walks the nine filter chips (Show: All, On, Available; Type:
    // chat, embed, speech, transcribe, rerank, image) and "clear" (always present: Show always has
    // one chip selected); the eleventh stop is the first provider header (the first one is corp).
    await page.getByPlaceholder('Search models across providers').focus()
    for (let stop = 1; stop <= 9; stop += 1) {
      await page.keyboard.press('Tab')
      await expect(page.locator(':focus')).toHaveClass(/mdl-chip/)
    }
    await page.keyboard.press('Tab')
    await expect(page.locator(':focus')).toHaveClass(/mdl-clear/)
    await page.keyboard.press('Tab')
    await expect(page.locator(':focus')).toHaveAttribute('data-testid', 'prov-head-corp')
    await page.keyboard.press('Enter')
    await expect(head(page, 'corp')).toHaveAttribute('aria-expanded', 'true')

    // Move on to MiniMax with the keyboard only: focus its header, open it, step onto the first switch.
    await head(page, 'minimax').focus()
    await page.keyboard.press('Enter')
    await expect(head(page, 'minimax')).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByTestId('prov-section-minimax').getByRole('switch').first()).toBeVisible()
    await page.keyboard.press('Tab')
    const focused = page.locator(':focus')
    await expect(focused).toHaveAttribute('role', 'switch')
    const was = await focused.getAttribute('aria-checked')
    await page.keyboard.press('Space')
    await expect.poll(() => ctl.callsTo('PATCH', /\/models\//).length).toBe(1)
    await expect(page.locator(':focus')).toHaveAttribute('aria-checked', was === 'true' ? 'false' : 'true')

    await addModal(page)
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('add-provider-modal')).toHaveCount(0)
    expect(errors.seen).toEqual([])
  })
})

test.describe('A provider whose model list is unknown (added without a key)', () => {
  const keyless = (page: Page, allow: RegExp[] = []) => open(page, allow, '#models/providers', { keyless: true })

  test('marks no model gone, shows no stale note, and keeps the key banner', async ({ page }) => {
    const { errors } = await keyless(page)
    await expand(page, 'ct150-check')
    const sec = page.getByTestId('prov-section-ct150-check')
    await expect(sec.locator('.prov-row')).toHaveCount(4)
    await expect(sec.locator('.prov-flag')).toHaveCount(0)
    await expect(sec).not.toContainText('gone')
    await expect(sec).not.toContainText('could not be refreshed')
    await expect(sec).not.toContainText('not loaded yet')
    await expect(page.getByTestId('prov-banner-key-ct150-check')).toContainText('No key stored')
    await page.getByRole('button', { name: 'Show details of ct150-check/vendor/alpha', exact: true }).click()
    await expect(page.getByTestId('prov-detail-model')).toContainText('Offered by provider')
    await expect(page.getByTestId('prov-detail-model')).toContainText('unknown')
    await expect(page.getByTestId('prov-detail-model')).not.toContainText('no longer listed')
    expect(errors.seen).toEqual([])
  })

  test('the header shows the provider name after the display name; a default-named provider shows one name', async ({ page }) => {
    const { errors } = await keyless(page)
    await expect(head(page, 'ct150-check').locator('.prov-name')).toHaveText('OpenRouter')
    await expect(head(page, 'ct150-check').locator('.prov-pname')).toHaveText('ct150-check')
    await expect(head(page, 'ct150-check')).toContainText('openrouter.ai')
    await expect(head(page, 'openrouter').locator('.prov-name')).toHaveText('OpenRouter')
    await expect(head(page, 'openrouter').locator('.prov-pname')).toHaveCount(0)
    await head(page, 'ct150-check').click()
    await expect(page.getByTestId('prov-detail-provider')).toContainText('ct150-check · openrouter.ai')
    expect(errors.seen).toEqual([])
  })

  test('Test connection and Refresh model list ask for a key and call nothing', async ({ page }) => {
    const { ctl, errors } = await keyless(page)
    await head(page, 'ct150-check').click()
    const panel = page.getByTestId('provider-detail')
    const testBtn = panel.getByRole('button', { name: 'Test connection' })
    await expect(testBtn).toBeEnabled()
    await testBtn.click()
    await expect(page.getByTestId('prov-test-line')).toHaveText('No key stored for this provider. Add a key, then test again.')
    await panel.getByRole('button', { name: 'Refresh model list' }).click()
    await expect(toast(page, 'Add a key for OpenRouter to load its model list.')).toBeVisible()
    expect(ctl.callsTo('POST', /\/ct150-check\/(test|refresh)$/)).toEqual([])
    expect(errors.seen).toEqual([])
  })
})

test.describe('Phone width', () => {
  test.use({ viewport: { width: 400, height: 800 } })

  test('the tab does not scroll sideways and rows wrap', async ({ page }) => {
    const { errors } = await open(page)
    await expand(page, 'openrouter')
    await page.getByRole('button', { name: 'Show details of openrouter/qwen/qwen3-coder', exact: true }).click()
    await expect(page.getByTestId('prov-detail-model')).toBeVisible()
    await noHorizontalScroll(page, page.getByTestId('providers-tab'))
    const rows = page.getByTestId('prov-section-openrouter').locator('.prov-row')
    const n = await rows.count()
    for (let i = 0; i < Math.min(n, 12); i += 1) {
      const over = await rows.nth(i).evaluate((el) => el.scrollWidth - el.clientWidth)
      expect(over, `row ${i} overflows`).toBeLessThanOrEqual(0)
    }
    const headOver = await head(page, 'openrouter').evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(headOver).toBeLessThanOrEqual(0)
    expect(errors.seen).toEqual([])
  })

  test('the Add provider dialog fits the screen after a check', async ({ page }) => {
    const { errors } = await open(page)
    const modal = await addModal(page)
    await noHorizontalScroll(page, modal)
    await modal.getByRole('button', { name: /^DeepSeek/ }).click()
    await modal.getByLabel('API key').fill('sk-live-abcdef123456')
    await modal.getByRole('button', { name: 'Check connection' }).click()
    await expect(modal.getByTestId('addprov-result')).toContainText('Reachable')
    await noHorizontalScroll(page, modal)
    const shell = page.locator('.modal-shell')
    const box = await shell.boundingBox()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(400.5)
    expect(errors.seen).toEqual([])
  })

  test('a model row in the Add provider dialog is two lines: id on top, type, context and price under it', async ({ page }) => {
    const { errors } = await open(page)
    const modal = await addModal(page)
    await modal.getByRole('button', { name: /^DeepSeek/ }).click()
    await modal.getByLabel('API key').fill('sk-live-abcdef123456')
    await modal.getByRole('button', { name: 'Check connection' }).click()
    await expect(modal.getByTestId('addprov-result')).toContainText('Reachable')
    const row = modal.locator('.addprov-row').first()
    await expect(row).toBeVisible()
    const box = async (loc: Locator) => (await loc.boundingBox())!
    const r = await box(row)
    const mid = await box(row.locator('.addprov-mid'))
    const type = await box(row.locator('.addprov-type'))
    const nums = row.locator('.addprov-num')
    const ctx = await box(nums.nth(0))
    const price = await box(nums.nth(1))
    // Line 2 holds all three, on one line, left-aligned under the id.
    expect(Math.abs(ctx.y - type.y)).toBeLessThanOrEqual(type.height)
    expect(Math.abs(price.y - ctx.y)).toBeLessThanOrEqual(4)
    expect(type.y).toBeGreaterThanOrEqual(mid.y + mid.height - 1)
    expect(Math.abs(type.x - mid.x)).toBeLessThanOrEqual(2)
    expect(ctx.x).toBeGreaterThan(type.x + type.width - 1)
    expect(price.x).toBeGreaterThan(ctx.x)
    // So the row is no taller than the id line plus the control line plus its padding.
    expect(r.height).toBeLessThanOrEqual(mid.height + type.height + 20)
    await noHorizontalScroll(page, modal)
    expect(errors.seen).toEqual([])
  })

  test('a model row in the tab is two lines: id and flags, then type, context and price', async ({ page }) => {
    const { errors } = await open(page)
    await expand(page, 'minimax')
    const row = page.getByTestId('prov-section-minimax').locator('.prov-row').first()
    await expect(row).toBeVisible()
    await expect(row.locator('.prov-mid small')).toBeHidden()
    const mid = (await row.locator('.prov-mid').boundingBox())!
    const meta = (await row.locator('.prov-meta').boundingBox())!
    const r = (await row.boundingBox())!
    expect(meta.y).toBeGreaterThanOrEqual(mid.y + mid.height - 1)
    expect(r.height).toBeLessThanOrEqual(mid.height + meta.height + 24)
    expect(errors.seen).toEqual([])
  })
})

test.describe('A provider switched off from routing', () => {
  const off = (page: Page, allow: RegExp[] = []) => open(page, allow, '#models/providers', { switchedOff: ['minimax'] })

  test('shows the off chip and "switched off", and Switch on sends enabled: true', async ({ page }) => {
    const { ctl, errors } = await off(page)
    await expect(head(page, 'minimax')).toContainText('off')
    await expect(head(page, 'minimax')).toContainText('switched off')
    await expect(head(page, 'minimax')).not.toContainText('of 7 on')
    await expect(head(page, 'openrouter')).not.toContainText('switched off')
    await expand(page, 'minimax')
    const line = page.getByTestId('prov-switched-off-minimax')
    await expect(line).toContainText('This provider is switched off. Its models are not called until it is switched on.')
    // Its model switches stay usable: they are stored state.
    await expect(sw(page, 'Switch on minimax/MiniMax-M2.5')).toBeEnabled()
    await line.getByRole('button', { name: 'Switch on', exact: true }).click()
    await expect.poll(() => ctl.callsTo('PATCH', /^\/api\/upstreams\/minimax$/).map((c) => c.body)).toEqual([{ enabled: true }])
    await expect(page.getByTestId('prov-off-chip-minimax')).toHaveCount(0)
    await expect(page.getByTestId('prov-switched-off-minimax')).toHaveCount(0)
    await expect(head(page, 'minimax')).toContainText('of 7 on')
    expect(errors.seen).toEqual([])
  })

  test('a refused Switch on shows the error and the chip stays', async ({ page }) => {
    const { ctl, errors } = await off(page, HTTP_NOISE)
    ctl.failUpstreamPatch = { status: 500, code: 'system.error', message: 'the config file could not be written' }
    await expand(page, 'minimax')
    await page.getByTestId('prov-switched-off-minimax').getByRole('button', { name: 'Switch on', exact: true }).click()
    await expect(toast(page, 'the config file could not be written')).toBeVisible()
    await expect(page.getByTestId('prov-off-chip-minimax')).toBeVisible()
    await expect(head(page, 'minimax')).toContainText('switched off')
    expect(errors.seen).toEqual([])
  })

  test('the detail panel shows Routes requests off and switches it on', async ({ page }) => {
    const { ctl, errors } = await off(page)
    await expand(page, 'minimax')
    const routes = page.getByTestId('prov-routes-switch').getByRole('switch')
    await expect(routes).toHaveAttribute('aria-checked', 'false')
    await routes.click()
    await expect.poll(() => ctl.callsTo('PATCH', /^\/api\/upstreams\/minimax$/).map((c) => c.body)).toEqual([{ enabled: true }])
    await expect(routes).toHaveAttribute('aria-checked', 'true')
    expect(errors.seen).toEqual([])
  })
})

test.describe('Refresh that could not reach the provider', () => {
  test('the per-provider refresh warns instead of reporting success', async ({ page }) => {
    const { ctl, errors } = await open(page)
    ctl.refreshStale = true
    await expand(page, 'minimax')
    await page.getByTestId('provider-detail').getByRole('button', { name: 'Refresh model list' }).click()
    await expect(toast(page, /Could not reach .*; showing the last known list\./)).toBeVisible()
    await expect(toast(page, 'model list refreshed')).toHaveCount(0)
    expect(errors.seen).toEqual([])
  })

  test('the header refresh counts only clean answers and says how many could not be reached', async ({ page }) => {
    const { ctl, errors } = await open(page)
    ctl.refreshStale = true
    await page.getByTestId('mdl-check-providers').click()
    await expect(toast(page, /Refreshed 0 model lists · \d+ could not be reached/)).toBeVisible()
    expect(ctl.callsTo('POST', /\/refresh$/).length).toBeGreaterThan(0)
    expect(errors.seen).toEqual([])
  })
})
