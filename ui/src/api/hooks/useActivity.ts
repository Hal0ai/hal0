// hal0 v3 dashboard — activity-log hook (durable structured audit trail).
//
// Backs the sidebar ActivityLog pane on the Slots page. Mirrors the
// `useLogs` journal hook in shape, against the `/api/activity` surface:
//   - GET  /api/activity         — historical backfill (one-shot, paged)
//   - SSE  /api/activity/stream   — durable backfill then live tail
//   - GET  /api/activity/export   — file download (csv|json), filters honoured
//
// Filter semantics: every filter (since/category/action/severity/outcome/
// actor/kind/search/limit) is forwarded to the backend so the wire payload
// is already filtered server-side. Callers MAY also filter the returned
// records client-side for instant feedback without re-opening the SSE.
//
// Epoch handling: each payload carries an `epoch` (per-process id). When it
// CHANGES between frames the backend restarted, so we reset the cursor to 0
// and clear the ring — otherwise a stale `since` would silently skip the
// backlog (the footer-blank-after-restart bug). The SSE reconnect uses the
// same capped-backoff pattern as useLogs.

import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

/** A single activity record — mirrors the backend record shape. */
export interface ActivityRecord {
  id: number
  ts: string
  kind: 'action' | 'event'
  category: string
  /** Dotted action, e.g. "slot.edit_config". */
  action: string
  target: string | null
  actor: 'dashboard' | 'cli' | string // also "mcp:<agent>" | "system"
  severity: 'info' | 'warn' | 'error' | 'ok'
  outcome: 'ok' | 'error' | 'pending' | null
  message: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  error: string | null
  duration_ms: number | null
  request_id: string | null
}

export type ActivitySeverity = 'info' | 'warn' | 'error' | 'ok'

/** Newest-first ring cap — bounded so a burst can't make the pane a
 *  firehose. The export button is the escape hatch for full history. */
export const ACTIVITY_RING_MAX = 200
/** Debounce SSE reconnect on rapid filter chip toggling. */
const SSE_RECONNECT_DEBOUNCE_MS = 200

export interface ActivityFilters {
  since?: number | null
  category?: string | null
  action?: string | null
  severity?: ActivitySeverity | null
  outcome?: string | null
  actor?: string | null
  kind?: 'action' | 'event' | null
  search?: string | null
  limit?: number | null
}

