/**
 * capability-catalog-pickers-v3 — Settings → Inference → Voice / Image-gen
 * model pickers (#1454).
 *
 * GET /api/capabilities ships `{backends, catalogs, selections}` where
 * `catalogs.voice.stt`, `catalogs.voice.tts`, and `catalogs.img.img` are BARE
 * ARRAYS of picker rows (each row carries `id` + a `backends` breakdown) —
 * see mockFixtures.ts buildCapabilities() for the canonical shape. The pages
 * used to index `.items`/`.models` off those arrays, which don't exist on an
 * Array, so `catalogItems` was always `[]`: the `<select>` branch never
 * rendered and the "no installed … models" hint lied even with models
 * installed.
 *
 * These specs pin the real bare-array shape rendering into a populated
 * <select> (not the free-text fallback) and the false empty-state hint
 * being absent.
 */
import { test, expect, json } from '../fixtures/apiMock'
import type { Locator } from '@playwright/test'

// #1683: FieldInfoIcon's description now portals to document.body (so
// overflow:hidden panels can't clip it), so it's no longer a DOM descendant
// of its row — `row.textContent` (what `toContainText` reads) doesn't see it
// anymore. Look the popup up via the Info button's aria-describedby instead.
async function fieldInfoPopup(row: Locator) {
  const btn = row.getByRole('button', { name: 'Info' })
  const id = await btn.getAttribute('aria-describedby')
  return row.page().locator(`[id="${id}"]`)
}

function capabilityRow(device: string, provider: string, model: string | null, slot: string, status: string) {
  return { device, backend: device, provider, model, enabled: !!model, slot, status }
}

// Scopes a `Model` row (`.s-row` with `.k span` text "Model") to the panel
// whose own title (`.s-panel`'s `.k span`) matches `title` — the AI
// Capabilities page renders four Model rows (TTS, STT, Embed, Rerank;
// Image deliberately has none — ComfyUI workflows pick their own
// checkpoint), so a bare index into a page-wide `.s-row` collection is no
// longer stable; scope by panel instead. Each test builds its own bound
// `panelModelRow` from its `page` fixture, mirroring how the neighbouring
// panel locators (e.g. `ttsPanel` in voice-page-provider-copy-v3) are
// built fresh per test.
function makePanelModelRow(page: import('@playwright/test').Page) {
  return (title: RegExp) =>
    page.locator('.s-panel')
      .filter({ has: page.locator('.k span', { hasText: title }) })
      .locator('.s-row')
      .filter({ has: page.locator('.k span', { hasText: /^Model$/ }) })
}

