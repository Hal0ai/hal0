<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSlotStats } from '../composables/useSlotStats.js'
import { useAgentStore } from '../stores/agent.js'
import { api } from '../composables/useApi.js'

const props = defineProps({
  open: Boolean,
  isMobile: Boolean,
})
const emit = defineEmits(['toggle', 'navigate'])

const route  = useRoute()
const agent  = useAgentStore()

const { running, total } = useSlotStats()

// ── Agent link (Phase 8) ───────────────────────────────────────────
// When a service-shape agent is installed (Hermes) the sidebar link
// goes OWUI-style — opens the agent's own web surface in a new tab.
// CLI shape (pi-coder) and "no agent installed" both route to the
// in-dashboard /agent page where the picker or transcript live.
//
// The hostname follows the same pattern as the OpenWebUI link below:
// resolved at runtime from /api/config/urls so a remote LAN browser
// gets the right hostname, not a hardcoded localhost.
const hermesUrl = ref('')
async function loadHermesUrl() {
  try {
    const r = await api('/api/config/urls')
    hermesUrl.value = r?.hermes ?? ''
  } catch { /* leave hermesUrl empty */ }
}

const agentItem = computed(() => {
  if (agent.shape === 'service' && hermesUrl.value) {
    return {
      label: 'Agent',
      external: true,
      href: hermesUrl.value,
      icon: 'M9 17H7A5 5 0 017 7h2m6 0h2a5 5 0 010 10h-2M8 12h8',
    }
  }
  return {
    to: '/agent',
    label: 'Agent',
    external: false,
    icon: 'M9 17H7A5 5 0 017 7h2m6 0h2a5 5 0 010 10h-2M8 12h8',
  }
})

// ── OpenWebUI chat link ────────────────────────────────────────────
// /api/config/urls returns the live hostnames + a runtime flag for
// whether hal0-openwebui.service is active. We refuse to render the
// link until the API answers — a hardcoded localhost:3001 used to ship
// here, which broke for anyone hitting the dashboard from another
// machine on the LAN.
const chatUrl = ref('')
const chatEnabled = ref(false)

async function loadChatUrl() {
  try {
    const r = await api('/api/config/urls')
    chatUrl.value = r?.openwebui ?? ''
    chatEnabled.value = !!r?.openwebui_enabled
  } catch {
    // Leave chatEnabled = false; the link stays hidden rather than
    // dangling at a 404 if the API is down.
  }
}

onMounted(() => {
  loadChatUrl()
  loadHermesUrl()
  // Pull initial agent state so the sidebar's link branches correctly
  // on first render even if the bell hasn't mounted yet.
  agent.fetchInstalled()
})

// ── Nav definition ─────────────────────────────────────────────────
const NAV_ITEMS = [
  {
    to: '/',
    label: 'Dashboard',
    icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6',
  },
  {
    to: '/slots',
    label: 'Slots',
    icon: 'M5 12h14M12 5l7 7-7 7',
  },
  {
    to: '/models',
    label: 'Models',
    icon: 'M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4',
  },
  {
    to: '/hardware',
    label: 'Hardware',
    icon: 'M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18',
  },
  {
    to: '/logs',
    label: 'Logs',
    icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  },
  {
    to: '/providers',
    label: 'Providers',
    icon: 'M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9',
  },
  {
    to: '/settings',
    label: 'Settings',
    icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065zM15 12a3 3 0 11-6 0 3 3 0 016 0z',
  },
]

// "Agent" lives between Providers and Settings — group with the other
// dashboard surfaces that touch installed surfaces, not with the inline
// model/slot management above. Insert at runtime so the link can
// switch between internal (router-link → /agent) and external
// (href → hermes web UI) without splitting the markup into two loops.
const NAV_AGENT_INDEX = NAV_ITEMS.findIndex((i) => i.to === '/settings')
const navItems = computed(() => {
  const items = [...NAV_ITEMS]
  items.splice(NAV_AGENT_INDEX, 0, agentItem.value)
  return items
})

function isActive(item) {
  if (item.external) return false
  if (item.to === '/') return route.path === '/'
  return route.path.startsWith(item.to)
}

function onNavClick() {
  emit('navigate')
}
</script>

