import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

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
    target: 'http://127.0.0.1:8080',
    changeOrigin: true,
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
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
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
