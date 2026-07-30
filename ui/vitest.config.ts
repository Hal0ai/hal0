import { defineConfig } from 'vitest/config'

// hal0 v3 dashboard — unit test config (vitest), separate from the app's
// vite.config.ts (which carries dev-server/proxy/build concerns this suite
// doesn't need). Node environment is enough for src/api/* logic — nothing
// under test touches the DOM.
export default defineConfig({
  test: {
    environment: 'node',
    // tests/e2e/port.test.ts covers the Playwright port derivation (#1399) —
    // pure logic, no browser, so it belongs to the unit suite not the e2e run.
    include: ['src/**/*.test.ts', 'tests/e2e/*.test.ts'],
  },
})
