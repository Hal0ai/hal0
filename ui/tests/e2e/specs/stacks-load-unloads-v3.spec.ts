/**
 * stacks-load-unloads-v3 — the Load-stack dialog discloses the teardown (#1511).
 *
 * Applying a stack is a declarative replace: converge unloads every running
 * slot the stack does not name (src/hal0/stacks/apply.py, pass 3). Opening the
 * Load dialog now dry-runs the apply (`POST /api/stacks/{slug}/apply?dry_run=true`)
 * and lists the dry-run's `unloads` before the operator commits; the confirm
 * button waits for that preview. After the commit the toast reports
 * `converged.unloaded` instead of a bare "loaded".
 *
 * Mocks: /api/stacks (list) and /api/stacks/coding/apply (dry + commit) via
 * page.route; everything else falls through to the apiMock defaults.
 */

import { test, expect, json } from '../fixtures/apiMock'

const STACKS = {
  active: null,
  drift: 'none',
  stacks: [
    {
      slug: 'coding',
      name: 'Coding',
      description: 'Fast coder',
      author: 'hal0',
      icon: '',
      tags: [],
      seed: false,
      slots: [{ slot: 'primary', model: 'qwen3-coder-30b', capabilities: [] }],
    },
  ],
}

test.describe('Stacks — Load dialog discloses unloads (#1511)', () => {
  test('dry-run unloads are listed before confirm; toast reports them after', async ({ page }) => {
    const applyCalls: string[] = []
    let releasePreview: () => void = () => {}
    const previewGate = new Promise<void>((resolve) => { releasePreview = resolve })

    await page.route('**/api/stacks', (route) => json(route, STACKS))
    await page.route('**/api/stacks/coding/apply**', async (route) => {
      const url = new URL(route.request().url())
      const dry = url.searchParams.get('dry_run') === 'true'
      applyCalls.push(dry ? 'dry' : 'commit')
      if (dry) {
        await previewGate
        return json(route, {
          stack: 'coding',
          dry_run: true,
          summary: [],
          changes: [],
          creates: [],
          unloads: ['coder', 'img'],
          warnings: [],
        })
      }
      return json(route, {
        stack: 'coding',
        dry_run: false,
        created: [],
        summary: [],
        changes: [],
        converged: {
          loaded: ['primary'], swapped: [], reloaded: [], skipped: [],
          unloaded: ['coder', 'img'], capabilities_applied: [], errors: [],
        },
        warnings: [],
      })
    })

    await page.goto('/#slots/stacks')
    await page.waitForSelector('[data-testid="st-load-coding"]', { timeout: 10_000 })
    await page.click('[data-testid="st-load-coding"]')

    const dialog = page.getByRole('dialog', { name: 'Load stack' })
    const confirm = dialog.locator('[data-testid="st-load-confirm"]')
    const notice = dialog.locator('[data-testid="st-load-unloads"]')

    // While the preview is in flight the operator cannot commit blind.
    await expect(notice).toContainText('Checking which running slots')
    await expect(confirm).toBeDisabled()

    releasePreview()
    await expect(notice).toHaveText(/2 running slots not in this stack will be unloaded: coder, img\./)
    await expect(confirm).toBeEnabled()
    expect(applyCalls).toEqual(['dry'])

    await confirm.click()
    await expect(
      page.locator('.hal0-toast, [role="status"]').filter({ hasText: 'unloaded 2 slots: coder, img' }),
    ).toBeVisible({ timeout: 5_000 })
    expect(applyCalls).toEqual(['dry', 'commit'])
  })

  test('a failed preview still discloses the replace semantics', async ({ page }) => {
    await page.route('**/api/stacks', (route) => json(route, STACKS))
    await page.route('**/api/stacks/coding/apply**', (route) =>
      json(route, { error: { code: 'internal', message: 'boom' } }, 500),
    )

    await page.goto('/#slots/stacks')
    await page.waitForSelector('[data-testid="st-load-coding"]', { timeout: 10_000 })
    await page.click('[data-testid="st-load-coding"]')

    const dialog = page.getByRole('dialog', { name: 'Load stack' })
    await expect(dialog.locator('[data-testid="st-load-unloads"]')).toContainText(
      'loading a stack unloads every running slot it does not include',
    )
    await expect(dialog.locator('[data-testid="st-load-confirm"]')).toBeEnabled()
  })
})
