// What the Memory area's component tests feed the mocked hooks: one engine answer, a bank list,
// per-bank stats, and the answers of the graph-status and models routes. Not imported by the app.
import type { BankStats, MemoryBank, MemoryEngine, MentalModel } from '@/api/hooks/useHindsight'
import { bankRows, bankTotals, engineState, type BankStatsEntry } from './area-pure'
import { parseMemoryRoute, type MemoryRoute } from './memory-route-pure'

/** A query result as the components read it. */
export interface FakeQuery<T> {
  data?: T
  isError?: boolean
  isFetching?: boolean
  error?: { message: string } | null
  refetch?: () => void
}

/**
 * The `area` object MemoryArea hands to a tab, built the way MemoryArea builds it, for a test
 * that mounts a tab on its own.
 */
export function makeArea(
  over: {
    hash?: string
    engineQuery?: FakeQuery<MemoryEngine>
    banksQuery?: FakeQuery<{ banks: MemoryBank[]; total?: number }>
    stats?: BankStatsEntry[]
    replaceRoute?: (patch: Partial<MemoryRoute>) => void
  } = {},
) {
  const engineQuery = over.engineQuery ?? { data: ENGINE }
  const banksQuery = over.banksQuery ?? { data: { banks: BANKS } }
  const banks = banksQuery.data?.banks ?? []
  const stats: BankStatsEntry[] =
    over.stats ?? banks.map((b) => ({ bankId: b.bank_id, stats: STATS[b.bank_id] ?? null }))
  const rows = bankRows(banks, stats)
  return {
    route: parseMemoryRoute(over.hash ?? '#memory/health'),
    replaceRoute: over.replaceRoute ?? (() => {}),
    engine: engineState(engineQuery),
    engineQuery,
    banksQuery,
    banks,
    banksLoading: banksQuery.data === undefined && !banksQuery.isError,
    banksFailed: !!banksQuery.isError && banksQuery.data === undefined,
    statsLoading: stats.some((entry) => entry.isLoading),
    rows,
    totals: bankTotals(rows, banksQuery.data?.total),
  }
}

export const ENGINE: MemoryEngine = {
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '1.2.0',
  features: null,
  banks_total: 3,
}

/** Real id shapes: a double colon, a dot, mixed case. */
export const BANKS: MemoryBank[] = [
  { bank_id: 'shared' },
  { bank_id: 'Hal0::Agents' },
  { bank_id: 'coding-agent::ultrareview-1.4' },
]

export function bankStats(bank: string, over: Partial<BankStats> = {}): BankStats {
  return {
    bank_id: bank,
    total_nodes: 0,
    total_links: 0,
    total_documents: 0,
    nodes_by_fact_type: {},
    links_by_link_type: {},
    pending_operations: 0,
    failed_operations: 0,
    operations_by_status: {},
    last_consolidated_at: null,
    pending_consolidation: 0,
    failed_consolidation: 0,
    total_observations: 0,
    ...over,
  }
}

export const STATS: Record<string, BankStats> = {
  shared: bankStats('shared', { total_nodes: 3000, total_documents: 410 }),
  'Hal0::Agents': bankStats('Hal0::Agents', { total_nodes: 24530, total_documents: 4100 }),
  'coding-agent::ultrareview-1.4': bankStats('coding-agent::ultrareview-1.4', {
    total_nodes: 12,
    total_documents: 2,
  }),
}

export const GRAPH = {
  enabled: true,
  extraction_slot: 'memory',
  mode: 'dedicated',
  slot_resolves: true,
  available_slots: ['agent', 'memory'],
}

/** The embedder record as GET /api/memory/models carries it. */
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
    conflict: null,
    ...over,
  }
}

export function modelsAnswer(embeddings: unknown = embedderRecord()) {
  return {
    embeddings,
    reranker: { provider: 'local', model: 'hs-rerank-1' },
    decision: { enabled: false, provider: 'local', model: 'decider', reachable: false },
  }
}

export function mentalModel(over: Partial<MentalModel> = {}): MentalModel {
  return {
    id: 'mm-1',
    name: 'Model',
    source_query: 'q',
    is_stale: false,
    last_refreshed_at: '2026-11-05T08:00:00Z',
    last_refresh_failed_at: null,
    ...over,
  }
}
