/**
 * firstrun.spec.ts — γ-1 First-run wizard (PLAN §10.3 path 1).
 *
 * Covers the post-prototype 9-step linear wizard (Variant A + Phase 8):
 *   1. Password         — skip
 *   2. Hardware + dirs  — accept defaults
 *   3. Primary chat     — pick smallest curated card (Phi-3 Mini)
 *   4. Capabilities     — leave smart defaults (CPU box → embed off etc.)
 *   5. HF token         — skipped (Phi-3 Mini is not gated by id heuristic)
 *   6. License          — check accept box, click install
 *   7. Agent            — leave "no agent" default (Phase 8 / ADR-0004)
 *   8. Install          — drive SSE events to completion
 *   9. Done             — click "Open chat" → window.open + install/complete
 *
 * The spec mocks the curated catalogue, capability catalogs, config/models,
 * auth/status, and the pull-stream SSE so we can assert UI updates in
 * lockstep with each event.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'

test.beforeEach(async ({ page, mockState }) => {
  mockState.installState.first_run = true
  await installSseHarness(page)

  // Stub window.open so the "Open chat" assertion captures the URL
  // without actually navigating away.
  await page.addInitScript(() => {
    ;(window).__openCalls = []
    Object.defineProperty(window, 'open', {
      configurable: true,
      writable: true,
      value: function (url, target) {
        ;(window).__openCalls.push({ url: String(url), target: String(target == null ? '' : target) })
        return null
      },
    })
  })

  // New wizard touches additional read endpoints not in the default mock
  // bundle. Provide deterministic empty/closed shapes so the composable
  // settles into "first-run, CPU box, nothing gated".
  await page.route('**/api/auth/status', (route) => json(route, { password_set: false }))
  // Default password-claim mock — accept whatever the wizard posts.
  // Token-flow specs below override this with a token-aware variant.
  await page.route('**/api/auth/password', (route) =>
    json(route, { ok: true, password_set: true, rotated: false }),
  )
  await page.route('**/api/capabilities', (route) =>
    json(route, {
      backends: [],
      catalogs: { embed: { embed: [], rerank: [] }, voice: { stt: [], tts: [] }, img: { img: [] } },
      selections: {},
    }),
  )
  await page.route('**/api/config/models', (route) => {
    if (route.request().method() === 'PUT') {
      return json(route, { roots: ['/var/lib/hal0/models'] })
    }
    return json(route, { roots: ['/var/lib/hal0/models'] })
  })

  // Per-model pull starter (capability-side path). Primary chat goes
  // through /api/install/pick-default which is already mocked in
  // installDefaultMocks.
  await page.route('**/api/models/*/pull', (route) =>
    json(route, { id: 'job1', state: 'queued' }),
  )
})

test('redirects to /firstrun, walks the 8-step wizard, opens chat', async ({ page, mockState, cleanState }) => {
  // SSE stream route — actual frames come from the in-page SSE harness.
  await page.route('**/api/models/*/pull/stream', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )

  await page.goto('/')

  // Guard redirects to /firstrun on a fresh install.
  await expect(page).toHaveURL(/\/firstrun$/)
  await expect(page.getByText('Welcome to hal0')).toBeVisible()

  // ── Step 1 — Password: skip ───────────────────────────────────────
  await page.getByRole('button', { name: /Skip — leave open/ }).click()

  // ── Step 2 — Hardware + storage: accept defaults ──────────────────
  await expect(page.getByText('We probed your hardware')).toBeVisible()
  await page.getByRole('button', { name: /Next →/ }).click()

  // ── Step 3 — Pick smallest curated card (Phi-3 Mini, 2.4 GB) ──────
  const phiCard = page.locator('.model-option', { hasText: 'Phi-3 Mini' })
  await expect(phiCard).toBeVisible()
  await phiCard.click()
  await expect(phiCard).toHaveClass(/selected/)
  await page.getByRole('button', { name: /Next: capabilities/ }).click()

  // ── Step 4 — Capabilities: leave defaults, advance ────────────────
  await expect(page.getByText('Pick which capabilities run at startup')).toBeVisible()
  await page.getByRole('button', { name: /Next →/ }).click()

  // ── Step 5 (skipped) → Step 6 — License acceptance ────────────────
  // Phi-3 Mini is not in the gated heuristic (id doesn't start with
  // llama-/meta-llama/whisper-v3-npu), so the HF-token step is skipped.
  await expect(page.locator('.license-row', { hasText: 'Phi-3 Mini' })).toBeVisible()

  const acceptCheckbox = page.locator('.accept-label input[type="checkbox"]')
  const acceptBtn = page.getByRole('button', { name: /Accept .* install/i })
  await expect(acceptBtn).toBeDisabled()
  await acceptCheckbox.check()
  await expect(acceptBtn).toBeEnabled()
  await acceptBtn.click()

  // ── Step 7 — Agent picker (Phase 8): leave "no agent" default ─────
  await expect(page.getByText('Bundle a third-party agent')).toBeVisible()
  // Default radio is "none" — just advance with the skip button copy.
  await page.getByRole('button', { name: /Skip — no agent/ }).click()

  // ── Step 8 — Install: SSE progress events ─────────────────────────
  await waitForSse(page, '/api/models/phi3-mini/pull/stream')

  const streamUrl = '/api/models/phi3-mini/pull/stream'
  const total = 2_400_000_000
  for (let i = 1; i <= 4; i++) {
    await emitSse(page, streamUrl, {
      state: 'running',
      bytes_downloaded: Math.round((total * i) / 5),
      bytes_total: total,
    })
    await page.waitForTimeout(20)
  }
  // 4/5 = 80% — the pull-state pill should reflect it.
  await expect(page.locator('.pull-state').first()).toHaveText(/80%/)

  // Final event flips state to completed → step 8.
  await emitSse(page, streamUrl, {
    state: 'completed',
    bytes_downloaded: total,
    bytes_total: total,
  })

  await expect(page.getByText("You're all set!")).toBeVisible({ timeout: 5_000 })

  // ── Step 8 — Open chat: window.open + POST /api/install/complete ──
  const completeReq = page.waitForRequest(
    (req) => req.url().endsWith('/api/install/complete') && req.method() === 'POST',
  )
  await page.getByRole('button', { name: /Open chat/i }).click()
  await completeReq

  await expect.poll(async () => {
    return await page.evaluate(() => ((window).__openCalls || []).length)
  }).toBe(1)
  const openCalls = await page.evaluate(() => (window).__openCalls)
  expect(openCalls[0].url).toBe(mockState.configUrls.openwebui)
  expect(openCalls[0].target).toBe('_blank')
  expect(mockState.installCompleteCount).toBe(1)
})