/** Build the shared `?…` query string from a filter set. */
export function buildActivityQuery(opts: ActivityFilters): string {
  const params = new URLSearchParams()
  if (opts.since != null) params.set('since', String(opts.since))
  if (opts.category) params.set('category', opts.category)
  if (opts.action) params.set('action', opts.action)
  if (opts.severity) params.set('severity', opts.severity)
  if (opts.outcome) params.set('outcome', opts.outcome)
  if (opts.actor) params.set('actor', opts.actor)
  if (opts.kind) params.set('kind', opts.kind)
  if (opts.search) params.set('search', opts.search)
  if (opts.limit != null) params.set('limit', String(opts.limit))
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

/** A direct (non-hook) URL builder for the export link / download. */
export function activityExportUrl(fmt: 'csv' | 'json', filters: ActivityFilters): string {
  const params = new URLSearchParams(buildActivityQuery(filters).replace(/^\?/, ''))
  params.set('fmt', fmt)
  return `${ENDPOINTS.activityExport}?${params.toString()}`
}

export interface ActivityEnvelope {
  records: ActivityRecord[]
  next_since: number | null
  epoch: string | null
}

export interface UseActivityHistoricalOptions extends ActivityFilters {
  /** When false the query is disabled. */
  enabled?: boolean
}

/**
 * One-shot historical backfill. Returns the parsed envelope so callers can
 * advance a cursor (`next_since`) and detect an epoch change.
 */
export function useActivityHistorical(opts: UseActivityHistoricalOptions = {}) {
  const { enabled = true, ...filters } = opts
  return useQuery({
    queryKey: ['activity', 'historical', filters],
    enabled,
    queryFn: async (): Promise<ActivityEnvelope> => {
      const qs = buildActivityQuery(filters)
      const body = await apiGet<unknown>(`${ENDPOINTS.activity}${qs}`)
      // Guard against a bare-array (older/mocked) payload.
      if (Array.isArray(body)) {
        return { records: body as ActivityRecord[], next_since: null, epoch: null }
      }
      const env = (body ?? {}) as Partial<ActivityEnvelope>
      return {
        records: Array.isArray(env.records) ? env.records : [],
        next_since: env.next_since ?? null,
        epoch: env.epoch ?? null,
      }
    },
  })
}

export interface UseActivityStreamOptions extends ActivityFilters {
  /** When false, no SSE connection is opened. Defaults to true. */
  follow?: boolean
}

export interface ActivityStreamResult {
  /** Live ring, NEWEST-FIRST, capped at ACTIVITY_RING_MAX. */
  records: ActivityRecord[]
  /** True when the SSE is down / reconnecting. */
  disconnected: boolean
  /** Latest epoch seen — exposed for debugging / display. */
  epoch: string | null
}

/**
 * SSE tail. The backend replays a durable backfill then live-tails, all
 * filtered server-side. Returns the ring newest-first so the pane renders
 * most-recent-at-top without re-sorting on every frame.
 *
 * Reconnects on any filter change with a 200ms debounce so a fast cascade
 * of chip clicks coalesces into one new connection. On an `epoch` change
 * the ring is cleared (backend restarted — the stream replays fresh).
 */
export function useActivityStream(opts: UseActivityStreamOptions = {}): ActivityStreamResult {
  const { follow = true, ...filters } = opts
  const [records, setRecords] = useState<ActivityRecord[]>([])
  const [disconnected, setDisconnected] = useState(false)
  const [epoch, setEpoch] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const epochRef = useRef<string | null>(null)
  const errorCountRef = useRef(0)

  const push = (record: ActivityRecord) => {
    setRecords((prev) => {
      // Dedup by id (durable backfill can overlap a reconnect replay).
      if (record.id != null && prev.some((r) => r.id === record.id)) return prev
      const next = [record, ...prev]
      return next.length > ACTIVITY_RING_MAX ? next.slice(0, ACTIVITY_RING_MAX) : next
    })
  }

  // Stable filter key so the effect only re-subscribes on a real change.
  const filterKey = JSON.stringify(filters)

  useEffect(() => {
    if (!follow) {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
      return
    }

    let cancelled = false
    let backoffTimer: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      if (cancelled) return
      try {
        const url = `${ENDPOINTS.activityStream}${buildActivityQuery(filters)}`
        esRef.current = new EventSource(url)
      } catch {
        setDisconnected(true)
        return
      }
      const es = esRef.current
      if (!es) return
      es.onmessage = (evt) => {
        try {
          const frame = JSON.parse(evt.data) as { record?: ActivityRecord; epoch?: string }
          const ep = frame?.epoch ?? null
          if (ep && ep !== epochRef.current) {
            // Backend restarted → fresh stream. Reset cursor + ring.
            if (epochRef.current != null) setRecords([])
            epochRef.current = ep
            setEpoch(ep)
          }
          const record = frame?.record
          if (record && record.ts && record.message != null) push(record)
        } catch {
          // ignore malformed frame
        }
      }
      es.onerror = () => {
        setDisconnected(true)
        errorCountRef.current += 1
        if (esRef.current) {
          esRef.current.close()
          esRef.current = null
        }
        const delay = Math.min(1000 * 2 ** Math.min(errorCountRef.current - 1, 4), 16_000)
        backoffTimer = setTimeout(connect, delay)
      }
      es.onopen = () => {
        setDisconnected(false)
        errorCountRef.current = 0
      }
    }

    const debounceTimer = setTimeout(connect, SSE_RECONNECT_DEBOUNCE_MS)

    return () => {
      cancelled = true
      clearTimeout(debounceTimer)
      if (backoffTimer) clearTimeout(backoffTimer)
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
    }
    // filterKey collapses the filter object into a stable dep; follow
    // toggles the connection on/off.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [follow, filterKey])

  return { records, disconnected, epoch }
}
