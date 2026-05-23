/**
 * stores/tweaks.js — DEV-only design-tweaks store.
 *
 * Backs the v2 Tweaks Panel (left sidebar dev-only overlay) that lets
 * the designer switch between SlotCard variants, NPU layouts, hero
 * strip styles, composer states, etc. Persists every choice to
 * localStorage under ``hal0:tweaks:v2`` so a reload preserves the
 * picked combination.
 *
 * Gated by ``import.meta.env.DEV`` — the production bundle still
 * IMPORTS this file (so route-level imports don't 404) but the store
 * is a no-op shim: all setters discard, getters return defaults.
 * This keeps prod bundle size minimal without breaking the import
 * graph.
 */
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const LS_KEY = 'hal0:tweaks:v2'
const IS_DEV = !!(import.meta.env && import.meta.env.DEV)

// Default variant per knob — designer can swap to validate a layout
// before we commit to it. Keys mirror the v2 design's tweaks-panel.jsx
// segmented controls.
const DEFAULTS = Object.freeze({
  slotCardVariant: 'a',       // 'a' | 'b' | 'c'
  // NPU trio render style. Defaults to 'block' — the single-card 3-row
  // layout from slots.jsx::NpuBlock. 'reactor' = the central FLM disc
  // + 3 spokes variant from slots.jsx::NpuReactor. Toggled from the
  // dev tweaks panel; surfaced in Slots.vue's NPU section.
  npuVariant: 'block',        // 'block' | 'reactor'
  heroStrip: 'sparkline',     // 'sparkline' | 'metrics' | 'minimal'
  composerState: 'idle',      // 'idle' | 'sending' | 'streaming' | 'swap' | 'no-tools' | 'offline'
  firstrunLayout: 'tiers',    // 'tiers' | 'wizard'
  personaPlacement: 'topbar', // 'topbar' | 'inline' | 'drawer'
  // Dashboard / view (slice #169)
  chatVariant: 'active',      // 'active' | 'empty'
  heroVariant: 'returning',   // 'returning' | 'post-install' | 'skip-path-empty'
  // ── User-facing appearance knobs (slice #173, Settings → Appearance).
  // Persist even in non-DEV builds because they're real preferences the
  // operator changes, not a designer-only knob. Persistence is gated
  // separately in `persist()` below.
  theme: 'dark',              // 'dark' (only option for v0.2; v0.3 adds light/auto)
  density: 'comfortable',     // 'compact' | 'comfortable' | 'spacious'
})

// Appearance keys (theme + density) persist in PROD too — they're real
// user preferences, not designer knobs. Everything else is dev-only.
const APPEARANCE_KEYS = ['theme', 'density']

function loadPersisted() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return { ...DEFAULTS }
    const parsed = JSON.parse(raw)
    if (!IS_DEV) {
      // In prod, only re-hydrate appearance keys; designer-only knobs
      // stay at their DEFAULTS so accidentally-persisted dev state from
      // an earlier build doesn't leak into the operator's view.
      const out = { ...DEFAULTS }
      for (const k of APPEARANCE_KEYS) {
        if (parsed[k] !== undefined) out[k] = parsed[k]
      }
      return out
    }
    return { ...DEFAULTS, ...parsed }
  } catch {
    return { ...DEFAULTS }
  }
}

function persist(state) {
  try {
    if (IS_DEV) {
      localStorage.setItem(LS_KEY, JSON.stringify(state))
      return
    }
    // Prod — persist appearance only.
    const subset = {}
    for (const k of APPEARANCE_KEYS) subset[k] = state[k]
    localStorage.setItem(LS_KEY, JSON.stringify(subset))
  } catch {
    // localStorage quota / disabled — silently ignore.
  }
}

export const useTweaksStore = defineStore('tweaks', () => {
  const initial = loadPersisted()

  const slotCardVariant   = ref(initial.slotCardVariant)
  const npuVariant        = ref(initial.npuVariant)
  const heroStrip         = ref(initial.heroStrip)
  const composerState     = ref(initial.composerState)
  const firstrunLayout    = ref(initial.firstrunLayout)
  const personaPlacement  = ref(initial.personaPlacement)
  // Slice #169 — dashboard variants (chat surface + hero strip flavour)
  const chatVariant       = ref(initial.chatVariant)
  const heroVariant       = ref(initial.heroVariant)
  // Slice #173 — Settings → Appearance
  const theme             = ref(initial.theme)
  const density           = ref(initial.density)

  function snapshot() {
    return {
      slotCardVariant: slotCardVariant.value,
      npuVariant: npuVariant.value,
      heroStrip: heroStrip.value,
      composerState: composerState.value,
      firstrunLayout: firstrunLayout.value,
      personaPlacement: personaPlacement.value,
      chatVariant: chatVariant.value,
      heroVariant: heroVariant.value,
      theme: theme.value,
      density: density.value,
    }
  }

  // Persist on any change — cheap enough for dev-only overlay.
  watch(
    [slotCardVariant, npuVariant, heroStrip, composerState, firstrunLayout, personaPlacement, chatVariant, heroVariant, theme, density],
    () => persist(snapshot()),
  )

  function reset() {
    slotCardVariant.value  = DEFAULTS.slotCardVariant
    npuVariant.value       = DEFAULTS.npuVariant
    heroStrip.value        = DEFAULTS.heroStrip
    composerState.value    = DEFAULTS.composerState
    firstrunLayout.value   = DEFAULTS.firstrunLayout
    personaPlacement.value = DEFAULTS.personaPlacement
    chatVariant.value      = DEFAULTS.chatVariant
    heroVariant.value      = DEFAULTS.heroVariant
    theme.value            = DEFAULTS.theme
    density.value          = DEFAULTS.density
  }

  return {
    // state
    slotCardVariant, npuVariant, heroStrip, composerState,
    firstrunLayout, personaPlacement,
    chatVariant, heroVariant,
    theme, density,
    // actions
    snapshot, reset,
    // constants
    DEFAULTS,
    IS_DEV,
  }
})