/**
 * First-run OTP field — wired in fix/ui-firstrun-otp-2026-05-21 so a
 * LAN browser (no loopback bypass) can claim ownership using the token
 * the installer printed. The wizard always posts `first_run_token` in
 * the password-claim body; an empty value is tolerated server-side on
 * the loopback path.
 */
test.describe('first-run OTP', () => {
  test('renders the OTP field on step 1 and posts the token verbatim', async ({
    page,
    cleanState: _cleanState,
  }) => {
    // Capture the POST body so we can assert the OTP rides along.
    let capturedBody: { password?: string; first_run_token?: string } | null = null
    await page.route('**/api/auth/password', (route) => {
      try {
        capturedBody = JSON.parse(route.request().postData() || '{}')
      } catch {
        capturedBody = null
      }
      return json(route, { ok: true, password_set: true, rotated: false })
    })

    await page.goto('/')
    await expect(page).toHaveURL(/\/firstrun$/)

    // Field renders + accepts text input.
    const tokenField = page.locator('#firstrun-token')
    await expect(tokenField).toBeVisible()
    await expect(tokenField).toHaveAttribute('type', 'text')

    await page.locator('#firstrun-password').fill('correct horse battery staple')
    await tokenField.fill('deadbeefcafebabe1234567890abcdef')

    await page.getByRole('button', { name: /Set password/ }).click()

    // Wizard advances to step 2 only after the OTP-bearing POST succeeded.
    await expect(page.getByText('We probed your hardware')).toBeVisible()

    expect(capturedBody).not.toBeNull()
    expect(capturedBody!.password).toBe('correct horse battery staple')
    expect(capturedBody!.first_run_token).toBe('deadbeefcafebabe1234567890abcdef')
  })

  test('surfaces an inline error on auth.first_run_otp_required', async ({
    page,
    cleanState: _cleanState,
  }) => {
    // First call rejects with the OTP-required code; UI must show inline
    // error near the token field, NOT a generic toast, AND must stay on
    // step 1 so the operator can correct the token.
    await page.route('**/api/auth/password', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'auth.first_run_otp_required',
            message: 'first-run OTP is required',
            details: {},
          },
        }),
      }),
    )

    await page.goto('/')
    await expect(page).toHaveURL(/\/firstrun$/)

    await page.locator('#firstrun-password').fill('correct horse battery staple')
    // Submit with an empty token; the mock 401s regardless.
    await page.getByRole('button', { name: /Set password/ }).click()

    // Inline error visible next to the token field.
    await expect(page.locator('#firstrun-token + .field-err, .field-err').first()).toContainText(
      /Token rejected/i,
    )

    // Step indicator still on step 1 (we didn't advance).
    await expect(page.getByText('We probed your hardware')).not.toBeVisible()

    // Editing the field clears the error (immediate feedback).
    await page.locator('#firstrun-token').fill('a')
    await expect(page.locator('.field-err')).toHaveCount(0)
  })

  test('surfaces inline error on auth.first_run_otp_invalid too', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.route('**/api/auth/password', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'auth.first_run_otp_invalid',
            message: 'first-run OTP did not validate',
            details: {},
          },
        }),
      }),
    )

    await page.goto('/')
    await expect(page).toHaveURL(/\/firstrun$/)

    await page.locator('#firstrun-password').fill('correct horse battery staple')
    await page.locator('#firstrun-token').fill('wrong-token-value')
    await page.getByRole('button', { name: /Set password/ }).click()

    await expect(page.locator('.field-err').first()).toContainText(/Token rejected/i)
    await expect(page.getByText('We probed your hardware')).not.toBeVisible()
  })
})
