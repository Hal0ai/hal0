/**
 * settings-agent-chat-v3 — Settings → Integrations → Agent Chat, schema-driven
 * rendering + the #2108 tool_model gap + the preview-before-apply drawer.
 *
 * `[brain_chat].tool_model` previously had no dashboard path at all
 * (AgentsBrainPage.jsx never read or wrote it). This spec pins:
 *   1. the row renders (RichSelect + description) — the schema-driven
 *      renderer closes the gap for any Hal0Config leaf, not just this one;
 *   2. the fresh-install "no live target" banner (#2108 part 2: the `agent`
 *      slot ships with no bound model) renders from the server's
 *      GET /api/settings/fields `live_target`, not a frontend guess;
 *   3. Save opens a preview drawer showing the real before/after ChangeSet
 *      (POST /api/settings/preview) before anything is written, and Apply
 *      sends the identical patch through PUT /api/settings.
 *
 * None of `/api/settings*` or `/api/slots` are in apiMock's in-bundle mock
 * allowlist, so every route below is a per-spec `page.route` override —
 * the catch-all in apiMock.ts would otherwise answer them with `{}`.
 */
import { test, expect } from '../fixtures/apiMock'
import { pickRichOption } from '../fixtures/richSelect'
import type { Page } from '@playwright/test'

// Trimmed pydantic JSON Schema for Hal0Config, extracted verbatim via
// `Hal0Config.model_json_schema()` (see tests/api/test_settings_fields.py
// for the backend-side pin on this same shape) — real field names, real
// descriptions, real constraints, so `_schemaField`'s $ref/allOf walk is
// exercised the same way it is against the live schema.
const SETTINGS_SCHEMA = {
  properties: {
    brain_chat: { $ref: '#/$defs/BrainChatConfig' },
  },
  $defs: {
    BrainChatConfig: {
      type: 'object',
      title: 'BrainChatConfig',
      properties: {
        enabled: { type: 'boolean', default: true, description: "Hard kill switch for the dashboard's agent-chat steward." },
        read_only: { type: 'boolean', default: true, description: 'When true, every mutating or admin-write tool is refused server-side.' },
        model: { type: 'string', default: '', description: "Which model/slot drives the steward chat. Empty keeps the persona's preferred_model." },
        tool_model: {
          type: 'string',
          default: 'hal0/agent',
          description:
            "Where a tool-calling ROUND reroutes when the chat model can't emit tool calls this runtime parses. Default 'hal0/agent' — the always-on fallback anchor — but that slot ships unbound on a fresh install.",
        },
        max_rounds: { type: 'integer', default: 8, minimum: 1, maximum: 100, description: 'Runaway backstop: max tool-calling rounds in one chat turn.' },
        completion_timeout_s: { type: 'number', default: 300.0, exclusiveMinimum: 0, description: 'Transport timeout (seconds) for each LLM round.' },
      },
    },
  },
}

const APPLY_PLAN_REGISTRY = {
  'brain_chat.enabled': { apply_class: 'immediate', services: [] },
  'brain_chat.read_only': { apply_class: 'immediate', services: [] },
  'brain_chat.model': { apply_class: 'immediate', services: [] },
  'brain_chat.tool_model': { apply_class: 'immediate', services: [] },
  'brain_chat.max_rounds': { apply_class: 'immediate', services: [] },
  'brain_chat.completion_timeout_s': { apply_class: 'immediate', services: [] },
}

function brainChatConfig(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    read_only: true,
    model: '',
    tool_model: 'hal0/agent',
    max_rounds: 8,
    completion_timeout_s: 300,
    ...overrides,
  }
}

async function mockSettingsSurface(
  page: Page,
  { toolModel = 'hal0/agent', liveTarget = false }: { toolModel?: string; liveTarget?: boolean } = {},
) {
  const live = brainChatConfig({ tool_model: toolModel })

  await page.route('**/api/settings/schema', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SETTINGS_SCHEMA) }),
  )
  await page.route('**/api/settings/apply-plan', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ apply_classes: ['immediate', 'service-restart', 'manual-restart'], registry: APPLY_PLAN_REGISTRY }),
    }),
  )
  await page.route('**/api/settings/fields', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        fields: [
          {
            path: 'brain_chat.tool_model',
            group: 'brain_chat',
            label: 'Tool model',
            description: SETTINGS_SCHEMA.$defs.BrainChatConfig.properties.tool_model.description,
            type: 'string',
            enum: null,
            constraints: {},
            default: 'hal0/agent',
            current: toolModel,
            secret: false,
            apply_class: 'immediate',
            services: [],
            live_target: liveTarget,
          },
        ],
      }),
    }),
  )
  await page.route('**/api/slots', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { name: 'agent', type: 'llm', device: 'gpu', model: '', state: 'empty', metrics: {} },
        { name: 'utility', type: 'llm', device: 'cpu', model: 'qwen3-0.6b', state: 'ready', metrics: {} },
      ]),
    }),
  )
  // GET/PUT /api/settings share one handler so a save is reflected on the
  // next read within the same test (react-query refetches after mutate).
  await page.route('**/api/settings', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ brain_chat: live }) })
    }
    return route.continue()
  })
}

