<script setup>
/**
 * TopBar.vue — v2 dash chrome (slice #168).
 *
 * Layout left → right:
 *   Wordmark + version chip │ route eyebrow │ spacer │
 *   ⌘K btn │ host chip │ AgentApprovalBell
 *
 * The wordmark+version block is duplicated across the dashboard's
 * marketing surface; it stays here at 18px to match the v0.3 design
 * source (/tmp/hal0-design-v3/dash/chrome.jsx ~lines 87–115).
 *
 * Mobile (<720px): the hamburger replaces the wordmark block (the
 * <BottomTabs> takes over primary nav).
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useLemonadeStore } from '../stores/lemonade.js'
import { useToastStore } from '../stores/toast.js'
import Wordmark from './Wordmark.vue'
import AgentApprovalBell from './AgentApprovalBell.vue'
import Menu from './primitives/Menu.vue'
import { ref } from 'vue'

const props = defineProps({
  isMobile: Boolean,
  sidebarOpen: Boolean,
})
const emit = defineEmits(['toggle-sidebar', 'open-cmdk'])

const route    = useRoute()
const system   = useSystemStore()
const lemonade = useLemonadeStore()
const toasts   = useToastStore()

// ── Version ──────────────────────────────────────────────────────────
const version = computed(() => system.status?.version ?? null)

// ── Eyebrow ──────────────────────────────────────────────────────────
// Matches the v0.3 chrome.jsx labels table: [eyebrow, title] per route
// name. ``firstrun`` suppresses the breadcrumb so the wizard reads
// clean.
const ROUTE_LABELS = {
  dashboard:  ['Overview',  'Dashboard'],
  firstrun:   ['Setup',     'FirstRun'],
  slots:      ['Lifecycle', 'Slots'],
  'slot-detail': ['Lifecycle', 'Slots'],
  models:     ['Catalog',   'Models'],
  hardware:   ['System',    'Hardware'],
  backends:   ['Runtime',   'Backends'],
  providers:  ['Runtime',   'Providers'],
  logs:       ['Runtime',   'Logs'],
  agent:      ['Tools',     'Agent'],
  'agents-mcp':    ['Tools', 'MCP Servers'],
  'agents-memory': ['Tools', 'Memory'],
  settings:   ['Configure', 'Settings'],
}

const eyebrow = computed(() => {
  const name = route.name ? String(route.name) : ''
  return ROUTE_LABELS[name] || ['', '']
})

const showEyebrow = computed(() => route.name !== 'firstrun' && eyebrow.value[0])

// ── Host chip ───────────────────────────────────────────────────────
// Reads hostname from /api/status (system store). Uptime is a placeholder
// — when /v1/health surfaces a host uptime in a future Lemonade build it
// can drop in here without a UI change.
const hostname = computed(() => system.status?.hostname || 'hal0')
const hostUptime = computed(() => {
  // /api/status does not yet carry uptime; show the lemond version as a
  // poor-man's "we're connected to something" once it lands, otherwise
  // ``up`` with no value (the design tolerates the empty case).
  const v = lemonade.version
  return v ? `lemond ${v}` : 'up'
})
const hostHealthy = computed(() => lemonade.health === 'up')

// ── Overflow menu ────────────────────────────────────────────────────
// `⋯` next to the host chip; jumps to external surfaces (Chat Pro UI,
// docs, GitHub, Discord). Items go through useToastStore for the
// stub-Discord case so the operator sees feedback.
const overflowBtnEl = ref(null)
const overflowOpen  = ref(false)

function openExternal(href, label) {
  // Use a real anchor so target=_blank semantics + noopener apply
  // even though we trigger it programmatically.
  const a = document.createElement('a')
  a.href = href
  a.target = '_blank'
  a.rel = 'noopener noreferrer'
  // Tag the element so the spec can find it in the DOM after the click.
  if (label) a.setAttribute('data-overflow-label', label)
  document.body.appendChild(a)
  a.click()
  // Leave the element in the DOM briefly so the spec can read target.
  setTimeout(() => a.remove(), 50)
}

const overflowItems = [
  {
    label: 'Open Chat Pro UI',
    onClick: () => openExternal('https://hal0-chat.thinmint.dev', 'chat-pro'),
  },
  {
    label: 'Docs',
    onClick: () => openExternal('https://hal0.dev/docs/v0.2-upgrade', 'docs'),
  },
  {
    label: 'GitHub',
    onClick: () => openExternal('https://github.com/Hal0ai/hal0', 'github'),
  },
  {
    label: 'Discord',
    // Placeholder URL — the real invite lands once the community
    // server is provisioned. Toast surfaces the "coming soon" hint.
    onClick: () => {
      toasts.push('Discord link coming', 'info')
    },
  },
]

function toggleOverflow() {
  overflowOpen.value = !overflowOpen.value
}

function closeOverflow() {
  overflowOpen.value = false
}

// ── ⌘K stub ──────────────────────────────────────────────────────────
// The real command palette lands in slice #175; until then, a click on
// the trigger surfaces a toast through the v2 store so operators see
// the affordance is wired.
function onCmdK() {
  emit('open-cmdk')
  toasts.push('Command palette — slice #12', 'info')
}
</script>

<template>
  <header class="topbar" role="banner" aria-label="Top navigation bar">
    <!-- Brand block: hamburger (mobile) + wordmark + version pill -->
    <div class="tb-brand">
      <button
        v-if="isMobile"
        class="tb-hamburger"
        type="button"
        :aria-label="sidebarOpen ? 'Close menu' : 'Open menu'"
        :aria-expanded="sidebarOpen"
        @click="emit('toggle-sidebar')"
      >
        <svg v-if="sidebarOpen" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path d="M4 4l8 8M12 4l-8 8" stroke-linecap="round" />
        </svg>
        <svg v-else width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path d="M3 5h10M3 8h10M3 11h10" stroke-linecap="round" />
        </svg>
      </button>
      <Wordmark size="text-base" />
      <span
        v-if="version"
        class="ver mono"
        :title="`Version ${version}`"
      >v{{ version }}</span>
    </div>

    <!-- Route eyebrow (centre-left) -->
    <div v-if="showEyebrow" class="tb-eyebrow mono" aria-hidden="true">
      <span class="seg">{{ eyebrow[0] }}</span>
      <span class="sep">/</span>
      <span class="now">{{ eyebrow[1] }}</span>
    </div>

    <div class="tb-spacer" />

    <!-- ⌘K trigger -->
    <button class="tb-cmdk" type="button" @click="onCmdK" aria-label="Open command palette (Ctrl+K)">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
        <circle cx="7" cy="7" r="4" />
        <path d="M10 10l3 3" stroke-linecap="round" />
      </svg>
      <span class="cmdk-text">Command palette</span>
      <kbd>⌘K</kbd>
    </button>

    <!-- Host chip -->
    <div
      class="tb-host"
      :class="{ down: !hostHealthy }"
      :title="`Connected host: ${hostname}`"
      role="status"
    >
      <span class="host-dot" />
      <b>{{ hostname }}</b>
      <span class="ut">· {{ hostUptime }}</span>
    </div>

    <!-- Overflow menu (slice #175). External-link jumps live here so
         the TopBar stays uncluttered. -->
    <button
      ref="overflowBtnEl"
      class="tb-overflow"
      type="button"
      :aria-expanded="overflowOpen"
      aria-haspopup="menu"
      aria-label="More options"
      data-testid="topbar-overflow"
      @click="toggleOverflow"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <circle cx="5"  cy="12" r="1.6" />
        <circle cx="12" cy="12" r="1.6" />
        <circle cx="19" cy="12" r="1.6" />
      </svg>
    </button>

    <Menu
      :open="overflowOpen"
      :anchor="overflowBtnEl"
      :items="overflowItems"
      :on-close="closeOverflow"
      side="right"
    />

    <!-- Agent approvals bell (canonical surface per ADR-0004 §5) -->
    <AgentApprovalBell />
  </header>
</template>

<style scoped>
.topbar {
  grid-column: 1 / -1;
  grid-row: 1;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 20px 0 18px;
  height: 52px;
  background: var(--color-bg, #0a0a0a);
  border-bottom: 1px solid var(--color-border, #2a2a2a);
  position: relative;
  z-index: 30;
}

/* Amber halo under the topbar — picked up from the marketing site. */
.topbar::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(
    to right,
    transparent 0%,
    color-mix(in srgb, var(--hal0-accent) 55%, transparent) 50%,
    transparent 100%
  );
  pointer-events: none;
}

