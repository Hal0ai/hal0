/**
 * toast-queue-v3 (GH #1473) — one toast implementation, with real queueing.
 *
 * Two competing toast systems existed: `useToastStore` (zustand, installed
 * as the canonical `window.__hal0Toast` by `globals-install.ts`, which runs
 * BEFORE React even mounts) and `main.jsx`'s own single-slot `useState`,
 * which unconditionally overwrote `window.__hal0Toast` on every App mount.
 * Nothing ever rendered the store's queue (`useToastStore.queue`'s only
 * import in the whole codebase was `globals-install.ts` installing the
 * global — never a consumer component), so:
 *
 *   - any toast fired before App mounted (bundle init, the AuthGate login
 *     screen) went into the invisible store queue and was silently dropped;
 *   - a SECOND toast while a first was still showing replaced it instead of
 *     queueing, because main.jsx's `toast` was a single value, not an array.
 *
 * Fixed by rendering a ToastHost off `useToastStore` (via the same
 * window-hook-bridge pattern `board-hook-bridge.ts` uses for
 * `__hal0UseBoardChat`) and deleting main.jsx's own state + mount-time
 * `window.__hal0Toast` overwrite — the store's `push` was already the
 * global everything calls into; only the render side was missing/wrong.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Toast queue (#1473)', () => {
  test('a second toast queues instead of replacing the first', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.evaluate(() => (window as any).__hal0Toast?.('first toast', 'info'))
    await page.evaluate(() => (window as any).__hal0Toast?.('second toast', 'success'))

    // Both must be visible together — a single-slot implementation would
    // have the second overwrite the first before this assertion runs.
    await expect(page.locator('.hal0-toast', { hasText: 'first toast' })).toBeVisible()
    await expect(page.locator('.hal0-toast', { hasText: 'second toast' })).toBeVisible()
  })

  test('three toasts in a row all render, each dismissible independently', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.evaluate(() => {
      const t = (window as any).__hal0Toast
      t?.('alpha', 'info')
      t?.('beta', 'success')
      t?.('gamma', 'error')
    })
    await expect(page.locator('.hal0-toast')).toHaveCount(3)

    await page.locator('.hal0-toast', { hasText: 'beta' }).locator('.toast-close').click()
    await expect(page.locator('.hal0-toast')).toHaveCount(2)
    await expect(page.locator('.hal0-toast', { hasText: 'alpha' })).toBeVisible()
    await expect(page.locator('.hal0-toast', { hasText: 'gamma' })).toBeVisible()
  })
})
