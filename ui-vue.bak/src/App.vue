<script setup>
/**
 * App.vue — v2 dash chrome shell (slice #168).
 *
 * Lays out TopBar / Sidebar / main / Footer + the mobile-only
 * BottomTabs. Owns the responsive breakpoint resolution; each chrome
 * piece takes its variant as a prop so they stay dumb to viewport
 * math.
 *
 * Breakpoints (matches issue #168 acceptance):
 *   ≥ 1280  desktop full           sidebar full (232)
 *   1080-1279  desktop dense       sidebar icon-collapse (56)
 *   720-1079   tablet              sidebar overlay drawer
 *   < 720     mobile               sidebar hidden, BottomTabs on
 *
 * Mounts:
 *   <BannerStack scope="global"/> above the route view so banners from
 *   useBannerStore render everywhere.
 *   <ToastStack/> for the v2 toast queue; the legacy v1 <ToastContainer/>
 *   stays mounted alongside since useApi.js still uses the old store.
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSystemStore } from './stores/system.js'
import { useNuclearEvictBanner } from './composables/useNuclearEvictBanner.js'
import TopBar from './components/TopBar.vue'
import Sidebar from './components/Sidebar.vue'
import Footer from './components/Footer.vue'
import BottomTabs from './components/BottomTabs.vue'
import ToastContainer from './components/ToastContainer.vue'
import CommandPalette from './components/CommandPalette.vue'
import RestartBanner from './components/RestartBanner.vue'
import BannerStack from './components/primitives/BannerStack.vue'
import ToastStack from './components/primitives/ToastStack.vue'

const system = useSystemStore()
const route  = useRoute()
const router = useRouter()

// The primitives sandbox mounts its OWN BannerStack + ToastStack; the
// app-level instances are suppressed there to avoid duplicate DOM in
// the sandbox spec (matches the slice #167 spec assertions).
const isSandbox = computed(() => route.name === 'primitives-sandbox')

// PR-11: subscribe once at the app shell so the nuclear-evict toast
// banner fires on every dashboard route. Also kicks
// useLemonadeStore.init() so /v1/health polling runs while the
// dashboard is open.
useNuclearEvictBanner()

// ── Breakpoint resolution ─────────────────────────────────────────
// Four bands; each band drives a boolean prop. Computed flags rather
// than CSS-only because Sidebar + BottomTabs need to know mode for
// their internal layout (e.g. drawer transform / hidden state).
const vw = ref(typeof window !== 'undefined' ? window.innerWidth : 1280)
function onResize() { vw.value = window.innerWidth }

const isFull     = computed(() => vw.value >= 1280)
const isCollapsed = computed(() => vw.value >= 1080 && vw.value < 1280)
const isDrawer   = computed(() => vw.value >= 720 && vw.value < 1080)
const isMobile   = computed(() => vw.value < 720)

const sidebarOpen = ref(true)   // for drawer mode

function toggleSidebar() {
  if (isDrawer.value || isMobile.value) {
    sidebarOpen.value = !sidebarOpen.value
  }
}

// Auto-close drawer on route change so the user isn't trapped behind it.
watch(() => route.fullPath, () => {
  if (isDrawer.value) sidebarOpen.value = false
})

// On breakpoint change, reset the drawer to closed so a resize from
// drawer-open → full doesn't strand the overlay open.
watch(isDrawer, (newVal, oldVal) => {
  if (newVal !== oldVal) sidebarOpen.value = false
})

// ── Command palette stub ──────────────────────────────────────────
const cmdkOpen = ref(false)

function onCmdKOpen() {
  cmdkOpen.value = true
}

function onCmdkSelect(item) {
  cmdkOpen.value = false
  if (item?.to) router.push(item.to)
  else if (typeof item?.handler === 'function') item.handler()
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
  if (e.key === 'Escape') {
    if (cmdkOpen.value) { cmdkOpen.value = false; return }
    if (isDrawer.value && sidebarOpen.value) sidebarOpen.value = false
  }
}

let pollInterval = null
onMounted(() => {
  window.addEventListener('resize', onResize, { passive: true })
  window.addEventListener('keydown', handleKey)
  onResize()
  // Default drawer state: open on full/collapsed, closed on drawer/mobile.
  sidebarOpen.value = !(isDrawer.value || isMobile.value)
  system.fetchStatus()
  pollInterval = setInterval(() => system.fetchStatus(), 5000)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', handleKey)
  clearInterval(pollInterval)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'is-full':      isFull,
      'is-collapsed': isCollapsed,
      'is-drawer':    isDrawer,
      'is-mobile':    isMobile,
      'drawer-open':  isDrawer && sidebarOpen,
    }"
    data-testid="app-shell"
  >
    <!-- Skip-link — visible only on focus, jumps to the route view.
         First focusable element in the document so Tab from page top
         exposes it before any other UI. -->
    <a href="#main-content" class="skip-link" data-testid="skip-link">
      Skip to main content
    </a>

    <TopBar
      :is-mobile="isMobile || isDrawer"
      :sidebar-open="sidebarOpen"
      @toggle-sidebar="toggleSidebar"
      @open-cmdk="onCmdKOpen"
    />

    <Sidebar
      v-if="!isMobile"
      :open="sidebarOpen"
      :is-drawer="isDrawer"
      :is-collapsed="isCollapsed"
      @navigate="isDrawer && (sidebarOpen = false)"
    />

    <!-- Drawer backdrop -->
    <div
      v-if="isDrawer && sidebarOpen"
      class="mobile-backdrop"
      aria-hidden="true"
      @click="sidebarOpen = false"
    />

    <main class="app-main" id="main-content" tabindex="-1">
      <!-- Global banners — render on every route. Suppressed on the
           primitives sandbox so its own scoped BannerStack stays the
           sole instance. -->
      <BannerStack v-if="!isSandbox" scope="global" />

      <!-- Pre-v2 banner shim. Keeps the slice #163 nuclear-evict path
           rendering while v2 absorbs it. -->
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

    <Footer v-if="!isMobile" />

    <BottomTabs v-if="isMobile" />

    <!-- Legacy v1 toast queue (useToastsStore — still used by useApi.js) -->
    <ToastContainer />
    <!-- v2 primitives toast queue (useToastStore — slice #167 onwards).
         Suppressed on the primitives sandbox so its own ToastStack
         stays the sole instance. -->
    <ToastStack v-if="!isSandbox" />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 232px 1fr;
  grid-template-rows: 52px 1fr 48px;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-bg);
}

/* ≥ 1280 (full) is the default. */

/* 1080-1279 — icon-collapse rail. */
.app-shell.is-collapsed {
  grid-template-columns: 56px 1fr;
}

/* 720-1079 — drawer; collapse the main grid to a single column so the
   overlay sidebar floats over content. */
.app-shell.is-drawer {
  grid-template-columns: 1fr;
  grid-template-areas:
    "topbar"
    "main"
    "footer";
}

/* < 720 — no sidebar in grid, no footer (BottomTabs replaces). */
.app-shell.is-mobile {
  grid-template-columns: 1fr;
  grid-template-rows: 52px 1fr 56px;
  grid-template-areas:
    "topbar"
    "main"
    "footer";
}

.app-main {
  grid-column: 2;
  grid-row: 2;
  overflow-y: auto;
  min-width: 0;
  min-height: 0;
}

.app-shell.is-drawer .app-main,
.app-shell.is-mobile .app-main {
  grid-column: 1;
}

.mobile-backdrop {
  position: fixed;
  inset: 52px 0 0 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(2px);
  z-index: 40;
  animation: bd-in 0.16s ease;
}
@keyframes bd-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}

/* Fade transition for the route view */
.fade-enter-active, .fade-leave-active { transition: opacity 0.12s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
