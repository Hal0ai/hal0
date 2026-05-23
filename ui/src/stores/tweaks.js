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
  npuVariant: 'rollup',       // 'rollup' | 'fan-out' | 'compact'
  heroStrip: 'sparkline',     // 'sparkline' | 'metrics' | 'minimal'
  composerState: 'idle',      // 'idle' | 'typing' | 'error'
  firstrunLayout: 'tiers',    // 'tiers' | 'wizard'
  personaPlacement: 'topbar', // 'topbar' | 'inline' | 'drawer'
})

function loadPersisted() {
  if (!IS_DEV) return { ...DEFAULTS }
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return { ...DEFAULTS }
    const parsed = JSON.parse(raw)
    return { ...DEFAULTS, ...parsed }
  } catch {
    return { ...DEFAULTS }
  }
}

function persist(state) {
  if (!IS_DEV) return
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(state))
  } catch {
    // localStorage quota / disabled — silently ignore in dev.
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

  function snapshot() {
    return {
      slotCardVariant: slotCardVariant.value,
      npuVariant: npuVariant.value,
      heroStrip: heroStrip.value,
      composerState: composerState.value,
      firstrunLayout: firstrunLayout.value,
      personaPlacement: personaPlacement.value,
    }
  }

  // Persist on any change — cheap enough for dev-only overlay.
  watch(
    [slotCardVariant, npuVariant, heroStrip, composerState, firstrunLayout, personaPlacement],
    () => persist(snapshot()),
  )

  function reset() {
    slotCardVariant.value  = DEFAULTS.slotCardVariant
    npuVariant.value       = DEFAULTS.npuVariant
    heroStrip.value        = DEFAULTS.heroStrip
    composerState.value    = DEFAULTS.composerState
    firstrunLayout.value   = DEFAULTS.firstrunLayout
    personaPlacement.value = DEFAULTS.personaPlacement
  }

  return {
    // state
    slotCardVariant, npuVariant, heroStrip, composerState,
    firstrunLayout, personaPlacement,
    // actions
    snapshot, reset,
    // constants
    DEFAULTS,
    IS_DEV,
  }
})
