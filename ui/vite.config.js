import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// ai.thinmint.dev and ai-dev.thinmint.dev both point at this Vite dev
// server (via Traefik on 10.0.1.200).  /api + /v1 go straight to
// hal0-api on hal0-test (10.0.1.230:8080) — bypasses Caddy entirely.
// hal0-test runs with HAL0_AUTH_ENABLED=1; we satisfy it by injecting
// X-Forwarded-Email=admin here, matching the no-auth Caddy vhost.  This
// is dev-only — production traffic should go through Caddy where
// inbound forwarded headers are stripped before re-injection.
function apiProxy() {
  return {
    target: 'http://10.0.1.230:8080',
    changeOrigin: true,
    configure: (proxy) => {
      proxy.on('proxyReq', (proxyReq) => {
        proxyReq.setHeader('X-Forwarded-Email', 'admin')
        proxyReq.setHeader('X-Forwarded-User', 'admin')
      })
    },
  }
}

export default defineConfig({
  plugins: [vue(), tailwindcss()],
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
    allowedHosts: ['ai.thinmint.dev', 'ai-dev.thinmint.dev', 'localhost', '127.0.0.1'],
    hmr: {
      host: 'ai.thinmint.dev',
      protocol: 'wss',
      clientPort: 443,
    },
    proxy: {
      '/api': apiProxy(),
      '/v1': apiProxy(),
    },
  },
})
