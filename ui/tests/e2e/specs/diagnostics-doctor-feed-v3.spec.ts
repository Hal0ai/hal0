/**
 * diagnostics-doctor-feed-v3 — #1458.
 *
 * GET /api/doctor is LIVE (src/hal0/api/routes/doctor.py) and returns the same
 * typed Diagnosis rows `hal0 doctor verify --json` prints. The panel hook
 * (src/api/hooks/useDiagnoses.ts) never fetched it: it synthesised one
 * info-severity HAL0-SYS-INFO card from /api/system-info and hardcoded
 * `doctorFeedPending: true`, so Settings ▸ Diagnostics ▸ Doctor rendered
 * "all clear" on a box whose server verdict was `warn`.
 *
 * This spec pins the wiring: server rows drop into the generic renderer and
 * the chip reflects the SERVER verdict.
 *
 * /api/doctor is answered by the fixture's `/api/` catch-all with `{}`; a
 * page.route registered afterwards wins.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

/** A realistic DoctorResponse (routes/doctor.py DoctorResponse/DiagnosisOut). */
const DOCTOR_WARN = {
  verdict: 'warn',
  diagnoses: [
    {
      id: 'HAL0-API-UNREACHABLE',
      severity: 'info',
      confidence: 'high',
      summary: 'hal0 API reachable',
      detail: '',
      fixable: false,
      evidence: [{ kind: 'endpoint', summary: 'GET /api/health → 200', data: {} }],
      next_steps: [],
    },
    {
      id: 'HAL0-RUNNERS-NONE-HEALTHY',
      severity: 'warn',
      confidence: 'high',
      summary: 'no runner is healthy',
      detail: 'Every configured slot is offline or errored.',
      fixable: false,
      evidence: [{ kind: 'table_row', summary: 'slots: 0 healthy / 3 configured', data: {} }],
      next_steps: [{ kind: 'command', label: 'run: hal0 slots status', target: 'hal0 slots status' }],
    },
    {
      id: 'HAL0-HERMES-DOWN',
      severity: 'warn',
      confidence: 'high',
      summary: 'hermes is not responding',
      detail: 'hal0-agent@hermes.service is inactive.',
      fixable: true,
      evidence: [{ kind: 'command', summary: 'systemctl is-active hal0-agent@hermes → inactive', data: {} }],
      next_steps: [
        { kind: 'command', label: 'restart hermes', target: 'systemctl restart hal0-agent@hermes' },
      ],
    },
  ],
}

async function mockDoctor(page: Page, body: unknown, status = 200) {
  await page.route('**/api/doctor', (route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

async function mockSystemInfo(page: Page, hardware: Record<string, unknown>) {
  await page.route('**/api/system-info', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ hardware, features: {}, backends: {} }),
    }),
  )
}

async function openDoctor(page: Page) {
  await page.goto('/#settings')
  await page.locator('.settings-nav .nav-item', { hasText: 'Doctor' }).click()
  await expect(page.getByTestId('diagnosis-panel')).toBeVisible()
}

test.describe('Diagnostics panel reads the live doctor feed (#1458)', () => {
  test('server warn rows render and the chip reflects the SERVER verdict', async ({ page }) => {
    await mockDoctor(page, DOCTOR_WARN)
    await mockSystemInfo(page, { platform_label: 'Strix Halo', cpu_name: 'AMD Ryzen AI Max+ 395' })
    await openDoctor(page)

    // The server's warn rows are rendered by the generic renderer.
    const runners = page.getByTestId('diagnosis-card-HAL0-RUNNERS-NONE-HEALTHY')
    await expect(runners).toBeVisible({ timeout: 10_000 })
    await expect(runners.getByTestId('diagnosis-severity')).toContainText(/warn/i)
    await expect(runners.getByTestId('diagnosis-evidence')).toContainText('0 healthy / 3 configured')

    const hermes = page.getByTestId('diagnosis-card-HAL0-HERMES-DOWN')
    await expect(hermes).toBeVisible()
    await expect(hermes.getByTestId('diagnosis-next-step').first()).toContainText(/restart hermes/i)

    // The chip must NOT say "all clear" when the server verdict is warn.
    await expect(page.getByTestId('diagnosis-verdict')).toContainText(/needs attention/i)
    await expect(page.getByTestId('diagnosis-verdict')).not.toContainText(/all clear/i)

    // No "the route does not exist" stub while the route is answering.
    await expect(page.getByTestId('diagnosis-feed-stub')).toHaveCount(0)

    // The synthesised system-info fallback must not double up on the feed.
    await expect(page.getByTestId('diagnosis-card-HAL0-SYS-INFO')).toHaveCount(0)
  })

  test('a 404 from /api/doctor falls back to the synthesised system-info card', async ({ page }) => {
    await mockDoctor(page, { error: { code: 'not_found', message: 'Not Found', details: {} } }, 404)
    await mockSystemInfo(page, { platform_label: 'Strix Halo', cpu_name: 'AMD Ryzen AI Max+ 395' })
    await openDoctor(page)

    await expect(page.getByTestId('diagnosis-card-HAL0-SYS-INFO')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('diagnosis-verdict')).toContainText(/all clear/i)
    // …and the stub explains WHY there are no server verdicts.
    await expect(page.getByTestId('diagnosis-feed-stub')).toContainText(/doctor feed unavailable/i)
  })
})
