/**
 * Route helpers for the Memory area's specs (#memory/<tab>).
 *
 * The suite runs under the forced mock, which answers most `/api/memory/*` GETs in the bundle
 * (six banks: primary, big, hermes, scratch, ingest, empty; `ingest` has 7 operations waiting and
 * 1 failed). Two of the routes Health reads are NOT in the bundle's allow-list and reach
 * `page.route`: `/api/memory/models` (the catch-all answers `{}`) and `/api/memory/graph/status`
 * (the default fixture answers with slot routing off). The helpers below set those, and replace
 * bundle-answered routes where a state needs it (a bundle-answered route has to be named in
 * `window.__hal0MockPassthrough` before a `page.route` for it is ever reached).
 */
import type { Page } from '@playwright/test'

import { json } from './apiMock'

/** `GET /api/memory/graph/status` with extraction routed to a slot that resolves. */
export const GRAPH_RESOLVES = {
  enabled: true,
  extraction_slot: 'memory',
  mode: 'dedicated',
  slot_resolves: true,
  available_slots: ['agent', 'memory'],
  llm_timeout_s: 300,
  extraction_limits: { max_concurrent: 2, max_tokens: 4096, llm_retries: 1, task_retries: 2, retry_backoff_s: 120 },
  in_flight: 0,
  builds_ok: 0,
  errors: 0,
  last_built_at: null,
  last_error: null,
}

/** The embedder record as `GET /api/memory/models` carries it; clean unless overridden. */
export function embedderRecord(over: Record<string, unknown> = {}) {
  return {
    provider: 'local',
    model: 'hs-embed-1',
    recorded: 'hs-embed-1',
    recorded_provider: 'local',
    configured: 'hs-embed-1',
    configured_provider: 'local',
    mixed: false,
    needs_switch: false,
    source: 'record',
    recorded_at: '2026-06-01T09:00:00Z',
    effective: { provider: 'local', model: 'hs-embed-1', source: 'process' },
    conflict: null,
    record_error: null,
    store_unavailable: null,
    ...over,
  }
}

/** The store was built with the engine default and settings ask for another model. */
export const RECORD_UPGRADED = { recorded: 'BAAI/bge-small-en-v1.5', needs_switch: true }
/** The store may hold vectors from two models. */
export const RECORD_MIXED = { mixed: true, needs_switch: true }
/** The running engine is on another model than the record says. */
export const RECORD_CONFLICT = { conflict: { live: { provider: 'local', model: 'BAAI/bge-small-en-v1.5' } } }

export async function routeModels(page: Page, record: Record<string, unknown> = {}) {
  await page.route('**/api/memory/models', (route) =>
    json(route, {
      embeddings: embedderRecord(record),
      reranker: { provider: 'local', model: 'hs-rerank-1' },
      decision: { enabled: false, provider: 'local', model: 'decider', reachable: false },
    }),
  )
}

export async function routeGraph(page: Page, over: Record<string, unknown> = {}) {
  await page.route('**/api/memory/graph/status', (route) => json(route, { ...GRAPH_RESOLVES, ...over }))
}

const FACTS: Record<string, number> = { primary: 26, big: 600, hermes: 612, scratch: 88, ingest: 4310, empty: 0 }

/**
 * Replace every bank's stats with ones where nothing has failed (`ingest` keeps its 7 waiting
 * operations: waiting is normal). With `routeModels` and `routeGraph` this is the all-clear box.
 */
export async function routeCleanStats(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
      /^\/api\/memory\/banks\/[^/]+\/stats$/,
    ]
  })
  await page.route(/\/api\/memory\/banks\/[^/]+\/stats$/, (route) => {
    const bank = decodeURIComponent(route.request().url().split('/banks/')[1].split('/')[0])
    const facts = FACTS[bank] ?? 10
    return json(route, {
      bank_id: bank,
      total_nodes: facts,
      total_links: facts * 2,
      total_documents: Math.round(facts / 6),
      nodes_by_fact_type: {},
      links_by_link_type: {},
      pending_operations: bank === 'ingest' ? 7 : 0,
      failed_operations: 0,
      operations_by_status: {},
      last_consolidated_at: null,
      pending_consolidation: 0,
      failed_consolidation: 0,
      total_observations: 0,
    })
  })
}

/** The all-clear box: a consistent embedder record, extraction resolving, nothing failed. */
export async function routeAllClear(page: Page) {
  await routeModels(page)
  await routeGraph(page)
  await routeCleanStats(page)
}

/** The engine does not answer: its own check says so, and the bank list fails. */
export async function routeEngineDown(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
      '/api/memory/engine',
      '/api/memory/banks',
    ]
  })
  await page.route('**/api/memory/engine', (route) =>
    json(route, {
      enabled: true,
      engine: 'hindsight',
      reachable: false,
      version: null,
      features: null,
      banks_total: null,
    }),
  )
  await page.route('**/api/memory/banks**', (route) =>
    json(
      route,
      { error: { code: 'memory.engine_unreachable', message: 'hindsight-api is not responding', details: {} } },
      503,
    ),
  )
}

/** Turn the memory subsystem off, as `[memory].enabled = false` does. Call before `page.goto`. */
export async function memoryOff(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0MockMemoryEnabled?: boolean }).__hal0MockMemoryEnabled = false
  })
}
