import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'

// hal0 v3 dashboard — React+TS+Vite scaffold (Phase A).
// `npm run dev` serves on 5173; /api+/v1 are proxied to the local hal0-api on
// 8080. Set VITE_ALLOWED_HOSTS (comma-separated) to expose the dev server on
// custom hostnames (e.g. behind a reverse proxy); defaults to localhost.
// Set VITE_HMR_HOST when serving HMR through that proxy over WSS.
const allowedHosts = process.env.VITE_ALLOWED_HOSTS
  ?.split(',')
  .map((s) => s.trim())
  .filter(Boolean) ?? ['localhost']

const hmrHost = process.env.VITE_HMR_HOST

function apiProxy() {
  return {
    // Override with VITE_API_TARGET to validate the dev UI against a remote
    // hal0-api (e.g. CT105 at http://10.0.1.142:8080) without a deploy.
    target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8080',
    changeOrigin: true,
  }
}

// VERS-flash (docs/rework/handoff-r5-drive2.md §3): `ui/index.html` used to
// hardcode `v0.5.0-alpha.1` in <title>/<meta description>, which both
// flashed a stale number before React mounted AND had drifted from the
// backend's real version (pyproject `0.9.8`). ui/package.json is now kept
// reconciled to that backend version, and this plugin stamps it into the
// HTML at build time — one source of truth, baked in before first paint, so
// there's nothing stale to flash.
const appVersion = JSON.parse(
  readFileSync(fileURLToPath(new URL('./package.json', import.meta.url)), 'utf-8'),
).version as string

function versionHtmlPlugin(version: string): Plugin {
  return {
    name: 'hal0-version-html',
    transformIndexHtml(html) {
      return html.replace(/%APP_VERSION%/g, version)
    },
  }
}

export default defineConfig({
  plugins: [
    react({
      // The design prototype files live in src/dash/*.jsx and were originally
      // transpiled in-browser by @babel/standalone. Tell @vitejs/plugin-react
      // to compile them at build time instead.
      include: [/\.jsx?$/, /\.tsx?$/],
    }),
    tailwindcss(),
    versionHtmlPlugin(appVersion),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // Same single source of truth the <title> stamp uses (appVersion, read from
  // package.json above), exposed to app code so src/sentry.ts can tag events
  // with the exact UI build that produced them instead of carrying a second
  // version constant that would drift.
  define: {
    __HAL0_UI_VERSION__: JSON.stringify(appVersion),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts,
    // HMR over WSS is only needed when the dev server is reached through a
    // TLS-terminating reverse proxy; set VITE_HMR_HOST to enable it.
    ...(hmrHost
      ? { hmr: { host: hmrHost, protocol: 'wss', clientPort: 443 } }
      : {}),
    proxy: {
      '/api': apiProxy(),
      '/v1': apiProxy(),
    },
  },
  esbuild: {
    // The prototype .jsx files use top-level `const Foo = ...` patterns and
    // rely on globals (React, ReactDOM, plus dash/*-installed window props).
    // Keep the JSX loader for .jsx but don't enforce strict module isolation
    // — the dash/ files are intentionally side-effect imports that publish to
    // `window`.
    loader: 'tsx',
    include: /src\/.*\.(jsx?|tsx?)$/,
    exclude: [],
  },
})
