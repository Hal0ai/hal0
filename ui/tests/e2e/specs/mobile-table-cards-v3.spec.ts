/**
 * mobile-table-cards-v3 — the tables that used to scroll sideways at phone width are card rows.
 *
 * mobile-layout-v3 tolerates content inside its own horizontal scroller, which let four tables
 * stay awkward on a phone: Memory ▸ Agents (and its built-in parts table), Memory ▸ Operations
 * (Queue by bank), and the activity table on Overview ▸ Serving now and Slots ▸ NPU. At 720px and
 * under each row is a card: name and status on the first line, every other value a labelled pair
 * under it. Above 720px they are still tables.
 *
 * Forced mock; the Agents tab's evidence routes are answered here (see memory-agents-v3).
 */
import { test, expect, MOCK_DATA, json, type Page, type Locator } from '../fixtures/apiMock'
import { NPU_OCCUPANCY_MOCK, NPU_XDNA_SLOT } from '../fixtures/mock-data'

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 900 }

const TAGS: Record<string, Record<string, number>> = {
  primary: { 'agent:hermes': 880, 'agent:unknown': 321 },
  big: { 'harness:claude-code': 8975, 'agent:pi': 8105 },
}

async function routeAgents(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0MockPassthrough?: unknown[] }).__hal0MockPassthrough = [
      /^\/api\/memory\/banks\/[^/]+\/tags$/,
    ]
  })
  await page.route(/\/api\/memory\/banks\/[^/]+\/tags(\?|$)/, (route) => {
    const bank = decodeURIComponent(new URL(route.request().url()).pathname.split('/banks/')[1].split('/')[0])
    return json(route, { items: Object.entries(TAGS[bank] ?? {}).map(([tag, count]) => ({ tag, count })) })
  })
  await page.route(/\/api\/agents(\?|$)/, (route) =>
    json(route, { agents: [{ name: 'hermes', installed_at: '2026-06-01T09:00:00Z', status: 'installed', unit_active: true }], count: 1 }),
  )
  await page.route(/\/api\/bridge\/clients(\?|$)/, (route) => json(route, { items: [] }))
  await page.route('**/api/memory/agents', (route) =>
    json(route, {
      banks: [],
      install: { harnesses: ['claude-code'], command: 'c', mint_command: 'm', key_file: 'k', clients: {} },
      console_url: null,
    }),
  )
}

async function routeNpu(page: Page) {
  await page.addInitScript(
    ({ slots, occ }) => {
      let real: any
      Object.defineProperty(window, 'HAL0_DATA', {
        configurable: true,
        get: () => real,
        set(v) {
          real = v
          if (v && typeof v === 'object') {
            v.slots = slots
            v.npu_occupancy = occ
          }
        },
      })
    },
    { slots: [MOCK_DATA.slots.find((s: any) => s.name === 'npu'), NPU_XDNA_SLOT], occ: NPU_OCCUPANCY_MOCK },
  )
}

type Place = {
  name: string
  hash: string
  setup?: (page: Page) => Promise<void>
  table: (page: Page) => Locator
  wrap: (page: Page) => Locator
  labels: string[]
}

const PLACES: Place[] = [
  {
    name: 'Memory ▸ Agents',
    hash: '#memory/agents',
    setup: routeAgents,
    table: (p) => p.getByTestId('ma-agents-table'),
    wrap: (p) => p.getByRole('region', { name: 'Agents table' }),
    labels: ['Reads', 'Writes', 'Where its facts are', 'Status'],
  },
  {
    name: 'Memory ▸ Agents (built into hal0)',
    hash: '#memory/agents',
    setup: routeAgents,
    table: (p) => p.locator('.ma-agents-builtin-table'),
    wrap: (p) => p.getByRole('region', { name: 'Built into hal0 table' }),
    labels: ['Reads', 'Writes', 'Facts carrying its tag'],
  },
  {
    name: 'Memory ▸ Operations (queue by bank)',
    hash: '#memory/operations',
    table: (p) => p.locator('.ma-ops-banks-table'),
    wrap: (p) => p.getByRole('region', { name: 'Queue by bank table' }),
    labels: ['Waiting', 'Failed', 'Oldest waiting'],
  },
  {
    name: 'Overview ▸ Serving now',
    hash: '#dashboard',
    table: (p) => p.locator('table.ov-stable'),
    wrap: (p) => p.locator('.act-tablewrap').first(),
    labels: ['Model', 'State', 'tok/s', 'Peak', 'Req', 'TTFT p50', 'Ctx'],
  },
  {
    name: 'Slots ▸ NPU',
    hash: '#slots/npu',
    setup: routeNpu,
    table: (p) => p.getByTestId('npu-lanes-table'),
    wrap: (p) => p.locator('.act-tablewrap').first(),
    labels: ['Role · model', 'Cols', 'tok/s', 'Peak', 'Req', 'TTFT p50', 'Last used'],
  },
]

