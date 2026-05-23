<script setup>
/**
 * BottomTabs.vue — v2 dash chrome (slice #168).
 *
 * Visible on <720px viewports only. Five fixed tabs at the bottom of
 * the viewport: Home · Slots · Models · Logs · More.
 *
 * "More" toggles a sheet listing the secondary surfaces (Hardware,
 * Backends, Settings, Agent). The sheet itself is a small overlay
 * that closes on outside click / Esc / nav.
 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const moreOpen = ref(false)

const TABS = [
  { id: 'home',   to: '/',       label: 'Home',   icon: 'home' },
  { id: 'slots',  to: '/slots',  label: 'Slots',  icon: 'slots' },
  { id: 'models', to: '/models', label: 'Models', icon: 'models' },
  { id: 'logs',   to: '/logs',   label: 'Logs',   icon: 'logs' },
  { id: 'more',   to: null,      label: 'More',   icon: 'more' },
]

const MORE_ITEMS = [
  { to: '/hardware',  label: 'Hardware' },
  { to: '/backends',  label: 'Backends' },
  { to: '/agent',     label: 'Agent'    },
  { to: '/settings',  label: 'Settings' },
]

function isActive(tab) {
  if (tab.id === 'more') return false
  if (tab.to === '/') return route.path === '/'
  return route.path === tab.to || route.path.startsWith(tab.to + '/')
}

function onTab(tab) {
  if (tab.id === 'more') {
    moreOpen.value = !moreOpen.value
    return
  }
  moreOpen.value = false
  router.push(tab.to)
}

function onMore(item) {
  moreOpen.value = false
  router.push(item.to)
}

function onKey(e) {
  if (e.key === 'Escape' && moreOpen.value) moreOpen.value = false
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <nav class="bottom-tabs" aria-label="Primary mobile navigation" data-testid="bottom-tabs">
    <div v-if="moreOpen" class="more-sheet" role="menu" data-testid="bottom-tabs-more">
      <button
        v-for="it in MORE_ITEMS"
        :key="it.to"
        type="button"
        class="more-row"
        role="menuitem"
        @click="onMore(it)"
      >{{ it.label }}</button>
    </div>
    <div
      v-if="moreOpen"
      class="more-backdrop"
      aria-hidden="true"
      @click="moreOpen = false"
    />
    <button
      v-for="t in TABS"
      :key="t.id"
      type="button"
      class="bottom-tab"
      :class="{ active: isActive(t), 'more-open': t.id === 'more' && moreOpen }"
      :aria-current="isActive(t) ? 'page' : undefined"
      :data-testid="`bottom-tab-${t.id}`"
      @click="onTab(t)"
    >
      <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <template v-if="t.icon === 'home'">
          <path d="M2 7l6-5 6 5v7H2V7z" />
          <path d="M6 14V9h4v5" />
        </template>
        <template v-else-if="t.icon === 'slots'">
          <rect x="2" y="3" width="12" height="3" rx="0.5" />
          <rect x="2" y="7" width="12" height="3" rx="0.5" />
          <rect x="2" y="11" width="12" height="3" rx="0.5" />
        </template>
        <template v-else-if="t.icon === 'models'">
          <path d="M2 4l6-2 6 2-6 2-6-2z" />
          <path d="M2 8l6 2 6-2" />
          <path d="M2 12l6 2 6-2" />
        </template>
        <template v-else-if="t.icon === 'logs'">
          <path d="M3 3h10M3 6h10M3 9h7M3 12h5" />
        </template>
        <template v-else>
          <circle cx="3" cy="8" r="1" fill="currentColor" stroke="none" />
          <circle cx="8" cy="8" r="1" fill="currentColor" stroke="none" />
          <circle cx="13" cy="8" r="1" fill="currentColor" stroke="none" />
        </template>
      </svg>
      <span class="lbl">{{ t.label }}</span>
    </button>
  </nav>
</template>

<style scoped>
.bottom-tabs {
  display: none;
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 80;
  background: var(--color-bg);
  border-top: 1px solid var(--color-border);
  grid-template-columns: repeat(5, 1fr);
  padding-bottom: env(safe-area-inset-bottom);
}
.bottom-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 9px 6px 7px;
  background: transparent;
  border: none;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.02em;
  cursor: pointer;
  position: relative;
}
.bottom-tab.active {
  color: var(--hal0-accent);
}
.bottom-tab.active::after {
  content: '';
  position: absolute;
  top: 0;
  left: 20%;
  right: 20%;
  height: 2px;
  background: var(--hal0-accent);
  border-radius: 0 0 2px 2px;
}
.bottom-tab.more-open { color: var(--hal0-accent); }
.bottom-tab svg { width: 18px; height: 18px; }

.more-sheet {
  position: fixed;
  bottom: 56px;
  right: 8px;
  z-index: 81;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 12px 32px -8px rgba(0, 0, 0, 0.7);
  display: flex;
  flex-direction: column;
  min-width: 160px;
  padding: 6px;
  animation: more-in 0.16s ease;
}
@keyframes more-in {
  from { transform: translateY(8px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}
.more-row {
  padding: 9px 14px;
  background: transparent;
  border: none;
  color: var(--color-fg);
  font-family: var(--font-mono);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  border-radius: 4px;
}
.more-row:hover {
  background: var(--color-surface-2);
}
.more-backdrop {
  position: fixed;
  inset: 0 0 56px 0;
  background: transparent;
  z-index: 79;
}

@media (max-width: 719px) {
  .bottom-tabs {
    display: grid;
  }
}
</style>
