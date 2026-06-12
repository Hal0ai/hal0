/**
 * slot-indicator — exercises the `slotIndicator` mapping helper exported
 * from ui/src/dash/slots.jsx onto window. Single source of truth for the
 * status-dot colour/tooltip; this spec pins the state → dot.cls table
 * so future tweaks can't silently regress.
 *
 * Contract (container-only classification, slot-status.js):
 *   error / crashed             → red
 *   serving + fresh             → green pulse (GREEN ONLY during in-flight)
 *   serving + last>1h           → yellow "ready" (stuck-request demotion)
 *   running + healthy / ready   → yellow (resident, awaiting prompt)
 *   pulling / starting / warming→ amber pulse
 *   !enabled / disabled         → grey "off"
 *   stopped / offline           → grey "stopped" (auto-reloads on request)
 *
 * Slots without container enrichment (`container_status == null`, e.g. a
 * stale /api/status union entry) classify on the bare state string.
 * RECENTLY_LIVE_MS is the hung-SERVING threshold.
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
    // READY is "resident and waiting" → yellow, not green. Green is
    // reserved for state=serving (in-flight only).
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      const slot = { state: 'ready', last_used_at: (now - 12 * 60 * 1000) / 1000, model: 'foo' }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('stale')
    expect(ind.label).toBe('ready')
    expect(ind.tooltip).toMatch(/Ready — last used 12 min ago/)
  })

  test('ready + last_used >1h ago → stale (yellow)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const now = Date.now()
      // 90 minutes ago
      const slot = { state: 'ready', last_used_at: (now - 90 * 60 * 1000) / 1000 }
      return (window as any).slotIndicator(slot, now)
    })
    expect(ind.cls).toBe('stale')
    expect(ind.tooltip).toMatch(/Ready — last used 1h ago/)
  })

  test('ready + last_used null → stale (yellow), tooltip notes container healthy', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      const slot = { state: 'ready', last_used_at: null }
      return (window as any).slotIndicator(slot, Date.now())
    })
    expect(ind.cls).toBe('stale')
    expect(ind.tooltip).toMatch(/Ready — container healthy/)
  })

  test('warming → warming (pulsing amber, "starting")', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'warming', model: 'Qwen3.5-0.8B-GGUF' })
    })
    expect(ind.cls).toBe('warming')
    expect(ind.label).toBe('starting')
    expect(ind.tooltip).toMatch(/Starting container — Qwen3.5-0.8B-GGUF/)
  })

  test('starting + pulling also map to warming', async ({ page }) => {
    const out = await page.evaluate(() => {
      return ['starting', 'pulling'].map((state) =>
        (window as any).slotIndicator({ state }).cls,
      )
    })
    expect(out).toEqual(['warming', 'warming'])
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

  test('offline → offline (grey "stopped", auto-reload tooltip)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'offline' })
    })
    expect(ind.cls).toBe('offline')
    expect(ind.label).toBe('stopped')
    expect(ind.tooltip).toBe('Container stopped (auto-reloads on next request)')
  })

  test('idle state maps to stale (yellow "ready" — resident, quiet)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'idle', last_used_at: null })
    })
    expect(ind.cls).toBe('stale')
    expect(ind.label).toBe('ready')
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

  test('serving + last_used >1h ago → stale (stuck-request demotion)', async ({ page }) => {
    // The hung-SERVING guard demotes a stale in-flight marker to the
    // yellow "ready" dot instead of holding a misleading green pulse.
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
    expect(ind.label).toBe('ready')
    expect(ind.tooltip).toMatch(/last used 1h ago/)
  })

  test('!enabled → offline (grey "off")', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({ state: 'ready', enabled: false })
    })
    expect(ind.cls).toBe('offline')
    expect(ind.label).toBe('off')
  })

  test('container running + healthy overrides a stale offline state → stale (yellow)', async ({ page }) => {
    // Container enrichment override: even if slot.state lags at offline,
    // a running + healthy container means the model is resident → yellow.
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({
        state: 'offline',
        container_status: 'running',
        container_health: true,
        model: 'foo',
      })
    })
    expect(ind.cls).toBe('stale')
    expect(ind.label).toBe('ready')
  })

  test('container stopped → offline (grey, auto-reloads on next request)', async ({ page }) => {
    const ind = await page.evaluate<Indicator>(() => {
      return (window as any).slotIndicator({
        state: 'offline',
        container_status: 'stopped',
        container_health: false,
        model: 'foo',
      })
    })
    expect(ind.cls).toBe('offline')
    expect(ind.label).toBe('stopped')
    expect(ind.tooltip).toMatch(/auto-reloads on next request/)
  })
})
