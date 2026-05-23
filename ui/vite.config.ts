import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// hal0 v3 dashboard — React+TS+Vite scaffold (Phase A).
// hal0.thinmint.dev terminates at Traefik on 10.0.1.200 and forwards to the
// hal0 LXC (10.0.1.142). Locally `npm run dev` serves on 5173; /api+/v1 are
// proxied to the local hal0-api on 8080. HAL0_AUTH_ENABLED=0 on dev, so no
// header injection is required.
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
    allowedHosts: ['hal0.thinmint.dev', 'ai-dev.thinmint.dev', 'localhost', '127.0.0.1', '10.0.1.141', '10.0.1.142'],
    hmr: {
      host: 'hal0.thinmint.dev',
      protocol: 'wss',
      clientPort: 443,
    },
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
