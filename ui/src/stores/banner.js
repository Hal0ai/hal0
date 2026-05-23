/**
 * stores/banner.js — Pinia store for the v2 dashboard banner catalog.
 *
 * One source of truth for every "banner state" the design calls out.
 * The 19 entries below mirror the BANNER_CATALOG in
 * /tmp/hal0-design-v3/dash/primitives.jsx (v0.3) verbatim, with the
 * JSX ``body`` collapsed to plain strings (Vue components render the
 * monospace + emphasis via their own template).
 *
 * Slice #167 added the 19th entry, `skip-path` (slots scope, info kind),
 * which is the v0.3 fold-in for the FirstRun "skip the picker" surface.
 *
 * Banners are scope-tagged so a view (Dashboard / Slots / Models / …)
 * can ask for ``activeByScope('slots')`` and get only the banners
 * relevant to that route. The catalog itself is immutable; what
 * changes is the ``active`` map (id → catalog-entry merged with any
 * per-show overrides).
 *
 * Actions
 * -------
 *   show(id, overrides?) — push a catalog entry into the active map.
 *   dismiss(id)          — remove from active map (no-op if absent).
 *   toggle(id, on?)      — set explicit on/off, or flip when omitted.
 *   clearScope(scope)    — dismiss everything matching a scope (used
 *                          on route change to clean stale banners).
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// ─────────────────────────────────────────────────────────────────────
// Catalog — 19 entries, exact id + scope + kind + copy from the
// design prototype. Body strings drop the JSX wrappers; Vue templates
// can re-apply mono/strong styling on render.
// ─────────────────────────────────────────────────────────────────────
export const BANNER_CATALOG = Object.freeze([
  // ── Global ──────────────────────────────────────────────────────
  {
    id: 'lemond-offline', scope: 'global', kind: 'err',
    eyebrow: 'Runtime · critical',
    heading: 'lemond is offline',
    body: 'Slot state is stale and inference requests will fail. Restart lemond or inspect the runtime logs to diagnose.',
    actions: [
      { label: 'Restart lemond', primary: true },
      { label: 'View status' },
      { label: 'Troubleshooting docs' },
    ],
  },
  {
    id: 'update-available', scope: 'global', kind: 'info',
    eyebrow: 'Update available',
    heading: 'hal0 v0.2.2 is available',
    body: 'Includes lemonade v10.7.0 pin bump and one FLM CHANGELOG note. Update expects a brief outage during lemond + hal0-api restart.',
    actions: [
      { label: 'Update now', primary: true },
      { label: 'Read release notes' },
      { label: 'Remind me later' },
    ],
  },
  {
    id: 'restart-required', scope: 'global', kind: 'warn',
    eyebrow: 'Restart required',
    heading: 'Lemonade restart required to apply config changes',
    body: 'ctx_size and llamacpp.args changed on primary. Changes apply on next restart.',
    actions: [
      { label: 'Restart now', primary: true },
      { label: 'Later' },
    ],
  },

  // ── Slots view ──────────────────────────────────────────────────
  {
    id: 'nuclear-evict', scope: 'slots', kind: 'warn',
    eyebrow: 'Lemonade · nuclear evict',
    heading: 'Lemonade evicted all loaded models',
    body: "At 14:23:01 a model load triggered the runtime's nuclear evict policy. Cause: CUDA out of memory while loading sd-turbo. Affected slots (4): primary, embed, rerank, agent. Reload to restore.",
    actions: [
      { label: 'View logs', primary: true },
      { label: 'Reload all' },
    ],
  },
  {
    id: 'npu-swap', scope: 'slots', kind: 'warn',
    eyebrow: 'NPU trio · swap in progress',
    heading: 'Swapping NPU chat: gemma3:1b → llama-3.2-3b-npu',
    body: 'Voice + embed paused for ~14s while FLM restarts. Coresident slots will resume automatically.',
    dismissable: false,
  },
  {
    id: 'load-queue', scope: 'slots', kind: 'warn',
    eyebrow: 'Lemonade · queue depth',
    heading: '3 slots queued to load',
    body: 'Lemonade serialises model loads. The runtime will process queued slots one at a time; this banner clears when the queue empties.',
  },
  {
    id: 'llamacpp-args-drift', scope: 'slots', kind: 'warn',
    eyebrow: 'Lemonade · config drift',
    heading: 'llamacpp.args is missing the mandatory baseline',
    body: 'Required: --parallel 1 --threads N. Without it, concurrent llama-server children can deadlock the GPU.',
    actions: [
      { label: 'Restore baseline', primary: true },
      { label: 'View config' },
    ],
  },
  {
    id: 'catalog-drift', scope: 'slots', kind: 'warn',
    eyebrow: 'Catalog · drift',
    heading: 'registry.toml is newer than server_models.json',
    body: "Models added or removed in registry.toml won't appear until you sync. Sync will restart lemond.",
    actions: [
      { label: 'Sync now', primary: true },
      { label: 'Diff catalog' },
    ],
  },
  {
    id: 'all-slots-disabled', scope: 'slots', kind: 'warn',
    eyebrow: 'Slots · no active targets',
    heading: 'All slots are disabled',
    body: 'hal0 has no active inference targets. Enable at least one slot to use chat, embed, transcription, etc.',
  },
  {
    id: 'model-missing', scope: 'slots', kind: 'err',
    eyebrow: 'Slot · file not found',
    heading: 'Model file missing on disk for slot primary',
    body: 'Expected: /var/lib/hal0/models/qwen3.6-27b-mtp-q4_k_m.gguf. The file was removed externally. Delete the slot or re-pull the model.',
    actions: [
      { label: 'Re-pull from /models', primary: true },
      { label: 'Delete slot' },
    ],
  },

  // ── Models view ─────────────────────────────────────────────────
  {
    id: 'hf-gated', scope: 'models', kind: 'warn',
    eyebrow: 'HuggingFace · gated repo',
    heading: 'HF_TOKEN required to pull this model',
    body: 'The repository requires authentication. Add HF_TOKEN in Settings, then re-attempt the download.',
    actions: [
      { label: 'Add HF token', primary: true },
    ],
  },
  {
    id: 'disk-full', scope: 'models', kind: 'err',
    eyebrow: 'Disk · ENOSPC',
    heading: 'Disk full — downloads paused',
    body: 'Only 2.1 GB free on /var. Free at least 38 GB to resume.',
    actions: [
      { label: 'Pause all', primary: true },
      { label: 'Resume after freeing space' },
    ],
  },

  // ── Logs view ───────────────────────────────────────────────────
  {
    id: 'ws-disconnect', scope: 'logs', kind: 'err',
    eyebrow: 'Stream · disconnected',
    heading: 'Lost connection to lemond — logs are paused',
    body: 'WebSocket /logs/stream closed unexpectedly. Reconnecting in 5s…',
    actions: [
      { label: 'Reconnect now', primary: true },
    ],
  },

  // ── FirstRun ────────────────────────────────────────────────────
  {
    id: 'fr-reentered', scope: 'firstrun', kind: 'warn',
    eyebrow: 'Picker · post-install',
    heading: 'You currently have hal0-Pro installed',
    body: "Picking another tier will replace your slot selections. Models already on disk won't be re-downloaded.",
  },
  {
    id: 'fr-ram-low', scope: 'firstrun', kind: 'warn',
    eyebrow: 'Hardware · low RAM',
    heading: 'Detected RAM is below the Lite minimum (16 GB)',
    body: 'hal0 needs at least 16 GB of unified RAM to load any bundled chat model. You can still install hal0 — Settings → Lemonade admin can point at an external model store.',
  },

  // ── Agent ───────────────────────────────────────────────────────
  {
    id: 'cognee-degraded', scope: 'agent', kind: 'warn',
    eyebrow: 'Memory · degraded',
    heading: 'Cognee memory DB is in degraded mode',
    body: 'Reads are working; writes are failing. Recent records may be missing. Restart Cognee or inspect logs.',
    actions: [
      { label: 'Restart Cognee', primary: true },
      { label: 'View logs' },
    ],
  },
  {
    id: 'no-agent', scope: 'agent', kind: 'info',
    eyebrow: 'Agent · not installed',
    heading: 'No bundled agent installed yet',
    body: 'Install Hermes (service) or pi-coder (CLI) to enable approval flows, memory writes, and persona dispatch.',
    actions: [
      { label: 'Install Hermes', primary: true },
    ],
  },

  // ── Dashboard ───────────────────────────────────────────────────
  {
    id: 'post-install', scope: 'dashboard', kind: 'info',
    eyebrow: 'FirstRun · just installed',
    heading: 'Welcome to hal0 — hal0-Pro is loaded',
    body: 'Try a message below. primary (Qwen3.6-27B-MTP) is your default chat persona. The persona dropdown lets you swap to coder or the NPU agent.',
    actions: [
      { label: 'Take the tour', primary: true },
      { label: 'Dismiss' },
    ],
  },

  // ── Slots (skip-path) — added in v0.3 fold-in (slice #167) ──────
  // Mirror of /tmp/hal0-design-v3/dash/primitives.jsx lines 372–380.
  // Lives under "slots" scope: shown on the Slots route when the user
  // skipped the FirstRun bundle picker, prompting them to configure
  // each seeded slot one-by-one or re-run the picker.
  {
    id: 'skip-path', scope: 'slots', kind: 'info',
    eyebrow: 'Slots · skip-path',
    heading: 'Six seeded slots, none configured',
    body: 'You skipped the bundle picker. Each seeded slot below has a Configure button that opens the Create-slot modal pre-filled. Or run the bundle picker again from Settings → FirstRun.',
    actions: [
      { label: 'Run picker', primary: true },
    ],
  },
])

const CATALOG_BY_ID = new Map(BANNER_CATALOG.map((b) => [b.id, b]))

export const useBannerStore = defineStore('banner', () => {
  // active is a plain object so Vue tracks add/remove reactively
  // without the Map-reactivity caveat. Keys = catalog id.
  const active = ref({})

  function show(id, overrides = {}) {
    const base = CATALOG_BY_ID.get(id)
    if (!base) return
    active.value = { ...active.value, [id]: { ...base, ...overrides } }
  }

  function dismiss(id) {
    if (!(id in active.value)) return
    const next = { ...active.value }
    delete next[id]
    active.value = next
  }

  function toggle(id, on) {
    const isOn = id in active.value
    const want = (on === undefined) ? !isOn : !!on
    if (want) show(id)
    else dismiss(id)
  }

  function clearScope(scope) {
    const next = { ...active.value }
    let changed = false
    for (const id of Object.keys(next)) {
      const base = CATALOG_BY_ID.get(id)
      if (base && base.scope === scope) {
        delete next[id]
        changed = true
      }
    }
    if (changed) active.value = next
  }

  const activeList = computed(() => Object.values(active.value))

  function activeByScope(scope) {
    return activeList.value.filter((b) => b.scope === scope)
  }

  function isActive(id) {
    return id in active.value
  }

  return {
    // state
    active,
    // getters
    activeList,
    // helpers
    activeByScope, isActive,
    // actions
    show, dismiss, toggle, clearScope,
    // constants
    CATALOG: BANNER_CATALOG,
  }
})
