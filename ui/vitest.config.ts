import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

// hal0 v3 dashboard — unit test config (vitest), separate from the app's
// vite.config.ts (which carries dev-server/proxy/build concerns this suite
// doesn't need). Node environment is enough for src/api/* logic and even a
// server-side React smoke render (react-dom/server's renderToStaticMarkup
// needs no DOM) — nothing under test needs jsdom/happy-dom.
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
  // This config intentionally skips `@vitejs/plugin-react` (Fast Refresh /
  // dev-only transforms this suite doesn't need), but esbuild's own default
  // JSX transform is the *classic* runtime (`React.createElement`, expecting
  // a global `React`), while the app's real vite.config.ts (via the react
  // plugin) uses the automatic runtime. Without this, importing any
  // dash/*.jsx module that defines top-level JSX (e.g. memory-v2-shared.jsx's
  // icon glyph map) throws `ReferenceError: React is not defined` at import
  // time — even when the only thing under test is a plain pure helper
  // function that has nothing to do with JSX. Matching esbuild's `jsx` mode
  // to the app's runtime fixes that without pulling in the full plugin.
  esbuild: {
    jsx: 'automatic',
  },
  test: {
    environment: 'node',
    // tests/e2e/port.test.ts covers the Playwright port derivation (#1399) —
    // pure logic, no browser, so it belongs to the unit suite not the e2e run.
    // `.tsx` added for memoryOverviewV2.smoke.test.tsx (task C2) — a
    // server-side React mount smoke test, needs real JSX in a `.ts`-adjacent
    // test file.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'tests/e2e/*.test.ts'],
    // macOS AppleDouble sidecars (`._foo.test.ts`) appear on non-native
    // mounts and match the include glob — they are resource forks, not
    // TypeScript, and explode esbuild's parser.
    exclude: ['**/._*', '**/node_modules/**'],
  },
})
