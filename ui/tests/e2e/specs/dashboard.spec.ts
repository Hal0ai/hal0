/**
 * dashboard.spec.ts — Test chat panel: chat dropdown filters non-chat slots.
 *
 * Regression for: pre-fix, the dropdown rendered every model `/v1/models`
 * returned (embed, rerank, stt, tts included) and defaulted to data[0],
 * which was the embed slot's model. Post-fix, the dropdown intersects
 * /v1/models with chat-capable slots derived from /api/slots ×
 * /api/capabilities.
 */
import { test, expect, json } from '../fixtures/apiMock'

test('Test chat dropdown lists only chat-capable upstreams', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Three upstreams: an embed slot, a chat slot, an stt slot. Only the
  // chat one should survive the filter.
  await page.route('**/v1/models', (route) =>
    json(route, {
      object: 'list',
      data: [
        { id: 'nomic-embed.gguf', object: 'model', owned_by: 'embed' },
        { id: 'qwen3-chat.gguf', object: 'model', owned_by: 'primary' },
        { id: 'moonshine-base', object: 'model', owned_by: 'stt' },
      ],
    }),
  )
  await page.route('**/api/slots', (route) =>
    json(route, [
      { name: 'embed', model_id: 'nomic-embed-text-v1.5-q8_0' },
      { name: 'primary', model_id: 'qwen3-chat-id' },
      { name: 'stt', model_id: 'moonshine-base-en' },
    ]),
  )
  await page.route('**/api/capabilities', (route) =>
    json(route, {
      backends: [],
      catalogs: {
        chat: {
          chat: [{ id: 'qwen3-chat-id', capabilities: ['chat'] }],
        },
      },
      selections: {},
    }),
  )

  await page.goto('/')

  const dropdown = page.locator('select.chat-model')
  await expect(dropdown).toBeVisible()
  // Wait for the post-mount fetch to settle and the options to populate.
  await expect(dropdown.locator('option')).toHaveCount(1, { timeout: 5_000 })
  await expect(dropdown.locator('option').first()).toHaveText('qwen3-chat.gguf')

  // Selected value mirrors the only chat-capable model.
  await expect(dropdown).toHaveValue('qwen3-chat.gguf')

  // Negative assertions: embed/stt models must not leak in.
  await expect(dropdown).not.toContainText('nomic-embed')
  await expect(dropdown).not.toContainText('moonshine')
})

test('Test chat dropdown is empty when no chat-capable slot exists', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Pre-fix this test would fail because the embed model would leak in
  // and become the default — sending /v1/chat/completions against an
  // embedding endpoint.
  await page.route('**/v1/models', (route) =>
    json(route, {
      object: 'list',
      data: [{ id: 'nomic-embed.gguf', object: 'model', owned_by: 'embed' }],
    }),
  )
  await page.route('**/api/slots', (route) =>
    json(route, [{ name: 'embed', model_id: 'nomic-embed-text-v1.5-q8_0' }]),
  )
  await page.route('**/api/capabilities', (route) =>
    json(route, {
      backends: [],
      catalogs: { chat: { chat: [] } },
      selections: {},
    }),
  )

  await page.goto('/')

  const dropdown = page.locator('select.chat-model')
  await expect(dropdown).toBeVisible()
  // "No models" placeholder option is the only entry.
  await expect(dropdown.locator('option')).toHaveCount(1)
  await expect(dropdown.locator('option').first()).toHaveText('No models')
})
