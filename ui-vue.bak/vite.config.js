import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// ai-dev.thinmint.dev now terminates at Caddy on hal0-dev VM (10.0.1.141:80),
// which fans out /api+/v1 → 8080, /chat/* → 3001 (OpenWebUI), /* → 5173
// (this Vite server). When developing without Caddy in front (e.g. hitting
// http://localhost:5173 directly) Vite still needs to proxy /api+/v1 to
// the local hal0-api. HAL0_AUTH_ENABLED=0 on dev, so no header injection.
function apiProxy() {
  return {
    target: 'http://127.0.0.1:8080',
    changeOrigin: true,
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
    allowedHosts: ['ai-dev.thinmint.dev', 'localhost', '127.0.0.1', '10.0.1.141'],
    hmr: {
      host: 'ai-dev.thinmint.dev',
      protocol: 'wss',
      clientPort: 443,
    },
    proxy: {
      '/api': apiProxy(),
      '/v1': apiProxy(),
    },
  },
})