// Mirrors the real /api/capabilities envelope (orchestrator.py get_state /
// catalogs_by_slot) — same shape mockFixtures.ts documents as ground truth.
// `embed`/`rerank` catalogs and selections nest under a top-level `embed`
// key (`catalogs.embed.embed` / `catalogs.embed.rerank`), exactly parallel
// to `catalogs.voice.stt`/`.tts` and `catalogs.img.img` — see
// mockFixtures.ts buildCapabilities().
const CAPS_MOCK = {
  backends: [
    { id: 'npu', label: 'NPU', short: 'NPU', provider: 'flm', multiplex: true },
    { id: 'cpu', label: 'CPU', short: 'CPU', provider: 'llamacpp', multiplex: false },
    { id: 'gpu-rocm', label: 'GPU · ROCm', short: 'ROCm', provider: 'llamacpp', multiplex: false },
  ],
  catalogs: {
    embed: {
      embed: [
        { id: 'nomic-embed-text-v1.5', capabilities: ['embed'], size_gb: 0.14, backends: [{ id: 'gpu-rocm', provider: 'llama-server', downloaded: true, pullable: true }] },
      ],
      rerank: [
        { id: 'bge-reranker-v2-m3', capabilities: ['rerank'], size_gb: 0.56, backends: [{ id: 'gpu-rocm', provider: 'llama-server', downloaded: true, pullable: true }] },
      ],
    },
    voice: {
      stt: [
        { id: 'Whisper-Large-v3-Turbo', capabilities: ['stt'], size_gb: 1.5, backends: [{ id: 'npu', provider: 'flm', downloaded: true, pullable: true }] },
        { id: 'gemma4-it:e2b', capabilities: ['stt'], size_gb: 2.0, backends: [{ id: 'npu', provider: 'flm', downloaded: true, pullable: true }] },
      ],
      tts: [
        { id: 'kokoro-v1', capabilities: ['tts'], size_gb: 0.3, backends: [{ id: 'cpu', provider: 'kokoro', downloaded: true, pullable: true }] },
        { id: 'qwen3-tts', capabilities: ['tts'], size_gb: 1.2, backends: [{ id: 'gpu-rocm', provider: 'sdcpp', downloaded: true, pullable: true }] },
      ],
    },
    img: {
      img: [
        { id: 'sd-turbo', capabilities: ['image'], size_gb: 2.1, backends: [{ id: 'gpu-rocm', provider: 'sdcpp', downloaded: true, pullable: true }] },
      ],
    },
  },
  selections: {
    embed: {
      embed: capabilityRow('gpu-rocm', 'llama-server', 'nomic-embed-text-v1.5', 'embed', 'serving'),
      rerank: capabilityRow('', '', null, 'rerank', 'offline'),
    },
    voice: {
      stt: capabilityRow('npu', 'flm', 'Whisper-Large-v3-Turbo', 'stt', 'serving'),
      tts: capabilityRow('cpu', 'kokoro', 'kokoro-v1', 'tts', 'serving'),
    },
    img: {
      img: capabilityRow('', '', null, 'img', 'offline'),
    },
  },
}

