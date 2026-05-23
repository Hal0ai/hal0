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
import { mockFetch, MOCK_DATA } from '../composables/useMock.js'

const ENDPOINT = '/api/backends'

// Mock fallback now lives in `composables/useMock.js` (slice #166) so
// every consumer — store, Playwright fixtures, future MCP store — sees
// one shape. `MOCK_BACKENDS` here is kept as a local re-export for
// historical callers; new code should import from `useMock`.
const MOCK_BACKENDS = MOCK_DATA.backends.map((b) => ({ ...b }))

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
      // `mockFetch` substitutes the MOCK_DATA.backends shape on 404 or
      // when VITE_MOCK_LEMONADE=1; real responses pass through. The
      // mock build returns the same `{backends, lemonade}` envelope as
      // the live endpoint per ADR-0008 §5.
      const res = await mockFetch(ENDPOINT, { headers: { Accept: 'application/json' } })
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
      // `isMocked` tracks whether the LAST fetch was substituted — the
      // mockFetch path returns a synthetic Response we can detect by
      // shape rather than a flag, so we re-derive from the response
      // identity used (a 200 with our exact backend ids).
      isMocked.value = isMockShape(backends.value)
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

  /**
   * Heuristic: the mock dataset is uniquely identifiable by the
   * `ryzenai-server` row in `unavailable` state. The real backend has
   * the same key in different combinations, but the mock keeps it
   * always present + windows-only. Good enough for an indicator badge.
   */
  function isMockShape(list) {
    if (!Array.isArray(list)) return false
    const ryzen = list.find((b) => b?.id === 'ryzenai-server')
    return !!(ryzen && ryzen.state === 'unavailable' && ryzen.note === 'Windows-only')
  }

  async function install(id) {
    const res = await mockFetch(`${ENDPOINT}/${encodeURIComponent(id)}/install`, {
      method: 'POST',
    })
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`)
    await fetchAll()
  }

  async function uninstall(id) {
    const res = await mockFetch(`${ENDPOINT}/${encodeURIComponent(id)}`, {
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