async function openAgentChat(page: Page) {
  await page.goto('/#settings/agents')
  await expect(page.locator('.settings-content h2').first()).toHaveText('Agent Chat')
}

test.describe('Settings → Agent Chat (schema-driven, #2108)', () => {
  test('tool_model renders as a labelled RichSelect row', async ({ page }) => {
    await mockSettingsSurface(page)
    await openAgentChat(page)

    await expect(page.locator('.s-row .k', { hasText: 'Tool model' })).toBeVisible()
    const trigger = page.getByTestId('brain-chat-tool-model-select')
    await expect(trigger).toBeVisible()
    // The closed trigger already renders the current value's row text.
    await expect(trigger).toContainText('hal0/agent')
  })

  test('a fresh-install tool_model with no live target shows the plain-language banner', async ({ page }) => {
    await mockSettingsSurface(page, { toolModel: 'hal0/agent', liveTarget: false })
    await openAgentChat(page)

    await expect(page.locator('.banner-warn')).toContainText(/no live target/i)
    await expect(page.locator('.banner-warn')).toContainText('hal0/agent')
  })

  test('a live-target tool_model shows no banner', async ({ page }) => {
    await mockSettingsSurface(page, { toolModel: 'hal0/utility', liveTarget: true })
    await openAgentChat(page)

    await expect(page.locator('.banner-warn')).toHaveCount(0)
  })

  test('Review & save previews the real ChangeSet, then Apply persists it', async ({ page }) => {
    await mockSettingsSurface(page, { toolModel: 'hal0/agent', liveTarget: false })
    await openAgentChat(page)

    const trigger = page.getByTestId('brain-chat-tool-model-select')
    await pickRichOption(trigger, 'hal0/utility')
    await expect(trigger).toContainText('hal0/utility')

    let previewBody: unknown = null
    await page.route('**/api/settings/preview', (route) => {
      previewBody = route.request().postDataJSON()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          changeset: {
            changes: [
              {
                path: 'brain_chat.tool_model',
                before: 'hal0/agent',
                after: 'hal0/utility',
                kind: 'changed',
                apply_class: 'immediate',
                services: [],
              },
            ],
            unknown: [],
          },
          apply_plan: { immediate: ['brain_chat.tool_model'], service_restart: {}, manual_restart: [], unknown: [] },
        }),
      })
    })

    await page.getByRole('button', { name: /review & save/i }).click()

    // The preview drawer shows the real diff before anything is written.
    await expect(page.locator('.drawer.open')).toContainText('brain_chat.tool_model')
    await expect(page.locator('.drawer.open')).toContainText('hal0/agent')
    await expect(page.locator('.drawer.open')).toContainText('hal0/utility')
    expect(previewBody).toEqual({ brain_chat: { tool_model: 'hal0/utility' } })

    let putBody: unknown = null
    await page.route('**/api/settings', (route) => {
      if (route.request().method() !== 'PUT') return route.continue()
      putBody = route.request().postDataJSON()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          brain_chat: brainChatConfig({ tool_model: 'hal0/utility' }),
          _hal0: {
            apply_plan: { immediate: ['brain_chat.tool_model'], service_restart: {}, manual_restart: [], unknown: [] },
            changeset: {
              changes: [{ path: 'brain_chat.tool_model', before: 'hal0/agent', after: 'hal0/utility', kind: 'changed', apply_class: 'immediate', services: [] }],
              unknown: [],
            },
          },
        }),
      })
    })

    await page.locator('.drawer.open').getByRole('button', { name: /^apply$/i }).click()

    // The exact patch previewed is the exact patch applied — same body,
    // preview and apply agree because they're the same ChangeSet.
    expect(putBody).toEqual({ brain_chat: { tool_model: 'hal0/utility' } })
    await expect(page.locator('.drawer.open')).toHaveCount(0)
    await expect(page.locator('.hal0-toast')).toContainText(/saved/i)
  })
})
