/**
 * slot-indicator — exercises the `slotIndicator` mapping helper exported
 * from ui/src/dash/slots.jsx onto window. Single source of truth for the
 * status-dot colour/tooltip; this spec pins the state → dot.cls table
 * so future tweaks can't silently regress (e.g. flipping idle to green
 * or dropping the 1h "recently live" threshold).
 */
import { test, expect } from '../fixtures/apiMock'

type Indicator = { cls: string; label: string; tooltip: string }

const RECENTLY_LIVE_MS = 60 * 60 * 1000

test.describe('slotIndicator helper', () => {
  test.beforeEach(async ({ page }) => {
    // Any page renders the dashboard bundle; #slots ensures slots.jsx
    // is loaded so window.slotIndicator is defined.
    await page.goto('/#slots')
    await page.waitForFunction(() => typeof (window as any).slotIndicator === 'function')
  })

  test('ready + last_used within 1h → recent (green)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      const slot = { state: 'ready', last_used_at: (now - 12 * 60 * 1000) / 1000, model: 'foo' }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('recent')
    expect(ind.label).toBe('ready')
    expect(ind.tooltip).toMatch(/Loaded, last used 12 min ago/)
  })

  test('ready + last_used >1h ago → stale (yellow)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      // 90 minutes ago
      const slot = { state: 'ready', last_used_at: (now - 90 * 60 * 1000) / 1000 }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('stale')
    expect(ind.tooltip).toMatch(/Loaded, idle \(1h ago\)/)
  })

  test('ready + last_used null → stale (yellow), tooltip notes no requests', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const slot = { state: 'ready', last_used_at: null }
      return (window as any).slotIndicator(slot, Date.now())
    })
    expect(ind.cls).toBe('stale')
    expect(ind.tooltip).toMatch(/no requests since hal0-api started/)
  })

  test('warming → warming (pulsing amber)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'warming', model: 'Qwen3.5-0.8B-GGUF' })
    })
    expect(ind.cls).toBe('warming')
    expect(ind.tooltip).toMatch(/Warming up Qwen3.5-0.8B-GGUF/)
  })

  test('starting + pulling + unloading also map to warming', async ({ page }) => {
    const out = await page.evaluate(() => {
      return ['starting', 'pulling', 'unloading'].map((state) =>
        (window as any).slotIndicator({ state }).cls,
      )
    })
    expect(out).toEqual(['warming', 'warming', 'warming'])
  })

  test('error → error (red), tooltip surfaces metadata.message', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({
        state: 'error',
        metadata: { message: 'model file missing' },
      })
    })
    expect(ind.cls).toBe('error')
    expect(ind.tooltip).toBe('Error: model file missing')
  })

  test('offline → offline (grey)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'offline' })
    })
    expect(ind.cls).toBe('offline')
    expect(ind.tooltip).toBe('Offline')
  })

  test('idle state maps to stale (yellow)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'idle', last_used_at: null })
    })
    expect(ind.cls).toBe('stale')
    expect(ind.label).toBe('idle')
  })

  test('RECENTLY_LIVE_MS exported and equals 1 hour', async ({ page }) => {
    const ms = await page.evaluate(() => (window as any).RECENTLY_LIVE_MS)
    expect(ms).toBe(RECENTLY_LIVE_MS)
  })

  test('boundary: exactly 1h ago is still recent (≤, not <)', async ({ page }) => {
    const cls = await page.evaluate(() => {
      const now = Date.now()
      const slot = { state: 'ready', last_used_at: (now - 60 * 60 * 1000) / 1000 }
      return (window as any).slotIndicator(slot, now).cls
    })
    expect(cls).toBe('recent')
  })

  test('boundary: 1h + 1s ago becomes stale', async ({ page }) => {
    const cls = await page.evaluate(() => {
      const now = Date.now()
      const slot = { state: 'ready', last_used_at: (now - 60 * 60 * 1000 - 1000) / 1000 }
      return (window as any).slotIndicator(slot, now).cls
    })
    expect(cls).toBe('stale')
  })
})
