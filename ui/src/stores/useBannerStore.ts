// hal0 v3 dashboard — banner store (Phase B1).
//
// Ported verbatim from ui-vue.bak/src/stores/banner.js. 19 entries; one
// source of truth for every "banner state" the design calls out. Lives
// in zustand so it's framework-shared with the rest of the v3 UI state.
//
// The dash/primitives.jsx prototype installs its OWN banner context on
// `window.__hal0Banners`. We don't replace that wiring yet (rip-and-
// replace is Phase B2 / B3) — this store stands alongside, ready for
// the migration. Views opting in pass through `useBannerStore` instead
// of `useBanners()`; everything else keeps working off the prototype's
// React context.

import { create } from 'zustand'

export type BannerKind = 'info' | 'ok' | 'warn' | 'err'
export type BannerScope =
  | 'global'
  | 'slots'
  | 'models'
  | 'logs'
  | 'firstrun'
  | 'agent'
  | 'dashboard'

export interface BannerAction {
  label: string
  primary?: boolean
  onClick?: () => void
}

export interface BannerEntry {
  id: string
  scope: BannerScope
  kind: BannerKind
  eyebrow: string
  heading: string
  body: string
  actions?: BannerAction[]
  dismissable?: boolean
}

export const BANNER_CATALOG: ReadonlyArray<BannerEntry> = Object.freeze([
  // ── Global ──────────────────────────────────────────────────────
  {
    id: 'lemond-offline',
    scope: 'global',
    kind: 'err',
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
    id: 'update-available',
    scope: 'global',
    kind: 'info',
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
    id: 'restart-required',
    scope: 'global',
    kind: 'warn',
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
    id: 'nuclear-evict',
    scope: 'slots',
    kind: 'warn',
    eyebrow: 'Lemonade · nuclear evict',
    heading: 'Lemonade evicted all loaded models',
    body: "At 14:23:01 a model load triggered the runtime's nuclear evict policy. Cause: CUDA out of memory while loading sd-turbo. Affected slots (4): primary, embed, rerank, agent. Reload to restore.",
    actions: [
      { label: 'View logs', primary: true },
      { label: 'Reload all' },
    ],
  },
  {
    id: 'npu-swap',
    scope: 'slots',
    kind: 'warn',
    eyebrow: 'NPU trio · swap in progress',
    heading: 'Swapping NPU chat: gemma3:1b → llama-3.2-3b-npu',
    body: 'Voice + embed paused for ~14s while FLM restarts. Coresident slots will resume automatically.',
    dismissable: false,
  },
  {
    id: 'load-queue',
    scope: 'slots',
    kind: 'warn',
    eyebrow: 'Lemonade · queue depth',
    heading: '3 slots queued to load',
    body: 'Lemonade serialises model loads. The runtime will process queued slots one at a time; this banner clears when the queue empties.',
  },
  {
    id: 'llamacpp-args-drift',
    scope: 'slots',
    kind: 'warn',
    eyebrow: 'Lemonade · config drift',
    heading: 'llamacpp.args is missing the mandatory baseline',
    body: 'Required: --parallel 1 --threads N. Without it, concurrent llama-server children can deadlock the GPU.',
    actions: [
      { label: 'Restore baseline', primary: true },
      { label: 'View config' },
    ],
  },
  {
    id: 'catalog-drift',
    scope: 'slots',
    kind: 'warn',
    eyebrow: 'Catalog · drift',
    heading: 'registry.toml is newer than server_models.json',
    body: "Models added or removed in registry.toml won't appear until you sync. Sync will restart lemond.",
    actions: [
      { label: 'Sync now', primary: true },
      { label: 'Diff catalog' },
    ],
  },
  {
    id: 'all-slots-disabled',
    scope: 'slots',
    kind: 'warn',
    eyebrow: 'Slots · no active targets',
    heading: 'All slots are disabled',
    body: 'hal0 has no active inference targets. Enable at least one slot to use chat, embed, transcription, etc.',
  },
  {
    id: 'model-missing',
    scope: 'slots',
    kind: 'err',
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
    id: 'hf-gated',
    scope: 'models',
    kind: 'warn',
    eyebrow: 'HuggingFace · gated repo',
    heading: 'HF_TOKEN required to pull this model',
    body: 'The repository requires authentication. Add HF_TOKEN in Settings, then re-attempt the download.',
    actions: [{ label: 'Add HF token', primary: true }],
  },
  {
    id: 'disk-full',
    scope: 'models',
    kind: 'err',
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
    id: 'ws-disconnect',
    scope: 'logs',
    kind: 'err',
    eyebrow: 'Stream · disconnected',
    heading: 'Lost connection to lemond — logs are paused',
    body: 'WebSocket /logs/stream closed unexpectedly. Reconnecting in 5s…',
    actions: [{ label: 'Reconnect now', primary: true }],
  },

  // ── FirstRun ────────────────────────────────────────────────────
  {
    id: 'fr-reentered',
    scope: 'firstrun',
    kind: 'warn',
    eyebrow: 'Picker · post-install',
    heading: 'You currently have hal0-Pro installed',
    body: "Picking another tier will replace your slot selections. Models already on disk won't be re-downloaded.",
  },
  {
    id: 'fr-ram-low',
    scope: 'firstrun',
    kind: 'warn',
    eyebrow: 'Hardware · low RAM',
    heading: 'Detected RAM is below the Lite minimum (16 GB)',
    body: 'hal0 needs at least 16 GB of unified RAM to load any bundled chat model. You can still install hal0 — Settings → Lemonade admin can point at an external model store.',
  },

  // ── Agent ───────────────────────────────────────────────────────
  {
    id: 'cognee-degraded',
    scope: 'agent',
    kind: 'warn',
    eyebrow: 'Memory · degraded',
    heading: 'Cognee memory DB is in degraded mode',
    body: 'Reads are working; writes are failing. Recent records may be missing. Restart Cognee or inspect logs.',
    actions: [
      { label: 'Restart Cognee', primary: true },
      { label: 'View logs' },
    ],
  },
  {
    id: 'no-agent',
    scope: 'agent',
    kind: 'info',
    eyebrow: 'Agent · not installed',
    heading: 'No bundled agent installed yet',
    body: 'Install Hermes (service) or pi-coder (CLI) to enable approval flows, memory writes, and persona dispatch.',
    actions: [{ label: 'Install Hermes', primary: true }],
  },

  // ── Dashboard ───────────────────────────────────────────────────
  {
    id: 'post-install',
    scope: 'dashboard',
    kind: 'info',
    eyebrow: 'FirstRun · just installed',
    heading: 'Welcome to hal0 — hal0-Pro is loaded',
    body: 'Try a message below. primary (Qwen3.6-27B-MTP) is your default chat persona. The persona dropdown lets you swap to coder or the NPU agent.',
    actions: [
      { label: 'Take the tour', primary: true },
      { label: 'Dismiss' },
    ],
  },

  // ── Slots (skip-path) ───────────────────────────────────────────
  {
    id: 'skip-path',
    scope: 'slots',
    kind: 'info',
    eyebrow: 'Slots · skip-path',
    heading: 'Six seeded slots, none configured',
    body: 'You skipped the bundle picker. Each seeded slot below has a Configure button that opens the Create-slot modal pre-filled. Or run the bundle picker again from Settings → FirstRun.',
    actions: [{ label: 'Run picker', primary: true }],
  },
])

