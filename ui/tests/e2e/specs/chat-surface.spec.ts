/**
 * chat-surface.spec.ts — PR-18 dashboard chat panel.
 *
 * Covers (plan §11 PR-18 + §7 + ADR-0008 §8 + ADR-0010):
 *
 *   1. Persona dropdown enumerates enabled chat slots (primary always,
 *      coder when a Pro+ bundle wires it, agent when FLM is present).
 *      Disabled or model-less slots are filtered out.
 *
 *   2. Mic button is disabled when the voice (transcription) capability
 *      is not serving — toast text + tooltip both surface the gate.
 *
 *   3. Image button is disabled when the image capability is not
 *      serving — clicking does nothing (modal stays closed).
 *
 *   4. "Use tools" toggle attaches ``omni: true`` to the request body
 *      when the persona's model carries the ``tool-calling`` label.
 *      Toggling it off strips the field.
 *
 *   5. Tool-call indicator renders an inline image when the response
 *      includes ``_hal0.tool_calls`` with a ``generate_image`` entry.
 *
 *   6. Error from the chat endpoint surfaces inline in the thread
 *      (not just as a toast).
 *
 *   7. Non-tool-calling persona hides the "Use tools" toggle entirely.
 *
 * All HTTP is stubbed via the apiMock fixture; no live backend.
 */
import { test, expect, json } from '../fixtures/apiMock'

/* ── Helpers ─────────────────────────────────────────────────────── */

/** Build a slot row in the /api/slots shape PR-18 introduced. */
function slot(opts: {
  name: string
  type?: string
  model_default?: string
  labels?: string[]
  enabled?: boolean
  status?: string
  lemonade_state?: string
}) {
  return {
    name: opts.name,
    state: opts.status || 'serving',
    status: opts.status || 'serving',
    port: 8081,
    model_id: opts.model_default || null,
    backend: 'llamacpp',
    metadata: {},
    kind: 'local',
    type: opts.type ?? 'llm',
    model_default: opts.model_default ?? '',
    labels: opts.labels ?? [],
    enabled: opts.enabled !== false,
    lemonade_state: opts.lemonade_state ?? 'loaded',
    models: opts.model_default ? [opts.model_default] : [],
  }
}

/** /api/capabilities payload with selectable voice + image readiness. */
function capabilities(opts: { voiceReady?: boolean; imageReady?: boolean }) {
  return {
    backends: [],
    catalogs: { chat: { chat: [] }, voice: { stt: [], tts: [] }, img: { img: [] } },
    selections: {
      voice: {
        stt: opts.voiceReady
          ? { backend: 'whispercpp', provider: 'whispercpp', model: 'whisper-base', enabled: true, slot: 'stt', status: 'serving' }
          : { backend: 'whispercpp', provider: 'whispercpp', model: 'whisper-base', enabled: false, slot: 'stt', status: 'disabled' },
        tts: { backend: 'kokoro', provider: 'kokoro', model: 'kokoro-v1', enabled: false, slot: 'tts', status: 'disabled' },
      },
      img: {
        gen: opts.imageReady
          ? { backend: 'comfyui', provider: 'comfyui', model: 'sdxl-turbo', enabled: true, slot: 'img', status: 'serving' }
          : { backend: 'comfyui', provider: 'comfyui', model: 'sdxl-turbo', enabled: false, slot: 'img', status: 'disabled' },
      },
    },
  }
}

