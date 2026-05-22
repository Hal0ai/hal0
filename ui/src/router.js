import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('./views/Dashboard.vue'),
    meta: { title: 'Dashboard' },
  },
  {
    // Canonical FirstRun wizard route. ``/welcome`` is preserved below
    // as an alias for back-compat with older bookmarks.
    path: '/firstrun',
    name: 'firstrun',
    component: () => import('./views/FirstRun.vue'),
    meta: { title: 'Setup', skipFirstRunGuard: true },
  },
  {
    path: '/slots',
    name: 'slots',
    component: () => import('./views/Slots.vue'),
    meta: { title: 'Slots' },
  },
  {
    path: '/slots/:name',
    name: 'slot-detail',
    component: () => import('./views/Slots.vue'),
    meta: { title: 'Slots' },
  },
  {
    path: '/models',
    name: 'models',
    component: () => import('./views/Models.vue'),
    meta: { title: 'Models' },
  },
  {
    path: '/hardware',
    name: 'hardware',
    component: () => import('./views/Hardware.vue'),
    meta: { title: 'Hardware' },
  },
  {
    path: '/logs',
    name: 'logs',
    component: () => import('./views/Logs.vue'),
    meta: { title: 'Logs' },
  },
  {
    path: '/providers',
    name: 'providers',
    component: () => import('./views/Providers.vue'),
    meta: { title: 'Providers' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./views/Settings.vue'),
    meta: { title: 'Settings' },
  },
  {
    // Phase 8 bundled-agent surface. The page is one host component
    // with horizontal tabs driven by ?tab=overview|inbox|activity|chat
    // so the URL is shareable + reload-stable.
    path: '/agent',
    name: 'agent',
    component: () => import('./views/Agent.vue'),
    meta: { title: 'Agent' },
  },
  {
    path: '/welcome',
    name: 'welcome',
    component: () => import('./views/FirstRun.vue'),
    meta: { title: 'Setup', skipFirstRunGuard: true },
  },
  {
    path: '/:catchAll(.*)',
    name: 'not-found',
    component: () => import('./views/NotFound.vue'),
    meta: { title: 'Not Found' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ── First-run guard ──────────────────────────────────────────────────────────
// On every navigation away from the wizard, check /api/install/state and
// redirect to /firstrun when the dashboard is being loaded into a fresh
// install (no models on disk AND no sentinel). The result is cached
// per-session so we don't hit /api/install/state on every route change.
let _firstRunDecision = null  // null = unknown, true = wizard, false = clear
let _firstRunInflight = null

async function _resolveFirstRun() {
  if (_firstRunDecision !== null) return _firstRunDecision
  if (_firstRunInflight) return _firstRunInflight
  _firstRunInflight = (async () => {
    try {
      const r = await fetch('/api/install/state')
      if (!r.ok) return false  // fail open — don't trap the user in a wizard if the API is misbehaving
      const body = await r.json()
      _firstRunDecision = !!body?.first_run
      return _firstRunDecision
    } catch {
      return false
    } finally {
      _firstRunInflight = null
    }
  })()
  return _firstRunInflight
}

router.beforeEach(async (to) => {
  if (to.meta?.skipFirstRunGuard) return true
  const needsWizard = await _resolveFirstRun()
  if (needsWizard) return { name: 'firstrun' }
  return true
})

// Expose a reset so FirstRun.vue can clear the cache after POST /install/complete.
export function resetFirstRunGuard() { _firstRunDecision = null }

router.afterEach((to) => {
  const base = 'hal0'
  document.title = to.meta?.title ? `${to.meta.title} — ${base}` : base
})

export default router