const firstRow = (table: Locator) => table.locator('tbody > tr:not(.ov-lane-h):not(.ov-stopped-row)').first()

async function open(page: Page, place: Place) {
  await place.setup?.(page)
  await page.goto('/' + place.hash)
  await expect(place.table(page).first()).toBeVisible({ timeout: 10_000 })
}

test.describe('phone width', () => {
  test.use({ viewport: PHONE })

  for (const place of PLACES) {
    test(`${place.name}: rows are cards, nothing scrolls sideways`, async ({ page }) => {
      await open(page, place)
      const wrap = place.wrap(page)
      expect(await wrap.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)

      const row = firstRow(place.table(page).first())
      await row.scrollIntoViewIfNeeded()
      await expect(row).toBeVisible()
      // Every value of the row is visible and carries its label.
      for (const label of place.labels) {
        const cell = row.locator(`td[data-label="${label}"]`)
        await expect(cell).toHaveCount(1)
        await expect(cell).toBeVisible()
        const drawn = await cell.evaluate((el) => {
          const cs = getComputedStyle(el, '::before')
          return { text: cs.content, w: parseFloat(cs.width) }
        })
        expect(drawn.text).toContain(label)
      }
      // The header row is clipped from sight but still in the page for screen readers.
      const header = place.table(page).first().locator('thead')
      const box = await header.boundingBox()
      expect(box === null || (box.width <= 1 && box.height <= 1)).toBe(true)
      await expect(place.table(page).first().locator('thead th').first()).toHaveAttribute('role', 'columnheader')
    })
  }

  test('Overview: the row opens its slot, and logs / unload are reachable without hover', async ({ page }) => {
    await open(page, PLACES[3])
    const row = page.locator('tr.ov-srow').first()
    await row.scrollIntoViewIfNeeded()
    await expect(row.getByRole('button', { name: 'logs' })).toBeVisible()
    await expect(row.getByRole('button', { name: 'unload' })).toBeVisible()
    const name = (await row.locator('.ov-nm').textContent())!.trim()
    await row.locator('.ov-nm').click()
    await expect(page).toHaveURL(new RegExp(`#slots/${name}$`))
  })

  test('Memory ▸ Operations: the bank link in a card still opens that bank', async ({ page }) => {
    await open(page, PLACES[2])
    const link = firstRow(PLACES[2].table(page)).locator('th a')
    await link.scrollIntoViewIfNeeded()
    const bank = (await link.textContent())!.trim()
    await link.click()
    await expect(page).toHaveURL(new RegExp(`#memory/operations.*${bank}`))
  })

  test('NPU: the lane name and its live rate share the first line', async ({ page }) => {
    await open(page, PLACES[4])
    const row = firstRow(PLACES[4].table(page))
    const name = await row.locator('td.tc-name').boundingBox()
    const rate = await row.locator('td[data-label="tok/s"]').boundingBox()
    expect(Math.abs(name!.y - rate!.y)).toBeLessThanOrEqual(2)
  })
})

test.describe('desktop width', () => {
  test.use({ viewport: DESKTOP })

  for (const place of PLACES) {
    test(`${place.name}: still a table`, async ({ page }) => {
      await open(page, place)
      const table = place.table(page).first()
      await expect(table.locator('thead th').first()).toBeVisible()
      const row = firstRow(table)
      await expect(row).toBeVisible()
      const display = await table.evaluate((el) => getComputedStyle(el).display)
      expect(display).toBe('table')
      // Cells sit side by side on one line.
      const ys = await row.locator('> td, > th').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
      expect(ys.length).toBeGreaterThan(2)
      expect(Math.max(...ys) - Math.min(...ys)).toBeLessThanOrEqual(12)
      // No label is drawn.
      const label = await row.locator('td[data-label]').first().evaluate((el) => getComputedStyle(el, '::before').content)
      expect(['none', 'normal']).toContain(label)
    })
  }
})
