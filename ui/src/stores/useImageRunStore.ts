// hal0 dashboard — the Image Gen run of this browser.
//
// The Generate store (`dash/image/generate-store.ts`) reports "this browser's own run" here while
// it runs: `{ running, cancel }`, and `null` once it ends (success, error or abort). The run is
// reported by the store that owns it, not by the Generate panel, so it survives the panel
// unmounting, and a surface outside the Image Gen pane (the status dock) can show that run and
// offer its Cancel.

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
