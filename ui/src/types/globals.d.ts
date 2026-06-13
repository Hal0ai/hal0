// hal0 v3 dashboard — global ambient types for the design prototype.
// The prototype (src/dash/*.jsx) was originally compiled in the browser by
// @babel/standalone and used `window.*` to communicate between files. We keep
// that pattern in Phase A — the side-effect imports in src/main.tsx install
// components onto `window`, and other dash modules read them back from there.
// Phase B (API wiring) will introduce proper modules; Phase A only needs TS
// to stop complaining about the well-known globals.

declare global {
  interface Window {
    React: typeof import('react')
    ReactDOM: typeof import('react-dom/client')

    // dash/data.jsx
    HAL0_DATA: any
    parseSizeGB: (s: string) => number
    slotsUsingModel: (id: string) => any[]

    // dash/primitives.jsx, chrome.jsx, dashboard.jsx, slots.jsx, models.jsx, etc.
    [key: string]: any
  }

  // React and ReactDOM are installed as globals by src/main.tsx before any
  // dash/*.jsx side-effect module runs. The .jsx files reference `React`
  // directly (e.g. `const { useState } = React`).
  var React: typeof import('react')
  var ReactDOM: typeof import('react-dom/client')
}

export {}
