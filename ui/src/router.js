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
    // v2 IA: /providers renamed → /backends. Old route kept as a
    // redirect so external bookmarks + tests don't 404 mid-rebase.
    path: '/providers',
    redirect: { name: 'backends' },
  },
  {
    path: '/backends',
    name: 'backends',
    component: () => import('./views/Backends.vue'),
    meta: { title: 'Backends' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./views/Settings.vue'),
    meta: { title: 'Settings' },
  },
  {
    // PR-13: Lemonade admin panel — config view + edit of lemond's
    // /internal/config surface. Separate route (not a section on the
    // main Settings page) so the unsaved-changes-on-leave behaviour
    // stays isolated to this panel.
    path: '/settings/lemonade',
    name: 'settings-lemonade',
    component: () => import('./views/Settings/LemonadeAdmin.vue'),
    meta: { title: 'Lemonade admin' },
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
    // Slice #14 / issue #180 — v0.3 MCP Servers surface. KPI strip +
    // clients ribbon + live oscilloscope + Install / Config / Logs /
    // Connect modals. Replaces the slice #168 ComingSoon placeholder.
    path: '/agents/mcp',
    name: 'agents-mcp',
    component: () => import('./views/McpView.vue'),
    meta: { title: 'MCP Servers' },
  },
  {
    // Slice #168 placeholder for the v0.3 Agents · Memory row.
    // Phase 9 owns the real surface; until then the link surfaces
    // a clean "v0.3" placeholder rather than dropping the user on
    // the NotFound view.
    path: '/agents/memory',
    name: 'agents-memory',
    component: () => import('./views/ComingSoon.vue'),
    meta: {
      title: 'Memory',
      detail: 'Coming soon — Phase 9 / v0.3 ships this surface.',
    },
  },
  {
    path: '/welcome',
    name: 'welcome',
    component: () => import('./views/FirstRun.vue'),
    meta: { title: 'Setup', skipFirstRunGuard: true },
  },
  {
    // PR-17 first-run bundle picker (ADR-0010). Sits between the
    // install wizard's complete-event and the dashboard's first render:
    // when capabilities.toml is empty + the bundle-chosen marker is
    // absent, the dashboard guard redirects here so the user picks a
    // tier (or explicitly clicks Skip) before any model loads.
    path: '/bundles',
    name: 'bundle-picker',
    component: () => import('./views/FirstRun/BundlePicker.vue'),
    meta: {
      title: 'Pick a starting bundle',
      skipFirstRunGuard: true,
      skipBundlePickerGuard: true,
    },
  },
  {
    // Slice #167 — primitives test sandbox. Mounts each v2 primitive
    // in isolation so Playwright can exercise their behaviour without
    // any view-level dependencies. The route is registered in all
    // builds (saves a Vite env-flag branch) but is invisible from the
    // sidebar / TopBar. ``skipFirstRunGuard`` keeps the page reachable
    // even on a fresh install where the FirstRun guard would otherwise
    // redirect to /firstrun.
    path: '/_primitives_test',
    name: 'primitives-sandbox',
    component: () => import('./components/primitives/_sandbox/PrimitivesSandbox.vue'),
    meta: { title: 'Primitives sandbox', skipFirstRunGuard: true },
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

// ── Bundle picker guard (ADR-0010 / PR-17) ──────────────────────────────────
// After the install wizard completes, the first dashboard load drops
// the user into the bundle picker until they either pick a tier or
// explicitly skip. Decision is cached per-session like the first-run
// guard above.
let _bundleDecision = null   // null = unknown, true = picker, false = clear
let _bundleInflight = null

async function _resolveBundlePicker() {
  if (_bundleDecision !== null) return _bundleDecision
  if (_bundleInflight) return _bundleInflight
  _bundleInflight = (async () => {
    try {
      const r = await fetch('/api/bundles')
      if (!r.ok) return false  // fail open
      const body = await r.json()
      _bundleDecision = !!body?.picker_pending
      return _bundleDecision
    } catch {
      return false
    } finally {
      _bundleInflight = null
    }
  })()
  return _bundleInflight
}

router.beforeEach(async (to) => {
  if (to.meta?.skipFirstRunGuard) {
    // Even on a skip-guard route we still want to honour the bundle
    // picker reset path (e.g. navigating directly to /bundles after
    // skip → re-pick later in v0.3). The bundle guard exits early on
    // routes that ALSO declare skipBundlePickerGuard.
    if (to.meta?.skipBundlePickerGuard) return true
    return true
  }
  const needsWizard = await _resolveFirstRun()
  if (needsWizard) return { name: 'firstrun' }
  if (to.meta?.skipBundlePickerGuard) return true
  const needsBundle = await _resolveBundlePicker()
  if (needsBundle) return { name: 'bundle-picker' }
  return true
})

// Expose resets so FirstRun.vue can clear the cache after
// POST /install/complete, and BundlePicker.vue after pick/skip.
export function resetFirstRunGuard() { _firstRunDecision = null }
export function resetBundlePickerGuard() { _bundleDecision = null }

router.afterEach((to) => {
  const base = 'hal0'
  document.title = to.meta?.title ? `${to.meta.title} — ${base}` : base
})

export default router
