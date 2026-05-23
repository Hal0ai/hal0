<script setup>
/**
 * Sidebar.vue — v2 dash chrome (slice #168).
 *
 * Variants per breakpoint (driven from App.vue):
 *   ≥1280    full width (232px, --sidebar-w)
 *   1080–1279 icon collapse (56px, --sidebar-w-collapsed), label tooltip on hover
 *   720–1079  overlay drawer (hamburger in TopBar opens it)
 *   <720      hidden entirely; <BottomTabs> takes over
 *
 * Nav order (v0.3): Dashboard / Slots / Models / Hardware / Backends /
 * Logs / **Agents · v0.3 group** / Settings.
 *
 * The Agents group renders inline. When `useAgentStore.installed`
 * is empty the entire group collapses to a single "Set up agent →"
 * link to /agent (matches the chrome.jsx pattern: no agent → no
 * sub-tree, just a CTA row).
 *
 * Bottom: Lemonade status block (state dot + N/M loaded). Click
 * routes to /logs?source=lemond — slice #176 owns the Logs source
 * filter wiring; until then it lands at /logs which renders fine.
 */
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentStore } from '../stores/agent.js'
import { useLemonadeStore } from '../stores/lemonade.js'

const props = defineProps({
  /** Desktop: collapsed-icon-rail vs full. Mobile/drawer: open vs closed. */
  open: Boolean,
  /** True when viewport ≤ 1079 (App.vue resolves the breakpoint). */
  isDrawer: Boolean,
  /** True when viewport ≥ 720 and ≤ 1279 (icon-collapse rail). */
  isCollapsed: Boolean,
})
const emit = defineEmits(['navigate', 'close'])

const route   = useRoute()
const router  = useRouter()
const agent   = useAgentStore()
const lemonade = useLemonadeStore()

onMounted(() => {
  // Bell already calls ensureBootstrapped; we still nudge fetchInstalled
  // so the agent group renders correctly even if the bell hasn't mounted
  // (e.g. in tests that probe the sidebar in isolation).
  agent.fetchInstalled()
})

// ── Primary nav (excluding agents group) ───────────────────────────
const NAV = [
  { to: '/',          name: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
  { to: '/slots',     name: 'slots',     label: 'Slots',     icon: 'slots'     },
  { to: '/models',    name: 'models',    label: 'Models',    icon: 'models'    },
  { to: '/hardware',  name: 'hardware',  label: 'Hardware',  icon: 'hardware'  },
  { to: '/backends',  name: 'backends',  label: 'Backends',  icon: 'backends'  },
  { to: '/logs',      name: 'logs',      label: 'Logs',      icon: 'logs'      },
]

const TRAILING_NAV = [
  { to: '/settings', name: 'settings', label: 'Settings', icon: 'settings' },
]

// ── Agents · v0.3 group ────────────────────────────────────────────
const agentInstalled = computed(() => (agent.installed?.length ?? 0) > 0)

const AGENT_GROUP = [
  { to: '/agent',          label: 'Agents',      icon: 'agent',  badge: 'pending' },
  { to: '/agents/mcp',     label: 'MCP Servers', icon: 'mcp' },
  { to: '/agents/memory',  label: 'Memory',      icon: 'memory', disabled: true, tip: 'v0.3' },
]

function isActive(item) {
  if (!item?.to) return false
  if (item.to === '/') return route.path === '/'
  return route.path === item.to || route.path.startsWith(item.to + '/')
}

function onNavClick(item) {
  if (item.disabled) return
  emit('navigate')
}

function onLemondClick() {
  router.push({ path: '/logs', query: { source: 'lemond' } })
  emit('navigate')
}

const lemondHealth = computed(() => lemonade.health)
const loadedCount  = computed(() => lemonade.loadedModels?.length ?? 0)
const maxModels    = computed(() => lemonade.maxModels ?? '—')
const lemondDotClass = computed(() => ({
  up:       lemondHealth.value === 'up',
  warn:     lemondHealth.value === 'degraded',
  err:      lemondHealth.value === 'down',
}))
</script>

<template>
  <aside
    class="sidebar"
    :class="{
      collapsed: isCollapsed && !isDrawer,
      drawer:    isDrawer,
      'drawer-open': isDrawer && open,
    }"
    role="navigation"
    aria-label="Main navigation"
  >
    <div class="sb-section">Navigate</div>

    <nav class="sb-list" aria-label="Primary">
      <router-link
        v-for="it in NAV"
        :key="it.to"
        :to="it.to"
        class="sb-row"
        :class="{ active: isActive(it) }"
        :aria-current="isActive(it) ? 'page' : undefined"
        :title="it.label"
        @click="onNavClick(it)"
      >
        <component :is="`icon-${it.icon}`" />
        <span class="lbl">{{ it.label }}</span>
      </router-link>

      <!-- Agents · v0.3 group ----------------------------------- -->
      <div v-if="agentInstalled" class="sb-group">
        <div class="sb-group-h mono">Agents · v0.3</div>
        <router-link
          v-for="it in AGENT_GROUP"
          :key="it.to"
          :to="it.disabled ? '' : it.to"
          class="sb-row sb-sub"
          :class="{ active: !it.disabled && isActive(it), disabled: it.disabled }"
          :event="it.disabled ? '' : 'click'"
          :tabindex="it.disabled ? -1 : 0"
          :title="it.tip || it.label"
          :aria-disabled="it.disabled ? 'true' : undefined"
          @click="onNavClick(it)"
        >
          <component :is="`icon-${it.icon}`" />
          <span class="lbl">{{ it.label }}</span>
          <span
            v-if="it.badge === 'pending' && agent.pendingCount > 0"
            class="cnt num"
            :aria-label="`${agent.pendingCount} pending`"
          >{{ agent.pendingCount }}</span>
          <span v-else-if="it.disabled" class="cnt dim mono">soon</span>
        </router-link>
      </div>

      <!-- "Set up agent" CTA when nothing installed yet ---------- -->
      <router-link
        v-else
        to="/agent"
        class="sb-row sb-cta"
        :class="{ active: isActive({ to: '/agent' }) }"
        @click="onNavClick({ to: '/agent' })"
      >
        <component :is="`icon-agent`" />
        <span class="lbl">Set up agent →</span>
      </router-link>

      <router-link
        v-for="it in TRAILING_NAV"
        :key="it.to"
        :to="it.to"
        class="sb-row"
        :class="{ active: isActive(it) }"
        :aria-current="isActive(it) ? 'page' : undefined"
        :title="it.label"
        @click="onNavClick(it)"
      >
        <component :is="`icon-${it.icon}`" />
        <span class="lbl">{{ it.label }}</span>
      </router-link>
    </nav>

    <div class="sb-spacer" />

    <!-- Lemonade status block (bottom-of-sidebar) -->
    <div
      class="sb-status"
      role="button"
      tabindex="0"
      :aria-label="`Lemonade ${lemondHealth} · ${loadedCount}/${maxModels} loaded. Click for runtime logs.`"
      @click="onLemondClick"
      @keydown.enter="onLemondClick"
      @keydown.space.prevent="onLemondClick"
    >
      <div class="row">
        <span class="k">lemond</span>
        <span class="v" :class="lemondDotClass" data-testid="lemond-state">
          <span class="dot" />{{ lemondHealth }}
        </span>
      </div>
      <div v-if="lemonade.version" class="row">
        <span class="k">version</span>
        <span class="v">{{ lemonade.version }}</span>
      </div>
      <div class="ln" />
      <div class="row">
        <span class="k">loaded</span>
        <span class="v num" data-testid="lemond-loaded"><b>{{ loadedCount }}</b>/{{ maxModels }}</span>
      </div>
      <div class="nudge">View runtime logs →</div>
    </div>
  </aside>
