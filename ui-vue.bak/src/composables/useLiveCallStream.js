/**
 * useLiveCallStream — slice #14 mock real-time tool-call bus.
 *
 * Ports the React `useLiveCallStream` in
 *   /tmp/hal0-design-v3/dash/mcp.jsx (lines 8–63)
 *
 * Tick: every 500ms (`setInterval`). Per running server with
 * `server.activity.rpm > 0`, probabilistically generates one call event
 * with probability `rpm / 60 / 2` (per 500ms tick), pulling a random
 * client (from `server.clients`, fallback to the global CLIENTS list)
 * and a random tool from the per-server vocabulary below.
 *
 * Calls older than 60s are garbage-collected each tick. The returned
 * `calls` ref is a `Map<serverId, Array<{ts, client, tool}>>` so
 * consumers (the timeline + KPI strip) can index by server id. `now`
 * is the timestamp of the most recent tick — re-running every render.
 *
 * Tests: deterministic via `page.clock()` — fixture below seeds the
 * clock + the random source so the timeline glow/fade behaviour is
 * inspectable without flake. The composable accepts an optional
 * `rngFn` arg so the spec can stub `Math.random()` per tick.
 *
 * Production hook-up: the supervisor will publish a WebSocket at
 * `WS /api/mcp/stream` emitting the same `{serverId, ts, client, tool}`
 * shape. Swap the `tick()` body for a WS subscription when that
 * endpoint lands — consumers do not need to change.
 */
import { onBeforeUnmount, ref, watch } from 'vue'

const CLIENTS = ['claude-code', 'cursor', 'claude-desktop']

const TOOLS = {
  'hal0-admin':      ['slot.list', 'slot.restart', 'model.search', 'journal.tail', 'lemond.status'],
  'hal0-memory':     ['recall', 'write', 'ns.list', 'graph.query', 'doc.add'],
  filesystem:        ['read_file', 'write_file', 'list_directory', 'grep', 'stat'],
  github:            ['repo.read', 'search.code', 'issue.list', 'pr.diff', 'actions.run'],
  postgres:          ['query', 'schema.tables', 'explain', 'describe'],
  'obsidian-vault':  [],
  'brave-search':    [],
  'timed-reminders': [],
}

const TICK_MS = 500
const WINDOW_MS = 60_000

export function useLiveCallStream(serversRef, options = {}) {
  const rng = typeof options.rngFn === 'function' ? options.rngFn : Math.random
  const tickMs = typeof options.tickMs === 'number' ? options.tickMs : TICK_MS

  /** Map<serverId, Array<{ts, client, tool}>> — never reassigned, only mutated. */
  const calls = ref(new Map())
  /** Ref of the most recent tick wall-clock ms. */
  const now = ref(Date.now())

  let timer = null

  function pushSyntheticCall(serverId, tool, client, ts) {
    const arr = calls.value.get(serverId) || []
    arr.push({ ts, client, tool })
    calls.value.set(serverId, arr)
  }

  function tick() {
    const t = Date.now()
    const list = serversRef.value || []
    for (const s of list) {
      if (s.state !== 'running') continue
      const rpm = s?.activity?.rpm || 0
      if (rpm <= 0) continue
      // probability per tick = rpm / 60 / (1000 / tickMs)
      const p = (rpm / 60) * (tickMs / 1000)
      if (rng() < p) {
        const tools = TOOLS[s.id] || ['call']
        const clientPool = (s.clients && s.clients.length) ? s.clients : CLIENTS
        const tool = tools[Math.floor(rng() * tools.length)] || 'call'
        const client = clientPool[Math.floor(rng() * clientPool.length)]
        pushSyntheticCall(s.id, tool, client, t)
      }
    }
    // GC entries older than WINDOW_MS.
    for (const [id, arr] of calls.value.entries()) {
      const fresh = arr.filter((c) => t - c.ts < WINDOW_MS)
      if (fresh.length === 0) {
        calls.value.delete(id)
      } else if (fresh.length !== arr.length) {
        calls.value.set(id, fresh)
      }
    }
    // Trigger reactivity — Map mutations don't notify by themselves.
    calls.value = new Map(calls.value)
    now.value = t
  }

  function start() {
    if (timer) return
    timer = setInterval(tick, tickMs)
  }
  function stop() {
    if (timer) clearInterval(timer)
    timer = null
  }

  // Auto-start when servers list becomes non-empty; auto-stop when empty.
  watch(serversRef, (val) => {
    if (val && val.length > 0) start()
    else stop()
  }, { immediate: true })

  onBeforeUnmount(stop)

  return { calls, now, _tick: tick, _start: start, _stop: stop }
}

export const __TEST__ = { CLIENTS, TOOLS, TICK_MS, WINDOW_MS }
