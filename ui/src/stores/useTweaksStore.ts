// hal0 v3 dashboard — tweaks store (Phase B1).
//
// DEV-only design-tweaks knobs persisted to localStorage
// `hal0:tweaks:v3`. Mirrors ui-vue.bak/src/stores/tweaks.js. The
// prototype dash/tweaks-panel.jsx already has its own `useTweaks` hook
// that writes EDITMODE-flagged props back to the source file; this
// zustand store is the v3 React-app counterpart used by views that
// don't go through the prototype's panel.

import { create } from 'zustand'

const LS_KEY = 'hal0:tweaks:v3'
const IS_DEV = !!(import.meta.env && (import.meta.env as any).DEV)

export interface TweaksState {
  slotCardVariant: 'a' | 'b' | 'c' | 'instrument' | 'list' | 'spec'
  heroStrip: 'sparkline' | 'metrics' | 'minimal'
  composerState: 'idle' | 'sending' | 'streaming' | 'swap' | 'no-tools' | 'offline'
  firstrunLayout: 'tiers' | 'wizard' | 'grid' | 'table'
  personaPlacement: 'topbar' | 'inline' | 'drawer' | 'composer-left' | 'above'
  chatVariant: 'active' | 'empty'
  heroVariant: 'returning' | 'post-install' | 'skip-path-empty'
  // User-facing (persist in prod too)
  theme: 'dark'
  density: 'compact' | 'comfortable' | 'spacious'
}

const DEFAULTS: TweaksState = {
  slotCardVariant: 'instrument',
  heroStrip: 'sparkline',
  composerState: 'idle',
  firstrunLayout: 'grid',
  personaPlacement: 'composer-left',
  chatVariant: 'active',
  heroVariant: 'returning',
  theme: 'dark',
  density: 'comfortable',
}

const APPEARANCE_KEYS: Array<keyof TweaksState> = ['theme', 'density']

function loadPersisted(): TweaksState {
  try {
    if (typeof localStorage === 'undefined') return { ...DEFAULTS }
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return { ...DEFAULTS }
    const parsed = JSON.parse(raw) as Partial<TweaksState>
    if (!IS_DEV) {
      const out: TweaksState = { ...DEFAULTS }
      for (const k of APPEARANCE_KEYS) {
        if (parsed[k] !== undefined) (out as any)[k] = parsed[k]
      }
      return out
    }
    return { ...DEFAULTS, ...parsed }
  } catch {
    return { ...DEFAULTS }
  }
}

function persist(state: TweaksState) {
  try {
    if (typeof localStorage === 'undefined') return
    if (IS_DEV) {
      localStorage.setItem(LS_KEY, JSON.stringify(state))
      return
    }
    const subset: Partial<TweaksState> = {}
    for (const k of APPEARANCE_KEYS) (subset as any)[k] = state[k]
    localStorage.setItem(LS_KEY, JSON.stringify(subset))
  } catch {
    // quota / disabled
  }
}

interface TweaksStore extends TweaksState {
  set: <K extends keyof TweaksState>(key: K, value: TweaksState[K]) => void
  reset: () => void
  IS_DEV: boolean
  DEFAULTS: TweaksState
}

export const useTweaksStore = create<TweaksStore>((set) => ({
  ...loadPersisted(),
  IS_DEV,
  DEFAULTS,
  set(key, value) {
    set((s) => {
      const next = { ...s, [key]: value }
      persist(next as TweaksState)
      return next
    })
  },
  reset() {
    persist(DEFAULTS)
    set({ ...DEFAULTS })
  },
}))
