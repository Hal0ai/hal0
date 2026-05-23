<script setup>
/**
 * dashboard/SnapshotStrip.vue — slice #169.
 *
 * Single-line per-slot snapshot row, lifted from the v0.3 design
 * (`.snap` / `.snap-row` in /tmp/hal0-design-v3/dashboard.css).
 *
 * Each row clicks through to ``/slots/:name``. Per-slot metric strip
 * is type-aware:
 *
 *   llm           → tok/s · TTFT · ctx · KV%
 *   embed         → req/min · p50 · dim
 *   transcription → req/min · p50 · model
 *   tts           → req/min · p50 · model
 *
 * KV% renders as "—" for GPU llm slots — the bundled llama-vulkan
 * does not emit `llamacpp:kv_cache_usage_ratio` (memory:
 * hal0_llama_vulkan_no_kv_cache_metric). Until that scrape lands we
 * surface the gap honestly.
 *
 * Empty / not-configured rows render a "Configure →" affordance
 * routing to the Slots view.
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '../../stores/system.js'
import SnapshotRowSkeleton from '../skeletons/SnapshotRowSkeleton.vue'

const router = useRouter()
const system = useSystemStore()

// Initial-load skeleton: render placeholder rows before /api/status
// has ever returned (gate on !status, not on loading, so re-polls
// don't flash skeletons over already-rendered rows).
const showSkeleton = computed(() => !system.status && system.loading)

const SLOT_ORDER = ['primary', 'nano', 'agent', 'embed', 'embed-rerank', 'stt', 'tts', 'img']
function orderKey(name) {
  const i = SLOT_ORDER.indexOf(name)
  return i >= 0 ? [0, i, name] : [1, 0, name]
}

const rows = computed(() => {
  const raw = system.slots || []
  const sorted = [...raw].sort((a, b) => {
    const ka = orderKey(a.name)
    const kb = orderKey(b.name)
    if (ka[0] !== kb[0]) return ka[0] - kb[0]
    if (ka[1] !== kb[1]) return ka[1] - kb[1]
    return ka[2].localeCompare(kb[2])
  })
  return sorted
})

function stateClass(slot) {
  const st = slot.lemonade_state ?? slot.status
  if (st === 'loaded' || st === 'serving' || st === 'ready') return 'ok'
  if (st === 'loading' || st === 'idle') return 'idle'
  if (st === 'error' || st === 'failed') return 'err'
  return 'off'
}

function deviceLabel(slot) {
  const d = (slot.device || '').toLowerCase()
  if (d === 'npu') return 'NPU'
  if (d === 'gpu-rocm') return 'ROCm'
  if (d === 'gpu-vulkan') return 'Vulkan'
  if (d === 'cpu') return 'CPU'
  return d || '—'
}

function metricCells(slot) {
  const m = slot.metrics || {}
  const t = (slot.type || 'llm').toLowerCase()
  if (t === 'llm') {
    const tps = (m.tokens_per_sec ?? m.tps ?? 0).toFixed(0)
    const ttft = m.ttft_avg_seconds != null ? `${(m.ttft_avg_seconds * 1000).toFixed(0)} ms` : '—'
    const ctx = m.ctx_size ? `${m.ctx_size}` : '—'
    const isGpuLlm = ['gpu-rocm', 'gpu-vulkan'].includes((slot.device || '').toLowerCase())
    const kv = isGpuLlm
      ? '—'
      : (m.kv_cache_usage != null ? `${(m.kv_cache_usage * 100).toFixed(0)}%` : '—')
    return [
      { k: 'tok/s', v: tps },
      { k: 'TTFT',  v: ttft },
      { k: 'ctx',   v: ctx },
      { k: 'KV',    v: kv },
    ]
  }
  if (t === 'embed' || t === 'embedding') {
    return [
      { k: 'req/m', v: (m.requests_per_min ?? 0).toFixed(0) },
      { k: 'p50',   v: m.p50_ms != null ? `${m.p50_ms.toFixed(0)} ms` : '—' },
      { k: 'dim',   v: m.dim ?? '—' },
    ]
  }
  return [
    { k: 'req/m', v: (m.requests_per_min ?? 0).toFixed(0) },
    { k: 'p50',   v: m.p50_ms != null ? `${m.p50_ms.toFixed(0)} ms` : '—' },
  ]
}

function rowClick(slot) {
  router.push(`/slots/${slot.name}`)
}

function configureClick(e, slot) {
  e.stopPropagation()
  router.push(`/slots/${slot.name}`)
}
</script>

<template>
  <section class="snap" data-testid="snapshot-strip" aria-labelledby="snap-h">
    <header class="snap-head">
      <span id="snap-h">Slot snapshot</span>
      <span class="ct">· {{ rows.length }}</span>
      <span class="right" @click="router.push('/slots')">Manage slots →</span>
    </header>
    <div
      v-if="showSkeleton"
      class="snap-rows skel-rows"
      data-testid="snapshot-skeleton"
      aria-busy="true"
    >
      <SnapshotRowSkeleton v-for="i in 5" :key="i" />
    </div>
    <div v-else-if="rows.length === 0" class="snap-empty">
      No slots configured.
      <a href="#" @click.prevent="router.push('/slots')">Configure slots →</a>
    </div>
    <div v-else class="snap-rows" role="list">
      <div
        v-for="slot in rows"
        :key="slot.name"
        class="snap-row"
        :class="{ empty: !slot.model }"
        :data-testid="`snap-row-${slot.name}`"
        :data-slot-name="slot.name"
        role="listitem"
        tabindex="0"
        @click="rowClick(slot)"
        @keydown.enter="rowClick(slot)"
      >
        <span class="dot" :class="`dot-${stateClass(slot)}`" :title="slot.lemonade_state || slot.status || 'unknown'" />
        <span class="name">{{ slot.name }}</span>
        <span class="model">{{ slot.model || 'no model loaded' }}</span>
        <span class="chips">
          <span class="chip">{{ deviceLabel(slot) }}</span>
          <span v-if="slot.is_default" class="chip default" title="Default chat persona">✦</span>
          <span v-if="slot.coresident_group" class="chip co" title="Coresident with other NPU slots">co</span>
        </span>
        <span class="badge metric-strip" :aria-label="`metrics for ${slot.name}`">
          <span v-for="cell in metricCells(slot)" :key="cell.k" class="metric">
            <span class="mk">{{ cell.k }}</span>
            <span class="mv">{{ cell.v }}</span>
          </span>
        </span>
        <span class="cta">
          <a
            v-if="!slot.model"
            href="#"
            class="configure-link"
            @click="(e) => configureClick(e, slot)"
          >Configure →</a>
          <span v-else class="chev">›</span>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.snap {
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 8px;
  background: var(--color-surface, var(--bg-1, #111));
  overflow: hidden;
}
.snap-head {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-fg-muted, var(--fg-3, #888));
}
.snap-head .ct { color: var(--color-fg-faint, var(--fg-5, #555)); margin-left: 6px; }
.snap-head .right {
  margin-left: auto;
  text-transform: none;
  letter-spacing: 0;
  cursor: pointer;
  color: var(--hal0-accent, var(--accent, #feaf00));
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11px;
}
.snap-empty {
  padding: 28px 16px;
  text-align: center;
  font-size: 12px;
  color: var(--color-fg-muted, var(--fg-3, #888));
  font-family: var(--font-mono, var(--jbm, monospace));
}
.snap-empty a { color: var(--hal0-accent, var(--accent, #feaf00)); text-decoration: none; margin-left: 6px; }
.snap-empty a:hover { text-decoration: underline; }

.snap-rows { display: flex; flex-direction: column; }
.snap-row {
  display: grid;
  grid-template-columns: 14px 100px 1fr auto auto auto;
  gap: 14px;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
  font-size: 12.5px;
  cursor: pointer;
  outline: none;
}
.snap-row:last-child { border-bottom: none; }
.snap-row:hover, .snap-row:focus-visible {
  background: var(--color-surface-2, var(--bg-2, #181818));
}
.snap-row:focus-visible {
  box-shadow: inset 2px 0 0 var(--hal0-accent, var(--accent, #feaf00));
}

.dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--color-fg-faint, #555);
  box-shadow: 0 0 6px transparent;
}
.dot.dot-ok  { background: var(--color-success, #22c55e); box-shadow: 0 0 6px var(--color-success, #22c55e); }
.dot.dot-idle { background: var(--hal0-accent, var(--accent, #feaf00)); }
.dot.dot-err { background: var(--color-danger, #ef6b6b); }
.dot.dot-off { background: var(--color-fg-faint, #555); }

.name {
  color: var(--color-fg, var(--fg, #e5e5e5));
  font-weight: 500;
  font-family: var(--font-mono, var(--jbm, monospace));
}
.model {
  color: var(--color-fg-muted, var(--fg-2, #bbb));
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.snap-row.empty .model {
  color: var(--color-fg-faint, var(--fg-4, #777));
  font-style: italic;
}

.chips { display: flex; gap: 5px; align-items: center; }
.chip {
  display: inline-flex;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--color-surface-2, var(--bg-2, #181818));
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10px;
  color: var(--color-fg-muted, var(--fg-3, #888));
}
.chip.default {
  color: var(--hal0-accent, var(--accent, #feaf00));
  border-color: var(--hal0-accent, var(--accent, #feaf00));
}
.chip.co {
  color: var(--color-warning, var(--warn, #f59e0b));
  border-color: var(--color-warning, var(--warn, #f59e0b));
}

.metric-strip {
  display: inline-flex;
  gap: 12px;
}
.metric { display: inline-flex; align-items: baseline; gap: 4px; }
.mk {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10px;
  color: var(--color-fg-faint, var(--fg-4, #777));
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.mv {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 12px;
  color: var(--color-fg, var(--fg, #e5e5e5));
  font-variant-numeric: tabular-nums;
}

.cta {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 92px;
}
.configure-link {
  color: var(--hal0-accent, var(--accent, #feaf00));
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11.5px;
  text-decoration: none;
}
.configure-link:hover { text-decoration: underline; }
.chev { color: var(--color-fg-faint, var(--fg-5, #555)); font-size: 18px; line-height: 1; }

@media (max-width: 720px) {
  .snap-row { grid-template-columns: 14px 1fr auto auto; }
  .snap-row .model, .snap-row .chips { display: none; }
}
</style>
