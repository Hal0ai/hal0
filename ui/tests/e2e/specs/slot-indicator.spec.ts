/**
 * slot-indicator — exercises the `slotIndicator` mapping helper exported
 * from ui/src/dash/slots.jsx onto window. Single source of truth for the
 * status-dot colour/tooltip; this spec pins the state → dot.cls table
 * so future tweaks can't silently regress.
 *
 * Contract (2026-05-27 user spec):
 *   error                  → red
 *   serving + fresh        → green pulse (GREEN ONLY during in-flight)
 *   serving + last>1h      → yellow (stuck-request guard)
 *   ready / lemo=loaded    → yellow (loaded, awaiting prompt)
 *   idle / lemo=idle       → grey (evicted, not in VRAM; hot-reload on next request)
 *   warming/starting/…     → amber pulse
 *   !enabled / disabled    → grey "off"
 *   offline                → grey
 *
 * The pre-2026-05-27 "recent (green) vs stale (yellow)" distinction
 * for READY slots is gone: GREEN now signals only active serving.
 * RECENTLY_LIVE_MS is repurposed as the hung-SERVING threshold.
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

  test('ready + last_used within 1h → stale (yellow, "ready")', async ({ page }) => {
    // Per 2026-05-27 spec: READY is "loaded and waiting" → yellow, not
    // green. Green is reserved for state=serving (in-flight only).
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      const slot = { state: 'ready', last_used_at: (now - 12 * 60 * 1000) / 1000, model: 'foo' }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('stale')
    expect(ind.label).toBe('ready')
    expect(ind.tooltip).toMatch(/Loaded — last used 12 min ago/)
  })

  test('ready + last_used >1h ago → stale (yellow)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      // 90 minutes ago
      const slot = { state: 'ready', last_used_at: (now - 90 * 60 * 1000) / 1000 }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('stale')
    expect(ind.tooltip).toMatch(/Loaded — last used 1h ago/)
  })

  test('ready + last_used null → stale (yellow), tooltip notes model in VRAM', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const slot = { state: 'ready', last_used_at: null }
      return (window as any).slotIndicator(slot, Date.now())
    })
    expect(ind.cls).toBe('stale')
    expect(ind.tooltip).toMatch(/Loaded — model in VRAM/)
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

  test('idle state maps to offline (grey)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'idle', last_used_at: null })
    })
    expect(ind.cls).toBe('offline')
    expect(ind.label).toBe('idle')
  })

  test('RECENTLY_LIVE_MS exported and equals 1 hour', async ({ page }) => {
    const ms = await page.evaluate(() => (window as any).RECENTLY_LIVE_MS)
    expect(ms).toBe(RECENTLY_LIVE_MS)
  })

  // READY → yellow regardless of last_used_at (the 1h threshold no longer
  // gates a colour transition for READY; it gates the in-flight stuck
  // guard for SERVING only). Both boundaries below map to 'stale'.
  test('boundary: exactly 1h ago READY is stale (yellow)', async ({ page }) => {
    const cls = await page.evaluate(() => {
      const now = Date.now()
      const slot = { state: 'ready', last_used_at: (now - 60 * 60 * 1000) / 1000 }
      return (window as any).slotIndicator(slot, now).cls
    })
    expect(cls).toBe('stale')
  })

  test('boundary: 1h + 1s ago READY is still stale (yellow)', async ({ page }) => {
    const cls = await page.evaluate(() => {
      const now = Date.now()
      const slot = { state: 'ready', last_used_at: (now - 60 * 60 * 1000 - 1000) / 1000 }
      return (window as any).slotIndicator(slot, now).cls
    })
    expect(cls).toBe('stale')
  })

  test('serving + fresh last_used → serving (green pulse)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      const slot = {
        state: 'serving',
        last_used_at: (now - 5 * 1000) / 1000,
        model: 'qwen3.5-4b-q4kxl',
      }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('serving')
    expect(ind.label).toBe('serving')
    expect(ind.tooltip).toMatch(/Serving qwen3.5-4b-q4kxl/)
  })

  test('serving + last_used >1h ago → stale (stuck-request guard)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      const slot = {
        state: 'serving',
        last_used_at: (now - 90 * 60 * 1000) / 1000,
        model: 'qwen3.5-4b-q4kxl',
      }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('stale')
    expect(ind.label).toBe('stuck?')
    expect(ind.tooltip).toMatch(/may be stuck/)
  })

  test('!enabled → offline (grey "off")', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'ready', enabled: false })
    })
    expect(ind.cls).toBe('offline')
    expect(ind.label).toBe('off')
  })

  test('lemonade_state=loaded → stale (yellow)', async ({ page }) => {
    // Lemonade enrichment override: even if slot.state is offline,
    // lemo=loaded means the model is in VRAM → yellow.
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({
        state: 'offline',
        lemonade_state: 'loaded',
        model: 'foo',
      })
    })
    expect(ind.cls).toBe('stale')
    expect(ind.label).toBe('ready')
  })

  test('lemonade_state=idle → offline (grey, evicted, not in VRAM)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({
        state: 'offline',
        lemonade_state: 'idle',
        model: 'foo',
      })
    })
    expect(ind.cls).toBe('offline')
    expect(ind.label).toBe('idle')
    expect(ind.tooltip).toMatch(/hot-reload on next request/)
  })
})
