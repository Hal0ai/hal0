/**
 * slots-v2.spec.ts — dash v2 /slots view (slice #170).
 *
 * Covers the new grouped-section layout, EmptySlotCard skip-path,
 * banner #19 wiring, NPU trio variants (NpuBlock default · NpuReactor
 * tweak), Create modal pre-fill, hotkey `N`, and /slots/:name → Edit
 * drawer routing.
 *
 * Existing specs that still apply:
 *   - slot-lifecycle.spec.ts — state walk + restart + unload + delete
 *   - models-slots-refactor.spec.ts — inline swap popover + edit + flags
 *   - lemonade-voice-chip.spec.ts — [CPU] chip on kokoro slots
 */
import { test, expect, json } from '../fixtures/apiMock'

test('skip-path: zero slots renders 6 EmptySlotCards + banner #19', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = []

  await page.goto('/slots')

  // Six EmptySlotCards rendered.
  const empties = page.getByTestId('empty-slot-card')
  await expect(empties).toHaveCount(6)

  // skip-path banner is in the slots-scoped BannerStack.
  await expect(page.locator('[data-banner-id="skip-path"]')).toBeVisible()
  await expect(page.locator('[data-banner-id="skip-path"]')).toContainText(/Six seeded slots/)

  // Each card has a Configure button. Click the first → Create modal
  // pre-filled with that seed's name (`primary` in the catalog).
  await empties.first().getByRole('button', { name: /Configure/ }).click()
  await expect(page.locator('#create-slot-name')).toHaveValue('primary')
})

test('hotkey N opens Create slot modal', async ({ page, mockState, cleanState }) => {
  // Seed at least one slot so the skip-path is NOT triggered.
  mockState.status.slots.push({
    name: 'primary', kind: 'llama-server', backend: 'vulkan', model: 'phi3', port: 8081, status: 'offline',
  })

  await page.goto('/slots')
  await expect(page.locator('.slot[data-slot-name="primary"]')).toBeVisible()

  // Modal closed initially.
  await expect(page.locator('#create-slot-name')).toHaveCount(0)

  // Pressing N opens it.
  await page.keyboard.press('n')
  await expect(page.locator('#create-slot-name')).toBeVisible()
})

test('/slots/:name opens Edit drawer for that slot', async ({ page, mockState, cleanState }) => {
  mockState.status.slots.push({
    name: 'primary',
    kind: 'llama-server',
    backend: 'vulkan',
    model: 'phi3',
    port: 8081,
    status: 'ready',
    context_size: 8192,
  })

  await page.goto('/slots/primary')

  const drawer = page.locator('[aria-labelledby="edit-slot-title"]')
  await expect(drawer).toBeVisible()
  await expect(page.locator('#edit-slot-title')).toContainText(/Edit primary/)

  // Closing the drawer routes back to /slots.
  await drawer.locator('.modal-close').click()
  await expect(page).toHaveURL(/\/slots$/)
})

test('grouped sections render by slot type', async ({ page, mockState, cleanState }) => {
  mockState.status.slots = [
    { name: 'primary', kind: 'llama-server', backend: 'vulkan', model: 'phi3', port: 8081, status: 'ready' },
    { name: 'embed',   kind: 'embedding',    backend: 'cpu',    model: 'bge',  port: 8082, status: 'ready' },
    { name: 'stt',     kind: 'transcription', backend: 'cpu',   model: 'wsp',  port: 8083, status: 'ready' },
    { name: 'tts',     kind: 'tts',          backend: 'cpu',    model: 'kkr',  port: 8084, status: 'ready', provider: 'kokoro' },
    { name: 'sd',      kind: 'image',        backend: 'rocm',   model: 'sdxl', port: 8085, status: 'ready' },
  ]

  await page.goto('/slots')

  // Each section heading should appear.
  await expect(page.locator('.sec h2', { hasText: /^Chat/ })).toBeVisible()
  await expect(page.locator('.sec h2', { hasText: /^Embed/ })).toBeVisible()
  await expect(page.locator('.sec h2', { hasText: /^Voice/ })).toBeVisible()
  await expect(page.locator('.sec h2', { hasText: /^Image/ })).toBeVisible()
})

