import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

// hal0 v3 dashboard — unit test config (vitest), separate from the app's
// vite.config.ts (which carries dev-server/proxy/build concerns this suite
// doesn't need). Node environment is enough for src/api/* logic — nothing
// under test touches the DOM.
export default defineConfig({
  // `@/…` is the app's own source alias (tsconfig paths + vite.config.ts). It
  // was missing here, so any unit test whose import graph reached a module
  // using `@/…` failed to resolve — which silently ruled out unit-testing
  // anything under src/dash/. Mirrors vite.config.ts.
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    // tests/e2e/port.test.ts covers the Playwright port derivation (#1399) —
    // pure logic, no browser, so it belongs to the unit suite not the e2e run.
    include: ['src/**/*.test.ts', 'tests/e2e/*.test.ts'],
  },
})
