// hal0 v3 dashboard — logs hook (Phase B1).
//
// Three transports per the brief:
//   - GET /api/logs               — historical snapshot (one-shot)
//   - SSE /api/logs/stream        — hal0 + lemond merged tail
//   - WS  /logs/stream            — raw lemond log channel (per
//                                   hal0_lemonade_ws_protocol memory)
//
// The hook keeps an in-memory ring of `LogEntry`. SSE drives the tail;
// the historical fetch primes the buffer.

import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface LogEntry {
  ts: string
  source: string
  level: string
  slot?: string | null
  msg: string
  group?: string
}

const RING_MAX = 2000

export function useLogsHistorical() {
  return useQuery({
    queryKey: ['logs', 'historical'],
    queryFn: async () => {
      const body = await apiGet<any>(ENDPOINTS.logs)
      if (Array.isArray(body)) return body as LogEntry[]
      if (Array.isArray(body?.entries)) return body.entries as LogEntry[]
      return [] as LogEntry[]
    },
  })
}

export interface UseLogsStreamOptions {
  /** When true, opens an SSE connection to `/api/logs/stream`. */
  follow?: boolean
  /** When set, also opens a WS to `/logs/stream` (lemond raw channel). */
  includeLemondWs?: boolean
}

/**
 * SSE + WS tail. Returns the live ring (newest last) and a
 * `disconnected` flag so the UI can show "stream paused" banners.
 */
export function useLogsStream(opts: UseLogsStreamOptions = {}) {
  const { follow = true, includeLemondWs = false } = opts
  const [ring, setRing] = useState<LogEntry[]>([])
  const [disconnected, setDisconnected] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  const push = (entry: LogEntry) => {
    setRing((prev) => {
      const next = prev.length >= RING_MAX ? prev.slice(prev.length - RING_MAX + 1) : prev.slice()
      next.push(entry)
      return next
    })
  }

  useEffect(() => {
    if (!follow) return
    try {
      esRef.current = new EventSource(ENDPOINTS.logsStream)
    } catch {
      setDisconnected(true)
      return
    }
    const es = esRef.current
    es.onmessage = (evt) => {
      try {
        const entry = JSON.parse(evt.data) as LogEntry
        if (entry?.ts && entry?.msg) push(entry)
      } catch {
        // ignore malformed
      }
    }
    es.onerror = () => {
      setDisconnected(true)
    }
    es.onopen = () => {
      setDisconnected(false)
    }
    return () => {
      es.close()
      esRef.current = null
    }
  }, [follow])

  useEffect(() => {
    if (!includeLemondWs) return
    try {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      wsRef.current = new WebSocket(`${proto}//${location.host}${ENDPOINTS.lemondLogsWs}`)
    } catch {
      return
    }
    const ws = wsRef.current
    ws.onmessage = (evt) => {
      try {
        const f = JSON.parse(evt.data) as any
        // Per hal0_lemonade_ws_protocol: logs.entry frames carry `{ts, level, msg}`
        if (f?.type === 'logs.entry' && f.ts && f.msg) {
          push({
            ts: f.ts,
            source: 'lemond',
            level: f.level ?? 'info',
            msg: f.msg,
          })
        }
      } catch {
        // ignore
      }
    }
    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [includeLemondWs])

  return { ring, disconnected }
}