/* ── Brand block ───────────────────────────────────────────────── */
.tb-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-mono);
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  width: calc(232px - 18px);
  flex-shrink: 0;
}

.tb-hamburger {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius, 6px);
  color: var(--color-fg-muted);
  cursor: pointer;
}
.tb-hamburger:hover {
  background: var(--color-surface-2);
  color: var(--color-fg);
}

.ver {
  color: var(--color-fg-faint);
  font-size: 10px;
  letter-spacing: 0.04em;
  font-feature-settings: 'zero' 1;
  font-family: var(--font-mono);
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--color-surface-2);
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 25%, var(--color-border));
}

/* ── Route eyebrow ─────────────────────────────────────────────── */
.tb-eyebrow {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-muted);
  display: flex;
  align-items: center;
  gap: 8px;
  letter-spacing: 0.02em;
}
.tb-eyebrow .sep { color: var(--color-fg-faint); }
.tb-eyebrow .now { color: var(--color-fg); font-weight: 500; }

.tb-spacer { flex: 1; }

/* ── ⌘K trigger ────────────────────────────────────────────────── */
.tb-cmdk {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 9px;
  height: 30px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius, 6px);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
}
.tb-cmdk:hover {
  color: var(--color-fg);
  border-color: var(--color-border-hi);
}
.tb-cmdk kbd {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-fg-muted);
}

/* ── Host chip ─────────────────────────────────────────────────── */
.tb-host {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius, 6px);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-muted);
  cursor: default;
}
.tb-host b {
  color: var(--color-fg);
  font-weight: 500;
}
.tb-host .ut { color: var(--color-fg-faint); }
.tb-host .host-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
  box-shadow: 0 0 8px var(--color-success);
}
.tb-host.down .host-dot {
  background: var(--color-danger);
  box-shadow: 0 0 6px var(--color-danger);
}

/* ── Overflow ⋯ button ─────────────────────────────────────────── */
.tb-overflow {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius, 6px);
  color: var(--color-fg-muted);
  cursor: pointer;
}
.tb-overflow:hover {
  color: var(--color-fg);
  border-color: var(--color-border-hi);
}

/* ── Mobile <720 ───────────────────────────────────────────────── */
@media (max-width: 719px) {
  .topbar { padding: 0 10px; gap: 8px; }
  .tb-brand { width: auto; gap: 6px; }
  .tb-eyebrow { display: none; }
  .tb-cmdk .cmdk-text,
  .tb-cmdk kbd { display: none; }
  .tb-host .ut { display: none; }
}
</style>