</template>

<script>
/**
 * Inline icon components — same vocabulary as chrome.jsx, registered
 * locally so `<component :is="icon-foo">` keeps the template terse.
 * Each renders a 16×16 line glyph with currentColor stroke.
 */
import { h } from 'vue'
function svg(children) {
  return () => h('svg', {
    width: 16, height: 16, viewBox: '0 0 16 16',
    fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    class: 'sb-ico', 'aria-hidden': 'true',
  }, children())
}
export default {
  components: {
    'icon-dashboard': svg(() => [
      h('rect', { x: 2, y: 2, width: 5, height: 5, rx: 1 }),
      h('rect', { x: 9, y: 2, width: 5, height: 9, rx: 1 }),
      h('rect', { x: 2, y: 9, width: 5, height: 5, rx: 1 }),
    ]),
    'icon-slots': svg(() => [
      h('rect', { x: 2, y: 3, width: 12, height: 3, rx: 0.5 }),
      h('rect', { x: 2, y: 7, width: 12, height: 3, rx: 0.5 }),
      h('rect', { x: 2, y: 11, width: 12, height: 3, rx: 0.5 }),
      h('circle', { cx: 4, cy: 4.5, r: 0.6, fill: 'currentColor', stroke: 'none' }),
      h('circle', { cx: 4, cy: 8.5, r: 0.6, fill: 'currentColor', stroke: 'none' }),
      h('circle', { cx: 4, cy: 12.5, r: 0.6, fill: 'currentColor', stroke: 'none' }),
    ]),
    'icon-models': svg(() => [
      h('path', { d: 'M2 4l6-2 6 2-6 2-6-2z' }),
      h('path', { d: 'M2 8l6 2 6-2' }),
      h('path', { d: 'M2 12l6 2 6-2' }),
    ]),
    'icon-hardware': svg(() => [
      h('rect', { x: 3, y: 3, width: 10, height: 10, rx: 1 }),
      h('rect', { x: 5.5, y: 5.5, width: 5, height: 5, rx: 0.5 }),
    ]),
    'icon-backends': svg(() => [
      h('circle', { cx: 4, cy: 4, r: 2 }),
      h('circle', { cx: 12, cy: 4, r: 2 }),
      h('circle', { cx: 4, cy: 12, r: 2 }),
      h('circle', { cx: 12, cy: 12, r: 2 }),
      h('path', { d: 'M6 4h4M4 6v4M12 6v4M6 12h4' }),
    ]),
    'icon-logs': svg(() => [
      h('path', { d: 'M3 3h10M3 6h10M3 9h7M3 12h5' }),
    ]),
    'icon-agent': svg(() => [
      h('circle', { cx: 8, cy: 6, r: 2.5 }),
      h('path', { d: 'M3 14c0-2.5 2.2-4.5 5-4.5s5 2 5 4.5' }),
    ]),
    'icon-mcp': svg(() => [
      h('rect', { x: 2, y: 4, width: 5, height: 8, rx: 0.6 }),
      h('rect', { x: 9, y: 4, width: 5, height: 8, rx: 0.6 }),
      h('path', { d: 'M7 8h2' }),
    ]),
    'icon-memory': svg(() => [
      h('path', { d: 'M3 6a3 3 0 0 1 6-1 3 3 0 0 1 6 1 3 3 0 0 1-2 3l2 3a3 3 0 0 1-3 2 3 3 0 0 1-3-1.5A3 3 0 0 1 6 14a3 3 0 0 1-3-2l2-3a3 3 0 0 1-2-3z' }),
    ]),
    'icon-settings': svg(() => [
      h('circle', { cx: 8, cy: 8, r: 2 }),
      h('path', { d: 'M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3' }),
    ]),
  },
}
</script>

