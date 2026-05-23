// hal0 v3 dashboard — global installer (Phase A + Phase B1 additions).
//
// The design prototype (src/dash/*.jsx) reads React and ReactDOM from the
// global scope (e.g. `const { useState } = React`). We install them here as
// a side-effect module so that any consumer that does
// `import './globals-install'` BEFORE its first `import './dash/foo.jsx'`
// is guaranteed to have the globals available at evaluation time.
//
// ES module evaluation is depth-first: imports of a module run to
// completion before the importer's own statements. So if main.tsx imports
// this file first, then imports a dash module, the globals are guaranteed
// to be in place.
//
// Phase B1 additions:
//   - QueryClient + QueryClientProvider installed on window so the
//     prototype's `dash/main.jsx` (which calls ReactDOM.createRoot itself)
//     can wrap <App/> in a provider via a tiny edit.
//   - Toast global rerouted through the zustand store so prototype JSX
//     keeps calling `window.__hal0Toast(...)`.

import React from 'react'
import * as ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { installToastGlobal } from './stores/useToastStore'

;(globalThis as any).React = React
;(globalThis as any).ReactDOM = ReactDOM
;(globalThis as any).Hal0QueryClient = queryClient
;(globalThis as any).Hal0QueryClientProvider = QueryClientProvider

installToastGlobal()