<template>
  <aside
    class="sidebar"
    :class="{ collapsed: !open }"
    role="navigation"
    aria-label="Main navigation"
  >
    <nav class="nav" aria-label="Primary">
      <template v-for="item in navItems" :key="item.to || item.href">
        <!-- External (Hermes service-shape link-out, OWUI-style) -->
        <a
          v-if="item.external"
          :href="item.href"
          target="_blank"
          rel="noopener noreferrer"
          class="nav-item"
          :title="item.label"
          @click="onNavClick"
        >
          <svg
            class="nav-icon"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            stroke-width="1.6"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" :d="item.icon" />
          </svg>
          <span v-if="open" class="nav-label">{{ item.label }}</span>
          <svg v-if="open" class="ext-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
          </svg>
        </a>
        <!-- Internal router target -->
        <router-link
          v-else
          :to="item.to"
          class="nav-item"
          :class="{ active: isActive(item) }"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="onNavClick"
        >
          <svg
            class="nav-icon"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            stroke-width="1.6"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" :d="item.icon" />
          </svg>
          <span v-if="open" class="nav-label">{{ item.label }}</span>
          <!-- Pending-approval pill, only on the Agent item when there are
               pending approvals queued. Mirrors the bell badge so an
               operator scanning the sidebar sees the same count. -->
          <span
            v-if="open && item.to === '/agent' && agent.pendingCount > 0"
            class="nav-badge"
            :aria-label="`${agent.pendingCount} pending approval${agent.pendingCount === 1 ? '' : 's'}`"
          >{{ agent.pendingCount }}</span>
        </router-link>
      </template>
    </nav>

    <!-- External link: OpenWebUI. Href is resolved from /api/config/urls -->
    <!-- so the link points at the host the dashboard was loaded from,    -->
    <!-- not a hardcoded localhost. Hidden when the unit isn't active.    -->
    <div class="sidebar-footer-links" v-if="open && chatEnabled && chatUrl">
      <a
        :href="chatUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="nav-item external"
        title="Open OpenWebUI chat interface"
      >
        <svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
        </svg>
        <span class="nav-label">Open Chat</span>
        <svg class="ext-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
        </svg>
      </a>
    </div>

    <!-- Slot count badge (footer) -->
    <div class="sidebar-status" :class="{ 'sidebar-status-collapsed': !open }">
      <span class="status-dot" :class="running > 0 ? 'live' : 'idle'" aria-hidden="true" />
      <span v-if="open" class="status-text" :class="{ live: running > 0 }">
        {{ running }}/{{ total }} slot{{ total !== 1 ? 's' : '' }} running
      </span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  grid-column: 1;
  grid-row: 2;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  overflow: hidden;
  transition: transform 0.2s ease;
}

/* Mobile off-canvas */
:global(.app-shell.is-mobile .sidebar) {
  position: fixed;
  top: 44px;
  left: 0;
  bottom: 0;
  width: min(260px, 85vw);
  z-index: 50;
  box-shadow: 8px 0 32px -8px rgba(0, 0, 0, 0.7);
  transform: translateX(-100%);
}
:global(.app-shell.is-mobile.mobile-nav-open .sidebar) {
  transform: translateX(0);
}
:global(.app-shell.is-mobile .sidebar.collapsed) {
  width: min(260px, 85vw);
}

/* ── Nav ──────────────────────────────────────────────────────── */
.nav {
  flex: 1;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius);
  color: var(--color-fg-muted);
  text-decoration: none;
  /* Mono-typographic nav, matching the marketing site's .hal0-nav items. */
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  font-feature-settings: 'zero' 1;
  letter-spacing: -0.01em;
  position: relative;
  user-select: none;
  transition: background 0.1s, color 0.1s;
  white-space: nowrap;
}

.nav-item:hover {
  background: var(--color-surface-2);
  color: var(--color-fg);
}

.nav-item.active {
  background: var(--color-accent-bg);
  color: var(--color-accent);
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--color-accent);
  /* Faint amber halo so the rail reads as a lit filament, not a stripe. */
  box-shadow: 0 0 12px -2px var(--hal0-accent);
}

.nav-item.external {
  color: var(--color-fg-faint);
  font-size: 12px;
}

.nav-item.external:hover .nav-label {
  border-bottom: 1px solid color-mix(in srgb, var(--hal0-accent) 60%, transparent);
}

.nav-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.nav-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ext-icon {
  width: 11px;
  height: 11px;
  opacity: 0.4;
  flex-shrink: 0;
}

/* Pending-approval badge on the Agent nav item. Mirrors the topbar bell. */
.nav-badge {
  min-width: 16px;
  height: 16px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  font-feature-settings: 'zero' 1, 'tnum' 1;
  display: grid;
  place-items: center;
  line-height: 1;
  margin-left: auto;
  flex-shrink: 0;
}

/* Collapsed rail: icon only */
.sidebar.collapsed .nav-item {
  justify-content: center;
  padding: 8px;
}
.sidebar.collapsed .nav-label,
.sidebar.collapsed .ext-icon {
  display: none;
}
.sidebar.collapsed .nav-item.active::before {
  display: none;
}

/* ── Footer links ─────────────────────────────────────────────── */
.sidebar-footer-links {
  padding: 4px 8px;
  border-top: 1px solid var(--color-border);
}

/* ── Status ───────────────────────────────────────────────────── */
.sidebar-status {
  padding: 10px 14px;
  border-top: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.sidebar-status-collapsed {
  justify-content: center;
  padding: 10px 8px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.3s;
}

.status-dot.live {
  background: var(--color-success);
  /* Brighter halo when slots are running; pair with status-text.live. */
  box-shadow: 0 0 10px 0 var(--color-success);
}

.status-dot.idle {
  background: var(--color-fg-faint);
}

.status-text {
  font-family: var(--font-mono);
  font-size: 10.5px;
  /* Slashed zero so "0/3 slots running" reads in the wordmark's voice. */
  font-feature-settings: 'zero' 1;
  letter-spacing: 0.02em;
  color: var(--color-fg-faint);
  white-space: nowrap;
  transition: color 0.2s, text-shadow 0.2s;
}

.status-text.live {
  color: var(--color-fg-muted);
  text-shadow: 0 0 8px var(--hal0-accent-glow);
}
</style>