<style scoped>
.sidebar {
  grid-column: 1;
  grid-row: 2;
  border-right: 1px solid var(--color-border);
  background: var(--color-bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-size: 13px;
  transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}

/* Icon-collapse rail (1080-1279) */
.sidebar.collapsed .sb-section,
.sidebar.collapsed .sb-group-h,
.sidebar.collapsed .lbl,
.sidebar.collapsed .cnt,
.sidebar.collapsed .nudge,
.sidebar.collapsed .sb-status .row .k,
.sidebar.collapsed .sb-status .row .v:not(.up):not(.warn):not(.err) {
  display: none;
}
.sidebar.collapsed .sb-row {
  justify-content: center;
  padding: 8px;
}
.sidebar.collapsed .sb-status {
  padding: 8px;
}

/* Drawer (720-1079) */
.sidebar.drawer {
  position: fixed;
  top: 52px;
  left: 0;
  bottom: 0;
  width: min(260px, 85vw);
  z-index: 50;
  box-shadow: 8px 0 32px -8px rgba(0, 0, 0, 0.7);
  transform: translateX(-100%);
}
.sidebar.drawer.drawer-open {
  transform: translateX(0);
}

.sb-section {
  padding: 14px 12px 6px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-fg-faint);
}

.sb-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 0 8px;
}

.sb-row {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 7px 10px;
  border-radius: var(--radius, 6px);
  cursor: pointer;
  color: var(--color-fg-muted);
  position: relative;
  font-weight: 400;
  text-decoration: none;
  font-family: var(--font-mono);
  font-size: 12px;
}
.sb-row:hover {
  background: var(--color-surface-2);
  color: var(--color-fg);
}
.sb-row.active {
  background: var(--color-accent-bg);
  color: var(--color-fg);
}
.sb-row.active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 6px;
  bottom: 6px;
  width: 1px;
  background: var(--hal0-accent);
  border-radius: 1px;
  box-shadow: 0 0 8px -1px var(--hal0-accent);
}
.sb-row.disabled {
  opacity: 0.45;
  cursor: not-allowed;
  pointer-events: none;
}
.sb-row .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sb-row .sb-ico { flex-shrink: 0; }

.cnt {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-fg-faint);
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--color-surface-2);
}
.sb-row.active .cnt {
  background: color-mix(in srgb, var(--hal0-accent) 18%, transparent);
  color: var(--hal0-accent);
}
.cnt.dim { background: transparent; }

/* Agents v0.3 sub-group */
.sb-group {
  border-top: 1px dashed var(--color-border);
  margin-top: 6px;
  padding-top: 4px;
}
.sb-group-h {
  padding: 8px 10px 4px;
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-fg-faint);
}
.sb-sub {
  padding-left: 14px;
}

.sb-cta {
  border-top: 1px dashed var(--color-border);
  margin-top: 6px;
  padding-top: 9px;
  color: var(--hal0-accent);
}

.sb-spacer { flex: 1; }

/* Lemonade status block */
.sb-status {
  margin: 12px 12px 14px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius, 6px);
  background: var(--color-surface);
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
}
.sb-status:hover { border-color: var(--color-border-hi); }
.sb-status .row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
}
.sb-status .row .k { color: var(--color-fg-faint); }
.sb-status .row .v { color: var(--color-fg); display: inline-flex; align-items: center; gap: 6px; }
.sb-status .row .v.up { color: var(--color-success); }
.sb-status .row .v.warn { color: var(--color-warn, #e8b94e); }
.sb-status .row .v.err { color: var(--color-danger); }
.sb-status .row .v .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}
.sb-status .ln {
  height: 1px;
  background: var(--color-border);
  margin: 6px 0;
}
.sb-status .nudge {
  color: var(--color-fg-faint);
  font-size: 10px;
  margin-top: 4px;
}
.sb-status:hover .nudge { color: var(--hal0-accent); }
</style>
