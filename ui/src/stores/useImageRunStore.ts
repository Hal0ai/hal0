// hal0 dashboard — the Image Gen run of this browser.
//
// The Generate panel reports "this browser's own run" upward while it runs: `{ running, cancel }`,
// and `null` once it ends. It lives here, not in the pane's local state, so a surface outside the
// Image Gen pane (the status dock) can show that run and offer its Cancel. `setRun` is stable for
// the store's lifetime, so it can be handed to the panel as `onRunChange` without re-rendering it.

import { create } from 'zustand'

export interface ImageRun {
  running: boolean
  cancel: () => void
}

interface ImageRunState {
  run: ImageRun | null
  setRun: (run: ImageRun | null) => void
}

export const useImageRunStore = create<ImageRunState>((set) => ({
  run: null,

  setRun(run) {
    set({ run })
  },
}))
