// hal0 v3 dashboard — global installer (Phase A).
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

import React from 'react'
import * as ReactDOM from 'react-dom/client'

;(globalThis as any).React = React
;(globalThis as any).ReactDOM = ReactDOM
