/**
 * models-flm-tab-v3 — Models → FLM on the shared catalog shell: the same toolbar, sections,
 * row and detail card as the other tabs, the header's Check updates and Download FLM model,
 * and Remove with its inline confirmation.
 *
 * /api/slots/flm/models is not in the in-bundle mock layer, so it is routed here.
 */
import { test, expect } from '../fixtures/apiMock'

const CATALOG = {
  probe_ok: true,
  models: [
    { model: 'qwen3:4b', installed: true, capabilities: ['chat'], family: 'qwen3', size_bytes: 3_100_000_000, in_use_by: [{ role: 'chat', slot: 'npu' }] },
    { model: 'gemma3n:e4b', installed: true, capabilities: ['chat', 'stt'], family: 'gemma3n', size_bytes: 4_400_000_000 },
    { model: 'embed-gemma:300m', installed: false, capabilities: ['embed'], family: 'embed-gemma', size_bytes: 300_000_000 },
  ],
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/slots/flm/models', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CATALOG) }),
  )
})

test.describe('Models → FLM', () => {
  test('uses the same list and detail layout as the other tabs', async ({ page }) => {
    await page.goto('/#models/flm')
    const tab = page.getByTestId('flm-models-tab')
    await expect(tab).toHaveClass(/models-layout/)
    await expect(tab.locator('.mdl-toolbar .mdl-search')).toBeVisible()
    await expect(tab.locator('.mdl-row')).toHaveCount(3)
    // In use leads, then installed, then what can be downloaded.
    await expect(tab.getByTestId('mdl-section-inuse').locator('.mdl-row')).toContainText('qwen3:4b')
    await expect(tab.getByTestId('flm-state-qwen3:4b')).toHaveText('in use · npu')
    await expect(tab.getByTestId('mdl-section-available').getByTestId('flm-state-embed-gemma:300m')).toHaveText('not installed')
    // The detail card opens on the first installed model, with the Downloads pane under it.
    await expect(tab.getByTestId('flm-detail').locator('.mdl-detail-h .nm')).toHaveText('gemma3n:e4b')
    await expect(tab.locator('.models-sidebar .mdl-dl')).toBeVisible()
  })

  test('the header carries Check updates and Download FLM model', async ({ page }) => {
    await page.goto('/#models/flm')
    await expect(page.getByTestId('mdl-check-flm')).toBeVisible()
    await page.getByTestId('mdl-add').click()
    // The catalogue is closed: adding is choosing one that is not installed yet.
    await expect(page.getByTestId('mdl-filter-available')).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.mdl-row')).toHaveCount(1)
    await expect(page.locator('.mdl-row')).toContainText('embed-gemma:300m')
    await expect(page.getByTestId('flm-download-embed-gemma:300m')).toHaveText('Download · 300 MB')
  })

  test('Remove asks first, and a model in use cannot be removed', async ({ page }) => {
    let deleted: string | null = null
    await page.route('**/api/slots/flm/models/*', (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback()
      deleted = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '')
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tag: deleted, removed: true, path: null, bytes_freed: 4_400_000_000, registry_removed: true, warnings: [] }),
      })
    })
    await page.goto('/#models/flm')

    await page.getByTestId('flm-model-qwen3:4b').click()
    await expect(page.getByTestId('flm-remove-qwen3:4b')).toBeDisabled()
    await expect(page.getByTestId('flm-remove-why-qwen3:4b')).toContainText('chat on npu')

    await page.getByTestId('flm-model-gemma3n:e4b').click()
    await page.getByTestId('flm-remove-gemma3n:e4b').click()
    await expect(page.getByTestId('flm-remove-confirm-gemma3n:e4b')).toContainText('free 4.4 GB')
    expect(deleted).toBeNull()
    await page.getByTestId('flm-remove-yes-gemma3n:e4b').click()
    await expect.poll(() => deleted).toBe('gemma3n:e4b')
  })

  test('the sidebar links to the tab', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.sidebar').getByText('FLM', { exact: true }).click()
    await expect(page).toHaveURL(/#models\/flm/)
    await expect(page.getByTestId('flm-models-tab')).toBeVisible()
  })
})
