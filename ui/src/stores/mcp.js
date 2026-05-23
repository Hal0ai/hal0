/**
 * stores/mcp.js — Pinia store for the v0.3 MCP Servers surface.
 *
 * Slice #14 (#180). Backs `/agents/mcp` McpView. The store is the
 * single source of truth for the server / client / catalog lists and
 * mutates them locally on install/uninstall/restart/toggle/save —
 * which matches the design's optimistic-UI feel and keeps the v0.3
 * surface usable offline (real endpoints land later).
 *
 * Endpoint stubs (all behind `mockFetch`):
 *   GET    /api/mcp/servers
 *   GET    /api/mcp/clients
 *   GET    /api/mcp/catalog
 *   GET    /api/mcp/servers/:id
 *   POST   /api/mcp/install
 *   DELETE /api/mcp/:id
 *   POST   /api/mcp/:id/{restart|enable|disable}
 *   PATCH  /api/mcp/:id/config
 *   WS     /api/mcp/stream                (live tool-call ticks; not
 *                                         wired here — useLiveCallStream
 *                                         is the consumer + replaces
 *                                         the mock generator with a
 *                                         real WS subscription later.)
 *
 * Filter ids match the v0.3 design: 'all' | 'running' | 'bundled' |
 * 'stopped' | 'issues'. 'issues' is failed-OR-installing per design.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockFetch } from '../composables/useMock.js'

const SERVERS_URL  = '/api/mcp/servers'
const CLIENTS_URL  = '/api/mcp/clients'
const CATALOG_URL  = '/api/mcp/catalog'
const INSTALL_URL  = '/api/mcp/install'

export const MCP_HOST_BASE = 'https://halo-strix.local'

export const useMcpStore = defineStore('mcp', () => {
  // ── State ────────────────────────────────────────────────────────
  const servers  = ref([])
  const clients  = ref([])
  const catalog  = ref([])
  const categories = ref([])
  const filter   = ref('all')
  /** Per-resource loading flags (servers/clients/catalog). */
  const loading  = ref({ servers: false, clients: false, catalog: false })
  /** Last error per-resource, surfaced via banner when present. */
  const error    = ref({ servers: null, clients: null, catalog: null })

  // ── Fetchers ─────────────────────────────────────────────────────
  async function fetchServers() {
    loading.value.servers = true
    error.value.servers = null
    try {
      const res = await mockFetch(SERVERS_URL)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      servers.value = Array.isArray(body?.servers) ? body.servers : []
    } catch (e) {
      error.value.servers = String(e?.message ?? e)
    } finally {
      loading.value.servers = false
    }
  }

  async function fetchClients() {
    loading.value.clients = true
    error.value.clients = null
    try {
      const res = await mockFetch(CLIENTS_URL)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      clients.value = Array.isArray(body?.clients) ? body.clients : []
    } catch (e) {
      error.value.clients = String(e?.message ?? e)
    } finally {
      loading.value.clients = false
    }
  }

  async function fetchCatalog() {
    loading.value.catalog = true
    error.value.catalog = null
    try {
      const res = await mockFetch(CATALOG_URL)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      catalog.value   = Array.isArray(body?.entries) ? body.entries : []
      categories.value = Array.isArray(body?.categories) ? body.categories : DEFAULT_CATEGORIES
    } catch (e) {
      error.value.catalog = String(e?.message ?? e)
    } finally {
      loading.value.catalog = false
    }
  }

  /** Fetch all three resources in parallel. */
  async function fetch() {
    await Promise.all([fetchServers(), fetchClients(), fetchCatalog()])
  }

  // ── Mutations (optimistic locally; backend stubs fire & forget) ──
  async function install(catalogItem) {
    // Optimistic: drop a synthetic "installing" row immediately.
    const id = catalogItem.id || catalogItem.name
    const row = {
      id,
      name: catalogItem.name,
      description: catalogItem.description || '',
      provider: catalogItem.author || 'community',
      bundled: false,
      state: 'installing',
      progress: 5,
      progressLabel: 'queued',
      transport: 'stdio',
      url: null,
      tools: null,
      version: '0.0.0',
      clients: [],
      activity: { rpm: 0, lastCall: null },
    }
    servers.value = [...servers.value, row]
    try {
      await mockFetch(INSTALL_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, source: 'catalog' }),
      })
    } catch {
      // network-mocked: swallow.
    }
    return row
  }

  async function uninstall(id) {
    const before = servers.value
    servers.value = servers.value.filter((s) => s.id !== id)
    try {
      await mockFetch(`/api/mcp/${id}`, { method: 'DELETE' })
    } catch {
      // restore on failure.
      servers.value = before
    }
  }

  async function restart(id) {
    try {
      await mockFetch(`/api/mcp/${id}/restart`, { method: 'POST' })
    } catch {}
  }

  async function toggleEnabled(id, on) {
    const verb = on ? 'enable' : 'disable'
    servers.value = servers.value.map((s) =>
      s.id === id
        ? {
            ...s,
            state: on ? 'running' : 'stopped',
            since: on ? 'just now' : 'stopped just now',
          }
        : s,
    )
    try {
      await mockFetch(`/api/mcp/${id}/${verb}`, { method: 'POST' })
    } catch {}
  }

  async function updateConfig(id, patch) {
    servers.value = servers.value.map((s) =>
      s.id === id ? { ...s, env: { ...(s.env || {}), ...(patch.env || {}) } } : s,
    )
    try {
      await mockFetch(`/api/mcp/${id}/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
    } catch {}
  }

  // ── Getters ──────────────────────────────────────────────────────
  function byFilter(tab) {
    const list = servers.value
    if (tab === 'all')      return list
    if (tab === 'bundled')  return list.filter((s) => s.bundled)
    if (tab === 'issues')   return list.filter((s) => s.state === 'failed' || s.state === 'installing')
    return list.filter((s) => s.state === tab)
  }

  const runningCount    = computed(() => servers.value.filter((s) => s.state === 'running').length)
  const clientsCount    = computed(() => clients.value.length)
  const failuresCount   = computed(() => servers.value.filter((s) => s.state === 'failed').length)
  const installingCount = computed(() => servers.value.filter((s) => s.state === 'installing').length)
  const stoppedCount    = computed(() => servers.value.filter((s) => s.state === 'stopped').length)
  const bundledCount    = computed(() => servers.value.filter((s) => s.bundled).length)
  const issuesCount     = computed(() => installingCount.value + failuresCount.value)

  return {
    servers, clients, catalog, categories,
    filter, loading, error,
    fetch, fetchServers, fetchClients, fetchCatalog,
    install, uninstall, restart, toggleEnabled, updateConfig,
    byFilter,
    runningCount, clientsCount, failuresCount, installingCount,
    stoppedCount, bundledCount, issuesCount,
  }
})

const DEFAULT_CATEGORIES = [
  { id: 'all',          label: 'All' },
  { id: 'files',        label: 'Files' },
  { id: 'data',         label: 'Data' },
  { id: 'search',       label: 'Search' },
  { id: 'browser',      label: 'Browser' },
  { id: 'comms',        label: 'Comms' },
  { id: 'issues',       label: 'Issues' },
  { id: 'ops',          label: 'Ops' },
  { id: 'iot',          label: 'IoT' },
  { id: 'productivity', label: 'Productivity' },
]
