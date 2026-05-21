<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { useSystemStore } from '../stores/system.js'
import { useSlotStats } from '../composables/useSlotStats.js'
import Wordmark from './Wordmark.vue'

const props = defineProps({
  isMobile: Boolean,
  sidebarOpen: Boolean,
})
const emit = defineEmits(['toggle-sidebar', 'open-cmdk'])

const system = useSystemStore()

// ── Derived status ─────────────────────────────────────────────────
const apiHealth = computed(() => {
  if (!system.status) return 'unknown'
  if (system.error)   return 'error'
  return 'ok'
})

const statusColor = computed(() => ({
  ok:      'bg-success',
  error:   'bg-danger',
  unknown: 'bg-fg-faint',
}[apiHealth.value]))

const version = computed(() => system.status?.version ?? null)
// Highlight the version pill while we ship prereleases — alpha/beta/rc
// in the version string flips it amber so operators see at a glance
// that this isn't a stable build. Falls back to the muted default once
// we tag a stable v1.0.0.
const isPrerelease = computed(() => {
  const v = version.value ?? ''
  return /-(alpha|beta|rc)\b/i.test(v) || /(a\d+|b\d+|rc\d+)$/i.test(v)
})

const { running: runningSlots, total: totalSlots } = useSlotStats()
</script>

<template>
  <header
    class="topbar"
    role="banner"
    aria-label="Top navigation bar"
  >
    <!-- Left: hamburger + brand -->
    <div class="topbar-brand">
      <button
        class="icon-btn"
        @click="$emit('toggle-sidebar')"
        :aria-label="isMobile && sidebarOpen ? 'Close menu' : 'Toggle sidebar'"
        :aria-expanded="sidebarOpen"
        :aria-controls="'sidebar'"
        type="button"
      >
        <!-- hamburger / close icon -->
        <svg v-if="isMobile && sidebarOpen" width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
        </svg>
        <svg v-else width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16"/>
        </svg>
      </button>

      <Wordmark size="text-base" />
      <span
        v-if="version"
        class="version-pill"
        :class="{ 'version-pill--prerelease': isPrerelease }"
        :title="isPrerelease ? `Prerelease — v${version}. APIs may shift across 0.1.x tags.` : `Version ${version}`"
      >v{{ version }}</span>
    </div>

    <!-- Center: command palette trigger -->
    <div class="topbar-center">
      <button
        class="cmdk-trigger"
        @click="$emit('open-cmdk')"
        :aria-label="'Open command palette (Ctrl+K)'"
        type="button"
      >
        <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z"/>
        </svg>
        <span class="cmdk-text">Search · jump · act…</span>
        <kbd class="kbd" aria-hidden="true">⌘K</kbd>
      </button>
    </div>

    <!-- Right: slot count + status dot -->
    <div class="topbar-right">
      <span v-if="totalSlots > 0" class="slot-stat" aria-label="`${runningSlots} of ${totalSlots} slots running`">
        <span class="slot-dot" :class="runningSlots > 0 ? 'live' : ''" aria-hidden="true" />
        <span class="mono-text">{{ runningSlots }}/{{ totalSlots }}</span>
        <span class="sr-only"> slots running</span>
      </span>

      <!-- API health indicator -->
      <div
        class="health-dot"
        :class="statusColor"
        :title="`API ${apiHealth}`"
        role="status"
        :aria-label="`API status: ${apiHealth}`"
      />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  grid-column: 1 / -1;
  grid-row: 1;
  display: flex;
  align-items: center;
  height: 44px;
  /* Vacuum-tube treatment — solid-state hal0-nav from the marketing site:
   * near-black at 85% with a heavy blur, plus an amber halo glow under
   * the filament that the ::after pseudo paints along the bottom edge. */
  background: rgba(10, 10, 10, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  box-shadow: 0 1px 24px -10px rgba(255, 176, 0, 0.28);
  padding: 0 12px 0 0;
  gap: 8px;
  z-index: 30;
  position: relative;
}

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
    color-mix(in srgb, var(--hal0-accent) 60%, transparent) 50%,
    transparent 100%
  );
  pointer-events: none;
}

/* ── Brand ─────────────────────────────────────────────────────── */
.topbar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px 0 14px;
  height: 100%;
  border-right: 1px solid var(--color-border);
  flex-shrink: 0;
  min-width: 220px;
}

.icon-btn {
  width: 28px;
  height: 28px;
  border-radius: var(--radius);
  display: grid;
  place-items: center;
  background: transparent;
  border: 1px solid transparent;
  color: var(--color-fg-muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s;
}
.icon-btn:hover {
  background: var(--color-surface-2);
  color: var(--color-fg);
}

.version-pill {
  font-family: var(--font-mono);
  font-size: 10px;
  /* Slashed-zero so a "v1.0.0" reads in the wordmark's voice. */
  font-feature-settings: 'zero' 1;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--color-surface-2);
  color: var(--color-fg-faint);
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 25%, var(--hal0-border));
}

.version-pill--prerelease {
  /* Amber-on-amber so operators see at a glance that this is an
     unstable build. Reverts to the muted default for stable tags. */
  background: color-mix(in srgb, var(--hal0-accent) 14%, var(--color-surface-2));
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 55%, transparent);
  font-weight: 600;
}

/* ── Center ────────────────────────────────────────────────────── */
.topbar-center {
  flex: 1;
  display: flex;
  align-items: center;
  padding: 0 12px;
}

.cmdk-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 400px;
  padding: 6px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-faint);
  font-size: 12px;
  font-family: var(--font-mono);
  font-feature-settings: 'zero' 1;
  letter-spacing: -0.01em;
  cursor: pointer;
  transition: border-color 0.1s, color 0.1s;
}
.cmdk-trigger:hover {
  border-color: var(--color-border-hi);
  color: var(--color-fg-muted);
}
.cmdk-text {
  flex: 1;
  text-align: left;
}
.kbd {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-fg-faint);
}

/* ── Right ─────────────────────────────────────────────────────── */
.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.slot-stat {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-fg-muted);
}
.slot-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-fg-faint);
  flex-shrink: 0;
}
.slot-dot.live {
  background: var(--color-success);
  /* Brighter halo when slots are running — "the rack is on". */
  box-shadow: 0 0 10px 0 var(--color-success);
}
.mono-text {
  font-family: var(--font-mono);
  font-size: 11px;
  font-feature-settings: 'zero' 1;
}

.health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.bg-success { background: var(--color-success); box-shadow: 0 0 6px -1px var(--color-success); }
.bg-danger  { background: var(--color-danger); }
.bg-fg-faint { background: var(--color-fg-faint); }

/* ── Mobile ────────────────────────────────────────────────────── */
@media (max-width: 768px) {
  .topbar-brand {
    min-width: unset;
    border-right: none;
    gap: 6px;
    padding: 0 8px 0 10px;
  }
  .topbar-brand :deep(.wordmark),
  .version-pill { display: none; }
  .topbar-center { padding: 0 8px; }
  .slot-stat { display: none; }
}

/* Utility */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