test.describe('Capability catalog pickers (#1454)', () => {
  test('Voice page: STT/TTS Model pickers render as <select> populated from the bare-array catalog', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('AI Capabilities')
    const panelModelRow = makePanelModelRow(page)

    // STT row
    const sttRow = panelModelRow(/^STT$/)
    const sttSelect = sttRow.locator('select')
    await expect(sttSelect).toBeVisible()
    await expect(sttSelect.locator('option', { hasText: 'Whisper-Large-v3-Turbo' })).toHaveCount(1)
    await expect(sttSelect.locator('option', { hasText: 'gemma4-it:e2b' })).toHaveCount(1)
    await expect(sttRow).not.toContainText('no installed STT models')

    // TTS row
    const ttsRow = panelModelRow(/^TTS$/)
    const ttsSelect = ttsRow.locator('select')
    await expect(ttsSelect).toBeVisible()
    await expect(ttsSelect.locator('option', { hasText: 'kokoro-v1' })).toHaveCount(1)
    await expect(ttsSelect.locator('option', { hasText: 'qwen3-tts' })).toHaveCount(1)
    await expect(ttsRow).not.toContainText('no installed TTS models')
  })

  test('Image-gen panel has no Model row — checkpoint choice belongs to ComfyUI workflows', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/imagegen')
    await expect(page.locator('.settings-content h2').first()).toHaveText('AI Capabilities')

    const panel = page.locator('.s-panel').filter({ has: page.locator('.k span', { hasText: /^Image generation$/ }) })
    // Engine select must still render before we assert the Model row away
    // (guards against a blank-panel false pass).
    await expect(panel.locator('select option', { hasText: 'comfyui' })).toHaveCount(1)
    await expect(makePanelModelRow(page)(/^Image generation$/)).toHaveCount(0)
    await expect(panel.locator('a[href="#slots/image"]')).toHaveCount(1)
  })

  test('Voice page: empty catalog still shows the honest "no installed" hint (not a false negative)', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, {
      backends: [],
      catalogs: { voice: { stt: [], tts: [] }, img: { img: [] } },
      selections: {
        voice: { stt: capabilityRow('', '', null, 'stt', 'offline'), tts: capabilityRow('', '', null, 'tts', 'offline') },
        img: { img: capabilityRow('', '', null, 'img', 'offline') },
      },
    }))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('AI Capabilities')
    const panelModelRow = makePanelModelRow(page)

    const sttRow = panelModelRow(/^STT$/)
    const ttsRow = panelModelRow(/^TTS$/)
    await expect(await fieldInfoPopup(sttRow)).toContainText('no installed STT models')
    await expect(await fieldInfoPopup(ttsRow)).toContainText('no installed TTS models')
    // Genuinely-empty catalog falls back to the free-text input, not a <select>.
    await expect(sttRow.locator('select')).toHaveCount(0)
  })

  test('embed panel lists the embed catalog', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/capabilities')
    await expect(page.locator('.settings-content h2').first()).toHaveText('AI Capabilities')
    const row = makePanelModelRow(page)(/^Embeddings$/)
    await expect(row.locator('select option', { hasText: 'nomic-embed-text-v1.5' })).toHaveCount(1)
  })

  test('rerank panel lists the rerank catalog and links memory settings', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/capabilities')
    const panel = page.locator('.s-panel').filter({ has: page.locator('.k span', { hasText: /^Reranking$/ }) })
    await expect(panel.locator('select option', { hasText: 'bge-reranker-v2-m3' })).toHaveCount(1)
    await expect(panel.locator('a[href="#settings/memory"]')).toHaveCount(1)
  })

  test('toggling Enabled marks the panel dirty and lights its Save button', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/capabilities')
    const panel = page.locator('.s-panel').filter({ has: page.locator('.k span', { hasText: /^Embeddings$/ }) })
    const save = panel.getByRole('button', { name: 'Save embeddings' })
    await expect(save).toBeDisabled()
    await panel.getByRole('checkbox').click()
    await expect(save).toBeEnabled()
    await expect(panel.getByRole('button', { name: 'Reset' })).toBeVisible()
  })

  test('failed /api/capabilities probe shows the per-panel note with Retry, then recovers', async ({ page }) => {
    // #1498/#1527 escape hatch: opt /api/capabilities out of forced-mock
    // substitution so our 500 fulfil below actually reaches react-query —
    // without this, mockFetch papers any non-ok networkFirst response over
    // with the baked payload and capsQuery.isError is unreachable (the
    // test-infra gap flagged in the #1467 note at the bottom of this file).
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockPassthrough: string[] }).__hal0MockPassthrough = ['/api/capabilities']
    })
    let fail = true
    await page.route('**/api/capabilities', (route) =>
      fail ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }) : json(route, CAPS_MOCK))
    await page.goto('/#settings/capabilities')

    const panel = page.locator('.s-panel').filter({ has: page.locator('.k span', { hasText: /^Embeddings$/ }) })
    await expect(panel.getByText('capability probe failed — Save disabled')).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Save embeddings' })).toBeDisabled()

    fail = false
    await panel.getByRole('button', { name: 'Retry' }).click()
    await expect(panel.getByText('capability probe failed — Save disabled')).toHaveCount(0)
    await expect(panel.locator('select option', { hasText: 'nomic-embed-text-v1.5' })).toHaveCount(1)
  })
})

// #1467 item 4: neither page had an isError branch for GET /api/capabilities
// — a failed probe rendered blank/unchecked controls as if nothing were
// configured, and Save stayed clickable (gated only on `loading`, i.e.
// isLoading, which is false once the query has settled to an error). Fixed
// by mirroring AdvancedPage.jsx's isError pattern: an error banner + Save
// suppressed via `errored` alongside the existing dirty/loading/pending
// gates (VoicePage.jsx Save STT / Save TTS, ImageGenPage.jsx Save Image-gen).
//
// The isError branch WAS untestable here (mockFetch's FORCED+networkFirst
// branch substituted the baked payload for any non-ok response before it
// reached react-query). The #1498/#1527 `__hal0MockPassthrough` escape
// hatch closed that gap — the "failed /api/capabilities probe" spec above
// opts the endpoint out of substitution and drives the 500 → per-panel
// note → Retry → recovery path end to end.
