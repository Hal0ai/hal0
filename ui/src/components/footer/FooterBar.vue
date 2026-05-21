<script setup>
/**
 * FooterBar.vue — the always-visible 28px collapsed status bar.
 *
 * Left → right:
 *   1. Live dot
 *   2. **hal0** · <hostname>
 *   3. Stats (CPU / MEM/RAM / VRAM / GPU — adaptive)
 *   4. Slot tally pill (●  3/4)
 *   5. Progress chips (only when in-flight)
 *   6. Activity ticker
 *   7. Alt+~ hint · chevron
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useFooterStore } from '../../stores/footer.js'
import { useSystemStore } from '../../stores/system.js'
import { useStats } from '../../composables/useStats.js'
import ProgressChip from './ProgressChip.vue'
import ActivityTicker from './ActivityTicker.vue'

const footer = useFooterStore()
const system = useSystemStore()

defineEmits(['toggle', 'open-tab'])

// Poll faster when expanded (per spec).
const STATS_INTERVAL_COLLAPSED = 5000
const STATS_INTERVAL_EXPANDED = 2000
const { stats } = useStats(footer.expanded ? STATS_INTERVAL_EXPANDED : STATS_INTERVAL_COLLAPSED)

// ── Hostname (bold "hal0" + hostname suffix from /api/status) ──────
const hostname = computed(() => system.status?.hostname || '')

// ── Stat formatters ─────────────────────────────────────────────────
const hw = computed(() => stats.value || {})

const isUnified = computed(() => {
  // memory_kind from probe; fall back to is_uma
  const kind = system.hardware?.memory_kind || (system.hardware?.is_uma ? 'unified' : 'discrete')
  return kind === 'unified'
})

function pct(v) {
  if (v == null) return '—'
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n)) return '—'
  // Some probes return 0..1, others 0..100. Normalise.
  const p = n <= 1.5 ? n * 100 : n
  return `${Math.round(p)}%`
}
function gb(mb) {
  if (mb == null) return null
  const n = Number(mb)
  if (!Number.isFinite(n)) return null
  return n / 1024
}

const cpuPct = computed(() => pct(hw.value.cpu_util ?? hw.value.cpu_percent))
const gpuPct = computed(() => pct(hw.value.gpu_util ?? hw.value.gpu_percent))

const memUsed = computed(() => gb(hw.value.gtt_used_mb) ?? gb(hw.value.ram_used_mb) ?? hw.value.ram_used_gb ?? null)
const memTotal = computed(() => gb(hw.value.unified_memory_mb) ?? gb(hw.value.gtt_total_mb) ?? gb(hw.value.ram_total_mb) ?? null)
const ramUsed = computed(() => gb(hw.value.ram_used_mb) ?? hw.value.ram_used_gb ?? null)
const ramTotal = computed(() => gb(hw.value.ram_total_mb))
const vramUsed = computed(() => gb(hw.value.vram_used_mb))
const vramTotal = computed(() => gb(hw.value.vram_total_mb))

function fmt(used, total) {
  if (used == null || total == null) return '—'
  return `${Number(used).toFixed(1)}/${Number(total).toFixed(0)}`
}

// ── Slot tally + dot ───────────────────────────────────────────────
const tally = computed(() => footer.slotTally)
const slotDot = computed(() => footer.worstSlotDot)

// ── Proxmox integration status ─────────────────────────────────────
// Persistent indicator: pill renders only when the integration is
// configured but unreachable. Hidden when ok (transparent) and when
// unconfigured (bare-metal deployments stay quiet). Clicking opens
// Settings so the operator can re-test or rotate the token.
const pveBroken = computed(() => {
  const h = hw.value.host
  return !!(h && h.configured && !h.ok)
})
const pveError = computed(() => hw.value.host?.error || 'Cluster poll failed')

// ── Active job chips (deduped, max 3 visible, ttl on terminal) ─────
const visibleChips = ref([])

watch(
  () => footer.inFlightJobs,
  (jobs) => {
    const now = Date.now() / 1000
    // Active = in-flight; failed kept 10s; completed/cancelled kept 4s.
    const keep = []
    for (const j of jobs) {
      if (j.state === 'queued' || j.state === 'running') keep.push(j)
      else if (j.state === 'failed' && (now - (j.completedAt || now)) < 10) keep.push(j)
      else if ((j.state === 'completed' || j.state === 'cancelled') && (now - (j.completedAt || now)) < 4) keep.push(j)
    }
    visibleChips.value = keep.slice(0, 3)
  },
  { deep: true, immediate: true },
)

let chipReaper = null
onMounted(() => {
  chipReaper = setInterval(() => {
    const now = Date.now() / 1000
    visibleChips.value = visibleChips.value.filter((j) => {
      if (j.state === 'queued' || j.state === 'running') return true
      const dt = now - (j.completedAt || now)
      if (j.state === 'failed') return dt < 10
      return dt < 4
    })
  }, 1000)
})
onBeforeUnmount(() => clearInterval(chipReaper))

function chipLabel(job) {
  const model = job.model || job.id
  // Truncate model name to keep chip compact.
  const short = String(model).length > 18 ? String(model).slice(0, 16) + '…' : String(model)
  if (job.state === 'failed') return `↓ ${short}`
  return `↓ ${short}`
}

// ── Click handlers ────────────────────────────────────────────────
function onTallyClick(e) {
  e.stopPropagation()
  footer.setTab('slots')
}
function onChipClick(e) {
  e.stopPropagation()
  footer.setTab('jobs')
}
function onTickerClick(e) {
  footer.setTab('activity')
}
</script>

<template>
  <div
    class="bar"
    role="button"
    tabindex="0"
    :aria-expanded="footer.expanded"
    aria-controls="hal0-footer-pane"
    aria-label="Toggle status footer"
    @click="$emit('toggle')"
    @keydown.enter.prevent="$emit('toggle')"
    @keydown.space.prevent="$emit('toggle')"
  >
    <!-- 1. Health dot -->
    <span class="bar-dot" :class="`dot-${footer.healthDot}`" aria-hidden="true"></span>

    <!-- 2. Brand · hostname -->
    <span class="bar-brand">
      <span class="brand-h">hal0</span><span v-if="hostname" class="brand-host"> · {{ hostname }}</span>
    </span>

    <!-- 3. Stats (adaptive) -->
    <span class="bar-stats mono" aria-hidden="false">
      <template v-if="isUnified">
        <span class="stat">CPU {{ cpuPct }}</span>
        <span class="sep">·</span>
        <span class="stat">MEM {{ fmt(memUsed, memTotal) }} GB</span>
        <span class="sep">·</span>
        <span class="stat">GPU {{ gpuPct }}</span>
      </template>
      <template v-else>
        <span class="stat">CPU {{ cpuPct }}</span>
        <span class="sep">·</span>
        <span class="stat">RAM {{ fmt(ramUsed, ramTotal) }} GB</span>
        <span class="sep">·</span>
        <span class="stat" v-if="vramTotal != null && vramTotal > 0">VRAM {{ fmt(vramUsed, vramTotal) }} GB</span>
        <span class="sep" v-if="vramTotal != null && vramTotal > 0">·</span>
        <span class="stat">GPU {{ gpuPct }}</span>
      </template>
    </span>

    <!-- 4. Slot tally rollup -->
    <button
      type="button"
      class="bar-tally"
      :class="`dot-${slotDot}`"
      :title="`Slots: ${tally.running}/${tally.total} active`"
      :aria-label="`Slots ${tally.running} of ${tally.total} active`"
      @click="onTallyClick"
    >
      <span class="tally-dot" :class="`dot-${slotDot}`" aria-hidden="true"></span>
      <span class="tally-text mono">{{ tally.running }}/{{ tally.total }}</span>
    </button>

    <!-- 4b. Proxmox pill (only when configured && !ok) -->
    <router-link
      v-if="pveBroken"
      to="/settings"
      class="bar-pve"
      :title="pveError"
      :aria-label="`Proxmox integration unreachable: ${pveError}`"
      @click.stop
    >
      <span class="pve-dot" aria-hidden="true"></span>
      <span class="pve-text mono">PVE</span>
    </router-link>

    <!-- 5. Progress chips -->
    <span class="bar-chips" v-if="visibleChips.length">
      <ProgressChip
        v-for="j in visibleChips"
        :key="j.id"
        :label="chipLabel(j)"
        :pct="j.pct ?? null"
        :state="j.state"
        @click="onChipClick"
      />
    </span>

    <!-- 6. Activity ticker -->
    <ActivityTicker :event="footer.lastMeaningfulEvent" @click="onTickerClick" />

    <!-- 7. Hint + chevron -->
    <kbd class="bar-hint mono" aria-hidden="true">Alt+~</kbd>
    <span class="bar-chevron" :class="{ open: footer.expanded }" aria-hidden="true">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6"/>
      </svg>
    </span>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 10px;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  font-size: 11.5px;
  cursor: pointer;
  user-select: none;
  overflow: hidden;
  width: 100%;
}
.bar:hover { background: var(--color-surface-2); }

.bar-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-fg-faint);
}
.dot-ok    { background: var(--color-success); box-shadow: 0 0 6px color-mix(in oklch, var(--color-success) 40%, transparent); }
.dot-warn  { background: var(--color-warning); }
.dot-error { background: var(--color-danger); box-shadow: 0 0 6px color-mix(in oklch, var(--color-danger) 40%, transparent); }
.dot-idle  { background: var(--color-fg-faint); }

.bar-brand {
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-feature-settings: 'zero' 1, 'ss02' 1;
  flex-shrink: 0;
}
.brand-h { font-weight: 700; color: var(--color-fg); }
.brand-host { color: var(--color-fg-faint); font-weight: 400; }

.bar-stats {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--color-fg-muted);
  font-feature-settings: 'zero' 1, 'tnum' 1;
  white-space: nowrap;
  flex-shrink: 0;
}
.stat { color: var(--color-fg-muted); }
.sep { color: var(--color-fg-faint); opacity: 0.5; }

.bar-tally {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  height: 18px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-fg-muted);
  font-size: 10.5px;
  cursor: pointer;
  flex-shrink: 0;
}
.bar-tally:hover { background: var(--color-surface-3); color: var(--color-fg); }
.tally-dot { width: 6px; height: 6px; border-radius: 50%; }
.tally-text { font-feature-settings: 'zero' 1, 'tnum' 1; }

.bar-chips { display: inline-flex; gap: 4px; flex-shrink: 0; }

.bar-pve {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  height: 18px;
  background: color-mix(in oklch, var(--color-danger) 14%, transparent);
  border: 1px solid color-mix(in oklch, var(--color-danger) 35%, var(--color-border));
  border-radius: 999px;
  color: var(--color-danger);
  font-size: 10.5px;
  text-decoration: none;
  flex-shrink: 0;
  cursor: pointer;
}
.bar-pve:hover { background: color-mix(in oklch, var(--color-danger) 22%, transparent); }
.pve-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-danger);
  box-shadow: 0 0 6px color-mix(in oklch, var(--color-danger) 40%, transparent);
}
.pve-text { letter-spacing: 0.05em; font-weight: 600; }

.bar-hint {
  font-size: 10px;
  color: var(--color-fg-faint);
  padding: 1px 4px;
  border: 1px solid var(--color-border);
  border-radius: 3px;
  background: var(--color-surface-2);
  flex-shrink: 0;
}

.bar-chevron {
  display: inline-flex;
  align-items: center;
  color: var(--color-fg-faint);
  transition: transform 0.18s var(--hal0-ease);
  flex-shrink: 0;
}
.bar-chevron.open { transform: rotate(180deg); }
@media (prefers-reduced-motion: reduce) {
  .bar-chevron { transition: none; }
}

/* Narrow-screen — hide secondary chunks. */
@media (max-width: 720px) {
  .bar-stats { display: none; }
  .bar-hint  { display: none; }
}
@media (max-width: 480px) {
  .bar-chips  { display: none; }
}
</style>
