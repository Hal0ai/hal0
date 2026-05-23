/**
 * stores/backends.js — Pinia store for installed/available inference backends.
 *
 * Hits ``GET /api/backends`` (planned by ADR-0008 §5; not yet shipped on
 * the backend per slice #142 / #145). When the endpoint 404s, the store
 * falls back to a hardcoded mock matching the v2 design's ``backends``
 * fixture so views render in dev. Real endpoint wins whenever available.
 *
 * Shape per row:
 *   { id, version, state: 'installed'|'unavailable'|'updating',
 *     usedBy: [slot_name], recommended?: bool, note?: string }
 *
 * ``lemonadeSelf`` is the runtime metadata for the Lemonade binary
 * itself ({version, pinned, sha, channel}) so the dashboard header can
 * show "lemonade v10.6.0 (pinned)" without re-querying.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const ENDPOINT = '/api/backends'

// Mock fallback — mirrors /tmp/hal0-design/hal0-v2/project/dash/data.jsx
// backends section. Loaded when the real endpoint returns 404/empty.
const MOCK_BACKENDS = [
  { id: 'llamacpp:rocm',   version: 'v1.0 (b9253)', state: 'installed', usedBy: [], recommended: true },
  { id: 'llamacpp:vulkan', version: 'v1.0 (b9253)', state: 'installed', usedBy: [] },
  { id: 'llamacpp:cpu',    version: 'v1.0 (b9253)', state: 'installed', usedBy: [] },
  { id: 'flm:npu',         version: 'v0.9.42 (deb)', state: 'installed', usedBy: [], recommended: true, note: 'manual deb' },
  { id: 'whispercpp',      version: 'v1.0 (vulkan)', state: 'installed', usedBy: [] },
  { id: 'sdcpp',           version: 'v1.0 (rocm)',  state: 'installed', usedBy: [] },
  { id: 'kokoro',          version: 'builtin · cpu', state: 'installed', usedBy: [] },
  { id: 'ryzenai-server',  version: '—', state: 'unavailable', usedBy: [], note: 'Windows-only' },
]

const MOCK_LEMONADE_SELF = {
  version: null,
  pinned: null,
  sha: null,
  channel: null,
}

export const useBackendsStore = defineStore('backends', () => {
  // ── State ────────────────────────────────────────────────────────
  const backends = ref([])
  const lemonadeSelf = ref({ ...MOCK_LEMONADE_SELF })
  const loading = ref(false)
  const error = ref(null)
  const isMocked = ref(false)  // true when fetch() fell back to MOCK_BACKENDS

  // ── Actions ──────────────────────────────────────────────────────
  async function fetchAll() {
    loading.value = true
    error.value = null
    try {
      const res = await fetch(ENDPOINT, { headers: { Accept: 'application/json' } })
      if (res.status === 404) {
        // Slice #142/#145 — endpoint not shipped yet. Mock fallback per
        // brief acceptance criteria so views render.
        backends.value = MOCK_BACKENDS.map((b) => ({ ...b }))
        lemonadeSelf.value = { ...MOCK_LEMONADE_SELF }
        isMocked.value = true
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      // Accept either {backends:[…], lemonade:{}} or a bare list for
      // forward-compat with whichever shape ships.
      if (Array.isArray(body)) {
        backends.value = body
      } else {
        backends.value = Array.isArray(body?.backends) ? body.backends : []
        if (body?.lemonade && typeof body.lemonade === 'object') {
          lemonadeSelf.value = { ...MOCK_LEMONADE_SELF, ...body.lemonade }
        }
      }
      isMocked.value = false
    } catch (e) {
      error.value = e?.message || String(e)
      // Soft-fail: keep last known list, fall back to mock if empty.
      if (backends.value.length === 0) {
        backends.value = MOCK_BACKENDS.map((b) => ({ ...b }))
        isMocked.value = true
      }
    } finally {
      loading.value = false
    }
  }

  async function install(id) {
    const res = await fetch(`${ENDPOINT}/${encodeURIComponent(id)}/install`, {
      method: 'POST',
    })
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`)
    await fetchAll()
  }

  async function uninstall(id) {
    const res = await fetch(`${ENDPOINT}/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`)
    await fetchAll()
  }

  // ── Getters ──────────────────────────────────────────────────────
  const byId = computed(() => {
    const m = new Map()
    for (const b of backends.value) {
      if (b && b.id) m.set(b.id, b)
    }
    return m
  })

  const installed = computed(() =>
    backends.value.filter((b) => b.state === 'installed'),
  )

  return {
    // state
    backends, lemonadeSelf, loading, error, isMocked,
    // getters
    byId, installed,
    // actions
    fetch: fetchAll, install, uninstall,
  }
})
