/**
 * board-unavailable-v3 (ADR-0046, #3265, #3269) — the Operator Board is the
 * Hermes kanban, so `/api/board/*` answers a 503 when it cannot reach it.
 * Each code (src/hal0/board/__init__.py: BoardNotInstalled, BoardUnreachable,
 * BoardNoToken) replaces the lanes with an empty state that names the fix and
 * links to the Agents page. The wire shape is hal0's `{error:{code,message,details}}`
 * envelope.
 *
 * The view must not retry these: a retry cannot fix them, and three backoff
 * rounds would hold the page on a spinner for ~7s. The empty state appearing
 * inside the default expect timeout (5s) is the proof.
 */
import { test, expect, json } from '../fixtures/apiMock'

const CASES = [
  {
    code: 'board.not_installed',
    title: 'No board yet',
    body: 'Install the Hermes agent to use the Operator Board.',
    link: 'Open Agents',
  },
  {
    code: 'board.unreachable',
    title: 'Hermes agent is not running',
    body: 'The board lives in the Hermes kanban. Start the agent to see it.',
    link: 'Open the Hermes card',
  },
  {
    code: 'board.no_token',
    title: 'Board token missing',
    body: 'Restart the Hermes agent so hal0 can mint its dashboard token.',
    link: 'Open the Hermes card',
  },
] as const

test.describe('Operator Board — Hermes kanban unavailable', () => {
  for (const c of CASES) {
    test(`${c.code} shows its empty state instead of lanes`, async ({ page }) => {
      await page.route(/\/api\/board\/board(\?|$)/, (route) =>
        json(route, { error: { code: c.code, message: c.code, details: {} } }, 503),
      )
      await page.goto('/#board')

      const state = page.getByTestId('board-unavailable')
      await expect(state).toBeVisible()
      await expect(state).toContainText(c.title)
      await expect(state).toContainText(c.body)
      const link = state.getByRole('link', { name: c.link })
      await expect(link).toHaveAttribute('href', '#agent')
      await expect(page.getByTestId('board-lane-backlog')).toHaveCount(0)
    })
  }

  test('the Agents link opens the Agents page', async ({ page }) => {
    await page.route(/\/api\/board\/board(\?|$)/, (route) =>
      json(route, { error: { code: 'board.not_installed', message: 'x', details: {} } }, 503),
    )
    await page.goto('/#board')
    await page.getByTestId('board-unavailable').getByRole('link', { name: 'Open Agents' }).click()
    // #agent is the Agents page. The old #agents is no page at all and fell back to the
    // Overview, which this test could not tell apart: it only saw the board go away (#4107).
    await expect(page).toHaveURL(/#agent$/)
    await expect(page.getByTestId('board-unavailable')).toHaveCount(0)
    await expect(page.getByTestId('agent-tab-nav')).toBeVisible()
  })

  test('a Hermes-side error is not mistaken for unavailability', async ({ page }) => {
    // board.upstream_error means Hermes answered; the unavailable empty state is only for the three 503s.
    await page.route(/\/api\/board\/board(\?|$)/, (route) =>
      json(route, { error: { code: 'board.upstream_error', message: 'boom', details: {} } }, 502),
    )
    await page.goto('/#board')
    await expect(page.getByTestId('board-view')).toBeVisible()
    await expect(page.getByTestId('board-unavailable')).toHaveCount(0)
  })
})
