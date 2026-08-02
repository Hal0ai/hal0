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
/** Coalesce a burst of SSE frames (the connect backfill) into one render. */
const FRAME_FLUSH_MS = 50

// Module-level ring cache keyed by filter set. The ActivityLog sidebar is
// mounted inside several early-return branches of SlotsView (loading / empty
// / populated) and inside the tab switch, so it UNMOUNTS on every state or
// sub-tab transition. Without this cache each remount reset the ring to []
// and forced a fresh SSE backfill — the pane visibly "forgot" recent
// activity. Seeding useState from (and writing through to) this cache makes
// the ring survive remounts and even navigating away and back.
const _ringCache = new Map<string, ActivityRecord[]>()
const _epochCache = new Map<string, string>()

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

/**
 * Recent-activity poll for the dashboard Activity card (dashboard-redesign).
 *
 * Deliberately NOT the SSE stream: the card shows only the most recent
 * handful of records, and opening an EventSource against a backend (or
 * mock) that answers non-`text/event-stream` logs a browser console error
 * on every page mount. A polled GET is quieter and fail-soft: 404 /
 * network error / bad shape → empty list, never a throw. Records are
 * returned NEWEST-FIRST.
 */
export function useActivityRecent(limit = 20) {
  return useQuery<ActivityRecord[]>({
    queryKey: ['activity', 'recent', limit],
    queryFn: async () => {
      let res: Response
      try {
        res = await fetch(`${ENDPOINTS.activity}?limit=${limit}`, {
          headers: { Accept: 'application/json' },
        })
      } catch {
        return []
      }
      if (!res.ok) return []
      try {
        const body = (await res.json()) as unknown
        const records = Array.isArray(body)
          ? (body as ActivityRecord[])
          : Array.isArray((body as Partial<ActivityEnvelope>)?.records)
            ? (body as ActivityEnvelope).records
            : []
        // Defensive newest-first ordering (backend backfill is oldest→newest).
        return [...records]
          .filter((r) => r && r.ts != null && r.message != null)
          .sort((a, b) => (b.id ?? 0) - (a.id ?? 0))
      } catch {
        return []
      }
    },
    refetchInterval: 5_000,
    retry: false,
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
  // Stable filter key so the effect only re-subscribes on a real change,
  // and so the persistent ring cache is scoped per filter set.
  const filterKey = JSON.stringify(filters)
  const [records, setRecords] = useState<ActivityRecord[]>(
    () => _ringCache.get(filterKey) ?? [],
  )
  const [disconnected, setDisconnected] = useState(false)
  const [epoch, setEpoch] = useState<string | null>(() => _epochCache.get(filterKey) ?? null)
  const esRef = useRef<EventSource | null>(null)
  const epochRef = useRef<string | null>(_epochCache.get(filterKey) ?? null)
  const errorCountRef = useRef(0)
  // Resume cursor — the highest activity id this filter set has already seen.
  //
  // Without it EVERY connect() asked the backend for `since=0`, i.e. a full
  // durable backfill (up to the server's 1000-row cap) streamed one SSE frame
  // at a time. ActivityLog is mounted in several mutually-exclusive branches
  // of SlotsView (loading skeleton / empty / populated), so a normal page load
  // unmounts and remounts it as soon as /api/slots resolves — two full
  // replays back-to-back, which is exactly the "activity log floods on load
  // then settles" symptom. The id-dedup below made the second replay a no-op
  // *semantically*, but only after every one of its frames had been pushed
  // through setRecords. Resuming from the cursor means a reconnect asks only
  // for what it has not already got.
  const sinceRef = useRef<number>(0)
  // Re-seed whenever the filter set changes: each filter set has its own ring.
  const seededForRef = useRef<string | null>(null)
  if (seededForRef.current !== filterKey) {
    seededForRef.current = filterKey
    const cached = _ringCache.get(filterKey) ?? []
    sinceRef.current = cached.reduce((mx, r) => (r.id != null && r.id > mx ? r.id : mx), 0)
  }

  // Frame batching: the connect backfill arrives as one SSE frame per record
  // (up to the ring cap), and a setState per frame re-rendered the whole
  // slots page once per record — the "activity log blocks the page load"
  // jank. Frames buffer here and flush through ONE setRecords per
  // FRAME_FLUSH_MS window instead.
  const pendingRef = useRef<ActivityRecord[]>([])
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Merge a frame batch into a newest-first ring: dedup by id (durable
  // backfill can overlap a reconnect replay, and a remount rehydrates from
  // the cache before the SSE replays), clamp to the cap. Null = no change.
  const mergeBatch = (
    prev: ActivityRecord[],
    batch: ActivityRecord[],
  ): ActivityRecord[] | null => {
    const seen = new Set(prev.map((r) => r.id))
    const add: ActivityRecord[] = []
    for (const r of batch) {
      if (r.id != null) {
        if (seen.has(r.id)) continue
        seen.add(r.id)
      }
      add.push(r)
    }
    if (!add.length) return null
    // Frames arrive oldest→newest; the ring is newest-first.
    add.reverse()
    const next = [...add, ...prev]
    return next.length > ACTIVITY_RING_MAX ? next.slice(0, ACTIVITY_RING_MAX) : next
  }

  const flush = () => {
    flushTimerRef.current = null
    const batch = pendingRef.current
    pendingRef.current = []
    if (!batch.length) return
    setRecords((prev) => {
      const merged = mergeBatch(prev, batch)
      if (merged == null) return prev
      _ringCache.set(filterKey, merged)
      return merged
    })
  }

  const push = (record: ActivityRecord) => {
    if (record.id != null && record.id > sinceRef.current) sinceRef.current = record.id
    pendingRef.current.push(record)
    if (flushTimerRef.current == null) {
      flushTimerRef.current = setTimeout(flush, FRAME_FLUSH_MS)
    }
  }

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
        // Resume from the cursor unless the caller pinned its own `since`
        // (then that value IS the contract and must not be overridden).
        // Always cap the connect backfill at the ring size (unless the
        // caller pinned a limit): the server would otherwise replay its
        // full 1000-row durable backlog one frame at a time, most of which
        // the ring immediately discards.
        const effective =
          filters.limit != null ? filters : { ...filters, limit: ACTIVITY_RING_MAX }
        const query =
          filters.since != null || sinceRef.current <= 0
            ? buildActivityQuery(effective)
            : buildActivityQuery({ ...effective, since: sinceRef.current })
        const url = `${ENDPOINTS.activityStream}${query}`
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
            // Backend restarted → fresh stream. Reset cursor + ring (and the
            // persistent cache, else a stale ring would rehydrate on remount).
            if (epochRef.current != null) {
              setRecords([])
              pendingRef.current = []
              _ringCache.delete(filterKey)
              // Ids restart with the new epoch — the old cursor is meaningless.
              sinceRef.current = 0
            }
            epochRef.current = ep
            _epochCache.set(filterKey, ep)
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
      if (flushTimerRef.current != null) {
        clearTimeout(flushTimerRef.current)
        flushTimerRef.current = null
      }
      // Don't drop buffered frames on teardown — a setState here wouldn't
      // run its updater (unmounting), so merge straight into the persistent
      // cache: the remounted pane (SlotsView swaps panes on /api/slots
      // resolve) rehydrates complete and the cursor stays truthful.
      const batch = pendingRef.current
      pendingRef.current = []
      if (batch.length) {
        const merged = mergeBatch(_ringCache.get(filterKey) ?? [], batch)
        if (merged != null) _ringCache.set(filterKey, merged)
      }
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
