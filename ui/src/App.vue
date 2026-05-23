<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSystemStore } from './stores/system.js'
import { useFooterStore } from './stores/footer.js'
import { useNuclearEvictBanner } from './composables/useNuclearEvictBanner.js'
import Sidebar from './components/Sidebar.vue'
import TopBar from './components/TopBar.vue'
import ToastContainer from './components/ToastContainer.vue'
import CommandPalette from './components/CommandPalette.vue'
import RestartBanner from './components/RestartBanner.vue'
import Footer from './components/footer/Footer.vue'

const system = useSystemStore()
const footer = useFooterStore()
const route  = useRoute()
const router = useRouter()

// PR-11: subscribe once at the app shell so the nuclear-evict toast
// banner fires on every dashboard route. Disconnect happens on unmount.
useNuclearEvictBanner()

// ── Responsive sidebar ────────────────────────────────────────────
// Desktop: sidebarOpen = expanded(true) / collapsed-icon-rail(false)
// Mobile (< md = 768px): off-canvas drawer
const isMobile    = ref(false)
const sidebarOpen = ref(true)
const cmdkOpen    = ref(false)

let mql = null
let pollInterval = null

function syncViewport(initial = false) {
  const mobile = mql ? mql.matches : window.matchMedia('(max-width: 768px)').matches
  if (mobile === isMobile.value && !initial) return
  isMobile.value = mobile
  sidebarOpen.value = !mobile  // default: open on desktop, closed on mobile
}

function isTextInput(el) {
  if (!el) return false
  const tag = (el.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if (el.isContentEditable) return true
  return false
}

function handleKey(e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    cmdkOpen.value = true
    return
  }
  // Alt+~ toggle footer — suppress inside text inputs.
  if (e.altKey && (e.key === '`' || e.key === '~' || e.code === 'Backquote')) {
    if (isTextInput(e.target)) return
    e.preventDefault()
    footer.toggleExpanded()
    return
  }
  // Alt+ArrowLeft/Right cycle main footer tabs (only when expanded).
  if (e.altKey && footer.expanded && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    if (isTextInput(e.target)) return
    e.preventDefault()
    footer.cycleTab(e.key === 'ArrowRight' ? 1 : -1)
    return
  }
  if (e.key === 'Escape') {
    if (cmdkOpen.value) { cmdkOpen.value = false; return }
    if (footer.expanded) { footer.collapse(); return }
    if (isMobile.value && sidebarOpen.value) sidebarOpen.value = false
  }
}

function onCmdkSelect(item) {
  cmdkOpen.value = false
  if (item?.to) router.push(item.to)
  else if (typeof item?.handler === 'function') item.handler()
}

// Close mobile drawer on navigation
watch(() => route.fullPath, () => {
  if (isMobile.value) sidebarOpen.value = false
})

onMounted(() => {
  mql = window.matchMedia('(max-width: 768px)')
  syncViewport(true)
  mql.addEventListener('change', syncViewport)
  window.addEventListener('keydown', handleKey)

  // Initial status fetch + 5s polling
  system.fetchStatus()
  pollInterval = setInterval(() => system.fetchStatus(), 5000)
})

onUnmounted(() => {
  clearInterval(pollInterval)
  mql?.removeEventListener('change', syncViewport)
  window.removeEventListener('keydown', handleKey)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'sidebar-collapsed': !sidebarOpen && !isMobile,
      'is-mobile': isMobile,
      'mobile-nav-open': isMobile && sidebarOpen,
      'footer-expanded': footer.expanded && !isMobile,
    }"
    :style="!isMobile ? { '--footer-row': footer.expanded ? `${28 + footer.height}px` : '28px' } : null"
  >
    <TopBar
      :is-mobile="isMobile"
      :sidebar-open="sidebarOpen"
      @toggle-sidebar="sidebarOpen = !sidebarOpen"
      @open-cmdk="cmdkOpen = true"
    />

    <Sidebar
      :open="sidebarOpen"
      :is-mobile="isMobile"
      @toggle="sidebarOpen = !sidebarOpen"
      @navigate="isMobile && (sidebarOpen = false)"
    />

    <!-- Mobile backdrop -->
    <div
      v-if="isMobile && sidebarOpen"
      class="mobile-backdrop"
      aria-hidden="true"
      @click="sidebarOpen = false"
    />

    <main class="app-main" id="main-content" tabindex="-1">
      <RestartBanner />
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

    <CommandPalette
      :open="cmdkOpen"
      @close="cmdkOpen = false"
      @select="onCmdkSelect"
    />

    <Footer />

    <ToastContainer />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 220px 1fr;
  grid-template-rows: 44px 1fr var(--footer-row, 28px);
  height: 100vh;
  overflow: hidden;
  background: var(--color-bg);
}

.app-shell.sidebar-collapsed {
  grid-template-columns: 56px 1fr;
}

.app-main {
  grid-column: 2;
  grid-row: 2;
  overflow-y: auto;
  min-width: 0;
  min-height: 0;
}

/* ── Mobile (< 768px) ─────────────────────────────────────────── */
.app-shell.is-mobile {
  grid-template-columns: 1fr;
  /* Mobile: footer is bar-only (28px) when collapsed; expanded uses an
     overlay sheet via fixed positioning, not the grid row. */
  grid-template-rows: 44px 1fr 28px;
}

.app-shell.is-mobile .app-main {
  grid-column: 1;
}

.mobile-backdrop {
  position: fixed;
  inset: 44px 0 0 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(2px);
  z-index: 40;
  animation: bd-in 0.16s ease;
}

@keyframes bd-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}
</style>
