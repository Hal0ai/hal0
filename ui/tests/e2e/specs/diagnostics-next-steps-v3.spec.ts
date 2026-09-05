/**
 * diagnostics-next-steps-v3 — w1a "Doctor next steps as data" (H4).
 *
 * `health_report.py`'s classifiers now emit real `NextStep`s (previously
 * hard-coded to `[]`) and `DiagnosisPanel.jsx` renders them as ACTING chips
 * — copy-to-clipboard for every command, a "Run" button for the subset that
 * maps onto an existing typed mutation, and a numbered StepsDrawer once a
 * diagnosis carries >=2 steps. This spec pins that behaviour against a
 * mocked `/api/doctor` feed shaped like the real one.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const DOCTOR_FEED = {
  verdict: 'critical',
  diagnoses: [
    {
      id: 'HAL0-API-UNREACHABLE',
      severity: 'info',
      confidence: 'high',
      summary: 'hal0 API reachable',
      detail: '',
      fixable: false,
      evidence: [],
      next_steps: [],
    },
    {
      id: 'HAL0-HERMES-DOWN',
      severity: 'warn',
      confidence: 'high',
      summary: 'Hermes is not responding',
      detail: 'hal0-agent@hermes.service is inactive.',
      fixable: false,
      evidence: [],
      // A single actionable command step — renders inline, no Steps opener.
      next_steps: [
        {
          kind: 'command',
          label: 'systemctl restart hal0-agent@hermes',
          target: 'systemctl restart hal0-agent@hermes',
        },
      ],
    },
    {
      id: 'HAL0-RUNNERS-NONE-HEALTHY',
      severity: 'critical',
      confidence: 'high',
      summary: 'no runner is healthy',
      detail: 'Every configured slot is offline or errored.',
      fixable: false,
      evidence: [],
      // Two steps -> the Steps drawer opener must appear.
      next_steps: [
        { kind: 'command', label: 'hal0 slot restart chat', target: 'hal0 slot restart chat' },
        { kind: 'doc', label: 'Manage slots', target: '/docs/guides/manage-slots' },
      ],
    },
  ],
}

async function mockDoctor(page: Page) {
  await page.route('**/api/doctor', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DOCTOR_FEED) }),
  )
}

async function openDoctor(page: Page) {
  await page.goto('/#settings')
  await page.locator('.settings-nav .nav-item', { hasText: 'Doctor' }).click()
  await expect(page.getByTestId('diagnosis-panel')).toBeVisible()
}

test.describe('Diagnosis next steps act (w1a)', () => {
  test('a command step shows its text in mono and copies to the clipboard', async ({
    page,
    context,
  }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await mockDoctor(page)
    await openDoctor(page)

    const card = page.getByTestId('diagnosis-card-HAL0-HERMES-DOWN')
    const step = card.getByTestId('diagnosis-next-step').first()
    await expect(step).toContainText('systemctl restart hal0-agent@hermes')

    await step.getByTestId('diagnosis-next-step-copy').click()
    await expect(page.locator('.toast-msg').last()).toContainText(/copied/i)
    const clip = await page.evaluate(() => navigator.clipboard.readText())
    expect(clip).toBe('systemctl restart hal0-agent@hermes')
  })

  test('a command mapped to a repairable service offers Run', async ({ page }) => {
    await mockDoctor(page)
    await openDoctor(page)

    const card = page.getByTestId('diagnosis-card-HAL0-HERMES-DOWN')
    const run = card.getByTestId('diagnosis-next-step-run')
    await expect(run).toBeVisible()
    await run.click()
    await expect(page.locator('.toast-msg').last()).toContainText(/hal0-agent@hermes/i)
  })

  test('a doc step is a real link that opens the target', async ({ page, context }) => {
    // The doc target resolves against the public docs site — stub it so the
    // popup never makes a real network request in CI.
    await context.route('https://hal0.dev/**', (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>docs stub</title>' }),
    )
    await mockDoctor(page)
    await openDoctor(page)

    const card = page.getByTestId('diagnosis-card-HAL0-RUNNERS-NONE-HEALTHY')
    const [popup] = await Promise.all([
      context.waitForEvent('page'),
      card.getByTestId('diagnosis-next-step').filter({ hasText: 'Manage slots' }).click(),
    ])
    await expect
      .poll(() => popup.url())
      .toContain('/docs/guides/manage-slots')
  })

  test('a diagnosis with >=2 steps offers a Steps drawer with numbered rows', async ({ page }) => {
    await mockDoctor(page)
    await openDoctor(page)

    const card = page.getByTestId('diagnosis-card-HAL0-RUNNERS-NONE-HEALTHY')
    await expect(card.getByTestId('diagnosis-open-steps')).toContainText('2')
    await card.getByTestId('diagnosis-open-steps').click()

    const drawer = page.getByTestId('steps-drawer')
    await expect(drawer).toBeVisible()
    const rows = drawer.getByTestId('steps-drawer-row')
    await expect(rows).toHaveCount(2)
    await expect(rows.nth(0)).toContainText('hal0 slot restart chat')
    await expect(rows.nth(1)).toContainText('Manage slots')
  })

  test('a diagnosis with a single step has no Steps opener', async ({ page }) => {
    await mockDoctor(page)
    await openDoctor(page)

    const card = page.getByTestId('diagnosis-card-HAL0-HERMES-DOWN')
    await expect(card.getByTestId('diagnosis-open-steps')).toHaveCount(0)
  })
})
