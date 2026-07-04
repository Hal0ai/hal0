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
import { parseRawLevel } from './rawLevel.js'

/** Unified journal entry — mirrors ``hal0.api.routes.journal.JournalEntry``. */
export interface JournalEntry {
  id: number
  ts: string
  /** Real event origin: ``hal0`` | ``system`` | ``slot:<name>`` | ``model:<id>`` … */
  source: string
  level: 'info' | 'warn' | 'error'
  msg: string
  /** Parsed slot name (or null) — populated by the backend projection so a
   *  per-slot filter works without the client re-parsing ``source``. */
  slot?: string | null
  data?: Record<string, unknown>
}

/** Back-compat alias — the old LogEntry name is still used by callers. */
export type LogEntry = JournalEntry & {
  /** Adjacent-grouping key for the Logs page collapser. */
  group?: string
  /** Channel the row came from: ``event`` (journal/EventBus) or ``raw``
   *  (per-slot journald text). Lets one renderer style both. */
  kind?: 'event' | 'raw'
}

/** Heuristic level for a raw journald line (which carries no severity under
 *  ``--output=cat``). Re-exported from the plain-JS `rawLevel.js` so it's
 *  unit-testable without a DOM. */
export { parseRawLevel }

const RING_MAX = 2000
/** Debounce SSE reconnect on rapid filter chip toggling. */
const SSE_RECONNECT_DEBOUNCE_MS = 200

/** ``merged``/``hal0``/``all`` = no source filter; any other value (e.g.
 *  ``system``, ``slot`` prefix, ``slot:npu``) narrows to that subsystem. */
export type JournalSource = string
export type JournalLevel = 'info' | 'warn' | 'error'

/** Source values that mean "everything" — never sent as a ``?source=`` param. */
const SOURCE_ALL = new Set(['merged', 'hal0', 'all', ''])

export interface UseLogsHistoricalOptions {
  source?: JournalSource
  /** Narrow to a single slot's events (server-side ``?slot=``). */
  slot?: string | null
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
  slot?: string | null
  level?: JournalLevel | null
  q?: string | null
  since?: number | null
  limit?: number
}): string {
  const params = new URLSearchParams()
  if (opts.source && !SOURCE_ALL.has(opts.source)) params.set('source', opts.source)
  if (opts.slot) params.set('slot', opts.slot)
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
  const {
    source = 'merged',
    slot = null,
    level = null,
    q = null,
    since = null,
    limit,
    enabled = true,
  } = opts
  return useQuery({
    queryKey: ['journal', 'historical', source, slot, level, q, since, limit],
    enabled,
    queryFn: async () => {
      const qs = buildJournalQuery({ source, slot, level, q, since, limit })
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
  /** Forwarded to the journal stream as ?slot=. */
  slot?: string | null
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
    slot = null,
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
        const url = `${ENDPOINTS.journalStream}${buildJournalQuery({ source, slot, level, q })}`
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
  }, [follow, source, slot, level, q])

  return { ring, disconnected }
}

// ── Per-slot raw journald tail ────────────────────────────────────────

export interface UseSlotLogsStreamOptions {
  /** When true (and slotName set), opens the SSE. */
  follow?: boolean
  /** Ring cap (default 500 — matches the old drawer behaviour). */
  max?: number
}

export interface SlotLogRow {
  id: number
  ts: string
  source: string
  slot: string
  level: JournalLevel
  msg: string
  kind: 'raw'
}

/**
 * SSE tail of a single slot's raw journald output
 * (`/api/slots/{name}/logs/stream`). Unlike the journal stream this carries
 * unstructured text lines (llama.cpp / ROCm model-loading detail) — the ONLY
 * source of granular load progress.
 *
 * Key differences from `useLogsStream`, deliberately:
 *   - **Blind append** into a bounded ring — NOT `logRing.appendEntry`. Raw
 *     journald legitimately repeats identical lines (progress bars), and the
 *     backend backfills once with no replay on reconnect, so content-dedup
 *     would wrongly drop real repeats.
 *   - Raw lines have no timestamp/level → we stamp client-arrival `ts` and
 *     infer `level` via `parseRawLevel`.
 *   - Surfaces the named `degraded` frame (journalctl unavailable / no unit)
 *     so the UI shows a reason instead of spinning forever.
 *
 * Reuses the capped-backoff reconnect discipline of `useLogsStream`.
 */
export function useSlotLogsStream(
  slotName: string | null | undefined,
  opts: UseSlotLogsStreamOptions = {},
) {
  const { follow = true, max = 500 } = opts
  const [ring, setRing] = useState<SlotLogRow[]>([])
  const [disconnected, setDisconnected] = useState(false)
  const [degraded, setDegraded] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const seqRef = useRef(0)
  const errorCountRef = useRef(0)

  useEffect(() => {
    // Reset the buffer whenever the target slot changes so lines from a
    // previously-selected slot never bleed into the new one.
    setRing([])
    setDegraded(null)
    seqRef.current = 0

    if (!follow || !slotName) {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
      return
    }

    let cancelled = false
    let backoffTimer: ReturnType<typeof setTimeout> | null = null

    const push = (line: string) => {
      const row: SlotLogRow = {
        id: (seqRef.current += 1),
        ts: new Date().toISOString(),
        source: `slot:${slotName}`,
        slot: slotName,
        level: parseRawLevel(line),
        msg: line,
        kind: 'raw',
      }
      setRing((prev) => {
        const next = prev.length >= max ? prev.slice(prev.length - max + 1) : prev.slice()
        next.push(row)
        return next
      })
    }

    const connect = () => {
      if (cancelled) return
      try {
        esRef.current = new EventSource(ENDPOINTS.slotLogsStream(slotName))
      } catch {
        setDisconnected(true)
        return
      }
      const es = esRef.current
      if (!es) return
      es.onmessage = (evt) => {
        try {
          // Each frame is a JSON-encoded string line (json.dumps(line)).
          const line = JSON.parse(evt.data)
          if (typeof line === 'string' && line.length) push(line)
        } catch {
          // Fall back to the raw payload if it wasn't JSON-wrapped.
          if (evt.data) push(String(evt.data))
        }
      }
      // Backend emits a named `degraded` frame (NOT the reserved `error`
      // name) when journalctl is unavailable — surface the reason.
      es.addEventListener('degraded', (evt: MessageEvent) => {
        try {
          const { message } = JSON.parse(evt.data)
          setDegraded(message || 'logs unavailable')
        } catch {
          setDegraded('logs unavailable')
        }
      })
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
  }, [slotName, follow, max])

  return { ring, disconnected, degraded }
}
