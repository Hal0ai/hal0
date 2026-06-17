// hal0 v3 dashboard — journal hook (Phase 3 of epic #322).
//
// Calls the ``/api/journal`` surface that landed in PR #330
// (Phase 1). Two transports:
//   - GET /api/journal               — historical backfill (one-shot)
//   - SSE /api/journal/stream        — live tail
//
// The hook keeps an in-memory ring of `JournalEntry`. SSE drives the
// tail; the historical fetch primes the buffer. SSE reconnects on
// param change with a short debounce so toggling source/level/q chips
// doesn't thrash a hot connection.
//
// Filter semantics: `source`/`level`/`q` are forwarded to the backend
// so the wire payload is already small. Callers MAY also filter the
// returned ring client-side (e.g. the Footer search box) for instant
// feedback without re-opening the SSE.

import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'
import { appendEntry } from './logRing.js'

/** Unified journal entry — mirrors ``hal0.api.routes.journal.JournalEntry``. */
export interface JournalEntry {
  id: number
  ts: string
  source: 'hal0'
  level: 'info' | 'warn' | 'error'
  msg: string
  data?: Record<string, unknown>
}

/** Back-compat alias — the old LogEntry name is still used by callers. */
export type LogEntry = JournalEntry & {
  /** Legacy field carried by the Logs page demo lines, not present on
   *  JournalEntry. Optional so it doesn't widen the journal contract. */
  slot?: string | null
  /** Same story — adjacent-grouping key for the Logs page collapser. */
  group?: string
}

const RING_MAX = 2000
/** Debounce SSE reconnect on rapid filter chip toggling. */
const SSE_RECONNECT_DEBOUNCE_MS = 200

export type JournalSource = 'merged' | 'hal0' | 'all'
export type JournalLevel = 'info' | 'warn' | 'error'

export interface UseLogsHistoricalOptions {
  source?: JournalSource
  level?: JournalLevel | null
  q?: string | null
  since?: number | null
  /** Defaults to 200 (matches backend default + LIMIT_MAX 500). */
  limit?: number
  /** When false the query is disabled. */
  enabled?: boolean
}

function buildJournalQuery(opts: {
  source?: JournalSource
  level?: JournalLevel | null
  q?: string | null
  since?: number | null
  limit?: number
}): string {
  const params = new URLSearchParams()
  if (opts.source && opts.source !== 'merged') params.set('source', opts.source)
  if (opts.level) params.set('level', opts.level)
  if (opts.q) params.set('q', opts.q)
  if (opts.since != null) params.set('since', String(opts.since))
  if (opts.limit != null) params.set('limit', String(opts.limit))
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

/**
 * One-shot historical backfill. Returns the parsed envelope so callers
 * can advance a cursor (`next_since`) — the LogsView pages through
 * older entries on scroll.
 */
export function useLogsHistorical(opts: UseLogsHistoricalOptions = {}) {
  const { source = 'merged', level = null, q = null, since = null, limit, enabled = true } = opts
  return useQuery({
    queryKey: ['journal', 'historical', source, level, q, since, limit],
    enabled,
    queryFn: async () => {
      const qs = buildJournalQuery({ source, level, q, since, limit })
      const body = await apiGet<{ entries: JournalEntry[]; next_since: number | null }>(
        `${ENDPOINTS.journal}${qs}`,
      )
      // Backend always returns `{entries, next_since}`. Guard against an
      // older / mocked payload that hands back a bare array so a stale
      // fixture doesn't break the hook signature.
      if (Array.isArray(body)) return { entries: body as JournalEntry[], next_since: null }
      return {
        entries: Array.isArray(body?.entries) ? body.entries : [],
        next_since: body?.next_since ?? null,
      }
    },
  })
}

export interface UseLogsStreamOptions {
  /** When true, opens an SSE connection to `/api/journal/stream`. */
  follow?: boolean
  /** Forwarded to the journal stream as ?source=. */
  source?: JournalSource
  /** Forwarded to the journal stream as ?level=. */
  level?: JournalLevel | null
  /** Forwarded to the journal stream as ?q= (server-side substring filter). */
  q?: string | null
}

/**
 * SSE tail. Returns the live ring (newest last) and a
 * `disconnected` flag so the UI can show "stream paused" banners.
 *
 * Reconnects on `source`/`level`/`q`/`follow` change with a 200ms debounce
 * so a fast cascade of filter-chip clicks coalesces into one new SSE.
 */
export function useLogsStream(opts: UseLogsStreamOptions = {}) {
  const {
    follow = true,
    source = 'merged',
    level = null,
    q = null,
  } = opts
  const [ring, setRing] = useState<JournalEntry[]>([])
  const [disconnected, setDisconnected] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  /** Increments on every reconnect; used to backoff on repeated errors. */
  const errorCountRef = useRef(0)

  const push = (entry: JournalEntry) => {
    // appendEntry dedups by content signature so re-opening the pane (which
    // reconnects the SSE and replays the tail) never double-renders a line.
    setRing((prev) => appendEntry(prev, entry, RING_MAX))
  }

  useEffect(() => {
    if (!follow) {
      // Close any open stream when follow flips off.
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
      return
    }

    // When filter params change we want a fresh connection — but
    // debounce a touch so rapid chip cycling doesn't open + close
    // a connection per click.
    let cancelled = false
    let backoffTimer: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      if (cancelled) return
      try {
        const url = `${ENDPOINTS.journalStream}${buildJournalQuery({ source, level, q })}`
        esRef.current = new EventSource(url)
      } catch {
        setDisconnected(true)
        return
      }
      const es = esRef.current
      if (!es) return
      es.onmessage = (evt) => {
        try {
          const entry = JSON.parse(evt.data) as JournalEntry
          if (entry?.ts && entry?.msg) push(entry)
        } catch {
          // ignore malformed
        }
      }
      es.onerror = () => {
        setDisconnected(true)
        errorCountRef.current += 1
        // Browser EventSource auto-reconnects, but a server-side close
        // (e.g. backend redeploy) can put us in a loop. Tear down and
        // schedule our own reconnect with a capped backoff so we don't
        // hammer the API during an outage.
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
  }, [follow, source, level, q])

  return { ring, disconnected }
}