const CATALOG_BY_ID = new Map(BANNER_CATALOG.map((b) => [b.id, b]))

interface BannerState {
  /** Map keyed by banner id; value is the catalog entry merged with any overrides. */
  active: Record<string, BannerEntry>
  show: (id: string, overrides?: Partial<BannerEntry>) => void
  dismiss: (id: string) => void
  toggle: (id: string, on?: boolean) => void
  clearScope: (scope: BannerScope) => void
  isActive: (id: string) => boolean
  activeByScope: (scope: BannerScope) => BannerEntry[]
}

export const useBannerStore = create<BannerState>((set, get) => ({
  active: {},

  show(id, overrides = {}) {
    const base = CATALOG_BY_ID.get(id)
    if (!base) return
    set((s) => ({ active: { ...s.active, [id]: { ...base, ...overrides } } }))
  },

  dismiss(id) {
    set((s) => {
      if (!(id in s.active)) return s
      const next = { ...s.active }
      delete next[id]
      return { active: next }
    })
  },

  toggle(id, on) {
    const present = id in get().active
    const want = on === undefined ? !present : !!on
    if (want) get().show(id)
    else get().dismiss(id)
  },

  clearScope(scope) {
    set((s) => {
      const next = { ...s.active }
      let changed = false
      for (const id of Object.keys(next)) {
        const base = CATALOG_BY_ID.get(id)
        if (base && base.scope === scope) {
          delete next[id]
          changed = true
        }
      }
      return changed ? { active: next } : s
    })
  },

  isActive(id) {
    return id in get().active
  },

  activeByScope(scope) {
    const list = Object.values(get().active)
    return list.filter((b) => b.scope === scope)
  },
}))
