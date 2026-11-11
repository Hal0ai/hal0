/**
 * Route helpers for the Memory area's specs (#memory/<tab>).
 *
 * The suite runs under the forced mock, which answers most `/api/memory/*` GETs in the bundle
 * (six banks: primary, big, hermes, scratch, ingest, empty; `ingest` has 7 operations waiting and
 * 1 failed). Two of the routes Health reads are NOT in the bundle's allow-list and reach
 * `page.route`: `/api/memory/models` (the catch-all answers `{}`) and `/api/memory/graph/status`
 * (the default fixture answers with slot routing off). The helpers below set those. A
 * bundle-answered route has to be named in `window.__hal0MockPassthrough` before a `page.route`
 * for it is ever reached.
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

/**
 * The all-clear strip: a consistent embedder record and extraction resolving. Every other figure
 * on Health is the health answer's (the bundle's, by `window.__hal0MockMemoryHealth`).
 */
export async function routeAllClear(page: Page) {
  await routeModels(page)
  await routeGraph(page)
}

/** Turn the memory subsystem off, as `[memory].enabled = false` does. Call before `page.goto`. */
export async function memoryOff(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0MockMemoryEnabled?: boolean }).__hal0MockMemoryEnabled = false
  })
}
