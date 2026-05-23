import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router.js'
import { useEvents } from './composables/useEvents.js'

// Self-hosted brand fonts — matches hal0-web. Importing here (instead
// of via <link href="…fonts.googleapis.com/…">) keeps the dashboard
// usable on hosts with no outbound internet and avoids the FOUC the
// CDN setup used to produce.
import '@fontsource-variable/geist/index.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/700.css'

import './style.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)

// Expose the Pinia instance on `window.__pinia` so E2E specs can drive
// stores directly (used by the slice #175 polish spec to verify the
// drift-banner catalog). Always on — the runtime cost is one property
// assignment and there's no sensitive state behind it that the spec
// doesn't already mock.
// eslint-disable-next-line no-underscore-dangle
window.__pinia = pinia

// Frontend-only push API. Lets any code (e.g. Settings save handler,
// theme toggle) emit a synthetic event into the Footer's Activity ring
// without round-tripping through the backend.
//
//   window.hal0Footer.push({ type: 'ui.config_saved', message: '…' })
//
// Synthetic ids are negative + monotonic so they sort before any
// backend id (which are positive monotonic) and never collide.
const _events = useEvents()
window.hal0Footer = {
  push: (evt) => _events.push(evt || {}),
}

app.mount('#app')
