/**
 * memory-agents-v3 — Memory ▸ Agents (#memory/agents, ADR-0058 decision 12).
 *
 * What reads and writes memory, from evidence: rows built from the tags on each bank, the
 * installed agents and the key holders; the callout for facts tagged `agent:unknown`; the
 * install command and its agent picker; copy feedback; the empty state; the engine not
 * answering; the agents route failing; and the layout at phone width.
 *
 * Forced mock. The bundle answers the bank list (primary, big, hermes, scratch, ingest, empty)
 * and would answer each bank's tags from its fact fixtures, which carry no `agent:` tag. So the
 * tags route is taken out of the bundle's hands (`__hal0MockPassthrough`) and answered here, in
 * the real route's shape (`{items: [{tag, count}]}`). `/api/memory/agents`, `/api/agents` and
 * `/api/bridge/clients` are not in the bundle and always reach `page.route`.
 */
import { mkdirSync } from 'node:fs'

import type { Page } from '@playwright/test'

import { test, expect, json } from '../fixtures/apiMock'
import { routeEngineDown } from '../fixtures/memoryArea'
import { pickRichOption } from '../fixtures/richSelect'

const SHOTS = 'test-results/memory-area'
const HARNESSES = ['claude-code', 'codex', 'opencode', 'pi', 'cursor-cli', 'copilot-cli', 'qwen-code']
const COMMAND = 'hal0 memory agents install <harness> --bridge-key-file hal0-memory.key'
const MINT = 'hal0 bridge add <client> --scope memory'

type Tags = Record<string, Record<string, number>>

const POPULATED: Tags = {
  primary: { 'agent:hermes': 880, 'agent:unknown': 321, 'agent:hermes__hal0-brain': 4, 'kind:durable': 600 },
  big: { 'harness:claude-code': 8975, 'agent:pi': 8105, 'agent:unknown': 12 },
  'coding-agent::hal0': { 'project:hal0': 40 },
}

async function routeEvidence(
  page: Page,
  over: {
    tags?: Tags
    installed?: string[]
    clients?: Array<Record<string, unknown>>
    codingBanks?: string[]
    command?: string
  } = {},
) {
  const tags = over.tags ?? POPULATED
  await page.addInitScript(() => {
    const w = window as unknown as { __hal0MockPassthrough?: unknown[] }
    w.__hal0MockPassthrough = [...(w.__hal0MockPassthrough ?? []), /^\/api\/memory\/banks\/[^/]+\/tags$/]
  })
  await page.route(/\/api\/memory\/banks\/[^/]+\/tags(\?|$)/, (route) => {
    const bank = decodeURIComponent(new URL(route.request().url()).pathname.split('/banks/')[1].split('/')[0])
    const items = Object.entries(tags[bank] ?? {}).map(([tag, count]) => ({ tag, count }))
    return json(route, { items })
  })
  await page.route(/\/api\/agents(\?|$)/, (route) => {
    const agents = (over.installed ?? ['hermes', 'pi']).map((name) => ({
      name,
      installed_at: '2026-06-01T09:00:00Z',
      status: 'installed',
      unit_active: true,
    }))
    return json(route, { agents, count: agents.length })
  })
  await page.route(/\/api\/bridge\/clients(\?|$)/, (route) =>
    json(route, {
      items: over.clients ?? [
        {
          name: 'laptop',
          note: '',
          scopes: ['agents', 'memory'],
          created_at: 1781000000,
          created_by: 'admin',
          revoked_at: null,
          last_seen_at: null,
        },
        {
          name: 'reader',
          note: '',
          scopes: ['agents'],
          created_at: 1781000000,
          created_by: 'admin',
          revoked_at: null,
          last_seen_at: null,
        },
      ],
    }),
  )
  await page.route('**/api/memory/agents', (route) =>
    json(route, {
      banks: over.codingBanks ?? ['coding-agent::hal0'],
      install: {
        harnesses: HARNESSES,
        command: over.command ?? COMMAND,
        mint_command: MINT,
        key_file: 'hal0-memory.key',
        clients: { pi: 'pi-memory' },
      },
      console_url: null,
    }),
  )
}

async function open(page: Page) {
  await page.goto('/#memory/agents')
  await expect(page.getByTestId('ma-agents')).toBeVisible()
}

async function noBoundary(page: Page) {
  await expect(page.getByRole('heading', { name: 'This view hit an error' })).toHaveCount(0)
}

// The dashboard scrolls inside its own pane, so a full-page capture stops at the window's edge.
// For a full-length picture the window is made as tall as the pane's content, then put back.
async function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true })
  const size = page.viewportSize()!
  const extra = await page.evaluate(() => {
    let el: Element | null = document.querySelector('[data-testid="ma-agents"]')
    while (el) {
      if (el.scrollHeight > el.clientHeight + 1 && getComputedStyle(el).overflowY !== 'visible') {
        el.scrollTop = 0
        return el.scrollHeight - el.clientHeight
      }
      el = el.parentElement
    }
    return 0
  })
  await page.setViewportSize({ width: size.width, height: size.height + extra })
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
  await page.setViewportSize(size)
}

const noPageOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

test.describe('Memory ▸ Agents', () => {
  test('lists agents from evidence, each with its bank, count and status', async ({ page }) => {
    await routeEvidence(page)
    await open(page)
    await expect(page.getByTestId('ma-agents')).toHaveAttribute('data-state', 'ready')

    const table = page.getByTestId('ma-agents-table')
    await expect(table.locator('thead th')).toHaveText(['Agent', 'Reads', 'Writes', 'Where its facts are', 'Status'])
    await expect(table.locator('tbody tr')).toHaveCount(4)

    const hermes = page.getByTestId('ma-agents-row-hermes')
    await expect(hermes).toContainText('primary: 880 facts tagged agent:hermes')
    await expect(hermes).toContainText('Conversation turns and checkpoints, private to Hermes.')
    await expect(page.getByTestId('ma-agents-status-hermes')).toHaveText('seen writing')

    await expect(page.getByTestId('ma-agents-row-pi')).toContainText('big: 8,105 facts tagged agent:pi')
    await expect(page.getByTestId('ma-agents-row-claude-code')).toContainText(
      'big: 8,975 facts tagged harness:claude-code',
    )

    // A key with the memory scope and no tagged facts: connected, and the row says what that means.
    await expect(page.getByTestId('ma-agents-status-laptop')).toHaveText('connected')
    await expect(page.getByTestId('ma-agents-row-laptop')).toContainText('Holds a key with the memory scope.')
    // A key without the memory scope is no evidence, and neither is a name nothing points at.
    await expect(page.getByTestId('ma-agents-row-reader')).toHaveCount(0)
    await expect(page.getByTestId('ma-agents-row-codex')).toHaveCount(0)

    await expect(page.getByTestId('ma-agents-summary')).toHaveText('4 agents · 3 seen writing · 1 connected')
    await expect(page.getByTestId('ma-agents-coding-banks')).toContainText('coding-agent::hal0')
    await expect(page.getByTestId('ma-agents-unattributed')).toBeVisible()

    // hal0's own parts: no chip, and brain chat's tagged facts are counted there, not as an agent.
    const builtIn = page.getByTestId('ma-agents-builtin')
    await expect(builtIn.locator('tbody th')).toHaveCount(3)
    await expect(builtIn.locator('.ma-chip')).toHaveCount(0)
    await expect(page.getByTestId('ma-agents-builtin-brain-count')).toHaveText(
      'primary: 4 facts tagged agent:hermes__hal0-brain',
    )
    await expect(page.getByTestId('ma-agents-row-hermes__hal0-brain')).toHaveCount(0)

    // The stub's link to the legacy page is gone.
    await expect(page.locator('[data-testid="ma-agents"] a[href*="memworkspace"]')).toHaveCount(0)
    // Nothing on the tab is a primary action.
    await expect(page.locator('[data-testid="ma-agents"] button.btn:not(.ghost)')).toHaveCount(0)
    await noBoundary(page)
    await shot(page, 'agents-populated')
  })

  test('calls out facts tagged agent:unknown: how many, and on which banks', async ({ page }) => {
    await routeEvidence(page)
    await open(page)
    const card = page.getByTestId('ma-agents-unknown')
    await expect(card.locator('dt')).toHaveText('Tagged agent:unknown')
    await expect(card.locator('.ma-stat-value')).toContainText('333')
    await expect(card.locator('.ma-stat-value')).toContainText('facts')
    await expect(card.locator('.ma-stat-helper')).toHaveText('on 2 banks')
    await expect(card.getByTestId('ma-agents-unknown-bank')).toHaveText(['primary: 321 facts', 'big: 12 facts'])
    await expect(card).toContainText('by a caller that sent no agent name')
    await expect(card.getByTestId('ma-agents-unknown-facts')).toHaveAttribute('href', '#memory/facts?bank=primary')
    await noBoundary(page)
    await shot(page, 'agents-unknown-callout')
  })

  test('has no callout when no fact carries the tag', async ({ page }) => {
    await routeEvidence(page, { tags: { primary: { 'agent:hermes': 3 } } })
    await open(page)
    await expect(page.getByTestId('ma-agents-table')).toBeVisible()
    await expect(page.getByTestId('ma-agents-unknown')).toHaveCount(0)
    await noBoundary(page)
  })

  test('switching the coding agent changes the command', async ({ page }) => {
    await routeEvidence(page)
    await open(page)
    const code = page.getByTestId('ma-agents-command').locator('code')
    await expect(code).toHaveText(COMMAND.replace('<harness>', 'claude-code'))

    const picker = page.getByRole('combobox', { name: 'Coding agent' })
    await expect(picker).toContainText('Claude Code')
    await pickRichOption(picker, 'codex')
    await expect(code).toHaveText(COMMAND.replace('<harness>', 'codex'))
    await expect(picker).toContainText('Codex')
    await expect(page.getByTestId('ma-agents-mint-command').locator('code')).toHaveText(
      'hal0 bridge add codex --scope memory',
    )
    await expect(page.getByTestId('ma-agents-connect-cli')).toContainText('agent:codex')
    await expect(page.getByTestId('ma-agents-connect-what')).toContainText('/api/memory/compat')
    await noBoundary(page)
  })

  test('names another bridge client for an agent whose own name is reserved', async ({ page }) => {
    await routeEvidence(page)
    await open(page)
    await pickRichOption(page.getByRole('combobox', { name: 'Coding agent' }), 'pi')
    await expect(page.getByTestId('ma-agents-mint-command').locator('code')).toHaveText(
      'hal0 bridge add pi-memory --scope memory',
    )
    await expect(page.getByTestId('ma-agents-command').locator('code')).toHaveText(COMMAND.replace('<harness>', 'pi'))
    await noBoundary(page)
  })

  test('copying the command announces it, and only afterwards', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await routeEvidence(page)
    await open(page)
    const box = page.getByTestId('ma-agents-command')
    const status = box.getByRole('status')
    await expect(status).toHaveText('')
    await box.getByRole('button', { name: 'Copy the command for Claude Code' }).click()
    await expect(status).toHaveText('Copied')
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(COMMAND.replace('<harness>', 'claude-code'))
    await noBoundary(page)
  })

  test('says what will appear when no agent has been seen, and leads to the connect card', async ({ page }) => {
    await routeEvidence(page, { tags: {}, installed: [], clients: [], codingBanks: [] })
    await open(page)
    await expect(page.getByTestId('ma-agents')).toHaveAttribute('data-state', 'empty')
    const empty = page.getByTestId('ma-agents-empty')
    await expect(empty.getByRole('heading')).toHaveText('No agents seen yet')
    await expect(empty).toContainText('once a bank holds a fact tagged with its name')
    await expect(page.getByTestId('ma-agents-table')).toHaveCount(0)
    await expect(page.getByTestId('ma-agents-unknown')).toHaveCount(0)
    await expect(page.getByTestId('ma-agents-command')).toBeVisible()

    await page.getByTestId('ma-agents-to-connect').click()
    await expect(page.getByRole('heading', { name: 'Connect a coding agent' })).toBeFocused()
    await noBoundary(page)
    await shot(page, 'agents-empty')
  })

  test('lists no agent while the engine does not answer, and still offers the command', async ({ page }) => {
    await routeEvidence(page)
    await routeEngineDown(page)
    await open(page)
    await expect(page.getByTestId('ma-agents')).toHaveAttribute('data-state', 'unreachable')
    await expect(page.getByTestId('ma-agents-unreachable')).toBeVisible()
    await expect(page.getByTestId('ma-agents-table')).toHaveCount(0)
    await expect(page.getByTestId('ma-agents-command')).toBeVisible()
    await noBoundary(page)
  })

  test('the agents route failing is an inline message with Check again, never raw output', async ({ page }) => {
    await routeEvidence(page)
    let fail = true
    await page.route('**/api/memory/agents', (route) =>
      fail
        ? json(route, { error: { code: 'internal', message: 'boom', details: {} } }, 500)
        : json(route, { banks: [], install: { harnesses: HARNESSES, command: COMMAND }, console_url: null }),
    )
    await open(page)
    const error = page.getByTestId('ma-agents-connect-error')
    await expect(error).toBeVisible({ timeout: 15_000 })
    await expect(error.getByRole('heading')).toHaveText('The install command did not load')
    await expect(page.getByTestId('ma-agents')).not.toContainText('boom')
    await expect(page.getByTestId('ma-agents')).not.toContainText('{')
    // The agents are read from the tags, so they are still listed.
    await expect(page.getByTestId('ma-agents-row-hermes')).toBeVisible()

    fail = false
    await page.getByTestId('ma-agents-connect-recheck').click()
    await expect(page.getByTestId('ma-agents-command')).toBeVisible()
    await noBoundary(page)
  })

  test('fits a 390 px screen: the tables scroll inside their cards, the page does not', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await routeEvidence(page)
    await open(page)
    await expect(page.getByTestId('ma-agents-table')).toBeVisible()
    expect(await noPageOverflow(page)).toBe(true)
    for (const name of ['Agents table', 'Built into hal0 table']) {
      const region = page.getByRole('region', { name })
      expect(await region.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
    }
    // The command wraps inside its box.
    const box = page.getByTestId('ma-agents-command')
    expect(await box.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
    // Opening the picker does not push the page sideways either.
    await page.getByRole('combobox', { name: 'Coding agent' }).click()
    await expect(page.getByRole('option')).toHaveCount(HARNESSES.length)
    expect(await noPageOverflow(page)).toBe(true)
    await page.keyboard.press('Escape')
    await noBoundary(page)
    await shot(page, 'agents-390')
  })
})