test('NpuBlock renders by default, NpuReactor renders when tweak set', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = [
    {
      name: 'agent',
      kind: 'flm',
      type: 'llm',
      backend: 'flm',
      device: 'npu',
      model: 'gemma3:1b',
      port: 8092,
      status: 'ready',
      coresident_group: 'npu-flm-trio',
      group: 'npu',
    },
    {
      name: 'stt-npu',
      kind: 'transcription',
      backend: 'flm',
      device: 'npu',
      model: 'whisper-int8',
      status: 'ready',
      coresident_group: 'npu-flm-trio',
      group: 'npu',
    },
    {
      name: 'embed-npu',
      kind: 'embedding',
      backend: 'flm',
      device: 'npu',
      model: 'bge-npu',
      status: 'ready',
      coresident_group: 'npu-flm-trio',
      group: 'npu',
    },
  ]

  await page.goto('/slots')

  // Default → NpuBlock.
  await expect(page.getByTestId('npu-block')).toBeVisible()
  await expect(page.getByTestId('npu-reactor')).toHaveCount(0)

  // Flip the tweak to reactor.
  await page.evaluate(async () => {
    const m = await import('/src/stores/tweaks.js')
    m.useTweaksStore().npuVariant = 'reactor'
  })

  await expect(page.getByTestId('npu-reactor')).toBeVisible()
  await expect(page.getByTestId('npu-block')).toHaveCount(0)
})

test('per-type metric strip — embed shows req/min, p50, dim, mem', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = [
    { name: 'embed', kind: 'embedding', backend: 'cpu', model: 'bge', port: 8082, status: 'ready' },
  ]
  mockState.slotsMetrics = {
    embed: { rpm: 142, lat: 18, dim: 768, mem: 0.42 },
  }
  await page.route('**/api/slots/metrics', (route) => json(route, mockState.slotsMetrics))

  await page.goto('/slots')

  const card = page.locator('.slot[data-slot-name="embed"]')
  await expect(card).toBeVisible()
  // Metric labels match the per-type strip from slots.jsx::metricsRow.
  await expect(card.locator('.slot-met .l', { hasText: 'req/min' })).toBeVisible()
  await expect(card.locator('.slot-met .l', { hasText: 'p50' })).toBeVisible()
  await expect(card.locator('.slot-met .l', { hasText: 'dim' })).toBeVisible()
  await expect(card.locator('.slot-met .l', { hasText: 'mem' })).toBeVisible()
})

test('llm KV% shows "—" on GPU slot (Lemonade gap)', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = [
    { name: 'primary', kind: 'llama-server', backend: 'vulkan', model: 'phi3', port: 8081, status: 'ready', context_size: 8192 },
  ]
  // kv_cache_usage omitted on purpose — mimics the missing-gauge case.
  mockState.slotsMetrics = {
    primary: { tokens_per_sec: 24.7, ttft_seconds: 0.180, ctx: 4096 },
  }
  await page.route('**/api/slots/metrics', (route) => json(route, mockState.slotsMetrics))

  await page.goto('/slots')

  const card = page.locator('.slot[data-slot-name="primary"]')
  await expect(card).toBeVisible()
  // KV row shows '—' (dim) when the gauge is missing.
  const kvRow = card.locator('.slot-met', { has: page.locator('.l', { hasText: 'kv' }) })
  await expect(kvRow.locator('.v')).toHaveText(/—/)
})

test('SlotCard overflow menu exposes View logs / Set default / Delete', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = [
    { name: 'custom-vk', kind: 'llama-server', backend: 'vulkan', model: 'phi3', port: 8081, status: 'ready' },
  ]

  await page.goto('/slots')

  const card = page.locator('.slot[data-slot-name="custom-vk"]')
  await card.locator('button[aria-label="More slot actions"]').click()

  const menu = page.locator('.hal0-menu')
  await expect(menu).toBeVisible()
  await expect(menu.locator('[role="menuitem"]', { hasText: 'View slot logs' })).toBeVisible()
  await expect(menu.locator('[role="menuitem"]', { hasText: 'Set as default' })).toBeVisible()
  await expect(menu.locator('[role="menuitem"]', { hasText: 'Copy curl example' })).toBeVisible()
  await expect(menu.locator('[role="menuitem"]', { hasText: 'Delete slot' })).toBeVisible()
})

test('ErrorSlotCard banner renders when slotAction fails', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = [
    { name: 'custom-vk', kind: 'llama-server', backend: 'vulkan', model: 'phi3', port: 8081, status: 'offline' },
  ]

  // /load returns a 500 so the row picks up the persistent error.
  await page.route('**/api/slots/custom-vk/load', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { message: 'sha256 mismatch on shard 2' } }) }),
  )

  await page.goto('/slots')

  const card = page.locator('.slot[data-slot-name="custom-vk"]')
  await card.locator('button[title^="Start"], button[title^="Pick a model"]').click()

  await expect(card.getByTestId('error-slot-banner')).toBeVisible({ timeout: 5_000 })
  await expect(card.getByTestId('error-slot-banner')).toContainText(/load failed/)
})
