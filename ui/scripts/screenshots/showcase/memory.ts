/**
 * Showcase overlay: the Memory area's bank list and operations queue.
 *
 * The in-bundle mock (ui/src/api/mockFixtures.ts) carries a detailed fact
 * story for a bank called `primary` and five test banks besides. The showcase
 * keeps that story but lists only `primary` and `hermes`, so the bank chips
 * agree with the "2 banks" the health answer reports, and empties the
 * operations queue, so the Operations tab agrees with the clear health verdict.
 */
import type { Page } from '@playwright/test'

import { json } from './core'

/** Exact paths, as regular expressions; shot.ts turns `re:` entries into RegExp. */
export const MEMORY_PASSTHROUGH = ['re:^/api/memory/banks$', 're:^/api/memory/banks/[^/]+/operations$']

export async function installMemory(page: Page) {
  // shape: buildMemoryBanks / MEM_BANKS (ui/src/api/mockFixtures.ts:940-977)
  await page.route(/\/api\/memory\/banks(\?|$)/, (route) =>
    json(route, {
      banks: [
        { bank_id: 'primary', name: 'primary', mission: 'memory of the box and its operator', fact_count: 902 },
        { bank_id: 'hermes', name: 'hermes', mission: 'the bundled home agent', fact_count: 382 },
      ],
      total: 2,
      limit: 100,
      offset: 0,
    }),
  )
  // shape: buildBankOperations (ui/src/api/mockFixtures.ts:1768-1788)
  await page.route(/\/api\/memory\/banks\/[^/]+\/operations(\?|$)/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const bank = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[4] || '')
    return json(route, { bank_id: bank, total: 0, limit: 50, offset: 0, operations: [] })
  })
}
