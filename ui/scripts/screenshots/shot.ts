/**
 * Capture helpers for the website screenshot set.
 *
 * `test` extends the e2e apiMock fixture (default mocks for every /api path)
 * and then lays the showcase overlay over it, so every shot starts from the
 * same busy, healthy box. `shoot()` waits for the page to settle and writes a
 * PNG into docs/screenshots/.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Locator, Page } from '@playwright/test'

import { test as base, expect } from '../../tests/e2e/fixtures/apiMock'
import { installSseHarness, waitForSse } from '../../tests/e2e/fixtures/sseHarness'
import { BACKENDS, CORE_PASSTHROUGH, HOST, MODELS, SLOTS, installCore } from './showcase/core'
import { AREA_PASSTHROUGH, installAreas } from './showcase/areas'
import { journalEntries } from './showcase/engines'

const HERE = path.dirname(fileURLToPath(import.meta.url))
export const OUT_DIR = process.env.SHOT_OUT || path.resolve(HERE, '../../../docs/screenshots')

export const DESKTOP = { width: 1440, height: 900 }
export const TABLET = { width: 834, height: 1112 }
export const PHONE = { width: 390, height: 844 }

/** Stills, not a recording: no transitions, no caret, no scrollbars. */
const STILL_CSS = `
  *, *::before, *::after {
    animation-duration: 0s !important;
    animation-delay: 0s !important;
    transition-duration: 0s !important;
    transition-delay: 0s !important;
    caret-color: transparent !important;
  }
  ::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
  * { scrollbar-width: none !important; }
`

export const test = base.extend<{ showcase: void }>({
  showcase: [
    async ({ page }, use) => {
      await page.addInitScript(
        ({ passthrough, slots, host, models, backends }) => {
          const w = window as any
          // `re:<source>` entries are exact-path patterns (the in-bundle mock
          // matches plain strings as prefixes).
          w.__hal0MockPassthrough = passthrough.map((p: string) =>
            p.startsWith('re:') ? new RegExp(p.slice(3)) : p,
          )
          // The in-bundle mock and a few direct readers use window.HAL0_DATA;
          // give them the showcase roster too.
          let real: any
          Object.defineProperty(window, 'HAL0_DATA', {
            configurable: true,
            get() {
              return real
            },
            set(v) {
              real = v
              if (v && typeof v === 'object') {
                v.slots = slots
                v.models = models
                v.backends = backends
                v.host = { ...v.host, ...host }
              }
            },
          })
          // Seen-once hints and tours stay dismissed.
          try {
            localStorage.setItem('hal0.onboarding.dismissed', '1')
          } catch {
            /* storage may be unavailable before the origin loads */
          }
        },
        { passthrough: [...CORE_PASSTHROUGH, ...AREA_PASSTHROUGH], slots: SLOTS, host: HOST, models: MODELS, backends: BACKENDS },
      )
      await installSseHarness(page)
      await installCore(page)
      await installAreas(page)
      await use()
    },
    { auto: true },
  ],
})

export { expect }

export type ShotOpts = {
  /** Keep the fixed dock in an element shot (it is hidden by default). */
  keepDock?: boolean
  /** Clip to this element instead of the viewport. */
  el?: Locator
  /** Capture the whole scrollable page. */
  fullPage?: boolean
}

export async function open(page: Page, hash: string, viewport = DESKTOP) {
  await page.setViewportSize(viewport)
  await page.goto('/' + hash)
  // Replay the journal into the open stream so the dock and Logs show lines.
  await waitForSse(page, '/api/journal/stream', 4_000).catch(() => {})
  await page.evaluate((rows) => {
    const streams = (window as any).__sseStreams || {}
    for (const url of Object.keys(streams)) {
      if (!url.includes('/api/journal/stream')) continue
      for (const entry of streams[url]) for (const r of rows) entry.dispatch(JSON.stringify(r))
    }
  }, journalEntries())
  await settle(page)
}

/** Fonts loaded, queries resolved, no spinners or skeletons, styles frozen. */
export async function settle(page: Page) {
  await page.addStyleTag({ content: STILL_CSS }).catch(() => {})
  await page.evaluate(() => (document as any).fonts?.ready)
  await page.waitForLoadState('networkidle').catch(() => {})
  await page
    .waitForFunction(
      () =>
        !document.querySelector(
          '.spinner, .skeleton, [aria-busy="true"], .loading, [data-loading="true"]',
        ),
      undefined,
      { timeout: 8_000 },
    )
    .catch(() => {})
  await page.waitForTimeout(700)
}

/** Size budget per image. The set stays lossless PNG; an image over the budget
 *  fails its test so it gets cropped, not recompressed. */
export const MAX_PNG_BYTES = 1.5 * 1024 * 1024

export async function shoot(page: Page, name: string, opts: ShotOpts = {}) {
  fs.mkdirSync(OUT_DIR, { recursive: true })
  await settle(page)
  const file = path.join(OUT_DIR, `${name}.png`)
  // Element shots scroll the element into view and capture all of it, even
  // when it is taller than the viewport; a dialog is captured as drawn. The
  // fixed dock is hidden in them unless the shot is of the dock.
  const style =
    opts.el && !opts.keepDock
      ? await page.addStyleTag({ content: '[data-testid="dock"] { visibility: hidden !important; }' })
      : null
  const common = { path: file, animations: 'disabled', caret: 'hide' } as const
  if (opts.el) {
    // Crop to what the element actually draws: a list inside a stretched
    // panel leaves empty space below its last row and right of its widest one.
    await opts.el.scrollIntoViewIfNeeded()
    const clip = await opts.el.evaluate((root) => {
      const r = root.getBoundingClientRect()
      let right = r.left
      let bottom = r.top
      for (const n of [root, ...root.querySelectorAll('*')] as Element[]) {
        const cs = getComputedStyle(n)
        if (cs.visibility === 'hidden' || cs.display === 'none') continue
        const b = n.getBoundingClientRect()
        if (b.width === 0 || b.height === 0) continue
        // Content is text, media, a control, or a painted box with no children
        // (a pip, a bar). A panel's own background is not content.
        const ownText = [...n.childNodes].some((c) => c.nodeType === 3 && c.textContent!.trim() !== '')
        const media = /^(IMG|SVG|CANVAS|INPUT|SELECT|TEXTAREA|BUTTON|VIDEO)$/i.test(n.tagName)
        const leafPaint = n.children.length === 0 && cs.backgroundColor !== 'rgba(0, 0, 0, 0)'
        if (n === root || !(ownText || media || leafPaint)) continue
        right = Math.max(right, Math.min(b.right, r.right))
        bottom = Math.max(bottom, Math.min(b.bottom, r.bottom))
      }
      const pad = 12
      return {
        x: r.left + window.scrollX,
        y: r.top + window.scrollY,
        width: Math.min(r.width, right - r.left + pad),
        height: Math.min(r.height, bottom - r.top + pad),
      }
    })
    await page.screenshot({ ...common, clip, fullPage: true })
  } else await page.screenshot({ ...common, fullPage: !!opts.fullPage })
  await style?.evaluate((n) => n.remove())
  const size = fs.statSync(file).size
  if (size > MAX_PNG_BYTES) {
    throw new Error(`${name}.png is ${(size / 1024 / 1024).toFixed(2)} MB, over the ${MAX_PNG_BYTES / 1024 / 1024} MB budget: crop it`)
  }
  return file
}