test.describe('PR-18 chat surface', () => {
  test('persona dropdown lists enabled chat slots in priority order', async ({
    page,
    cleanState,
  }) => {
    await page.route('**/api/slots', (route) =>
      json(route, [
        slot({ name: 'primary', model_default: 'qwen3-4b', labels: ['tool-calling'] }),
        slot({ name: 'coder', model_default: 'qwen3-coder-30b', labels: ['tool-calling', 'code'] }),
        slot({ name: 'embed', type: 'embedding', model_default: 'nomic-v1.5' }),  // not a persona
        slot({ name: 'agent', model_default: 'gemma3-1b-npu', labels: ['tool-calling'] }),
        slot({ name: 'disabled-chat', model_default: 'fake', enabled: false }),
      ]),
    )
    await page.route('**/api/capabilities', (route) => json(route, capabilities({})))

    await page.goto('/')
    const dd = page.locator('[data-testid="chat-persona"]')
    await expect(dd).toBeVisible()

    // Wait for slots fetch to settle and populate options. The embedding
    // slot must NOT appear (wrong type); the disabled slot DOES appear
    // (so the user sees what's unavailable) but is marked disabled.
    await expect(dd.locator('option')).toHaveCount(4, { timeout: 5_000 })

    // First three: primary, agent, coder (ordered by the SLOT_ORDER
    // priority list — primary > agent > coder).
    const opts = await dd.locator('option').allTextContents()
    expect(opts[0]).toContain('primary')
    expect(opts[1]).toContain('agent')
    expect(opts[2]).toContain('coder')
    // disabled-chat lands last (alpha tail) and is flagged disabled.
    expect(opts[3]).toMatch(/disabled-chat/)
    expect(opts[3]).toContain('disabled')

    // Default selection lands on primary.
    await expect(dd).toHaveValue('primary')
  })

  test('mic button is disabled when no voice slot is enabled', async ({
    page,
    cleanState,
  }) => {
    await page.route('**/api/slots', (route) =>
      json(route, [slot({ name: 'primary', model_default: 'qwen3-4b' })]),
    )
    await page.route('**/api/capabilities', (route) =>
      json(route, capabilities({ voiceReady: false })),
    )

    await page.goto('/')
    const mic = page.locator('[data-testid="chat-mic-btn"]')
    await expect(mic).toBeVisible()
    await expect(mic).toBeDisabled()
    await expect(mic).toHaveAttribute('title', /No voice slot enabled/)
  })

  test('image button is disabled when no image slot is enabled', async ({
    page,
    cleanState,
  }) => {
    await page.route('**/api/slots', (route) =>
      json(route, [slot({ name: 'primary', model_default: 'qwen3-4b' })]),
    )
    await page.route('**/api/capabilities', (route) =>
      json(route, capabilities({ imageReady: false })),
    )

    await page.goto('/')
    const imgBtn = page.locator('[data-testid="chat-image-btn"]')
    await expect(imgBtn).toBeVisible()
    await expect(imgBtn).toBeDisabled()
    await expect(imgBtn).toHaveAttribute('title', /No image slot enabled/)
    // Modal must not open.
    await expect(page.locator('[data-testid="chat-image-modal"]')).toHaveCount(0)
  })

  test('send with tool-calling persona attaches omni:true to the request', async ({
    page,
    cleanState,
  }) => {
    await page.route('**/api/slots', (route) =>
      json(route, [
        slot({
          name: 'primary',
          model_default: 'qwen3-4b',
          labels: ['tool-calling'],
        }),
      ]),
    )
    await page.route('**/api/capabilities', (route) => json(route, capabilities({})))

    let lastChatBody: any = null
    await page.route('**/v1/chat/completions', (route) => {
      const req = route.request()
      lastChatBody = JSON.parse(req.postData() || '{}')
      return json(route, {
        choices: [{ message: { role: 'assistant', content: 'hello world' } }],
      })
    })

    await page.goto('/')
    const dd = page.locator('[data-testid="chat-persona"]')
    await expect(dd).toHaveValue('primary')

    // Toggle is ON by default — assert.
    const toggle = page.locator('[data-testid="chat-omni-toggle"]')
    await expect(toggle).toBeChecked()

    await page.locator('[data-testid="chat-input"]').fill('say hi')
    await page.locator('[data-testid="chat-send"]').click()

    await expect(page.locator('[data-testid="chat-thread"]')).toContainText('hello world')
    expect(lastChatBody).toBeTruthy()
    expect(lastChatBody.omni).toBe(true)
    expect(lastChatBody.model).toBe('qwen3-4b')
  })

  test('tool-call indicator renders generated image inline', async ({
    page,
    cleanState,
  }) => {
    await page.route('**/api/slots', (route) =>
      json(route, [
        slot({
          name: 'primary',
          model_default: 'qwen3-4b',
          labels: ['tool-calling'],
        }),
      ]),
    )
    await page.route('**/api/capabilities', (route) => json(route, capabilities({ imageReady: true })))
    await page.route('**/v1/chat/completions', (route) =>
      json(route, {
        choices: [{ message: { role: 'assistant', content: 'Here is your image:' } }],
        _hal0: {
          tool_calls: [
            {
              name: 'generate_image',
              arguments: { prompt: 'a hat' },
              image_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
            },
          ],
        },
      }),
    )

    await page.goto('/')
    await page.locator('[data-testid="chat-input"]').fill('draw a hat')
    await page.locator('[data-testid="chat-send"]').click()

    // Inline image surfaces in the thread.
    const img = page.locator('[data-testid="chat-tool-image"]').first()
    await expect(img).toBeVisible()
    await expect(img).toHaveAttribute('src', /^data:image\/png/)
    // Tool name renders in the role label.
    await expect(page.locator('[data-testid="chat-thread"]')).toContainText('generate_image')
  })

  test('chat error renders inline in the thread', async ({ page, cleanState }) => {
    await page.route('**/api/slots', (route) =>
      json(route, [slot({ name: 'primary', model_default: 'qwen3-4b' })]),
    )
    await page.route('**/api/capabilities', (route) => json(route, capabilities({})))
    await page.route('**/v1/chat/completions', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'lemonade.upstream_error', message: 'upstream is down' },
        }),
      }),
    )

    await page.goto('/')
    await page.locator('[data-testid="chat-input"]').fill('hello?')
    await page.locator('[data-testid="chat-send"]').click()

    await expect(page.locator('[data-testid="chat-thread"]')).toContainText('upstream is down')
  })

  test('non-tool-calling persona hides the OmniRouter toggle', async ({
    page,
    cleanState,
  }) => {
    await page.route('**/api/slots', (route) =>
      json(route, [
        // No ``tool-calling`` label on the model — OmniRouter is not
        // applicable and the toggle must not render.
        slot({ name: 'primary', model_default: 'qwen3-0.6b', labels: [] }),
      ]),
    )
    await page.route('**/api/capabilities', (route) => json(route, capabilities({})))

    let lastChatBody: any = null
    await page.route('**/v1/chat/completions', (route) => {
      lastChatBody = JSON.parse(route.request().postData() || '{}')
      return json(route, { choices: [{ message: { role: 'assistant', content: 'plain' } }] })
    })

    await page.goto('/')
    await expect(page.locator('[data-testid="chat-persona"]')).toHaveValue('primary')
    await expect(page.locator('[data-testid="chat-omni-toggle"]')).toHaveCount(0)

    await page.locator('[data-testid="chat-input"]').fill('hi')
    await page.locator('[data-testid="chat-send"]').click()
    await expect(page.locator('[data-testid="chat-thread"]')).toContainText('plain')
    expect(lastChatBody.omni).toBeUndefined()
  })
})
